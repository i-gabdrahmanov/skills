#!/usr/bin/env python3
"""jira_discover.py — автоопределение Jira-конфига для pipeline.json.

Скрипт принимает JSON-метаданные (полученные оркестратором через MCP) и заполняет
секцию jira в ground/pipeline.json: типы задач, кастомные поля, доска, Workflow.

Использование:
    # на вход подаётся JSON с метой, на выход — обновлённый pipeline.json
    python3 jira_discover.py --project <root> < meta.json

Входной JSON (meta.json) должен содержать:
    - project_key: str
    - fields: list[dict]  — результат jira_search_fields
    - boards: list[dict]  — результат jira_get_agile_boards
    - issue_types: list[dict] — типы задач проекта

Скрипт НЕ вызывает MCP — только обрабатывает переданные данные.
Оркестратор feature-pipeline собирает мету на шаге 0.1 Config.
"""
import argparse
import json
import os
import sys
import re


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


def build_jira_config(meta):
    """Собирает полную секцию jira из метаданных."""
    project_key = meta.get("project_key", "")

    # Типы задач
    issue_types = discover_issue_types(meta.get("issue_types", []))

    # Кастомные поля
    discovered_fields = discover_fields(meta.get("fields", []))

    # Доска
    board = discover_board(meta.get("boards", []), project_key)

    config = {
        "enabled": True,
        "project_key": project_key,
        "auto_discovered": True,
        **issue_types,
        **discovered_fields,
        **DEFAULT_LINK_TYPES,
        **board,
    }

    return config


def update_pipeline_json(pipeline_path, jira_config):
    """Обновляет секцию jira в pipeline.json."""
    if not os.path.exists(pipeline_path):
        print(json.dumps({"error": f"pipeline.json не найден: {pipeline_path}"}))
        sys.exit(1)

    with open(pipeline_path, encoding="utf-8") as f:
        pipeline = json.load(f)

    pipeline["jira"] = jira_config

    with open(pipeline_path, "w", encoding="utf-8") as f:
        json.dump(pipeline, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return pipeline


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

    root = os.path.abspath(os.path.expanduser(args.project or os.getcwd()))
    pipeline_path = os.path.join(root, "ground", "pipeline.json")

    updated = update_pipeline_json(pipeline_path, jira_config)

    # Вывод отчёта — какие поля найдены, какие нет
    found = {k: v for k, v in jira_config.items() if v is not None and v is not False and v != {} and v != []}
    not_found = {k: v for k, v in jira_config.items() if v is None or v == {} or v == []}

    report = {
        "status": "ok",
        "project_key": project_key,
        "found_fields": list(found.keys()),
        "not_found": list(not_found.keys()),
        "detail": jira_config,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()