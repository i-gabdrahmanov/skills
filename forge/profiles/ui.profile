# Профиль поставки «ui» — фронт-репо: forge-ui (макет → UI-код → проверка против макета).
# Формат: <вид> <значение> — по строке; # — комментарий. Читают deploy.sh, resolve_hook_paths.py
# и doctor.py — в таргете как .gigacode/.forge-profile. Хуки (control-plane) едут целиком.
profile ui
skill forgeui
skill pipeline-state
skill config-helper
# только скрипты: их импортируют хуки и брифы (pipeline_phases, resolve_phases, skill_paths,
# init_pipeline_config, doctor) — без SKILL.md, чтобы Java-конвейер не светился скиллом
skill-scripts feature-pipeline
# /forge фронт-репо запускает ветку forgeui напрямую (router здесь не нужен)
commands forge-ui/commands
mcp forge-ui
