# Покрытие изменённых файлов (JaCoCo)

## Где искать отчёт

| Инструмент | Путь к XML |
|---|---|
| Gradle | `build/reports/jacoco/test/jacocoTestReport.xml` |
| Gradle (multi-module) | `<module>/build/reports/jacoco/test/jacocoTestReport.xml` для каждого модуля |
| Maven | `target/site/jacoco/jacoco.xml` |

Если JaCoCo не подключён в проекте — НЕ предлагай его подключить в рамках минорной
правки. Сообщи пользователю, что покрытие проверить нельзя, и спроси: продолжать без
этого шага или зафиксировать как отдельную задачу.

> **Важно (fail-closed):** `check_coverage.py` по умолчанию работает в режиме `--strict` —
> если JaCoCo-отчёт не найден, гейт даёт **FAIL** (exit 2), а не «тихо пропускает». Это
> закрывает дыру, когда на проекте без JaCoCo покрытие молча считалось пройденным. Если
> покрытие осознанно пропускается (JaCoCo нет, пользователь согласовал) — запускай с флагом
> `--lenient`, тогда отсутствие отчёта вернёт `skipped` (exit 0). Никогда не ставь `--lenient`
> «на автомате» — только после явного согласия пользователя.

## Структура XML

```xml
<report name="...">
  <package name="com/example/foo">
    <class name="com/example/foo/Bar" sourcefilename="Bar.java">
      <method name="doStuff" desc="(Ljava/lang/String;)V" line="42">
        <counter type="INSTRUCTION" missed="3" covered="12"/>
        <counter type="LINE" missed="1" covered="4"/>
        <counter type="BRANCH" missed="2" covered="2"/>
      </method>
      <counter type="LINE" missed="5" covered="40"/>
    </class>
  </package>
</report>
```

Покрытие класса по линиям: `covered / (covered + missed)`.

## Алгоритм проверки

> **Реализовано детерминированно в `scripts/check_coverage.py`** (общий для minor-defect-fix
> и feature-pipeline) — запускай его, а не считай покрытие глазами LLM. Скрипт сам берёт
> изменённые `*.java` (git diff), парсит JaCoCo XML, выдаёт per-file `OK/LOW/MISSING` и
> exit 2 при недоборе. Ниже — тот же алгоритм для справки/доработки.

```python
# псевдокод
changed_java_files = run("git diff --name-only HEAD")
# отфильтровать тесты и не-java
changed = [f for f in changed_java_files
           if f.endswith('.java') and '/test/' not in f]

for path in changed:
    fqcn = path_to_fqcn(path)  # src/main/java/com/foo/Bar.java -> com.foo.Bar
    cls = find_class_in_xml(fqcn)
    if cls is None:
        # Файл не покрыт тестами вообще ИЛИ не в classpath отчёта
        report("MISSING", path)
        continue
    line_counter = cls.find("./counter[@type='LINE']")
    covered = int(line_counter.get("covered"))
    missed = int(line_counter.get("missed"))
    total = covered + missed
    if total == 0:
        # Пустой класс (например, marker interface) — пропустить
        continue
    coverage = covered / total
    if coverage < 0.80:
        report("LOW", path, coverage)
```

## Поиск непокрытых строк (для дописывания тестов)

Когда покрытие < 80%, нужно понять, *какие именно* строки/ветки не покрыты, чтобы писать
прицельные тесты, а не "что-нибудь сверху".

JaCoCo HTML-отчёт (`build/reports/jacoco/test/html/index.html`) подсвечивает строки
цветом — но из CLI его читать неудобно. Используй XML на уровне `<method>`:

```xml
<method name="handleEdgeCase" line="87">
  <counter type="LINE" missed="6" covered="0"/>
</method>
```

`missed="6" covered="0"` — метод вообще не вызывается из тестов. Открой исходник по
`line="87"` и пиши тест на этот метод.

Если в методе `missed > 0, covered > 0` — частичное покрытие, обычно ветка `if/else` или
`catch`. Прочитай метод, найди ветку, которая не вызывается, напиши под неё тест.

## Если 80% объективно недостижимо

Не натягивай. Файл может быть:
- DTO/POJO с геттерами-сеттерами — JaCoCo иногда не учитывает Lombok-generated.
  Подключи плагин `lombok.config` с `lombok.addLombokGeneratedAnnotation = true`,
  но **только если** пользователь подтвердит, что это в скоупе минорной правки.
- Конфигурационным классом, который реально используется только в проде.
- Адаптером к внешней системе — покрытие через моки даёт ложную уверенность.

В этих случаях зафиксируй причину в отчёте в Jira (одной строкой) и предложи отдельный
тикет на тесты, если пользователь захочет.
