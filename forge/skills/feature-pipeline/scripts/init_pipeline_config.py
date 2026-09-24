#!/usr/bin/env python3
"""Scaffold <project>/ground/policy.json — единый параметр-стор конвейера.

Авто-детектит, что может (build-система, модули, пакет, версии, инструмент миграций),
и оставляет плейсхолдеры (null) для того, что должен заполнить человек/оркестратор:
Jira-ключ, Bitbucket workspace/repo, и т.п. Stdlib only.

Per-feature поля (inputs.*, decisions.* — story/spec/spec_anchor/mode/criticality/auto_max_risk/mode_task) живут в `ground/statements/<skill>/<feature>/manifest.json` v2 — здесь их нет.

Использование:
    python init_pipeline_config.py --project <root>          # создать (не перезапишет)
    python init_pipeline_config.py --project <root> --update  # обновить только детект-поля
    python init_pipeline_config.py --project <root> --force   # перезаписать целиком
    python init_pipeline_config.py --project <root> --print    # показать, что задетектил, без записи

Незаполненные обязательные поля помечаются null и попадают в "_incomplete" —
оркестратор по этому списку понимает, о чём спросить пользователя.
"""
# PEP 604 (`X | None`) в аннотациях ниже вычисляется лениво только с этим импортом; без него
# на Python 3.9 модуль падает TypeError на import. Его тянет doctor.py — и падал весь doctor.
from __future__ import annotations

import argparse, json, os, re, subprocess, sys, glob

SCHEMA_VERSION = "feature-pipeline/config@2"  # v2: project-wide в policy.json, per-feature в manifest.json

# cmd.exe (куда на Windows всегда уходит shell=True в run_pending_evals.py/check_tests_red.py,
# вне зависимости от оболочки, из которой запущен сам python) не умеет ни в shebang, ни
# в "./" без расширения — нужен gradlew.bat.
_GRADLEW = "gradlew.bat" if sys.platform == "win32" else "./gradlew"


def sh(cmd, cwd):
    try:
        return subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return ""


def _repo_root():
    # git toplevel или cwd — чтобы скиллу не нужен $(pwd)/$(git ...) в shell-вызове.
    return sh(["git", "rev-parse", "--show-toplevel"], os.getcwd()) or os.getcwd()


def detect_build_system(root):
    if glob.glob(os.path.join(root, "settings.gradle*")) or glob.glob(os.path.join(root, "build.gradle*")):
        return "gradle"
    if os.path.exists(os.path.join(root, "pom.xml")):
        return "maven"
    return None


def detect_default_branch(root):
    ref = sh(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], root)
    if ref:
        return ref.rsplit("/", 1)[-1]
    cur = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    return cur or "main"


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def detect_gradle_modules(root):
    txt = read_text(os.path.join(root, "settings.gradle")) or \
          read_text(os.path.join(root, "settings.gradle.kts"))
    return re.findall(r"include[\s(]+['\"]:?([^'\"]+)['\"]", txt)


def detect_maven_modules(root):
    txt = read_text(os.path.join(root, "pom.xml"))
    return re.findall(r"<module>\s*([^<]+?)\s*</module>", txt)


def gather_build_files(root, build_system):
    if build_system == "gradle":
        pats = ["build.gradle", "build.gradle.kts"]
    else:
        pats = ["pom.xml"]
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        if any(seg in dirpath for seg in (os.sep + ".git", os.sep + "build", os.sep + ".gradle", os.sep + "node_modules")):
            continue
        for fn in filenames:
            if fn in pats:
                found.append(os.path.join(dirpath, fn))
    return found


def detect_group(root, build_files):
    for bf in build_files:
        m = re.search(r"group\s*=\s*['\"]([\w.]+)['\"]", read_text(bf))
        if m:
            return m.group(1)
    return None


def detect_versions(build_files):
    java_v = spring_v = None
    for bf in build_files:
        t = read_text(bf)
        if java_v is None:
            m = re.search(r"JavaLanguageVersion\.of\((\d+)\)", t) or re.search(r"sourceCompatibility\s*=\s*['\"]?(\d+)", t)
            if m:
                java_v = m.group(1)
        if spring_v is None:
            m = re.search(r"spring[-]?boot[^\n]*?(\d+\.\d+\.\d+)", t, re.I)
            if m:
                spring_v = m.group(1)
    return java_v, spring_v


# Каталоги-артефакты сборки / служебные: changelog внутри них — не исходник, а копия
# (в реальном прогоне hits[0] хватал database/build/resources/main/db/changelog).
_SKIP_SEGMENTS = ("build", "out", "target", "bin", ".gradle", ".git", "node_modules")

# ── v1 → v2 migration (init_pipeline_config.py --check-migration / --migrate) ────
# В v1 весь конфиг лежал в ground/pipeline.json: и project-wide (quality/conventions/
# docs/jira/...), и per-feature (features.<key>: {skill, sources.*, pipeline.*,
# autonomy.*, steps}). v2 разделил их: policy.json для project-wide, manifest.json
# (per-feature) для inputs/decisions/steps.
#
# Ключи, ОЖИДАЕМЫЕ как project-wide. Это список для отчёта, а не фильтр: в policy.json
# переезжает весь top-level v1-конфига кроме features/<...> (см. _split_v1_project_wide).
# Ключ не из списка попадает в policy.json как есть И в unknown_keys отчёта — консервативно:
# лучше оставить ключ и показать его оператору, чем молча потерять.
_PROJECT_WIDE_KEYS = (
    "project", "conventions", "quality", "evidence", "risk",
    "security", "hooks", "docs", "sdd", "adr", "spec",
    "jira", "bitbucket", "delivery",
    "phases_override", "$schema", "_incomplete",
)

# Top-level per-feature ключи в v1 (features.<key> → manifest).
# Копируются в manifest как есть (skill, steps).
_LEGACY_FEATURE_TOP = {
    "skill": "skill",
    "steps": "steps",
}

# Маппинг вложенных путей feature.<key>.<dotted> → manifest.<section>.<key>.
# sources.* / pipeline.mode → inputs.* ; pipeline.mode_task + autonomy.* → decisions.*
# Ровно как в FORGE.md "Manual migration".
_LEGACY_FEATURE_NESTED = {
    "sources.story":       ("inputs", "story"),
    "sources.spec":        ("inputs", "spec"),
    "sources.spec_anchor": ("inputs", "spec_anchor"),
    "pipeline.mode":       ("inputs", "mode"),
    "pipeline.mode_task":  ("decisions", "mode_task"),
    "autonomy.criticality":        ("decisions", "criticality"),
    "autonomy.auto_max_risk":      ("decisions", "auto_max_risk"),
}

LEGACY_MANIFEST_VERSION = 2


def _split_v1_project_wide(pipeline: dict) -> tuple[dict, list[str]]:
    """Разбить v1-pipeline.json на (project_wide_dict, unknown_keys).

    unknown_keys — top-level ключи, которых нет ни в project-wide whitelist, ни в
    features/<...>. Они ПЕРЕНОСЯТСЯ в policy.json как есть (top-level, без обёртки) и
    дополнительно перечисляются в отчёте, чтобы оператор их пересмотрел.

    Раньше unknown только перечислялись в отчёте, а в policy.json НЕ попадали — то есть
    молча пропадали из мигрированного проекта (оставаясь лишь в pipeline.json.v1.bak).
    Это противоречило заявленному здесь принципу «лучше оставить ключ, чем потерять» и
    ломало, в частности, `autonomy.mode`: он project-wide по реестру параметров
    (file=pipeline), но `autonomy` в whitelist не входит, потому что
    `autonomy.criticality`/`auto_max_risk` — per-feature. Перенос as-is снимает и это:
    per-feature половина уезжает в manifest из features.<slug>, а top-level остаток
    остаётся в policy.
    """
    project_wide = {}
    unknown = []
    for k, v in pipeline.items():
        if k == "features":
            continue
        project_wide[k] = v          # переносим ВСЁ; whitelist влияет только на отчёт
        if k not in _PROJECT_WIDE_KEYS:
            unknown.append(k)
    return project_wide, unknown


def _split_feature_slug(feature_slug) -> tuple[str | None, str | None, str | None]:
    """Разобрать v1-ключ features.<slug> формата '<skill>/<feature>'.

    v1-конвенция: один '/', слева — имя скилла, справа — имя фичи. Возвращает
    (skill, feature, error). error != None при любой невалидности формата
    (нет '/', больше одного '/', пустые компоненты, не-строка). Не делаем
    тихих fallback'ов: 'forgefix' или 'forgefix/fix-y/extra' одинаково мусор."""
    if not isinstance(feature_slug, str):
        return None, None, f"feature slug должен быть строкой, получено {type(feature_slug).__name__}"
    parts = feature_slug.split("/")
    if len(parts) != 2:
        return None, None, (f"feature slug '{feature_slug}' имеет {len(parts) - 1} '/', "
                            f"ожидается ровно один (формат '<skill>/<feature>')")
    skill, feature = parts
    if not skill or not feature:
        return None, None, f"feature slug '{feature_slug}' содержит пустой skill или feature"
    return skill, feature, None


def _feature_to_manifest(feature_slug: str, feature_body: dict) -> tuple[dict | None, str | None]:
    """Преобразовать v1-features.<slug> → v2 manifest dict.

    Возвращает (manifest, error_msg). manifest = None при ошибке.
    Slug формата '<skill>/<feature>': слева — bare skill name (имя директории
    и значение manifest.skill), справа — bare feature name (имя директории
    и значение manifest.feature). feature_body.skill ОБЯЗАН совпадать с левой
    частью slug'а — иначе v1-конфиг несогласован и молча мигрировать опасно."""
    if not isinstance(feature_body, dict):
        return None, f"feature '{feature_slug}' is not a dict"

    skill_from_slug, bare_feature, slug_err = _split_feature_slug(feature_slug)
    if slug_err:
        return None, slug_err

    declared_skill = feature_body.get("skill")
    if not declared_skill:
        return None, (f"feature '{feature_slug}' has no 'skill' field — "
                      f"нельзя верифицировать путь манифеста (ожидалось '{skill_from_slug}')")
    if declared_skill != skill_from_slug:
        return None, (f"feature '{feature_slug}' несогласован: skill='{declared_skill}' "
                      f"не совпадает со skill из slug'а '{skill_from_slug}'")

    manifest = {
        "version": LEGACY_MANIFEST_VERSION,
        "skill": skill_from_slug,
        "feature": bare_feature,
        "inputs": {},
        "decisions": {},
        "steps": [],
    }
    for src, dst in _LEGACY_FEATURE_TOP.items():
        if src in feature_body:
            manifest[dst] = feature_body[src]
    for dotted, (section, key) in _LEGACY_FEATURE_NESTED.items():
        cur = feature_body
        parts = dotted.split(".")
        found = True
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                found = False
                break
            cur = cur[p]
        if found:
            manifest[section][key] = cur
    return manifest, None


def _plan_migration(root: str, pipeline: dict) -> dict:
    """Спланировать миграцию: что куда переедет. Возвращает dict для печати.

    Без побочных эффектов — используется и --check-migration, и --migrate.
    """
    project_wide, unknown = _split_v1_project_wide(pipeline)
    features = pipeline.get("features") or {}
    feature_plans = []
    feature_errors = []
    for slug, body in features.items():
        manifest, err = _feature_to_manifest(slug, body)
        if err:
            feature_errors.append({"feature": slug, "error": err})
            continue
        feature_plans.append({
            "feature": manifest["feature"],
            "feature_slug": slug,
            "manifest_path": f"ground/statements/{manifest['skill']}/{manifest['feature']}/manifest.json",
            "skill": manifest["skill"],
            "inputs": manifest["inputs"],
            "decisions": manifest["decisions"],
            "steps_count": len(manifest.get("steps") or []),
        })
    return {
        "root": root,
        "from": "ground/pipeline.json",
        "to_policy": "ground/policy.json",
        "to_backup": "ground/pipeline.json.v1.bak",
        "project_wide_keys": sorted(project_wide.keys()),
        "unknown_keys": sorted(unknown),
        "feature_count": len(features),
        "features": feature_plans,
        "feature_errors": feature_errors,
    }


def _write_migration(root: str, pipeline: dict, plan: dict) -> dict:
    """Выполнить миграцию: policy.json + manifest.json'ы + .v1.bak.

    Идемпотентность: backup файла (.v1.bak) перезаписывается только если
    существующий .v1.bak идентичен исходному pipeline.json (повторный прогон).
    В противном случае — отказываемся писать (защита от потери данных при
    повторной миграции после ручной правки)."""
    policy_path = os.path.join(root, "ground", "policy.json")
    backup_path = os.path.join(root, "ground", "pipeline.json.v1.bak")
    legacy_path = os.path.join(root, "ground", "pipeline.json")

    if os.path.exists(policy_path):
        return {"status": "error",
                "error": f"{policy_path} already exists; refusing to overwrite. "
                         f"Remove or merge manually before re-running --migrate."}

    if os.path.exists(backup_path):
        with open(backup_path, encoding="utf-8") as f:
            existing_backup = f.read()
        if existing_backup != json.dumps(pipeline, ensure_ascii=False, indent=2) + "\n":
            return {"status": "error",
                    "error": f"{backup_path} exists and differs from current pipeline.json. "
                             f"Refusing to overwrite a non-matching backup."}

    project_wide, _unknown = _split_v1_project_wide(pipeline)

    # Политику пишем первой: если упадём ниже — бэкап ещё не тронут, можно повторить.
    os.makedirs(os.path.dirname(policy_path), exist_ok=True)
    with open(policy_path, "w", encoding="utf-8") as f:
        json.dump(project_wide, f, ensure_ascii=False, indent=2)
        f.write("\n")

    written_manifests = []
    for entry in plan["features"]:
        mpath = os.path.join(root, entry["manifest_path"])
        manifest = {
            "version": LEGACY_MANIFEST_VERSION,
            "skill": entry["skill"],
            "feature": entry["feature"],
            "inputs": entry["inputs"],
            "decisions": entry["decisions"],
        }
        # steps читаем из исходного pipeline по slug'у (ключ в pipeline["features"]);
        # entry["feature"] — уже bare имя после фикса (slug бьётся в _plan_migration).
        src_slug = entry.get("feature_slug")
        if src_slug is None:
            # fallback для планов из старых версий: bare name == slug в обеих раскладках,
            # но для v1-данных ищем сначала bare, потом сам slug.
            src_slug = entry["feature"]
        src_steps = pipeline.get("features", {}).get(src_slug, {}).get("steps")
        if src_steps is None:
            src_steps = pipeline.get("features", {}).get(entry["feature"], {}).get("steps")
        if src_steps is not None:
            manifest["steps"] = src_steps
        os.makedirs(os.path.dirname(mpath), exist_ok=True)
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write("\n")
        written_manifests.append(mpath)

    # Бэкап исходного pipeline.json → .v1.bak (после успешной записи policy +
    # manifests; если упадём тут — v1 остаётся, оператор увидит warning и починит).
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(pipeline, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # Удаляем оригинальный pipeline.json (бэкап сохранён). Если оператор хочет
    # сохранить старый pipeline.json — пусть копирует ДО запуска --migrate.
    os.remove(legacy_path)

    return {
        "status": "migrated",
        "policy": policy_path,
        "backup": backup_path,
        "manifests": written_manifests,
        "feature_count": plan["feature_count"],
        "unknown_keys": plan["unknown_keys"],
    }


def _is_build_artifact(path, root):
    """True, если путь лежит внутри каталога сборки/служебного (посегментно от root)."""
    rel = os.path.relpath(path, root)
    return any(seg in _SKIP_SEGMENTS for seg in rel.split(os.sep))


def _find_changelog_dirs(root):
    """Все каталоги миграций (db/changelog | db/migration) по всему репо, без артефактов сборки."""
    found = []
    for d in ("db/changelog", "db/migration"):
        for hit in glob.glob(os.path.join(root, "**", d), recursive=True):
            if os.path.isdir(hit) and not _is_build_artifact(hit, root):
                found.append(hit)
    return found


def _primary_changelog(dirs):
    """Лучший одиночный changelog: под src/main/resources в приоритете, иначе кратчайший путь.
    Детерминированно (замена произвольного hits[0])."""
    def key(d):
        rel = d.replace(os.sep, "/")
        under_src = 0 if "/src/main/resources/" in rel + "/" else 1
        return (under_src, len(rel), rel)
    return sorted(dirs, key=key)[0]


def _owning_service(changelog_dir, service_roots):
    """Ближайший корень-сервиса (каталог с build-файлом), являющийся предком changelog-каталога.
    root всегда в наборе, поэтому результат непустой (корень репо → сам root)."""
    cl = os.path.abspath(changelog_dir)
    best = None
    for sr in service_roots:
        if cl == sr or cl.startswith(sr + os.sep):
            if best is None or len(sr) > len(best):
                best = sr
    return best


def _service_tool(service_root, build_files, global_tool):
    """Инструмент миграций конкретного сервиса — по его собственным build-файлам;
    если они молчат — откат на глобально задетекченный."""
    txt = " ".join(
        read_text(bf).lower() for bf in build_files
        if os.path.abspath(os.path.dirname(bf)) == service_root
    )
    if "liquibase" in txt:
        return "liquibase"
    if "flyway" in txt:
        return "flyway"
    return global_tool


def detect_migration_tool(root, build_files):
    """Глобальный (первичный) инструмент + лучший одиночный changelog — для скаляров
    conventions. Полный список сервисов даёт detect_migration_services()."""
    blob = " ".join(read_text(bf).lower() for bf in build_files)
    if "liquibase" in blob:
        tool = "liquibase"
    elif "flyway" in blob:
        tool = "flyway"
    else:
        tool = "none"
    changelog = None
    dirs = _find_changelog_dirs(root)
    if dirs:
        changelog = os.path.relpath(_primary_changelog(dirs), root)
    return tool, changelog


def detect_migration_services(root, build_files, global_tool):
    """Все сервисы монорепо с миграциями: по одной записи
    {service, migration_tool, changelog_path} на каждый найденный changelog-каталог.
    service — путь корня сервиса относительно репо (корень репо → "."). Дедуп по
    changelog_path, детерминированная сортировка."""
    service_roots = {os.path.abspath(os.path.dirname(bf)) for bf in build_files}
    service_roots.add(os.path.abspath(root))
    services = {}
    for cl in _find_changelog_dirs(root):
        cl_rel = os.path.relpath(cl, root)
        if cl_rel in services:
            continue
        owner = _owning_service(cl, service_roots)
        services[cl_rel] = {
            "service": os.path.relpath(owner, root),
            "migration_tool": _service_tool(owner, build_files, global_tool),
            "changelog_path": cl_rel,
        }
    return [services[k] for k in sorted(services)]


def detect_jacoco(build_files):
    return any("jacoco" in read_text(bf).lower() for bf in build_files)


def build_config(root):
    bs = detect_build_system(root)
    build_files = gather_build_files(root, bs) if bs else []
    if bs == "gradle":
        modules = detect_gradle_modules(root)
    elif bs == "maven":
        modules = detect_maven_modules(root)
    else:
        modules = []
    group = detect_group(root, build_files)
    java_v, spring_v = detect_versions(build_files)
    mig_tool, changelog = detect_migration_tool(root, build_files)
    migration_services = detect_migration_services(root, build_files, mig_tool)
    has_jacoco = detect_jacoco(build_files)
    is_git = bool(sh(["git", "rev-parse", "--show-toplevel"], root))

    gradle = bs == "gradle"
    cfg = {
        "$schema": SCHEMA_VERSION,
        "project": {
            "name": os.path.basename(os.path.abspath(root)),
            "build_system": bs,
            "is_multi_module": len(modules) > 1,
            "modules": modules,
            "default_branch": detect_default_branch(root),
            "java_version": java_v,
            "spring_boot_version": spring_v,
            "is_git": is_git,
            "data_dir": "ground",             # директория для state, манифестов, логов (prj-relative)
        },
        "conventions": {
            "package_root": group,            # эвристика по group; уточни при необходимости
            "migration_tool": mig_tool,       # liquibase | flyway | none (первичный/глобальный)
            "changelog_path": changelog,      # null если миграций нет (первичный сервис)
            # Все сервисы монорепо с миграциями (по одному на changelog-каталог). Пустой,
            # если миграций нет; в одиночном репо — ровно одна запись, совпадающая со скалярами.
            "migration_services": migration_services,
        },
        "quality": {
            "coverage_threshold": 0.80,
            "build_command": f"{_GRADLEW} clean build" if gradle else "mvn -q clean verify",
            "test_command": f"{_GRADLEW} test jacocoTestReport" if gradle else "mvn -q test jacoco:report",
            "coverage_report": "build/reports/jacoco/test/jacocoTestReport.xml" if gradle else "target/site/jacoco/jacoco.xml",
            "jacoco_configured": has_jacoco,
            "tdd": True,                      # TDD по умолчанию: тесты (RED) → код (GREEN); см. check_tests_red.py
            "eval_enabled": True,             # Eval-Driven Development: фаза 02-eval-plan + eval-guard (resolve_phases.enabled_by)
            "eval_threshold": 0.95,           # min доля зелёных eval'ов задачи (см. build_evals_from_design.py)
            "compile_test_command": f"{_GRADLEW} compileTestJava" if gradle else "mvn -q test-compile",
            "test_layer": "service-unit",     # по умолчанию ТОЛЬКО Mockito unit; НЕ писать JPA/@DataJpaTest/@SpringBootTest (red-judge блокирует)
            "charset_gate": {                 # чистота текста спеки (charset_hygiene): китайские/CJK → блок; мусор → warn
                "enabled": True,
                "cjk": "block",               # китайские/CJK-символы в brd/sdd/tech-design/мастере — блок
                "mojibake": "warn",           # символ замены U+FFFD (битая перекодировка)
                "zero_width": "warn",         # невидимые/zero-width/bidi-символы
                "mixed_script": "warn",       # слова кириллица+латиница (homoglyph, «странные слова»)
            },
        },
        "evidence": {
            "threshold": 0.95,                # min completeness evidence bundle перед доставкой
        },
        "risk": {
            "policy": "hooks/risk-policy.json",  # policy-as-code для risk ladder R0–R5
            "deny_first": True,               # рисковые действия fail-closed
        },
        "security": {
            "destructive_blocker": True,
            "pii_boundary": True,
            "prompt_guard": True,
        },
        "hooks": {
            "allowed_paths": [
                ".gigacode/",
                "ground/statements/",
                "docs/",
                "build/",
                ".git/",
            ],
        },
        "docs": {
            # Где живут документные артефакты (brd/sdd/tech-design/task-plan, system-analysis/grounding).
            # ЕДИНЫЙ источник правды о расположении — резолвится skill_paths.docs_base / _project.docs_base.
            "mode": "in-repo",                # in-repo | separate-repo
            "docs_path": "docs",              # in-repo: база под project_root
            "repo_path": None,                # separate-repo: АБСОЛЮТНЫЙ путь к внешнему репо спеки
            "feature_subdir": "feature-pipeline",      # подпапка фич под docs-базой
            "system_analysis_subdir": "system-analysis",  # подпапка системного обзора под docs-базой
            "master": {
                # МАСТЕР (system-analysis + specs/) отдельно от дельт. null → там же, где docs.
                "mode": None,          # None | in-repo | separate-repo (фолбэк на docs.mode)
                "repo_path": None,     # АБС. путь к клону репо мастер-спеки (separate-repo)
                "repo_url": None,      # origin для гайд-клона (авто-clone forge НЕ делает)
                "enabled": False,      # вести требования-мастер specs/<cap>/spec.md (фаза 06)
                "capability": None,    # имя капабилити (spec.md); None → project.name
                "adr_subdir": "adr",   # подпапка ADR под master-базой (<master_base>/adr)
            },
        },
        "sdd": {
            # Строгость ДКБ-разделов SDD (check_sdd_doc): hard | applicability | soft.
            # applicability (дефолт): арх. контекст + модель угроз обязательны (или «не применимо»),
            # пользовательские истории/принятые решения/регуляторка — warning.
            "security_gate": "applicability",
            "pull_before_grounding": False,  # git pull --ff-only мастер-репо перед grounding
        },
        "adr": {
            # Architecture Decision Records (rationale+статус). ADR-файлы в <master_base>/adr.
            "enabled": False,             # гейтить ADR на 02-design (check_adr в design-judge)
            "enforce_couplings": False,   # новая связка модулей требует accepted ADR (05-verify)
        },
        "spec": {
            # ПОВЕДЕНИЕ требований-мастера specs/<cap>/spec.md. ГДЕ он лежит и ведётся ли —
            # в docs.master (mode/repo_path/enabled/capability); тут только как он проверяется.
            # Мастер обновляется ПО ЗАПРОСУ командой /forge-spec merge, пайплайн в него не пишет.
            "id_prefix": "REQ",           # префикс стабильных ID требований (### REQ-0007: ...)
            "drift": "warn",              # off | warn — реакция spec-judge на неслитую дельту
            "scenario_floor": True,       # каждое требование обязано иметь ≥1 Given-When-Then
            "profile": "forge",           # СОСТАВ разделов мастера (forge = разделы с ДКБ);
                                          # detected — не навязывать состав чужому мастеру.
            # spec.grammar.* (ФОРМА требования) в скелет НЕ пишется намеренно: записанное
            # значение перекрыло бы детект формы (policy > детект), и ресерч спеки проекта
            # перестал бы применяться. Дефолты живут в spec_grammar.NATIVE.
        },
        "jira": {
            "enabled": None,                  # TODO: true/false
            "project_key": None,              # TODO: напр. "KIDPPRB"
            "auto_discovered": False,         # будет заполнен скриптом jira_discover.py
        }
    }
    # список незаполненного, по которому оркестратор спросит пользователя
    incomplete = []
    if not bs:
        incomplete.append("project.build_system")
    if not group:
        incomplete.append("conventions.package_root")
    if not is_git:
        incomplete.append("project.is_git (нужен git для чекпойнтов rollback и pipeline-state)")
    incomplete.append("jira.enabled")
    cfg["_incomplete"] = incomplete
    return cfg


def _answered(cfg: dict, entry: str) -> bool:
    """Отвечено ли поле из _incomplete. Ключ — первый токен записи (хвост в скобках —
    пояснение для оператора). None/отсутствие = не отвечено; False — валидный ответ
    (jira.enabled=false), КРОМЕ project.is_git: там False значит «git так и не завёлся»."""
    key = entry.split(" ")[0]
    val = cfg
    for part in key.split("."):
        if not isinstance(val, dict) or part not in val:
            return False
        val = val[part]
    if key == "project.is_git":
        return bool(val)
    return val is not None


def _resolve_dest(root: str, out: str | None) -> str:
    """Целевой файл. Приоритет: --out > policy.json (v2). Никогда не пишем в legacy
    pipeline.json напрямую — миграция идёт через чтение legacy, если policy.json ещё нет.
    Идемпотентно: при наличии policy.json пишем в него, при наличии только legacy pipeline.json
    — печатаем DEPRECATION note в stderr и пишем в policy.json (вперёд, не назад)."""
    if out:
        return out
    return os.path.join(root, "ground", "policy.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None, help="Project root (default: git toplevel или cwd)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--print", dest="dry", action="store_true")
    ap.add_argument("--legacy-read", action="store_true", default=False,
                    help="Прочитать существующий config из <project>/ground/pipeline.json (legacy v1), "
                         "если <project>/ground/policy.json ещё нет. Миграция идёт вперёд, не назад.")
    ap.add_argument("--out", default=None, help="Куда писать результат (по умолчанию <project>/ground/policy.json)")
    # v1 → v2 migration: dry-run / apply.
    ap.add_argument("--check-migration", dest="check_migration", action="store_true",
                    help="Dry-run: показать, что --migrate перенесёт из ground/pipeline.json "
                         "в policy.json + per-feature manifest.json. Без записи на диск.")
    ap.add_argument("--migrate", dest="migrate", action="store_true",
                    help="Применить v1→v2 миграцию: split pipeline.json → policy.json + "
                         "manifest.json'ы; оригинал сохраняется в pipeline.json.v1.bak.")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.project or _repo_root()))
    if not os.path.isdir(root):
        print(json.dumps({"error": f"нет директории {root}"}, ensure_ascii=False)); sys.exit(1)

    # v1 → v2 migration: отдельный код-путь, до основной логики scaffold'а policy.json.
    # --check-migration — печатает план (без побочных эффектов); --migrate — пишет.
    # Оба требуют наличия legacy ground/pipeline.json; при его отсутствии — exit 1.
    if args.check_migration or args.migrate:
        legacy_path = os.path.join(root, "ground", "pipeline.json")
        if not os.path.exists(legacy_path):
            print(json.dumps({
                "status": "no-migration-needed",
                "reason": f"{legacy_path} не существует — проект уже на v2 (или это новый init)",
            }, ensure_ascii=False, indent=2))
            sys.exit(0)
        try:
            with open(legacy_path, encoding="utf-8") as f:
                pipeline = json.load(f)
        except json.JSONDecodeError as e:
            print(json.dumps({"status": "error",
                              "error": f"битый JSON в {legacy_path}: {e}"},
                             ensure_ascii=False, indent=2))
            sys.exit(2)
        plan = _plan_migration(root, pipeline)
        if args.check_migration:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            sys.exit(0 if not plan["feature_errors"] else 2)
        # args.migrate
        if plan["feature_errors"]:
            print(json.dumps({"status": "error",
                              "errors": plan["feature_errors"],
                              "hint": "исправьте features.* в pipeline.json (slug формата "
                                      "'<skill>/<feature>' и skill в теле) — затем --migrate"},
                             ensure_ascii=False, indent=2))
            sys.exit(2)
        result = _write_migration(root, pipeline, plan)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # Любой отказ _write_migration (policy.json уже есть; .v1.bak конфликтует) → exit 2.
        # Это отличает «операционную проблему» от «успеха» и согласуется с exit-2 на
        # битый JSON и feature_errors (единый канал ошибок миграции).
        sys.exit(0 if result.get("status") == "migrated" else 2)

    detected = build_config(root)
    dest = _resolve_dest(root, args.out)

    policy_path = os.path.join(root, "ground", "policy.json")
    legacy_path = os.path.join(root, "ground", "pipeline.json")
    if not os.path.exists(policy_path) and os.path.exists(legacy_path) and not args.force:
        print(f"[init_pipeline_config] DEPRECATION: ground/pipeline.json → ground/policy.json (будет прочитан как legacy и смигрирован вперёд)", file=sys.stderr)

    if args.dry:
        print(json.dumps(detected, ensure_ascii=False, indent=2)); return

    if os.path.exists(dest) and not (args.force or args.update):
        print(json.dumps({"status": "exists", "path": dest,
                          "hint": "используй --update (обновить детект-поля) или --force (перезаписать)"},
                         ensure_ascii=False)); return

    if os.path.exists(dest) and args.update:
        # сохранить заполненные человеком поля, обновить только детектируемые секции.
        # v2: перебираем ТОЛЬКО ("project", "conventions", "quality") — per-feature поля
        # (autonomy.*, sources.*, pipeline.mode*) сюда НЕ попадают: они переехали в manifest.json
        # и в policy.json их не несем, даже если бы они там случайно оказались.
        with open(dest, encoding="utf-8") as f:
            existing = json.load(f)
        for sect in ("project", "conventions", "quality"):
            cur = existing.setdefault(sect, {})
            for k, v in detected[sect].items():
                # None-детект не затирает ответ человека (раньше update() клобберил
                # заполненный build_system/package_root обратно в null)
                if v is None and cur.get(k) is not None:
                    continue
                cur[k] = v
        existing["$schema"] = SCHEMA_VERSION
        # Маркер пересобирается ПО ФАКТУ: остаются только всё ещё не отвеченные поля.
        # Раньше detected["_incomplete"] переносился безусловно (jira/bitbucket попадали
        # всегда) → гейт арминга «_incomplete пуст» был недостижим.
        still = [i for i in detected["_incomplete"] if not _answered(existing, i)]
        if still:
            existing["_incomplete"] = still
        else:
            existing.pop("_incomplete", None)
        result = existing
    else:
        result = detected

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"status": "written", "path": dest,
                      "incomplete": result.get("_incomplete", [])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
