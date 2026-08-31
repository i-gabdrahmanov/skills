#!/usr/bin/env python3
"""jira_discover.py — автоопределение Jira-конфига для policy.json (v2 project-wide config).

Скрипт принимает JSON-метаданные (полученные оркестратором через MCP) и заполняет секцию `jira`
в ground/policy.json: типы задач, кастомные поля, доска, Workflow. Раньше жил в pipeline.json,
но v2-раскладка разнесла «глобальные параметры проекта» (build-система, конвенции, jira и т.п.)
по policy.json, а per-feature поля (inputs/decisions) — по ground/statements/<skill>/<feature>/manifest.json.
Секция `jira` глобальна для проекта → policy.json.

Использование:
    # на вход подаётся JSON с метой, на выход — обновлённый policy.json
    python3 jira_discover.py --project <root> < meta.json

Входной JSON (meta.json) должен содержать:
    - project_key: str
    - fields: list[dict]  — результат jira_search_fields
    - boards: list[dict]  — результат jira_get_agile_boards
    - issue_types: list[dict] — типы задач проекта

Скрипт НЕ вызывает MCP — только обрабатывает переданные данные.
Оркестратор feature-pipeline собирает мету на шаге 0.1 Config.

Legacy migration
----------------
Если policy.json ещё нет, но существует legacy ground/pipeline.json с секцией `jira` —
скрипт КОПИРУЕТ её в policy.json["jira"] (миграция вперёд) и УДАЛЯЕТ из pipeline.json,
печатая deprecation-уведомление в stderr. На существование pipeline.json без секции `jira`
не реагирует — init_pipeline_config.py сам разруливает порядок файлов.
"""
import argparse
import json
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _root  # noqa: E402  find_project_root: ко-локейд резолвер корня данных


# Маппинг известных имён кастомных полей на ключи конфига
# {регулярка для поиска по имени поля: ключ в jira-конфиге}
FIELD_MAP = {
    r"^Epic Link$": "epic_link_field",
    r"^Epic Name$": "epic_name_field",
    r"^Sprint$": "sprint_field",
    r"^Система$": "system_field",
    r"^BR_ST$": "br_st_field",
    r"^FR_ST$": "fr_st_field",
    r"^Процесс$": "process_field",
    r"^ФО/Стрим$": "fo_stream_field",
    r"^Acceptance Criteria$": "acceptance_criteria_field",
    r"^Критерии приемки$": "acceptance_criteria_field",
    r"^Story Points$": "story_points_field",
}

# Дефолтные типы связей
DEFAULT_LINK_TYPES = {
    "parent_link_type": "parent",
    "epic_link_type": "Epic Link",
    "blocked_link_type": "is blocked by",
}


def discover_fields(fields_list):
    """Сопоставляет список кастомных полей с известными именами, возвращает cf-id."""
    result = {}
    if not fields_list:
        return result

    for field in fields_list:
        field_id = field.get("id", "")
        field_name = field.get("name", "")
        if not field_id or not field_name:
            continue
        for pattern, config_key in FIELD_MAP.items():
            if re.search(pattern, field_name, re.IGNORECASE):
                result[config_key] = field_id
                break
    return result


def discover_issue_types(issue_types_list):
    """Определяет имена типов задач: Story, Task, Sub-task, Epic, Bug."""
    result = {
        "issue_type_story": "Story",
        "issue_type_subtask": "Sub-task",
        "issue_type_epic": "Epic",
        "issue_type_bug": "Bug",
    }
    if not issue_types_list:
        return result

    # Собираем имена и subtask-флаг
    names = {}
    for it in issue_types_list:
        name = it.get("name", "")
        is_subtask = it.get("subtask", False)
        names[name.lower()] = {"name": name, "subtask": is_subtask}

    # Story (ищем Story → История → первое не-subtask)
    story_found = False
    for candidate in ["story", "история", "user story"]:
        if candidate in names:
            result["issue_type_story"] = names[candidate]["name"]
            story_found = True
            break
    if not story_found:
        # берём первый не-subtask тип
        for name, info in names.items():
            if not info["subtask"]:
                result["issue_type_story"] = info["name"]
                break

    # Sub-task
    for candidate in ["sub-task", "subtask", "подзадача"]:
        if candidate in names:
            result["issue_type_subtask"] = names[candidate]["name"]
            break

    # Epic
    for candidate in ["epic", "эпик"]:
        if candidate in names:
            result["issue_type_epic"] = names[candidate]["name"]
            break

    # Bug
    for candidate in ["bug", "дефект", "error"]:
        if candidate in names:
            result["issue_type_bug"] = names[candidate]["name"]
            break

    return result


def discover_board(boards_list, project_key):
    """Определяет основную Agile-доску проекта."""
    result = {"board": {"id": None, "name": None, "sprint_naming": None}}
    if not boards_list:
        return result

    # Приоритет: доски с названием содержащим project_key или "развитие"/"support"
    candidates = []
    for board in boards_list:
        bname = board.get("name", "")
        bid = board.get("id")
        btype = board.get("type", "")
        score = 0
        blower = bname.lower()
        if project_key and project_key.lower() in blower:
            score += 3
        if "sprint" in blower and "scrum" in btype:
            score += 2
        if "развитие" in blower:
            score += 1
        if "поддержк" in blower or "support" in blower:
            score -= 1  # kanban, ниже приоритет
        candidates.append((score, bid, bname))

    if candidates:
        candidates.sort(key=lambda x: -x[0])
        best_score, best_id, best_name = candidates[0]
        if best_id:
            result["board"]["id"] = best_id
            result["board"]["name"] = best_name
            # пытаемся угадать шаблон имени спринта по имени доски
            sprint_pattern = guess_sprint_naming(best_name)
            if sprint_pattern:
                result["board"]["sprint_naming"] = sprint_pattern

    return result


def guess_sprint_naming(board_name):
    """Пытается угадать шаблон именования спринтов по имени доски."""
    if not board_name:
        return None
    # Если доска называется "... (sprint)", берём часть до скобок
    m = re.match(r"^(.+?)\s*\(sprint\)", board_name)
    if m:
        return m.group(1).strip() + " Sprint {N}_{YYYY}"
    # Иначе — просто шаблон
    return "Sprint {N}_{YYYY}"


def discover_conventions(issues_list):
    """Выводит конвенции проекта из недавних issue, чтобы создавать задачи «в едином ключе».

    issues_list: [{"summary": str, "components": [str|{"name"}], "labels": [str],
                   "epic": "KEY"|None}, ...] — последние N issue проекта (jira_search).
    Возвращает типовые компоненты/лейблы, частый родительский epic и образец префикса нейминга.
    """
    from collections import Counter
    comp, lab, epics, prefixes = Counter(), Counter(), Counter(), Counter()
    for it in issues_list or []:
        for c in it.get("components", []) or []:
            name = c.get("name") if isinstance(c, dict) else c
            if name:
                comp[name] += 1
        for l in it.get("labels", []) or []:
            if l:
                lab[l] += 1
        ep = it.get("epic")
        if ep:
            epics[ep] += 1
        s = (it.get("summary") or "").strip()
        m = re.match(r"^(\[[^\]]+\]|[A-Za-zА-Яа-я]+:)\s", s)  # "[KID] ..." / "Модуль: ..."
        if m:
            prefixes[m.group(1)] += 1

    def top(counter, n):
        return [k for k, _ in counter.most_common(n)]

    return {
        "common_components": top(comp, 5),
        "common_labels": top(lab, 5),
        "frequent_epic": (epics.most_common(1)[0][0] if epics else None),
        "summary_prefix": (prefixes.most_common(1)[0][0] if prefixes else None),
        "sampled_issues": len(issues_list or []),
    }


def build_jira_config(meta):
    """Собирает полную секцию jira из метаданных."""
    project_key = meta.get("project_key", "")

    # Типы задач
    issue_types = discover_issue_types(meta.get("issue_types", []))

    # Кастомные поля
    discovered_fields = discover_fields(meta.get("fields", []))

    # Доска
    board = discover_board(meta.get("boards", []), project_key)

    # Конвенции проекта (компоненты/лейблы/epic/нейминг) из недавних issue — «единый ключ»
    conventions = discover_conventions(meta.get("issues", []))

    config = {
        "enabled": True,
        "project_key": project_key,
        "auto_discovered": True,
        **issue_types,
        **discovered_fields,
        **DEFAULT_LINK_TYPES,
        **board,
        "conventions": conventions,
    }

    return config


def _read_json_or_empty(path: str) -> dict:
    """Читает JSON; пустой/битый файл трактуется как пустой dict (best-effort, не падаем)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write_json(path: str, payload: dict) -> None:
    """Атомарная запись JSON: write to <path>.tmp + os.replace. Защищает от полу-записи,
    которая оставила бы читателей (config.py) на битом файле и обрушила pipeline."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _migrate_legacy_jira_if_present(root: str, policy: dict) -> dict:
    """Если legacy ground/pipeline.json содержит секцию `jira`, а в policy.json её ещё нет —
    мигрируем вперёд: копируем в policy["jira"], удаляем из pipeline.json, печатаем DEPRECATION
    в stderr. Возвращает обновлённый policy (тот же объект, мутация in-place).

    Не трогает pipeline.json, если секции `jira` там нет (могут быть другие legacy-поля,
    которые init_pipeline_config уже скопировал в policy). На частично смигрированном
    проекте (policy.json есть, но БЕЗ jira) — НЕ подтягивает legacy, чтобы не перетереть
    уже свежий результат discover (он сам сразу пишет в policy)."""
    legacy_path = os.path.join(root, "ground", "pipeline.json")
    if not os.path.exists(legacy_path):
        return policy
    if "jira" in policy:
        return policy  # уже мигрировали или discover уже записал свежий — не трогаем
    legacy = _read_json_or_empty(legacy_path)
    legacy_jira = legacy.get("jira")
    if not legacy_jira:
        return policy  # в legacy нет jira — не наша забота
    policy["jira"] = legacy_jira
    # Удаляем из legacy: без секции pipeline.json обычно пуст или содержит только то,
    # что ещё не переехало (autonomy/sources — их мигрирует init_pipeline_config.py).
    # Не удаляем сам pipeline.json, чтобы init_pipeline_config мог доделать свою часть миграции.
    legacy.pop("jira", None)
    _atomic_write_json(legacy_path, legacy)
    print(f"[jira_discover] DEPRECATION: ground/pipeline.json → ground/policy.json: "
          f"секция jira смигрирована вперёд (legacy pipeline.json обновлён). "
          f"v3.0 удалит поддержку pipeline.json.",
          file=sys.stderr)
    return policy


def update_policy_json(policy_path: str, jira_config: dict, *, legacy_pipeline_path: str = "") -> dict:
    """Обновляет секцию jira в policy.json (v2). Создаёт файл при отсутствии.
    Если policy.json ещё нет, а есть legacy pipeline.json с секцией jira — смигрирует её
    в policy.json (миграция вперёд), затем перезаписывает свежим jira_config.
    Возвращает финальный policy (после merge)."""
    policy = _read_json_or_empty(policy_path)
    # Миграция legacy → policy (только если policy ещё не содержит jira).
    if legacy_pipeline_path:
        policy = _migrate_legacy_jira_if_present(os.path.dirname(os.path.dirname(policy_path)),
                                                 policy)
    policy["jira"] = jira_config
    _atomic_write_json(policy_path, policy)
    return policy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None, help="Project root")
    ap.add_argument("--print", dest="dry", action="store_true",
                    help="Показать результат без записи (читает stdin)")
    args = ap.parse_args()

    # Читаем метаданные из stdin
    try:
        meta = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Ошибка парсинга JSON: {e}"}))
        sys.exit(1)

    project_key = meta.get("project_key")
    if not project_key:
        print(json.dumps({"error": "meta.json должен содержать project_key"}))
        sys.exit(1)

    jira_config = build_jira_config(meta)

    if args.dry:
        print(json.dumps({"jira": jira_config}, ensure_ascii=False, indent=2))
        return

    root = os.path.abspath(os.path.expanduser(
        args.project if args.project else str(_root.find_project_root())))
    policy_path = os.path.join(root, "ground", "policy.json")
    legacy_pipeline_path = os.path.join(root, "ground", "pipeline.json")

    updated = update_policy_json(policy_path, jira_config,
                                 legacy_pipeline_path=legacy_pipeline_path)

    # Вывод отчёта — какие поля найдены, какие нет
    found = {k: v for k, v in jira_config.items() if v is not None and v is not False and v != {} and v != []}
    not_found = {k: v for k, v in jira_config.items() if v is None or v == {} or v == []}

    report = {
        "status": "ok",
        "project_key": project_key,
        "found_fields": list(found.keys()),
        "not_found": list(not_found.keys()),
        "detail": jira_config,
        "written_to": policy_path,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
