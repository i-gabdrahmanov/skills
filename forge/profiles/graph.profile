# Профиль поставки «graph» — репо спеки: spec-graph (плоская спека → граф заметок Obsidian).
# Формат: <вид> <значение> — по строке; # — комментарий. Читают install.sh, resolve_hook_paths.py
# и doctor.py — в таргете как .forge-profile. Хуки (control-plane) едут целиком.
profile graph
skill specgraph
skill pipeline-state
skill config-helper
# только скрипты: их импортируют хуки и брифы (pipeline_phases, resolve_phases, skill_paths,
# init_pipeline_config, doctor) — без SKILL.md, чтобы Java-конвейер не светился скиллом
skill-scripts feature-pipeline
# /graph репо спеки запускает ветку specgraph напрямую (router не нужен)
commands spec-graph/commands
mcp spec-graph
