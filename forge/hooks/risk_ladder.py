#!/usr/bin/env python3
"""risk_ladder.py — общий модуль risk-adaptive permission ladder R0–R5 (PDLC v3.5).

Импортируется хуками (gate-guard, destructive-blocker, pii-boundary, prompt-guard). Сам не хук.
Источник политики — risk-policy.json рядом. Принцип deny-first: на R3+ при неясности — блок.

Ключевое:
  classify(tool_name, tool_input, root) -> dict(level, reason, target, command)
  level_order(level) -> int        # R0=0 .. R5=5
  requirement(level) -> dict        # из level_requirements
  check_requirement(level, requirement, root, kind, agent_type) -> (allowed: bool, reason: str)
  agent_cap(agent_type) -> level|None
Утилиты: project_root, manifest_status, approval_exists, evidence_ok, load_policy.
"""
from __future__ import annotations

import glob
import json
import os
import sys  # must be before sys.path.insert
import re
import shlex
import subprocess
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _project import skills_dir, resolve_skill_path, gigacode_home

_POLICY_PATH = gigacode_home() / "hooks" / "risk-policy.json"

# Legacy-маппинг v2→v1 для dual-read fallback в config_get: per-feature входы/решения
# (inputs.*/decisions.*) в v1 жили в pipeline.json под другими ключами (sources.*/autonomy.*).
# На v1-проектах без манифеста config_get("inputs.story") должен прочитать "sources.story".
_LEGACY_PATHS = {
    "inputs.story": "sources.story",
    "inputs.spec": "sources.spec",
    "inputs.spec_anchor": "sources.spec_anchor",
    "inputs.mode": "pipeline.mode",
    "decisions.mode_task": "pipeline.mode_task",
    "decisions.criticality": "autonomy.criticality",
    "decisions.auto_max_risk": "autonomy.auto_max_risk",
}
SKILL = "feature-pipeline"
SKILLS_DIR = skills_dir()

_LEVELS = ["R0", "R1", "R2", "R3", "R4", "R5"]

# DeprecationWarning для legacy pipeline.json (v1) — будет удалено в v3.0.
# Эмитится ТОЛЬКО при фактическом чтении legacy-файла (не на v2-only проектах, где pipeline.json
# отсутствует). Текст содержит стабильный маркер "pipeline.json is deprecated" для регрессионных тестов.
_LEGACY_DEPRECATION_MSG = (
    "ground/pipeline.json is deprecated, migrate to ground/policy.json (v2). "
    "Legacy reader will be removed in v3.0."
)


# ── Нормализация git-команды ─────────────────────────────────────────────────────────
# Глобальные опции git ПЕРЕД подкомандой (`git -C <path> push --force`, `git --git-dir=...
# reset --hard origin/main`) обходили детекторы destructive-blocker (force-push, reset --hard):
# `git -C . push --force` не совпадает с `git\s+push`. Сворачиваем ведущий кластер глобальных
# опций в голый `git`, чтобы детектор подкоманды снова совпадал. Best-effort (как вся
# Bash-детекция): значения-опции в кавычках со спецсимволами не разбираем.
_GIT_OPT_VAL = r"(?:\"[^\"]*\"|'[^']*'|\S+)"
_GIT_GLOBAL_OPTS_RE = re.compile(
    r"\bgit\b(?:\s+(?:"
    r"-C\s+" + _GIT_OPT_VAL +
    r"|-c\s+" + _GIT_OPT_VAL +
    r"|--git-dir(?:=" + _GIT_OPT_VAL + r"|\s+" + _GIT_OPT_VAL + r")"
    r"|--work-tree(?:=" + _GIT_OPT_VAL + r"|\s+" + _GIT_OPT_VAL + r")"
    r"|--namespace(?:=" + _GIT_OPT_VAL + r"|\s+" + _GIT_OPT_VAL + r")"
    r"|--exec-path(?:=" + _GIT_OPT_VAL + r")?"
    r"|--(?:no-)?pager|--paginate|--bare|--no-replace-objects|--literal-pathspecs"
    r"))+",
    re.I,
)


def normalize_git_command(command: str) -> str:
    """Свернуть ведущие глобальные опции git (`git -C <p> push` → `git push`).

    Только для целей ДЕТЕКЦИИ — исполняется всегда исходная команда. Опции коммита/подкоманды
    (`git commit -C HEAD`) не трогаются: сворачивается лишь кластер сразу после `git` перед
    первой не-опцией. `..`/квотированные пути с пробелами — best-effort."""
    if not command or "git" not in command:
        return command or ""
    return _GIT_GLOBAL_OPTS_RE.sub("git", command)


def level_order(level: str) -> int:
    try:
        return _LEVELS.index(level)
    except ValueError:
        return 1  # неизвестное → как R1 (не падаем)


# Статус последней загрузки политики: "loaded" | "missing" | "corrupt" | "unknown".
# Нужен, чтобы отличать «файла нет/битый» (enforcement OFF → deny-first) от штатной загрузки.
_POLICY_STATUS = "unknown"


def load_policy() -> dict:
    """Загружает co-located risk-policy.json. Различает отсутствие и порчу файла.

    Раньше любая ошибка глоталась в {} → classify() ставил всему R1 → при default auto_max=R1
    ВСЁ проходило авто (тихий fail-OPEN самого рискового случая). Теперь статус фиксируется в
    _POLICY_STATUS; gate-guard на основании policy_loaded() держит deny-first в пайплайне."""
    global _POLICY_STATUS
    if _POLICY_PATH.exists():
        try:
            data = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
            _POLICY_STATUS = "loaded"
            return data
        except Exception:
            _POLICY_STATUS = "corrupt"
            return {}
    _POLICY_STATUS = "missing"
    return {}


def policy_loaded() -> bool:
    """True только если risk-policy.json реально прочитан и распарсен."""
    load_policy()
    return _POLICY_STATUS == "loaded"


def pipeline_cfg(root: Path) -> dict:
    """Прочитать legacy ground/pipeline.json (v1). DEPRECATED — будет удалено в v3.0.

    Используется как dual-read fallback в :func:`config_get`. Эмитит
    DeprecationWarning ТОЛЬКО при фактическом использовании legacy-файла
    (не на v2-only проектах, где pipeline.json отсутствует).
    """
    p = root / "ground" / "pipeline.json"
    if not p.exists():
        return {}
    try:
        warnings.warn(_LEGACY_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Потолок авто-прохода. R4/R5 (снятие гейтов, откат, чувствительные пути) НИКОГДА не проходят
# авто, даже если pipeline.json выставит auto_max_risk=R4/R5 (защита от прямой правки конфига —
# см. BLOCKER-1: пусть state-write-guard и блокирует Write в pipeline.json, кламп — второй слой).
_AUTO_MAX_CEILING = "R3"


def auto_max_risk(root: Path) -> str:
    """Порог авто-прохода: decisions.auto_max_risk активной фичи (выбор критичности),
    иначе из risk-policy.json autonomy_auto_max, иначе R1. Клампится потолком R3 — R4/R5 всегда
    требуют requirement (approval+evidence), обойти авто-порогом нельзя.

    Читаем через config_get, а НЕ через pipeline_cfg: в v2 решение живёт в
    manifest.decisions.auto_max_risk, а pipeline_cfg видит только legacy ground/pipeline.json.
    На v2-native проекте (policy.json без pipeline.json) выбор фичи молча игнорировался и
    порог падал на глобальный дефолт. config_get делает manifest-first + legacy-fallback
    (_LEGACY_PATHS: decisions.auto_max_risk → autonomy.auto_max_risk), так что v1 продолжает
    работать."""
    lvl = config_get(root, "decisions.auto_max_risk")
    if isinstance(lvl, str) and lvl in _LEVELS:
        resolved = lvl
    else:
        resolved = load_policy().get("autonomy_auto_max", "R1")
        if resolved not in _LEVELS:
            resolved = "R1"
    if level_order(resolved) > level_order(_AUTO_MAX_CEILING):
        return _AUTO_MAX_CEILING
    return resolved


def criticality_set(root: Path) -> bool:
    """Выбрана ли критичность фичи (v2: decisions.criticality манифеста активной фичи).

    Читаем через config_get, а НЕ через pipeline_cfg. Прежняя реализация смотрела только в
    legacy ground/pipeline.json, и на v2-native проекте это давало ДЕДЛОК: gate-guard блокировал
    любую R2+ запись с требованием выбрать критичность, set_criticality.py честно писал её в
    manifest.decisions.criticality (ровно туда, куда указывал текст отказа), а гейт её не видел
    и блокировал снова. config_get делает manifest-first + legacy-fallback
    (_LEGACY_PATHS: decisions.criticality → autonomy.criticality) — v1 продолжает работать."""
    return bool(config_get(root, "decisions.criticality"))


def config_get(root: Path, dotpath: str,
               skill: str | None = None, feature: str | None = None):
    """Значение по dot-path из project config; None если нет.

    Приоритет для inputs.*/decisions.* — НЕ policy.json, а per-feature manifest.json
    (ground/statements/<skill>/<feature>/manifest.json). policy.json — ТОЛЬКО для
    pipeline.*/quality.*/etc. project-wide ключей. skill/feature кверим из active_manifest,
    если не переданы явно."""
    dotpath = str(dotpath)

    # 1) manifest для inputs.*/decisions.* — приоритет над policy.
    if dotpath.startswith(("inputs.", "decisions.")):
        section = "inputs" if dotpath.startswith("inputs.") else "decisions"
        sub = dotpath.split(".", 1)[1]
        try:
            from _config_loader import load_manifest
        except Exception:
            load_manifest = None
        if load_manifest is not None:
            try:
                if not skill or not feature:
                    mp = active_manifest(root)
                    if mp is not None:
                        rel = mp.relative_to(root / "ground")
                        parts = rel.parts  # ('statements', '<skill>', '<feature>', 'manifest.json')
                        skill = skill or parts[1]
                        feature = feature or parts[2]
                if skill and feature:
                    man = load_manifest(root, skill, feature)
                    sec = man.get(section) if isinstance(man, dict) else None
                    if isinstance(sec, dict):
                        cur = sec
                        for part in sub.split("."):
                            if not isinstance(cur, dict):
                                break
                            cur = cur.get(part)
                        if cur is not None or sub in sec:
                            return cur
            except Exception:
                pass

    # 2) policy.json (v2) → pipeline.json (legacy) через canonical reader.
    try:
        from _config_loader import load_project_config as _lpc
        cur = _lpc(root)
    except Exception:
        cur = pipeline_cfg(root)
    for part in str(dotpath).split("."):
        if not isinstance(cur, dict):
            cur = None
            break
        cur = cur.get(part)
    if cur is not None:
        return cur

    # 3) Legacy fallback для inputs.*/decisions.* → старые пути в pipeline.json
    #    (напр. inputs.story → sources.story). Dual-read для v1-проектов.
    legacy = _LEGACY_PATHS.get(dotpath)
    if legacy:
        cfg = pipeline_cfg(root)
        cur = cfg
        for part in legacy.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if cur is not None:
            return cur

    return None


def active_step_id(root: Path) -> str | None:
    """id активного (in_progress) шага самого свежего манифеста активной фичи.
    Единый резолвер для хуков (был скопирован в sod-enforcer/inline-phase-guard)."""
    p = active_manifest(root)
    if not p:
        return None
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
        for s in man.get("steps", []):
            if s.get("status") == "in_progress":
                return s.get("id") or None
    except Exception:
        return None
    return None


_DONE = ("completed", "skipped")


def _phase_key(step_id: str) -> str:
    """Фаза шага без суффикса задачи: '04-build-T1' → '04-build', 'fix-red' → 'fix-red'.
    Нужна только для сравнения кандидатов между собой (одна фаза или разные)."""
    sid = str(step_id or "")
    return sid.rsplit("-", 1)[0] if sid.count("-") >= 2 else sid


def ready_step_ids(root: Path) -> list[str]:
    """ВСЕ шаги, работа над которыми может идти сейчас: явный in_progress, иначе все незакрытые
    с выполненными depends_on.

    Нужен там, где неоднозначность НЕ мешает решению. `current_step_id` на параллельных задачах
    (04-build-T1 + 04-test-T2) намеренно отдаёт None — для ролевых гейтов это правильно (роли
    конфликтуют), но для actor-aware проверки «оркестратор делает работу субагентной фазы»
    ответ один при любом кандидате, и None означал бы дыру ровно на build-фазе."""
    explicit = active_step_id(root)
    if explicit:
        return [explicit]
    p = active_manifest(root)
    if not p:
        return []
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
        steps = man.get("steps", []) or []
        status = {s.get("id"): s.get("status") for s in steps}
        return [s.get("id") for s in steps
                if s.get("id") and s.get("status") not in _DONE
                and all(status.get(d) in _DONE for d in (s.get("depends_on") or []))]
    except Exception:
        return []


def current_step_id(root: Path) -> str | None:
    """Шаг, работа над которым идёт ПРЯМО СЕЙЧАС — для фазовых гейтов (required_decisions,
    phase_approvals).

    Почему не `active_step_id`: статус `in_progress` в реальном прогоне НЕ проставляется никем —
    `update.py` переводит шаг pending → completed, а промежуточную пометку брифы не делают. Из-за
    этого гейты, привязанные к «активной фазе», молчали на живых прогонах (fail-open) — включая
    fail-closed решения вроде sources.spec/sources.spec_anchor.

    Резолв: (1) явный in_progress, если он есть; иначе (2) ПЕРВЫЙ незакрытый шаг, у которого
    выполнены depends_on. Если таких «готовых к работе» шагов несколько и они из РАЗНЫХ фаз
    (напр. 04-build-T1 и 04-test-T2 на параллельных задачах) — возвращаем None: гейт остаётся
    fail-open, а не блокирует работу по угаданной фазе. Шаги одной фазы (04-test-T1/04-test-T2)
    неоднозначности не создают — фаза у них общая."""
    explicit = active_step_id(root)
    if explicit:
        return explicit
    p = active_manifest(root)
    if not p:
        return None
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
        steps = man.get("steps", []) or []
        status = {s.get("id"): s.get("status") for s in steps}
        ready = [s.get("id") for s in steps
                 if s.get("status") not in _DONE
                 and all(status.get(d) in _DONE for d in (s.get("depends_on") or []))]
        ready = [s for s in ready if s]
        if not ready:
            return None
        if len({_phase_key(s) for s in ready}) > 1:
            return None
        return ready[0]
    except Exception:
        return None


def project_root(cwd: str) -> Path:
    """Корень проекта для ДАННЫХ (ground/, docs/) — ЕДИНЫЙ резолвер `_project.find_project_root`.

    Здесь был свой, git-only: `git rev-parse --show-toplevel`, иначе cwd. В git-репозитории
    ответы совпадают (в цепочке маркеров `.git` идёт первым), но вне git скрипты форжа
    (skill_paths/_config_loader → find_project_root) находили корень по build.gradle/pom.xml/
    ground/*, а хуки — нет. Расхождение «где лежат ДАННЫЕ пайплайна» между хуками и скриптами
    и есть тот класс, из-за которого гейты то молчат, то блокируют по чужому стейту (tasks/012).
    Фолбэк на git-toplevel оставлен на случай, если канонический резолвер недоступен."""
    try:
        from _project import find_project_root
        return find_project_root(Path(cwd) if cwd else Path.cwd())
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None, capture_output=True, text=True, timeout=3,
        )
        top = out.stdout.strip()
        if out.returncode == 0 and top:
            return Path(top)
    except Exception:
        pass
    return Path(cwd or os.getcwd())


# ── извлечение цели действия ──────────────────────────────────────────────────────────
def _target_path(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("Write", "WriteFile", "Edit", "edit", "write_file", "NotebookEdit"):
        return str(tool_input.get("file_path") or tool_input.get("path") or "")
    if tool_name in ("Bash", "run_shell_command"):
        cmd = str(tool_input.get("command") or "")
        # грубо вытащить путь-аргумент с расширением/слэшем для оценки blast-radius
        m = re.findall(r"[\w./~-]+/[\w./-]+|[\w-]+\.(?:java|kt|ya?ml|properties|sql|xml|md)", cmd)
        return " ".join(m)
    return ""


# ── read-only shell ───────────────────────────────────────────────────────────────────
# Классификатор оценивает blast-radius по ПУТИ, вытащенному из команды, и не различает
# чтение и запись. Из-за этого `cat src/main/resources/application.yml` получал R4 (нужен
# approval человека!), а `grep … src/main/java/**/auth/Foo.java` — R3, причём R4 держится и
# ВНЕ пайплайна. Чтение ничего не меняет: blast-radius нулевой независимо от пути.
# Список закрытый (allowlist), и любой признак записи его снимает — деградация в сторону
# гейта, а не в сторону пропуска.
_READ_ONLY_CMDS = frozenset({
    "cat", "head", "tail", "less", "more", "nl", "wc", "file", "stat", "du", "df",
    "grep", "egrep", "fgrep", "rg", "ack", "ag", "ls", "tree", "basename", "dirname",
    "realpath", "readlink", "diff", "cmp", "sort", "uniq", "cut", "tr", "jq", "yq",
    "xxd", "od", "md5sum", "shasum", "sha256sum", "which", "type", "pwd", "date",
    "echo", "printf", "true", "false", "column", "env", "hostname", "uname", "id",
})
_READ_ONLY_GIT_SUB = frozenset({
    "status", "diff", "log", "show", "rev-parse", "branch", "remote", "describe",
    "blame", "ls-files", "ls-tree", "cat-file", "shortlog", "grep", "whatchanged",
})
# Флаги, превращающие читателя в писателя.
_INPLACE_FLAG_RE = re.compile(r"(?:^|\s)-{1,2}i(?:\b|[^\w-])|--in-place\b")
_FIND_WRITE_RE = re.compile(r"-(?:delete|exec|execdir|ok|okdir|fprint\w*)\b")
# Редирект: `> f`, `>> f`, `2> f`. Безобидные цели (/dev/null, &1) чтение не отменяют.
_REDIR_TARGET_RE = re.compile(r"(?:^|[\s;|&])[0-9]*>>?\s*(&?[\w./~+-]+)")
_HARMLESS_REDIR = frozenset({"/dev/null", "&1", "&2", "/dev/stdout", "/dev/stderr"})
_SEGMENT_RE = re.compile(r"\|\||&&|[;|\n]")


def is_read_only_command(command: str) -> bool:
    """Команда только ЧИТАЕТ (можно не гейтить по blast-radius пути).

    Требования жёсткие: каждый сегмент конвейера — из закрытого списка читателей, никаких
    редиректов кроме /dev/null и &N, у `sed`/`perl` нет `-i`, у `find` — нет `-delete`/`-exec`.
    Неуверенность = не read-only (тогда решает обычный ladder)."""
    if not command or not command.strip():
        return False
    for target in _REDIR_TARGET_RE.findall(command):
        if target not in _HARMLESS_REDIR:
            return False
    for seg in _SEGMENT_RE.split(command):
        seg = seg.strip()
        if not seg:
            continue
        try:
            toks = shlex.split(seg)
        except ValueError:
            return False
        # ведущие присваивания окружения (FOO=bar cmd …) пропускаем
        while toks and re.match(r"^[A-Za-z_][\w]*=", toks[0]):
            toks.pop(0)
        if not toks:
            return False
        name = os.path.basename(toks[0])
        if name in ("sudo", "env"):
            return False
        if name == "git":
            sub = next((t for t in toks[1:] if not t.startswith("-")), "")
            if sub not in _READ_ONLY_GIT_SUB:
                return False
            continue
        if name in ("sed", "perl", "awk", "gawk"):
            if name in ("sed", "perl") and _INPLACE_FLAG_RE.search(seg):
                return False
            if name in ("awk", "gawk"):
                return False          # awk умеет писать файлы из программы — не разбираем
            continue
        if name == "find":
            if _FIND_WRITE_RE.search(seg):
                return False
            continue
        if name not in _READ_ONLY_CMDS:
            return False
    return True


def _command(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("Bash", "run_shell_command"):
        return str(tool_input.get("command") or "")
    return ""


def classify(tool_name: str, tool_input: dict, root: Path | None = None) -> dict:
    """Вернуть {level, reason, target, command}. Берём максимум из path_risk и command_risk."""
    policy = load_policy()
    tool_input = tool_input or {}
    target = _target_path(tool_name, tool_input)
    command = _command(tool_name, tool_input)

    best = policy.get("default_level", "R1")
    reason = "default"

    # path_risk — по убыванию риска R5..R0, первое совпадение даёт класс
    for lvl in ("R5", "R4", "R3", "R2", "R1", "R0"):
        for pat in policy.get("path_risk", {}).get(lvl, []):
            if target and re.search(pat, target):
                if level_order(lvl) >= level_order(best) or reason == "default":
                    best, reason = lvl, f"path~{pat}"
                break
        else:
            continue
        break

    # command_risk может поднять класс. Матчим по НОРМАЛИЗОВАННОЙ команде, иначе
    # `git -C . push`/`git -c k=v commit` классифицировались бы как R1-default (обход R4/R2).
    command_norm = normalize_git_command(command)
    for lvl, pats in policy.get("command_risk", {}).items():
        if lvl.startswith("_"):
            continue
        for pat in pats:
            if command_norm and re.search(pat, command_norm, re.I):
                if level_order(lvl) > level_order(best):
                    best, reason = lvl, f"cmd~{pat}"
    return {"level": best, "reason": reason, "target": target, "command": command}


def requirement(level: str) -> dict:
    return load_policy().get("level_requirements", {}).get(level, {})


def agent_cap(agent_type: str | None) -> str | None:
    if not agent_type:
        return None
    for pat, cap in load_policy().get("agent_caps", {}).items():
        if pat.startswith("_"):
            continue
        if re.search(pat, agent_type):
            return cap
    return None


# ── проверки выполнения требований уровня ─────────────────────────────────────────────
def active_manifest(root: Path) -> Path | None:
    """Активная фича = самый свежий manifest под statements/*/*/ ПО ВСЕМ skill-namespace
    (feature-pipeline, forgelite, forgefix), кроме archived. Резолв glob-овый, а не по списку
    имён, поэтому новая ветка forge подхватывается хуками без правки этого модуля. Так один
    общий control-plane обслуживает full-, lite- и fix-ветку: активна та, чей manifest свежее."""
    base = root / "ground" / "statements"
    newest, mt = None, -1.0
    try:
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            for d in skill_dir.iterdir():
                if not d.is_dir() or d.name == "archived":
                    continue
                mp = d / "manifest.json"
                if not mp.exists():
                    continue
                try:
                    m = mp.stat().st_mtime
                except OSError:
                    continue
                if m > mt:
                    newest, mt = mp, m
    except Exception:
        return None
    return newest


def manifest_status(root: Path) -> dict:
    p = active_manifest(root)
    if not p:
        return {}
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
        return {s.get("id"): s.get("status") for s in man.get("steps", [])}
    except Exception:
        return {}


def manifest_exists(root: Path) -> bool:
    return active_manifest(root) is not None


def active_feature_name(root: Path) -> str | None:
    """Слаг активной фичи (каталог самого свежего манифеста ПО ВСЕМ namespace).
    В отличие от _project.active_feature смотрит все ветки (fix/lite/full), а не только
    feature-pipeline — им пользуются гейты, привязывающие approval-маркер к фиче."""
    p = active_manifest(root)
    return p.parent.name if p else None


def approval_exists(root: Path, key: str) -> bool:
    """Есть ли ФИЗИЧЕСКИЙ маркер согласия по ключу (legacy-файл approvals/<key>.json).

    Только «файл существует» — для диагностических сообщений («маркер есть, но БЕЗ
    провенанса record_approval»). ВАЛИДНОСТЬ (провенанс + журнал) решает approval_valid
    и gate-guard: с миграции на журнал record_approval пишет маркеры строкой в
    ground/approvals.jsonl, и гейт-валидатор обязан смотреть туда. Этот предикат не трогаем —
    он про наличие файла, а не про провенанс."""
    return (root / "ground" / "approvals" / f"{key}.json").exists()


def approval_valid(root: Path, key: str) -> bool:
    """Валидное «да» человека по ключу (провенанс record_approval).

    С миграции на журнал record_approval пишет маркеры строкой в ground/approvals.jsonl, и
    прямой файл approvals/<key>.json перестал создаваться — `.exists()` по нему не видел
    легитимное согласие. Читаем через forge_events.approval(): она умеет и журнал (.jsonl),
    и legacy .json, и фильтрует по провенансу. Импорт лениво-мягкий — точку входа не роняем."""
    try:
        import forge_events as _FE
        return _FE.approval(root, key) is not None
    except Exception:
        return (root / "ground" / "approvals" / f"{key}.json").exists()


def evidence_ok(root: Path, threshold: float = 0.95) -> tuple[bool, str]:
    """Есть ли хотя бы один evidence-пакет с completeness >= threshold."""
    files = glob.glob(str(root / "ground" / "evidence" / "*.json"))
    if not files:
        return False, "нет ground/evidence/*.json"
    worst = 1.0
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            c = float(d.get("completeness", 0))
            worst = min(worst, c)
        except Exception:
            return False, f"битый evidence: {os.path.basename(f)}"
    return (worst >= threshold), f"min completeness={worst:.2f} (порог {threshold})"


# Одна и та же ФАЗА называется по-разному в трёх ветках forge: full (02-design), lite
# (lite-design), fix (fix-diag). level_requirements.steps ссылается на full-имя, а
# check_requirement резолвил его буквально — в fix/lite такого шага нет НИКОГДА, значит
# требование R2 (`src/main/**.java`!) было невыполнимо в принципе: любая запись прод-кода
# упиралась в «шаг 02-design не completed (=None)» без легального выхода. Резолвим по фазе.
_STEP_PHASE_ALIASES = {
    "02-design": ("02-design", "lite-design", "fix-diag"),
    "02-sdd": ("02-sdd",),
    "01-grounding": ("01-grounding", "lite-ground"),
}


def _step_requirement_state(status: dict, sid: str) -> tuple:
    """(state, ids): 'completed' | 'open' | 'absent' для требуемой фазы `sid`.

    Совпадением считается точный id, id с суффиксом задачи (`04-build-T1`) и любой алиас
    фазы из _STEP_PHASE_ALIASES. 'absent' — в манифесте нет ни одного шага этой фазы
    (ветка её просто не содержит)."""
    candidates = _STEP_PHASE_ALIASES.get(sid, (sid,))
    seen = [(k, v) for k, v in status.items()
            if isinstance(k, str) and any(k == c or k.startswith(c + "-") for c in candidates)]
    if not seen:
        return "absent", ""
    open_ids = [k for k, v in seen if v not in _DONE]
    if not open_ids:
        return "completed", ", ".join(k for k, _ in seen)
    return "open", ", ".join(open_ids)


def check_requirement(level: str, req: dict, root: Path, kind: str,
                      agent_type: str | None = None) -> tuple[bool, str]:
    """kind: 'jira' | 'write' | 'other' (git commit/push не гейтим — доставку делает
    пользователь сам). Вернуть (allowed, reason)."""
    mode = req.get("mode", "require")
    if mode in ("auto", "auto_log"):
        return True, f"{level} {mode}"

    status = manifest_status(root)

    # required completed steps — по ФАЗЕ, а не по литеральному id (см. _STEP_PHASE_ALIASES)
    for sid in req.get("steps", []):
        state, ids = _step_requirement_state(status, sid)
        if state == "absent":
            # Ветка не содержит этой фазы. Требовать её — вечный тупик, поэтому пропускаем.
            # Но ТОЛЬКО при читаемом манифесте: пустой status (манифест есть, но не
            # распарсился) — это неясность, а на неясности мы блокируем (deny-first).
            if status:
                continue
            return False, (f"{level}: манифест не читается — фазу '{sid}' не проверить "
                           f"(deny-first)")
        if state != "completed":
            return False, f"{level}: шаг {ids or sid} не completed"

    # approval marker — валидное «да» человека: журнал approvals.jsonl (текущий формат
    # record_approval) ИЛИ legacy-файл с провенансом. С миграции на журнал record_approval
    # пишет маркеры строкой в .jsonl, и голый `.exists()` по approvals/<key>.json перестал
    # видеть легитимное согласие — поэтому к требению смотрим approval_valid, а не approval_exists.
    appr = req.get("approval")
    # Ключ конкретного класса действия перекрывает общий ключ уровня: создание Jira-задачи —
    # R3, но 'security-review' там ни при чём (см. kind_approval в risk-policy.json).
    kind_key = (load_policy().get("kind_approval") or {}).get(kind)
    if isinstance(kind_key, str) and kind_key and not kind_key.startswith("_"):
        appr = kind_key
    if appr and not approval_valid(root, appr):
        # Без рецепта отказ выглядит тупиком, и модель уходит в override_judge (а это R4 —
        # ещё один deny). Печатаем ровно ту команду, которой маркер и заводится.
        return False, (
            f"{level}: нет валидного approval-маркера '{appr}'. Порядок: (1) спроси "
            f"пользователя и покажи, что именно делаешь; (2) после явного «да» — python3 "
            f"<project>/.gigacode/skills/pipeline-state/scripts/record_approval.py --key {appr} "
            f"--approved-by user --reason \"<кто/почему>\" (прямая запись в approvals/ "
            f"заблокирована state-write-guard); (3) повтори действие")

    # evidence bundle
    if req.get("evidence"):
        ok, why = evidence_ok(root)
        if not ok:
            return False, f"{level}: evidence не готов — {why}"

    return True, f"{level}: требования выполнены"
