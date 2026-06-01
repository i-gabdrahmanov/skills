"""
Generate ATS-optimized resume PDF for Татарников Г.Д.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.lib.colors import HexColor, black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
)

# Register Cyrillic fonts (Arial Unicode supports Russian)
pdfmetrics.registerFont(TTFont('ArialU', '/Library/Fonts/Arial Unicode.ttf'))
pdfmetrics.registerFont(TTFont('ArialUB', '/Library/Fonts/Arial Unicode.ttf'))  # fallback; we'll fake bold via style
# Try to register real bold if available
import os
for cand in ['/System/Library/Fonts/Supplemental/Arial Bold.ttf']:
    if os.path.exists(cand):
        pdfmetrics.registerFont(TTFont('ArialUB', cand))
        break

OUT_PATH = "/Users/iskandergabdrahmanov/Downloads/Татарников_Герман_Дмитриевич_v2.pdf"

ACCENT = HexColor('#1F4E79')
SUBTLE = HexColor('#6E6E6E')

styles = getSampleStyleSheet()

base = dict(fontName='ArialU', leading=13, fontSize=9.5, textColor=black)

s_name = ParagraphStyle('name', fontName='ArialUB', fontSize=20, leading=24, textColor=ACCENT, spaceAfter=2)
s_title = ParagraphStyle('title', fontName='ArialUB', fontSize=12, leading=15, textColor=black, spaceAfter=4)
s_section = ParagraphStyle('section', fontName='ArialUB', fontSize=11, leading=14, textColor=ACCENT, spaceBefore=10, spaceAfter=4)
s_subsection = ParagraphStyle('subsection', fontName='ArialUB', fontSize=10, leading=13, textColor=black, spaceBefore=4, spaceAfter=2)
s_body = ParagraphStyle('body', **base, alignment=TA_LEFT, spaceAfter=2)
s_bullet = ParagraphStyle('bullet', **base, leftIndent=10, bulletIndent=0, spaceAfter=2, alignment=TA_LEFT)
s_meta = ParagraphStyle('meta', fontName='ArialU', fontSize=8.5, leading=11, textColor=SUBTLE, spaceAfter=2)
s_date = ParagraphStyle('date', fontName='ArialU', fontSize=9, leading=11, textColor=SUBTLE)

def hr():
    return HRFlowable(width="100%", thickness=0.5, color=HexColor('#CCCCCC'),
                      spaceBefore=2, spaceAfter=4)

def bullet(text):
    return Paragraph(f'• {text}', s_bullet)

def section(title):
    return [Paragraph(title.upper(), s_section), hr()]

story = []

# ───── HEADER ─────
story.append(Paragraph('Татарников Герман Дмитриевич', s_name))
story.append(Paragraph('Middle Java Developer (Backend) | Spring Boot · Apache Kafka · PostgreSQL · Microservices', s_title))
story.append(Paragraph(
    'Мужчина, 26 лет, родился 29 июля 1999 г. &nbsp;|&nbsp; Казань (готов к переезду: Москва, Санкт-Петербург; командировки)',
    s_meta))
story.append(Paragraph(
    '+7 (999) 162-63-96 &nbsp;•&nbsp; gerka0604@gmail.com &nbsp;•&nbsp; Telegram: @DmitrichSOS &nbsp;•&nbsp; t.me/DmitrichSOS',
    s_meta))
story.append(Paragraph('Гражданство: РФ &nbsp;|&nbsp; Английский: B1 (Intermediate)', s_meta))

# ───── SUMMARY ─────
story += section('Профиль / Professional Summary')
story.append(Paragraph(
    'Middle Java Developer с коммерческим опытом <b>4+ года</b> в разработке высоконагруженных '
    'backend-сервисов в банковской сфере (ПАО Сбербанк) и госсекторе. Специализация — проектирование '
    'и поддержка распределённых систем на стеке <b>Java 17, Spring Boot, Apache Kafka, PostgreSQL, '
    'Hibernate, Docker, Kubernetes</b>. Опыт интеграций по REST API, SOAP/XML, Kafka; работа с '
    'событийно-ориентированной архитектурой (Event-Driven Architecture), микросервисами '
    '(Spring Cloud, OpenFeign), мониторингом (Prometheus, Grafana, ELK Stack) и CI/CD '
    '(Jenkins, GitLab CI). Применяю SOLID, GoF design patterns, Clean Code, TDD. Активно участвую '
    'в код-ревью и декомпозиции бизнес-требований.',
    s_body))
story.append(Spacer(1, 3))
story.append(Paragraph(
    '<b>Ключевые достижения:</b> сокращение времени транзакции с 5 минут до 3-5 сек '
    '(устранение N+1), ускорение массовых отчётов в 36 раз (JDBC Batching + CompletableFuture), '
    'снижение нагрузки на L1-поддержку на 30% за счёт автоматизации.',
    s_body))

# ───── EXPERIENCE ─────
story += section('Опыт работы — 4 года 3 месяца')

# Сбер
exp1_header = Table(
    [[Paragraph('<b>ПАО Сбербанк</b> &nbsp;|&nbsp; Middle Java Developer', s_subsection),
      Paragraph('Сентябрь 2023 — Декабрь 2025 (2 года 4 мес.)', s_date)]],
    colWidths=[11.5*cm, 5.5*cm])
exp1_header.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'),
                                  ('LEFTPADDING',(0,0),(-1,-1),0),
                                  ('RIGHTPADDING',(0,0),(-1,-1),0)]))
story.append(exp1_header)
story.append(Paragraph('Москва · Финансовый сектор · Банк · rabota.sber.ru', s_meta))
story.append(Paragraph(
    '<b>Проект:</b> КИД — Картотека Исполнительных Документов (высоконагруженная корпоративная система '
    'обработки исполнительных документов).', s_body))
story.append(Paragraph(
    '<b>Стек:</b> Java 17, Spring Boot 3, Spring Data JPA, Hibernate, Apache Kafka, PostgreSQL, '
    'Liquibase, Resilience4j, WebClient, Docker, Kubernetes, Jenkins, JUnit 5, Mockito, GigaCode (AI).',
    s_body))
story.append(Spacer(1, 2))
story.append(bullet(
    'Совмещал роли <b>системного аналитика и разработчика</b>: писал техническое задание (ТЗ), '
    'декомпозировал требования и реализовывал функционал end-to-end.'))
story.append(bullet(
    'Спроектировал и реализовал <b>отказоустойчивые пайплайны Apache Kafka</b> '
    '(Producer/Consumer) с кастомным <b>ErrorHandler</b> для ошибок десериализации; '
    'обеспечил семантику <b>At-Least-Once delivery</b> и идемпотентность обработки бизнес-событий.'))
story.append(bullet(
    'Разработал <b>Error Handling Service</b> для централизованной агрегации инцидентов '
    'через Kafka — снизил нагрузку на L1-поддержку на <b>30%</b>.'))
story.append(bullet(
    'Создал сервис генерации штрих-кодов с ежесуточной нагрузкой <b>50 000 документов</b> '
    'через <b>Spring Scheduler</b> и пакетные обновления <b>JDBC Batching</b>.'))
story.append(bullet(
    'Обеспечил надёжный запуск распределённых cron-задач через <b>Spring Scheduler + ShedLock</b> '
    '(защита от двойного запуска в кластере).'))
story.append(bullet(
    'Провёл <b>рефакторинг слоя Spring Data JPA / Hibernate</b>: устранил проблему N+1 через '
    'EntityGraph и fetch-стратегии — сократил время транзакции с <b>5 минут до 3–5 секунд</b>.'))
story.append(bullet(
    'Ускорил формирование массовых отчётов с <b>3 минут до 5 секунд</b> (×36) через '
    '<b>JDBC Batching</b> и асинхронную обработку <b>CompletableFuture</b>.'))
story.append(bullet(
    'Реализовал отказоустойчивый <b>REST API-клиент</b> на <b>Spring WebClient</b> '
    'с <b>Resilience4j Retry / Circuit Breaker</b> и паттерном <b>Dead Letter Queue</b> '
    '(persistent DLQ на PostgreSQL).'))
story.append(bullet(
    'Внедрил <b>AI-ассистента GigaCode</b> в командный процесс для генерации шаблонов '
    'unit-тестов (JUnit 5, Mockito) — повысил скорость покрытия кода на <b>20%</b>.'))
story.append(bullet(
    'Контейнеризация сервисов: <b>Docker, Docker Compose</b>; деплой в <b>Kubernetes</b> через '
    'CI/CD пайплайны на <b>Jenkins</b>. Мониторинг через <b>Prometheus + Grafana</b>, '
    'логирование через <b>ELK Stack</b>.'))
story.append(bullet(
    'Участие в <b>code review</b>, парное программирование, написание технической документации '
    'в Confluence.'))

story.append(Spacer(1, 6))

# Ай-Новус
exp2_header = Table(
    [[Paragraph('<b>Ай-Новус (i-Novus)</b> &nbsp;|&nbsp; Junior Java Developer', s_subsection),
      Paragraph('Октябрь 2021 — Сентябрь 2023 (2 года)', s_date)]],
    colWidths=[11.5*cm, 5.5*cm])
exp2_header.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'),
                                  ('LEFTPADDING',(0,0),(-1,-1),0),
                                  ('RIGHTPADDING',(0,0),(-1,-1),0)]))
story.append(exp2_header)
story.append(Paragraph('Казань · ИТ, системная интеграция · Разработка ПО · i-novus.ru', s_meta))
story.append(Paragraph(
    '<b>Проекты:</b> Электронное здравоохранение (ЕГИСЗ), госсектор; интеграции с государственными '
    'информационными системами.', s_body))
story.append(Paragraph(
    '<b>Стек:</b> Java 11, Spring Boot, Spring MVC, Hibernate, PostgreSQL, SOAP/XML (СМЭВ-3), '
    'OpenAPI 3.0 (Swagger), Selenium WebDriver, Maven, Git, фреймворк N2O.', s_body))
story.append(Spacer(1, 2))
story.append(bullet(
    'Выполнил <b>миграцию монолитного приложения с J2EE (EJB) на Spring Boot</b>: '
    'время развёртывания окружения разработчика сократилось с 7 минут до <b>10 секунд</b>.'))
story.append(bullet(
    'Разработал <b>интеграционный адаптер с СМЭВ-3</b> (SOAP/XML, WS-Security) — '
    'провёл технический митап для 20+ разработчиков по работе с госструктурами.'))
story.append(bullet(
    'Реализовал <b>модуль расчёта компенсаций</b> (отпускные, больничные) с учётом '
    'производственного календаря РФ и системы грейдов.'))
story.append(bullet(
    'Описал контракты <b>REST API в OpenAPI 3.0 (Swagger)</b> через Java-аннотации и YAML-спецификации.'))
story.append(bullet(
    'Разработал <b>UI-автотесты</b> на Selenium WebDriver для внутреннего фреймворка N2O.'))

# ───── KEY SKILLS ─────
story += section('Технические навыки / Hard Skills')

skills_table_data = [
    ['Языки программирования:', 'Java 8 / 11 / 17, SQL, YAML, XML'],
    ['Backend-фреймворки:', 'Spring Boot, Spring MVC, Spring Data JPA, Spring Security, Spring Cloud, Spring Scheduler, Spring Test, Spring WebFlux (WebClient)'],
    ['Базы данных:', 'PostgreSQL, Oracle, Liquibase, Flyway, Hibernate, JDBC, JDBC Batching, JPA, оптимизация SQL-запросов, индексирование'],
    ['Брокеры сообщений:', 'Apache Kafka (Producer/Consumer, Streams, Schema Registry), RabbitMQ (базово)'],
    ['Микросервисы:', 'Microservices Architecture, Event-Driven Architecture, REST API, OpenAPI 3.0 / Swagger, OpenFeign, Spring Cloud Gateway, Service Discovery (Eureka), Resilience4j (Retry, Circuit Breaker, Rate Limiter), Dead Letter Queue (DLQ)'],
    ['Кэширование:', 'Redis, Caffeine, Spring Cache'],
    ['Тестирование:', 'JUnit 5, Mockito, Spring Test, Testcontainers, Selenium WebDriver, TDD, Unit / Integration / E2E тесты'],
    ['DevOps / CI/CD:', 'Docker, Docker Compose, Kubernetes (kubectl, helm — базово), Jenkins, GitLab CI, Git, Bitbucket'],
    ['Мониторинг и логирование:', 'Prometheus, Grafana, ELK Stack (Elasticsearch, Logstash, Kibana), Spring Boot Actuator'],
    ['Многопоточность:', 'Multithreading, Concurrency, CompletableFuture, ExecutorService, Stream API'],
    ['Архитектура и подходы:', 'ООП, SOLID, GoF Design Patterns, Clean Code, Clean Architecture, DDD (базово), Code Review, Refactoring'],
    ['Методологии:', 'Agile, Scrum, Kanban'],
    ['Инструменты:', 'IntelliJ IDEA, Maven, Gradle, Postman, Confluence, Jira'],
    ['Интеграции:', 'REST, SOAP/XML, СМЭВ-3, gRPC (базово), WebSocket (базово)'],
    ['AI-инструменты:', 'GigaCode, GitHub Copilot — генерация unit-тестов, ускорение разработки'],
]

skill_rows = []
for label, value in skills_table_data:
    skill_rows.append([
        Paragraph(f'<b>{label}</b>', s_body),
        Paragraph(value, s_body),
    ])

skill_table = Table(skill_rows, colWidths=[4.6*cm, 12.4*cm])
skill_table.setStyle(TableStyle([
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('LEFTPADDING', (0,0), (-1,-1), 0),
    ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ('TOPPADDING', (0,0), (-1,-1), 1.5),
    ('BOTTOMPADDING', (0,0), (-1,-1), 1.5),
]))
story.append(skill_table)

# ───── ACHIEVEMENTS ─────
story += section('Ключевые достижения / Key Achievements')
story.append(bullet('Оптимизация SQL-транзакции: <b>5 минут → 3-5 секунд</b> (устранение N+1, EntityGraph).'))
story.append(bullet('Ускорение массовых отчётов: <b>3 минуты → 5 секунд (×36)</b> — JDBC Batching + CompletableFuture.'))
story.append(bullet('Развёртывание окружения: <b>7 минут → 10 секунд (×42)</b> — миграция J2EE → Spring Boot.'))
story.append(bullet('Снижение нагрузки на L1-поддержку на <b>30%</b> через автоматизированный Error Handling Service.'))
story.append(bullet('Покрытие unit-тестами <b>+20%</b> через внедрение AI-ассистента в команде.'))
story.append(bullet('Ежесуточная обработка <b>50 000 документов</b> в production без даунтайма.'))

# ───── EDUCATION ─────
story += section('Образование')
edu_table = Table(
    [[Paragraph('<b>Казанский государственный энергетический университет</b><br/>'
                'Теплоэнергетика и Теплотехника, специализация — Промышленная теплоэнергетика<br/>'
                '<font color="#6E6E6E" size="8.5">Высшее образование</font>', s_body),
      Paragraph('2021', s_date)]],
    colWidths=[14*cm, 3*cm])
edu_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'),
                                ('LEFTPADDING',(0,0),(-1,-1),0),
                                ('RIGHTPADDING',(0,0),(-1,-1),0)]))
story.append(edu_table)

story.append(Spacer(1, 4))
story.append(Paragraph('<b>Повышение квалификации и курсы:</b>', s_body))
story.append(bullet('<b>2022</b> — «Продвинутая Java» — Udemy (Spring, многопоточность, паттерны).'))
story.append(bullet('<b>2020</b> — «Java. От простого к сложному» — Udemy.'))
story.append(bullet('<b>2020</b> — «Java Core / Java-разработчик» — JavaRush.'))

# ───── LANGUAGES & ADDITIONAL ─────
story += section('Языки и дополнительная информация')
story.append(Paragraph(
    '<b>Языки:</b> Русский — родной &nbsp;•&nbsp; Английский — B1 (Intermediate, чтение технической '
    'документации) &nbsp;•&nbsp; Татарский — A1.', s_body))
story.append(Paragraph('<b>Водительские права:</b> категория B.', s_body))
story.append(Paragraph(
    '<b>Готовность:</b> полная занятость, на месте / удалённо / гибрид; готов к переезду '
    'в Москву или Санкт-Петербург, к командировкам.', s_body))

# Build PDF
doc = SimpleDocTemplate(
    OUT_PATH, pagesize=A4,
    leftMargin=1.8*cm, rightMargin=1.8*cm, topMargin=1.5*cm, bottomMargin=1.5*cm,
    title='Татарников Герман Дмитриевич — Middle Java Developer',
    author='Татарников Герман Дмитриевич',
)
doc.build(story)
print(f'OK: {OUT_PATH}')
