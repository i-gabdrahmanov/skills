# X.2 — Упаковка, установка, раскладка add-on

**Этап:** сквозная · **Зависит от:** 3.6 · **Статус:** done
**Решения:** D1, D2, D3

## Зачем
0.1 положил профиль и вид установки. Здесь установка доводится до боевой: два сценария размещения
спеки, удаление, диагностика, проверка на GigaCode.

## Что сделать
- Сценарий «репо спеки» (основной): `install.sh spec-graph <spec-repo>`, `uninstall.sh` снимает
  только своё (каталоги `.gigacode/*` общие с оператором — урок co-located), `doctor`/`preflight`
  для профиля `graph`.
- Сценарий «спека в репо кода, где стоит forge» (D2): граф докладывается в поставку forge — флаг
  `install.sh forge <repo> --with-graph` добавляет к профилю `back` скилл `specgraph`, MCP
  `spec-graph` и команду `/graph`. Вторая поставка в тот же репо по-прежнему запрещена.
- `INSTALL.md`: раздел spec-graph — оба сценария, первый запуск `/graph`, где лежит vault, как
  открыть его в Obsidian.
- Доктор окружения (`doctor.py` профиля `graph`): `extensions link`, `--scope workspace`,
  видимость MCP `spec-graph`, команды и хуков — вывод без текста проекта, пригодный для выноса.
  Локально — стоковый qwen в песочнице с подменённым HOME, как в forge-ui.

## Готово, когда
- `test_install_kinds.py` и `test_deploy_profiles.py` покрывают оба сценария; uninstall не трогает
  чужие файлы в `.gigacode/`.
- На qwen в песочнице `/graph` виден и `graph_status` отвечает.
- На GigaCode проверяет пользователь при установке в окружении: `graph_status` и раздел
  «Окружение» пакета диагностики (4.3) показывают, видны ли MCP, команда и хуки. Если `graph_status`
  недоступен совсем (MCP не поднялся), есть запасной путь без рантайма:
  `python3 .gigacode/extensions/spec-graph/hooks/doctor.py --project .` печатает тот же раздел.

## Результат
- **Репо спеки** — `install.sh spec-graph` (с 0.1). Добавлено: тест «uninstall не трогает чужое в
  `.gigacode/`» (свой скилл оператора переживает снятие).
- **Спека в репо кода (D2)** — `install.sh forge <репо> --with-graph`: профиль `back` без исключения
  `specgraph` + `commands spec-graph/commands` + `mcp spec-graph` + метка `with graph`. Профиль собирается в
  поставку на лету, исходные профили не меняются. `update.sh` по метке переставляет с тем же флагом;
  переустановка без флага граф снимает. К `forge-ui` и `spec-graph` флаг — отказ (exit 2). Вторая поставка в
  тот же репо по-прежнему запрещена.
- **Доктор без рантайма** — `hooks/graph_doctor.py --project .`. Проверяет:
  - цельность extension (`forge_extension.check`) и наличие графа в профиле;
  - MCP `spec-graph` в манифесте и живой `initialize`;
  - команду `/graph` и хук `graph-guard` в `hooks.json`;
  - ссылку рантайма на эту поставку;
  - движок, онтологию и конфиг; Obsidian (core-plugins, Bases, слой).
  Вывод без путей машины (`<проект>`, `~`). Коды: 0 / 1 / 2.
- `INSTALL.md`: раздел «Граф спецификации: spec-graph» — оба сценария, первый запуск `/graph`, где vault и
  как открыть его в Obsidian, отчёты `_graph/`, доктор, `/graph diag`.
- `test_install_kinds.py`: `test_spec_graph` (доктор, чужое в `.gigacode/`), новый `test_forge_with_graph`
  (состав, оба MCP, initialize, доктор, update.sh сохраняет флаг, переустановка без флага снимает граф, отказ
  флага к forge-ui).
- **Проверено** на стоковом qwen 0.22.2 в песочнице (`HOME` во временном каталоге): `install.sh spec-graph
  --runtime qwen` ставит ссылку и включает только для workspace; `qwen extensions list` видит `/graph`,
  скиллы и MCP `spec-graph`; `qwen mcp list` — spec-graph Connected; `graph_status` через тот же MCP отвечает
  вопросами первого запуска (кандидат `docs/spec`, 9 MD-файлов); доктор — exit 0 со ссылкой на поставку.
- **Отклонения:** доктор называется `graph_doctor.py`, а не `doctor.py`: в forge уже есть `doctor.py`
  конвейера (`feature-pipeline/scripts`), и одно имя у двух разных докторов путало бы. Отдельного
  `test_deploy_profiles.py` нет — оба сценария покрывает `test_install_kinds.py`.
- **Не проверено:** GigaCode — проверяет пользователь при установке: `graph_status`, раздел «Окружение»
  пакета диагностики или `graph_doctor.py`.
- **Заметка:** `skills-all` кладёт в поставку всё из `skills/` клона, включая неотслеживаемые каталоги (так
  было и до X.2). Ставить из чистого клона.
