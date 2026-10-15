# Профиль поставки «back» — бэк-репо (Java/Spring): полный forge. Профиль по умолчанию.
# Формат: <вид> <значение> — по строке; # — комментарий. Читают deploy.sh (что класть),
# resolve_hook_paths.py (какие MCP регистрировать) и doctor.py (какие пути реестра проверять) —
# в таргете как .gigacode/.forge-profile.
profile back
# все скиллы исходника, кроме фронтовой ветки (она живёт во фронт-репо, профиль ui)
skills-all
exclude-skill forgeui
# слэш-команды: /forge, /forge-fix, /forge-tech, /forge-spec, /forge-merge, /forge-archive
commands commands
mcp forge-master
