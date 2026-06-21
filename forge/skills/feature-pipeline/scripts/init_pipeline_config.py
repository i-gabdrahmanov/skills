#!/usr/bin/env python3
"""Scaffold <project>/ground/pipeline.json — единый параметр-стор конвейера.

Авто-детектит, что может (build-система, модули, пакет, версии, инструмент миграций),
и оставляет плейсхолдеры (null) для того, что должен заполнить человек/оркестратор:
Jira-ключ, Bitbucket workspace/repo, и т.п. Stdlib only.

Использование:
    python init_pipeline_config.py --project <root>          # создать (не перезапишет)
    python init_pipeline_config.py --project <root> --update  # обновить только детект-поля
    python init_pipeline_config.py --project <root> --force   # перезаписать целиком
    python init_pipeline_config.py --project <root> --print    # показать, что задетектил, без записи

Незаполненные обязательные поля помечаются null и попадают в "_incomplete" —
оркестратор по этому списку понимает, о чём спросить пользователя.
"""
import argparse, json, os, re, subprocess, sys, glob

SCHEMA_VERSION = "feature-pipeline/config@1"


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


def detect_migration_tool(root, build_files):
    blob = " ".join(read_text(bf).lower() for bf in build_files)
    if "liquibase" in blob:
        tool = "liquibase"
    elif "flyway" in blob:
        tool = "flyway"
    else:
        tool = "none"
    changelog = None
    for d in ("db/changelog", "db/migration"):
        hits = glob.glob(os.path.join(root, "**", d), recursive=True)
        if hits:
            changelog = os.path.relpath(hits[0], root)
            break
    return tool, changelog


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
            "migration_tool": mig_tool,       # liquibase | flyway | none
            "changelog_path": changelog,      # null если миграций нет
        },
        "quality": {
            "coverage_threshold": 0.80,
            "build_command": "./gradlew clean build" if gradle else "mvn -q clean verify",
            "test_command": "./gradlew test jacocoTestReport" if gradle else "mvn -q test jacoco:report",
            "coverage_report": "build/reports/jacoco/test/jacocoTestReport.xml" if gradle else "target/site/jacoco/jacoco.xml",
            "jacoco_configured": has_jacoco,
            "token_budget": 2000000,          # PDLC v3.5 cost circuit breaker (warn 80% / stop 120%)
            "tdd": True,                      # TDD по умолчанию: тесты (RED) → код (GREEN); см. check_tests_red.py
            "compile_test_command": "./gradlew compileTestJava" if gradle else "mvn -q test-compile",
            "test_layer": "service-unit",     # по умолчанию ТОЛЬКО Mockito unit; НЕ писать JPA/@DataJpaTest/@SpringBootTest (red-judge блокирует)
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
            "budget_default": 2000000,        # token/cost circuit breaker (warn 80% / stop 120%)
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
        },
        "jira": {
            "enabled": None,                  # TODO: true/false
            "project_key": None,              # TODO: напр. "KIDPPRB"
            "auto_discovered": False,         # будет заполнен скриптом jira_discover.py
        },
        "bitbucket": {
            "enabled": None,                  # TODO
            "workspace": None,                # TODO
            "repo_slug": None,                # TODO
        },
        "delivery": {
            "pr_strategy": "stacked",         # stacked | split | single
            "branch_prefix": "feature/",
        },
        "autonomy": {
            "mode": "gated",                  # gated | autopilot-to-pr | until-commit
            "gates": ["brd", "design", "jira", "commit", "pr", "report"],
            "level": "L2",                    # PDLC v3.5 L0–L5 (лестница автономии)
            "criticality": None,              # low|medium|high — ВЫБРАТЬ на «Гейте критичности» после BRD
            "auto_max_risk": "R1",            # порог авто-прохода; задаётся выбором критичности (low→R2, med→R1, high→R0)
        },
    }
    # список незаполненного, по которому оркестратор спросит пользователя
    incomplete = []
    if not bs:
        incomplete.append("project.build_system")
    if not group:
        incomplete.append("conventions.package_root")
    if not is_git:
        incomplete.append("project.is_git (нужен git init для фаз 6 и pipeline-state)")
    for k in ("jira.enabled", "bitbucket.enabled"):
        incomplete.append(k)
    cfg["_incomplete"] = incomplete
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None, help="Project root (default: git toplevel или cwd)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--print", dest="dry", action="store_true")
    ap.add_argument("--out", default=None, help="Куда писать результат (по умолчанию <project>/ground/pipeline.json)")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.project or _repo_root()))
    if not os.path.isdir(root):
        print(json.dumps({"error": f"нет директории {root}"}, ensure_ascii=False)); sys.exit(1)

    detected = build_config(root)
    dest = os.path.join(root, "ground", "pipeline.json") if not args.out else args.out

    if args.dry:
        print(json.dumps(detected, ensure_ascii=False, indent=2)); return

    if os.path.exists(dest) and not (args.force or args.update):
        print(json.dumps({"status": "exists", "path": dest,
                          "hint": "используй --update (обновить детект-поля) или --force (перезаписать)"},
                         ensure_ascii=False)); return

    if os.path.exists(dest) and args.update:
        # сохранить заполненные человеком поля, обновить только детектируемые секции
        with open(dest, encoding="utf-8") as f:
            existing = json.load(f)
        for sect in ("project", "conventions", "quality"):
            existing.setdefault(sect, {}).update({k: v for k, v in detected[sect].items()})
        existing["$schema"] = SCHEMA_VERSION
        existing["_incomplete"] = detected["_incomplete"]
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
