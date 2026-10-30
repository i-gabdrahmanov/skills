#!/usr/bin/env python3
"""forge_extension.py — сборка extension forge / forge-ui ВНУТРИ проекта.

Раскладка (одна поставка на репо):

    <repo>/.gigacode/extensions/forge/       бэк-репо (профиль back)
    <repo>/.gigacode/extensions/forge-ui/    фронт-репо (профиль ui)
        gigacode-extension.json  qwen-extension.json   манифест (одинаковый; второй — дев-машина)
        hooks/  hooks/hooks.json                        control-plane и его подключение
        skills/<скилл>/…  commands/*.md                 скиллы и слэш-команды поставки
        deploy-local.sh  .forge-profile  .forge-deployed

Почему внутри проекта. Рантайм GigaCode режет shell к скриптам вне каталога проекта (откат
extension-модели 2026-08-28: extension в ~/.gigacode/extensions, и гейты, зовущие свои скрипты
шеллом, переставали работать). Рантайм же грузит extensions только из ~/.gigacode/extensions —
загрузка из каталога проекта в коде есть, но не вызывается (qwen-code 0.21.14 и 0.22.2). Поэтому
файлы лежат в проекте, а в домашний каталог кладётся только ссылка: `gigacode extensions link`
пишет `{type: "link", source: <путь>}`, и рантайм читает манифест, хуки, скиллы и команды по
пути источника. Ссылку ставит install.sh; включение — только для этого workspace.

Имя extension'а уникально на проект (`forge-<каталог>-<хеш пути>`): реестр ссылок общий на
пользователя, и второй проект с именем `forge` получил бы «already installed». Каталог в
проекте при этом всегда `forge` / `forge-ui` — его и называют брифы.

Что делает этот модуль (его зовут install.sh и deploy-local.sh; stdlib-only, Python 3.9):
  • манифест: имя, версия, MCP-серверы профиля (`mcp <имя>` в .forge-profile) с зашитым
    интерпретатором и абсолютными путями — рантайм не режет args shlex'ом;
  • hooks/hooks.json из эталона settings.hooks.json: `${PYTHON}` и пути к хукам — абсолютные
    (переменная `${CLAUDE_PLUGIN_ROOT}` не нужна: установщик знает, где лежат файлы); каждый хук
    идёт через hooks/_run_hook.py — вне своего проекта хук молча не срабатывает;
  • ссылка в рантайме (link/unlink): `<cli> extensions link`, выключение под $HOME и включение
    для своего workspace, и взаимное выключение с другими forge-проектами этой машины — правила
    рантайма не покрывают проекты вне $HOME, и /forge двух проектов там конфликтовал бы;
  • тексты для модели (*.md): `.gigacode/skills/…`, `.gigacode/hooks/…`, `.gigacode/deploy-local.sh`
    → под каталог extension'а. Python-код не переписывается: он находит себя по __file__
    (_project.gigacode_dir), а подсказки строит через _project.install_rel.

Usage:
    python3 forge_extension.py build  --ext <dir> --project <repo> --kind forge|forge-ui [--version V]
    python3 forge_extension.py check  --ext <dir> --project <repo>      # exit 0 / 1 + список
    python3 forge_extension.py name   --project <repo> --kind forge|forge-ui
    python3 forge_extension.py link   --ext <dir> --project <repo> [--runtime gigacode|qwen]
    python3 forge_extension.py unlink --ext <dir> --project <repo> [--runtime gigacode|qwen]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import resolve_hook_paths as RHP  # noqa: E402

KINDS = {"forge": "back", "forge-ui": "ui"}
MANIFESTS = ("gigacode-extension.json", "qwen-extension.json")
HOOKS_FILE = "hooks.json"
RUNNER = "_run_hook.py"
PROFILE_FILE = ".forge-profile"
DESCRIPTIONS = {
    "forge": "Forge — PDLC control-plane бэк-репо (Java/Spring): спека → дизайн → TDD с гейтами на хуках; /forge.",
    "forge-ui": "forge-ui — фронт-часть стори во фронт-репо: макет → UI-код на библиотеке проекта → проверка против макета; /forge.",
}
# Пути, которые тексты для модели называют от корня проекта (старая раскладка) → под extension.
# `~/.gigacode/…` (запреты вида «не используй ~/.gigacode/skills») не трогаем: это не путь установки.
_TEXT_RE = re.compile(r"(?<!~/)(?<!HOME/)(?<!HOME}/)\.gigacode/(skills/|hooks/|deploy-local\.sh)")


def ext_dir(project_root, kind: str) -> Path:
    return Path(project_root) / ".gigacode" / "extensions" / kind


def install_rel(kind: str) -> str:
    return f".gigacode/extensions/{kind}"


def extension_name(kind: str, project_root) -> str:
    """`<kind>-<каталог>-<6 hex пути>`: детерминировано от настоящего пути проекта."""
    real = os.path.realpath(str(project_root))
    slug = re.sub(r"[^a-z0-9]+", "-", Path(real).name.lower()).strip("-")[:24].strip("-") or "repo"
    return f"{kind}-{slug}-{hashlib.sha1(real.encode('utf-8')).hexdigest()[:6]}"


def profile_mcp(profile_text: str) -> list:
    out = []
    for line in (profile_text or "").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0] == "mcp" and parts[1] in RHP.FORGE_MCP_SERVERS:
            out.append(parts[1])
    return out


def _cmd_path(p) -> str:
    return RHP.to_command_path(str(p))


def build_hooks(template: dict, ext: Path, project_root: str, python_cmd: str) -> dict:
    """Блок hooks эталона → `<python> <ext>/hooks/_run_hook.py <ext>/hooks/<хук>.py`."""
    hooks_prefix = _cmd_path(ext / "hooks") + "/"
    root = _cmd_path(project_root)
    via_runner = f"{RHP.PYTHON_PLACEHOLDER} {hooks_prefix}{RUNNER} {hooks_prefix}"

    def _walk(node):
        if isinstance(node, str):
            return (node.replace(f"{RHP.PYTHON_PLACEHOLDER} {RHP.PLACEHOLDER}/.gigacode/hooks/", via_runner)
                    .replace(RHP.PLACEHOLDER + "/.gigacode/hooks/", hooks_prefix)
                    .replace(RHP.PLACEHOLDER, root).replace(RHP.PYTHON_PLACEHOLDER, python_cmd))
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(i) for i in node]
        return node

    return _walk(template)


def build_mcp(template: dict, names: list, ext: Path, project_root: str, python_exe: str) -> dict:
    hooks_prefix = _cmd_path(ext / "hooks") + "/"
    root = _cmd_path(project_root)

    def _walk(node):
        if isinstance(node, str):
            return (node.replace(RHP.PLACEHOLDER + "/.gigacode/hooks/", hooks_prefix)
                    .replace(RHP.PLACEHOLDER, root).replace(RHP.PYTHON_EXE_PLACEHOLDER, python_exe))
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(i) for i in node]
        return node

    return {n: _walk(template[n]) for n in names if n in template}


def version_of(rev: str) -> str:
    """`forge/v0.5.3-14-gb7c8397` → `0.5.3-14-gb7c8397`; пусто/unknown → `0.0.0`."""
    v = (rev or "").strip()
    v = v[len("forge/v"):] if v.startswith("forge/v") else v
    return v if v and v != "unknown" else "0.0.0"


def rewrite_text(text: str, kind: str) -> str:
    return _TEXT_RE.sub(lambda m: f"{install_rel(kind)}/{m.group(1)}", text)


def rewrite_tree(ext: Path, kind: str) -> int:
    """Тексты для модели (*.md поставки) — под каталог extension'а. Возвращает число файлов."""
    n = 0
    for p in sorted(ext.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = rewrite_text(text, kind)
        if new != text:
            p.write_text(new, encoding="utf-8")
            n += 1
    return n


def build(ext: Path, project_root, kind: str, rev: str = "") -> dict:
    if kind not in KINDS:
        raise ValueError(f"неизвестная поставка «{kind}»: ожидается {', '.join(KINDS)}")
    ext = Path(ext)
    root = str(Path(project_root))
    profile = (ext / PROFILE_FILE).read_text(encoding="utf-8") if (ext / PROFILE_FILE).is_file() else ""
    hooks_tmpl = json.loads((ext / "hooks" / "settings.hooks.json").read_text(encoding="utf-8"))
    mcp_tmpl = json.loads((ext / "hooks" / "settings.mcp.json").read_text(encoding="utf-8")).get("mcpServers") or {}
    hooks = build_hooks(hooks_tmpl.get("hooks") or {}, ext, root, RHP.find_python_cmd())
    (ext / "hooks" / HOOKS_FILE).write_text(json.dumps({"hooks": hooks}, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    name = extension_name(kind, root)
    # displayName нет намеренно: `extensions list` показывает его ВМЕСТО имени, а установщик ищет
    # в списке имя — со displayName повторная установка не узнавала свою же ссылку
    manifest = {"name": name, "version": version_of(rev),
                "description": DESCRIPTIONS[kind], "forgeKind": kind, "forgeRevision": rev or "unknown",
                "forgeProject": _cmd_path(root),
                "mcpServers": build_mcp(mcp_tmpl, profile_mcp(profile), ext, root, RHP.find_python_exe())}
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    for m in MANIFESTS:
        (ext / m).write_text(text, encoding="utf-8")
    return {"name": name, "version": manifest["version"], "rewritten": rewrite_tree(ext, kind),
            "mcp": sorted(manifest["mcpServers"]), "hooks": HOOKS_FILE}


def _scripts_of(command: str) -> list:
    """Все .py команды хука: запускатель и сам хук."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    return [p for p in parts if p.endswith(".py")]


def check(ext: Path, project_root) -> list:
    """Что не так с собранным extension'ом (пусто — всё на месте)."""
    ext = Path(ext)
    problems = []
    kind = ext.name
    if kind not in KINDS:
        return [f"{ext}: каталог не forge/forge-ui"]
    texts = {}
    for m in MANIFESTS:
        try:
            texts[m] = json.loads((ext / m).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            problems.append(f"{m}: не читается ({e})")
    if len({json.dumps(v, sort_keys=True) for v in texts.values()}) > 1:
        problems.append("gigacode-extension.json и qwen-extension.json разошлись")
    man = texts.get(MANIFESTS[0]) or {}
    want = extension_name(kind, project_root)
    if man and man.get("name") != want:
        problems.append(f"имя extension'а «{man.get('name')}», а для этого пути проекта — «{want}»: проект переехал? "
                        f"bash {install_rel(kind)}/deploy-local.sh")
    for name, cfg in (man.get("mcpServers") or {}).items():
        args = [a for a in cfg.get("args") or [] if a.endswith(".py")]
        if not Path(cfg.get("command", "")).exists() and not cfg.get("command", "").startswith("python"):
            problems.append(f"MCP {name}: интерпретатора {cfg.get('command')} нет")
        for a in args:
            if not Path(a).is_file():
                problems.append(f"MCP {name}: нет {a}")
    try:
        hooks = json.loads((ext / "hooks" / HOOKS_FILE).read_text(encoding="utf-8")).get("hooks") or {}
    except (OSError, ValueError) as e:
        return problems + [f"hooks/{HOOKS_FILE}: не читается ({e}) — хуки не подключены"]
    if not hooks:
        problems.append(f"hooks/{HOOKS_FILE}: блок hooks пуст — enforcement выключен")
    prefix = _cmd_path(ext / "hooks") + "/"
    real_prefix = _cmd_path(os.path.realpath(str(ext / "hooks"))) + "/"   # /var ↔ /private/var (macOS) и т.п.
    for event, groups in hooks.items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                scripts = _scripts_of(h.get("command", ""))
                if len(scripts) != 2 or Path(scripts[0]).name != RUNNER:
                    problems.append(f"{event}/{h.get('name')}: команда не через {RUNNER} <хук>.py — "
                                    f"bash {install_rel(kind)}/deploy-local.sh")
                for script in scripts:
                    if not _cmd_path(os.path.realpath(script)).startswith(real_prefix):
                        problems.append(f"{event}/{h.get('name')}: {script} вне extension'а ({prefix})")
                    elif not Path(script).is_file():
                        problems.append(f"{event}/{h.get('name')}: нет файла {script}")
    return problems


# ── ссылка в рантайме ────────────────────────────────────────────────────────────

def runtime_cli(preferred: str = "") -> "str | None":
    """CLI рантайма: явный (флаг/FORGE_RUNTIME), иначе gigacode, иначе qwen (дев-машина)."""
    want = preferred or os.environ.get("FORGE_RUNTIME", "")
    if want:
        return shutil.which(want)
    return shutil.which("gigacode") or shutil.which("qwen")


def runtime_home(cli: str) -> Path:
    """Каталог рантайма с extensions: ~/.gigacode у GigaCode, ~/.qwen у стокового qwen."""
    return Path.home() / (".qwen" if Path(cli).name.startswith("qwen") else ".gigacode")


def _run(cli: str, args: list, cwd=None, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([cli, "extensions", *args], cwd=str(cwd) if cwd else None, input=stdin,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)


def links(home: Path) -> dict:
    """Ссылки рантайма: имя → путь источника (только type=link)."""
    out = {}
    root = home / "extensions"
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        for meta in sorted(d.glob(".*-extension-install.json")):
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if m.get("type") == "link" and m.get("source"):
                out[d.name] = str(m["source"])
    return out


def forge_peers(home: Path, own: str) -> list:
    """Другие forge-проекты этой машины: (имя, корень проекта) их ссылок."""
    out = []
    for name, src in links(home).items():
        p = Path(src)
        if name != own and p.name in KINDS and p.parent.name == "extensions" and p.parent.parent.name == ".gigacode":
            out.append((name, p.parents[2]))
    return out


def link(ext: Path, project_root, preferred: str = "") -> "tuple[bool, list]":
    """Поставить ссылку и включить extension только здесь. (ok, строки для человека)."""
    ext, root = Path(ext), Path(project_root)
    name = json.loads((ext / MANIFESTS[0]).read_text(encoding="utf-8"))["name"]
    cli = runtime_cli(preferred)
    if not cli:
        return False, ["CLI рантайма (gigacode) не найден — подключить вручную:",
                       f"  gigacode extensions link {ext}",
                       f"  gigacode extensions disable --scope user {name}",
                       f"  (cd {root} && gigacode extensions enable --scope workspace {name})"]
    tag = Path(cli).name
    home = runtime_home(cli)
    lines, ok = [], True
    src = links(home).get(name)
    if src and os.path.realpath(src) == os.path.realpath(str(ext)):
        lines.append(f"✓ ссылка {name} уже стоит ({tag}) — файлы обновлены на месте")
    else:
        if src:
            _run(cli, ["uninstall", name], cwd=root)
        r = _run(cli, ["link", str(ext)], cwd=root, stdin="y\n")
        if r.returncode == 0:
            lines.append(f"✓ {tag} extensions link: {name} → {ext}")
        else:
            ok = False
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
            lines.append(f"⚠ {tag} extensions link не прошёл ({tail[0][:160]}) — вручную: {tag} extensions link {ext}")
    a = _run(cli, ["disable", "--scope", "user", name], cwd=root)
    b = _run(cli, ["enable", "--scope", "workspace", name], cwd=root)
    if a.returncode == 0 and b.returncode == 0:
        lines.append(f"✓ включён только для workspace {root}")
    else:
        ok = False
        lines.append(f"⚠ не удалось ограничить extension этим workspace — вручную: {tag} extensions disable --scope user "
                     f"{name}; (cd {root} && {tag} extensions enable --scope workspace {name})")
    for peer, peer_root in forge_peers(home, name):
        _run(cli, ["disable", "--scope", "workspace", peer], cwd=root)
        if peer_root.is_dir():
            _run(cli, ["disable", "--scope", "workspace", name], cwd=peer_root)
        lines.append(f"✓ {peer} ({peer_root}) и {name} выключены друг у друга")
    home_s = str(Path.home())
    if not os.path.realpath(str(root)).startswith(os.path.realpath(home_s) + os.sep):
        lines.append(f"⚠ проект вне $HOME: в проектах вне $HOME без forge extension может остаться включённым "
                     f"(хуки там молчат — _run_hook.py; /forge и MCP видны). Выключить там: {tag} extensions "
                     f"disable --scope workspace {name}")
    lines.append(f"Перезапусти сессию {tag} в проекте — рантайм читает extensions на старте.")
    return ok, lines


def unlink(ext: Path, project_root, preferred: str = "") -> "tuple[bool, list]":
    ext, root = Path(ext), Path(project_root)
    try:
        name = json.loads((ext / MANIFESTS[0]).read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError):
        name = extension_name(ext.name, root)
    cli = runtime_cli(preferred)
    if not cli:
        return False, [f"⚠ CLI рантайма не найден — снять ссылку вручную: gigacode extensions uninstall {name}"]
    tag = Path(cli).name
    if name not in links(runtime_home(cli)):
        return True, [f"(ссылки {name} в {tag} нет — уже снята или ставилась вручную)"]
    r = _run(cli, ["uninstall", name], cwd=root)
    if r.returncode == 0:
        return True, [f"✓ ссылка снята: {tag} extensions uninstall {name}"]
    return False, [f"⚠ {tag} extensions uninstall {name} не прошёл — снять вручную"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--ext", required=True)
    b.add_argument("--project", required=True)
    b.add_argument("--kind", required=True, choices=sorted(KINDS))
    b.add_argument("--version", default="")
    c = sub.add_parser("check")
    c.add_argument("--ext", required=True)
    c.add_argument("--project", required=True)
    n = sub.add_parser("name")
    n.add_argument("--project", required=True)
    n.add_argument("--kind", required=True, choices=sorted(KINDS))
    for cmd in ("link", "unlink"):
        lk = sub.add_parser(cmd)
        lk.add_argument("--ext", required=True)
        lk.add_argument("--project", required=True)
        lk.add_argument("--runtime", default="")
    a = ap.parse_args(argv)
    if a.cmd in ("link", "unlink"):
        ok, lines = (link if a.cmd == "link" else unlink)(Path(a.ext), a.project, a.runtime)
        for line in lines:
            print("  " + line)
        return 0 if ok else 1
    if a.cmd == "name":
        print(extension_name(a.kind, a.project))
        return 0
    if a.cmd == "build":
        print(json.dumps(build(Path(a.ext), a.project, a.kind, a.version), ensure_ascii=False))
        return 0
    problems = check(Path(a.ext), a.project)
    print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False, indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
