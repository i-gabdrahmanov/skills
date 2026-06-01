"""
Generate resume PDF in hh.ru visual style for Татарников Г.Д.
Same content as v2, but with hh.ru-style layout:
- Two-column layout (dates/labels on left, content on right)
- Gray uppercase section titles with horizontal rule
- Gray pill-shaped skill tags
- hh.ru-style header with red logo dot
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.colors import HexColor, black, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Flowable
)

# Cyrillic fonts
pdfmetrics.registerFont(TTFont('HH', '/Library/Fonts/Arial Unicode.ttf'))
pdfmetrics.registerFont(TTFont('HHB', '/Library/Fonts/Arial Unicode.ttf'))
for cand in ['/System/Library/Fonts/Supplemental/Arial Bold.ttf']:
    if os.path.exists(cand):
        pdfmetrics.registerFont(TTFont('HHB', cand))
        break

OUT_PATH = "/Users/iskandergabdrahmanov/Downloads/Татарников_Герман_Дмитриевич_hh.pdf"

GRAY_LIGHT = HexColor('#A0A0A0')
GRAY_MED = HexColor('#7A7A7A')
GRAY_DARK = HexColor('#3D3D3D')
RULE = HexColor('#E0E0E0')
TAG_BG = HexColor('#F2F2F2')
HH_RED = HexColor('#E5252A')

# Styles
s_name = ParagraphStyle('name', fontName='HHB', fontSize=22, leading=26, textColor=black, spaceAfter=2)
s_subname = ParagraphStyle('subname', fontName='HH', fontSize=10, leading=13, textColor=GRAY_MED, spaceAfter=8)
s_contact = ParagraphStyle('contact', fontName='HH', fontSize=9.5, leading=13, textColor=black, spaceAfter=1)
s_contact_label = ParagraphStyle('cl', fontName='HH', fontSize=9.5, leading=13, textColor=GRAY_MED)

s_section = ParagraphStyle('section', fontName='HH', fontSize=11, leading=14,
                            textColor=GRAY_LIGHT, spaceBefore=14, spaceAfter=2)

s_label = ParagraphStyle('label', fontName='HH', fontSize=9, leading=12, textColor=GRAY_MED)
s_date = ParagraphStyle('date', fontName='HH', fontSize=9, leading=12, textColor=GRAY_MED)

s_title_big = ParagraphStyle('tb', fontName='HHB', fontSize=13, leading=16, textColor=black, spaceAfter=3)
s_company = ParagraphStyle('co', fontName='HHB', fontSize=11, leading=14, textColor=black, spaceAfter=1)
s_company_meta = ParagraphStyle('com', fontName='HH', fontSize=9, leading=12, textColor=GRAY_MED, spaceAfter=1)
s_role = ParagraphStyle('role', fontName='HH', fontSize=11, leading=14, textColor=black, spaceBefore=4, spaceAfter=4)

s_body = ParagraphStyle('body', fontName='HH', fontSize=9.5, leading=13, textColor=black, spaceAfter=4, alignment=TA_LEFT)
s_body_sm = ParagraphStyle('bs', fontName='HH', fontSize=9, leading=12, textColor=black, spaceAfter=2)


class SkillTag(Flowable):
    """A single gray pill-shaped skill tag."""
    def __init__(self, text, font='HH', font_size=9, pad_x=6, pad_y=3):
        super().__init__()
        self.text = text
        self.font = font
        self.font_size = font_size
        self.pad_x = pad_x
        self.pad_y = pad_y
        self.text_width = pdfmetrics.stringWidth(text, font, font_size)
        self.width = self.text_width + 2 * pad_x
        self.height = font_size + 2 * pad_y

    def draw(self):
        c = self.canv
        c.setFillColor(TAG_BG)
        c.setStrokeColor(TAG_BG)
        c.roundRect(0, 0, self.width, self.height, 2, fill=1, stroke=0)
        c.setFillColor(black)
        c.setFont(self.font, self.font_size)
        c.drawString(self.pad_x, self.pad_y + 1, self.text)


def tag_flow(tags, available_width):
    """Pack skill tags into rows like a flexbox wrap. Returns a Table flowable."""
    gap = 4
    rows = []
    current_row = []
    current_w = 0
    for t in tags:
        tag = SkillTag(t)
        if current_w + tag.width + (gap if current_row else 0) > available_width and current_row:
            rows.append(current_row)
            current_row = [tag]
            current_w = tag.width
        else:
            current_row.append(tag)
            current_w += tag.width + (gap if len(current_row) > 1 else 0)
    if current_row:
        rows.append(current_row)

    # Build a table where each row of tags is placed in cells inside a single-row sub-table
    outer_rows = []
    for row in rows:
        # Build per-tag cells then pad with empty space cell to fill
        cells = []
        col_widths = []
        for i, tag in enumerate(row):
            cells.append(tag)
            col_widths.append(tag.width)
            if i != len(row) - 1:
                cells.append('')
                col_widths.append(gap)
        # Trailing flex space
        used = sum(col_widths)
        if used < available_width:
            cells.append('')
            col_widths.append(available_width - used)
        sub = Table([cells], colWidths=col_widths, rowHeights=[row[0].height + 2])
        sub.setStyle(TableStyle([
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 1),
            ('BOTTOMPADDING', (0,0), (-1,-1), 1),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))
        outer_rows.append([sub])

    outer = Table(outer_rows, colWidths=[available_width])
    outer.setStyle(TableStyle([
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    return outer


# Layout constants (matches hh.ru proportions)
PAGE_W, PAGE_H = A4
LEFT_M = 1.8 * cm
RIGHT_M = 1.8 * cm
CONTENT_W = PAGE_W - LEFT_M - RIGHT_M  # ~17.4 cm
LCOL = 3.8 * cm   # left column (dates / section meta)
GAP = 0.4 * cm
RCOL = CONTENT_W - LCOL - GAP  # right column (~13.2 cm)


def section_header(title):
    """Section title in light gray + horizontal rule spanning the full content width."""
    tbl = Table(
        [[Paragraph(title, s_section)]],
        colWidths=[CONTENT_W],
    )
    tbl.setStyle(TableStyle([
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ]))
    return [
        tbl,
        HRFlowable(width=CONTENT_W, thickness=0.5, color=RULE, spaceBefore=0, spaceAfter=6),
    ]


def two_col(left_flow, right_flow):
    """A row with left meta (dates/labels) and right content."""
    if not isinstance(right_flow, list):
        right_flow = [right_flow]
    if not isinstance(left_flow, list):
        left_flow = [left_flow]
    t = Table(
        [[left_flow, '', right_flow]],
        colWidths=[LCOL, GAP, RCOL],
    )
    t.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ('TOPPADDING', (0,0), (-1,-1), 0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    return t


def bullet(text):
    return Paragraph(f'• {text}', s_body)


# ───── HEADER ─────
story = []

header_left = [
    Paragraph('Татарников Герман<br/>Дмитриевич', s_name),
    Paragraph('Мужчина, 26 лет, родился 29 июля 1999', s_subname),
    Paragraph('<font color="#000000">+7 (999) 162-63-96</font> '
              '<font color="#7A7A7A">— предпочитаемый способ связи  • </font>'
              '<font color="#7A7A7A">ТГ: t.me/DmitrichSOS</font>', s_contact),
    Paragraph('gerka0604@gmail.com', s_contact),
    Paragraph('telegram: @DmitrichSOS', s_contact),
    Spacer(1, 8),
    Paragraph('Проживает: Казань', s_contact),
    Paragraph('Гражданство: Россия', s_contact),
    Paragraph('Готов к переезду: Москва, Санкт-Петербург, готов к командировкам', s_contact),
]

# Small red dot in top-right corner (hh logo placeholder)
class HHDot(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 18
        self.height = 18
    def draw(self):
        c = self.canv
        c.setFillColor(HH_RED)
        c.setStrokeColor(HH_RED)
        c.circle(9, 9, 9, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont('HHB', 9)
        c.drawCentredString(9, 6, 'hh')

header_tbl = Table([[header_left, HHDot()]],
                   colWidths=[CONTENT_W - 1*cm, 1*cm])
header_tbl.setStyle(TableStyle([
    ('VALIGN', (0,0), (0,0), 'TOP'),
    ('VALIGN', (1,0), (1,0), 'TOP'),
    ('LEFTPADDING', (0,0), (-1,-1), 0),
    ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ('TOPPADDING', (0,0), (-1,-1), 0),
    ('BOTTOMPADDING', (0,0), (-1,-1), 0),
]))
story.append(header_tbl)
story.append(Spacer(1, 6))

# ───── ЖЕЛАЕМАЯ ДОЛЖНОСТЬ ─────
story += section_header('Желаемая должность и зарплата')
desired_right = [
    Paragraph('<b>Middle Java разработчик</b>', s_title_big),
    Paragraph('<font color="#7A7A7A">Backend | Spring Boot · Apache Kafka · PostgreSQL · Microservices</font>',
              s_body_sm),
    Spacer(1, 4),
    Paragraph('<font color="#7A7A7A">Специализации:</font>', s_body_sm),
    Paragraph('— Программист, разработчик', s_body_sm),
    Paragraph('<font color="#7A7A7A">Тип занятости:</font> полная занятость', s_body_sm),
    Paragraph('<font color="#7A7A7A">Формат работы:</font> на месте работодателя, удалённо, гибрид', s_body_sm),
    Paragraph('<font color="#7A7A7A">Желательное время в пути до работы:</font> не имеет значения', s_body_sm),
]
story.append(two_col([], desired_right))

# ───── ПРОФЕССИОНАЛЬНЫЙ ПРОФИЛЬ ─────
story += section_header('Профессиональный профиль')
summary_right = [
    Paragraph(
        'Middle Java Developer с коммерческим опытом <b>4+ года</b> в разработке высоконагруженных '
        'backend-сервисов в банковской сфере (ПАО Сбербанк) и госсекторе. Специализация — '
        'проектирование и поддержка распределённых систем на стеке <b>Java 17, Spring Boot, '
        'Apache Kafka, PostgreSQL, Hibernate, Docker, Kubernetes</b>.', s_body),
    Paragraph(
        'Опыт интеграций по REST API, SOAP/XML, Kafka; работа с событийно-ориентированной '
        'архитектурой (Event-Driven Architecture), микросервисами (Spring Cloud, OpenFeign), '
        'мониторингом (Prometheus, Grafana, ELK Stack) и CI/CD (Jenkins, GitLab CI). '
        'Применяю SOLID, GoF design patterns, Clean Code, TDD. Активно участвую в код-ревью '
        'и декомпозиции бизнес-требований.', s_body),
    Paragraph(
        '<b>Ключевые достижения:</b> сокращение времени транзакции с 5 минут до 3-5 сек '
        '(устранение N+1), ускорение массовых отчётов в 36 раз (JDBC Batching + CompletableFuture), '
        'снижение нагрузки на L1-поддержку на 30% за счёт автоматизации.', s_body),
]
story.append(two_col([Paragraph('Обо мне', s_label)], summary_right))

# ───── ОПЫТ РАБОТЫ ─────
story += section_header('Опыт работы — 4 года 3 месяца')

# Сбер
sber_left = [
    Paragraph('Сентябрь 2023 —<br/>Декабрь 2025', s_date),
    Paragraph('<font color="#3D3D3D">2 года 4 месяца</font>', s_label),
]
sber_right = [
    Paragraph('<b>ПАО Сбербанк</b>', s_company),
    Paragraph('Москва, rabota.sber.ru/', s_company_meta),
    Paragraph('Финансовый сектор', s_company_meta),
    Paragraph('• Банк', s_company_meta),
    Paragraph('Middle Java-разработчик', s_role),
    Paragraph('<b>Проект:</b> КИД — Картотека Исполнительных Документов '
              '(высоконагруженная корпоративная система обработки исполнительных документов).', s_body),
    Paragraph('<b>Стек:</b> Java 17, Spring Boot 3, Spring Data JPA, Hibernate, Apache Kafka, '
              'PostgreSQL, Liquibase, Resilience4j, WebClient, Docker, Kubernetes, Jenkins, '
              'JUnit 5, Mockito, GigaCode (AI).', s_body),
    bullet('Совмещал роли <b>системного аналитика и разработчика</b>: писал техническое задание (ТЗ), '
           'декомпозировал требования и реализовывал функционал end-to-end.'),
    bullet('Спроектировал и реализовал <b>отказоустойчивые пайплайны Apache Kafka</b> '
           '(Producer/Consumer) с кастомным <b>ErrorHandler</b> для ошибок десериализации; '
           'обеспечил семантику <b>At-Least-Once delivery</b> и идемпотентность обработки бизнес-событий.'),
    bullet('Разработал <b>Error Handling Service</b> для централизованной агрегации инцидентов '
           'через Kafka — снизил нагрузку на L1-поддержку на <b>30%</b>.'),
    bullet('Создал сервис генерации штрих-кодов с ежесуточной нагрузкой <b>50 000 документов</b> '
           'через <b>Spring Scheduler</b> и пакетные обновления <b>JDBC Batching</b>.'),
    bullet('Обеспечил надёжный запуск распределённых cron-задач через <b>Spring Scheduler + ShedLock</b> '
           '(защита от двойного запуска в кластере).'),
    bullet('Провёл <b>рефакторинг слоя Spring Data JPA / Hibernate</b>: устранил проблему N+1 через '
           'EntityGraph и fetch-стратегии — сократил время транзакции с <b>5 минут до 3–5 секунд</b>.'),
    bullet('Ускорил формирование массовых отчётов с <b>3 минут до 5 секунд</b> (×36) через '
           '<b>JDBC Batching</b> и асинхронную обработку <b>CompletableFuture</b>.'),
    bullet('Реализовал отказоустойчивый <b>REST API-клиент</b> на <b>Spring WebClient</b> '
           'с <b>Resilience4j Retry / Circuit Breaker</b> и паттерном <b>Dead Letter Queue</b> '
           '(persistent DLQ на PostgreSQL).'),
    bullet('Внедрил <b>AI-ассистента GigaCode</b> в командный процесс для генерации шаблонов '
           'unit-тестов (JUnit 5, Mockito) — повысил скорость покрытия кода на <b>20%</b>.'),
    bullet('Контейнеризация сервисов: <b>Docker, Docker Compose</b>; деплой в <b>Kubernetes</b> через '
           'CI/CD пайплайны на <b>Jenkins</b>. Мониторинг через <b>Prometheus + Grafana</b>, '
           'логирование через <b>ELK Stack</b>.'),
    bullet('Участие в <b>code review</b>, парное программирование, написание технической документации '
           'в Confluence.'),
]
story.append(two_col(sber_left, sber_right))

# Ай-Новус
inovus_left = [
    Paragraph('Октябрь 2021 —<br/>Сентябрь 2023', s_date),
    Paragraph('<font color="#3D3D3D">2 года</font>', s_label),
]
inovus_right = [
    Paragraph('<b>Ай-Новус</b>', s_company),
    Paragraph('Казань, i-novus.ru', s_company_meta),
    Paragraph('Информационные технологии, системная интеграция, интернет', s_company_meta),
    Paragraph('• Разработка программного обеспечения', s_company_meta),
    Paragraph('Junior Java-разработчик', s_role),
    Paragraph('<b>Проекты:</b> Электронное здравоохранение (ЕГИСЗ), госсектор; '
              'интеграции с государственными информационными системами.', s_body),
    Paragraph('<b>Стек:</b> Java 11, Spring Boot, Spring MVC, Hibernate, PostgreSQL, SOAP/XML '
              '(СМЭВ-3), OpenAPI 3.0 (Swagger), Selenium WebDriver, Maven, Git, фреймворк N2O.', s_body),
    bullet('Выполнил <b>миграцию монолитного приложения с J2EE (EJB) на Spring Boot</b>: '
           'время развёртывания окружения разработчика сократилось с 7 минут до <b>10 секунд</b>.'),
    bullet('Разработал <b>интеграционный адаптер с СМЭВ-3</b> (SOAP/XML, WS-Security) — '
           'провёл технический митап для 20+ разработчиков по работе с госструктурами.'),
    bullet('Реализовал <b>модуль расчёта компенсаций</b> (отпускные, больничные) с учётом '
           'производственного календаря РФ и системы грейдов.'),
    bullet('Описал контракты <b>REST API в OpenAPI 3.0 (Swagger)</b> через Java-аннотации и YAML-спецификации.'),
    bullet('Разработал <b>UI-автотесты</b> на Selenium WebDriver для внутреннего фреймворка N2O.'),
]
story.append(two_col(inovus_left, inovus_right))

# ───── КЛЮЧЕВЫЕ ДОСТИЖЕНИЯ ─────
story += section_header('Ключевые достижения')
achievements_right = [
    bullet('Оптимизация SQL-транзакции: <b>5 минут → 3-5 секунд</b> (устранение N+1, EntityGraph).'),
    bullet('Ускорение массовых отчётов: <b>3 минуты → 5 секунд (×36)</b> — JDBC Batching + CompletableFuture.'),
    bullet('Развёртывание окружения: <b>7 минут → 10 секунд (×42)</b> — миграция J2EE → Spring Boot.'),
    bullet('Снижение нагрузки на L1-поддержку на <b>30%</b> через автоматизированный Error Handling Service.'),
    bullet('Покрытие unit-тестами <b>+20%</b> через внедрение AI-ассистента в команде.'),
    bullet('Ежесуточная обработка <b>50 000 документов</b> в production без даунтайма.'),
]
story.append(two_col([Paragraph('Метрики', s_label)], achievements_right))

# ───── ОБРАЗОВАНИЕ ─────
story += section_header('Образование')

# Sub-header "Высшее"
story.append(two_col([], Paragraph('<font color="#3D3D3D"><b>Высшее</b></font>', s_body)))
edu_left = [Paragraph('2021<br/>Высшее', s_date)]
edu_right = [
    Paragraph('<b>Казанский государственный энергетический университет, Казань</b>', s_body),
    Paragraph('Теплоэнергетика и Теплотехника, Промышленная теплоэнергетика', s_body_sm),
]
story.append(two_col(edu_left, edu_right))

# Sub-header
story.append(two_col([], Paragraph('<font color="#3D3D3D"><b>Повышение квалификации, курсы</b></font>', s_body)))

courses = [
    ('2022', '<b>«Продвинутая Java»</b>', 'Udemy, https://www.udemy.com/certificate/UC-de5f91b0-b302-47ab-b010-083c68a2c2bf/'),
    ('2020', '<b>«Java. От простого к сложному»</b>', 'Udemy, https://www.udemy.com/certificate/UC-9488e74c-3f85-4973-b2a0-a7d8119c9bb7/'),
    ('2020', '<b>Java core</b>', 'Javarush, Java разработчик'),
]
for year, title, src in courses:
    story.append(two_col(
        [Paragraph(year, s_date)],
        [Paragraph(title, s_body), Paragraph(f'<font color="#7A7A7A">{src}</font>', s_body_sm)]
    ))

# ───── НАВЫКИ ─────
story += section_header('Навыки')

# Languages
lang_right = [
    Paragraph('<b>Русский</b> <font color="#7A7A7A">— Родной</font>', s_body),
    Paragraph('<b>Английский</b> <font color="#7A7A7A">— B1 — Средний</font>', s_body),
    Paragraph('<b>Татарский</b> <font color="#7A7A7A">— A1 — Начальный</font>', s_body),
]
story.append(two_col([Paragraph('Знание языков', s_label)], lang_right))

# Hard Skills — structured categories (15 rows)
hard_skills_data = [
    ('Языки программирования', 'Java 8 / 11 / 17, SQL, YAML, XML'),
    ('Backend-фреймворки', 'Spring Boot, Spring MVC, Spring Data JPA, Spring Security, Spring Cloud, '
                           'Spring Scheduler, Spring Test, Spring WebFlux (WebClient)'),
    ('Базы данных', 'PostgreSQL, Oracle, Liquibase, Flyway, Hibernate, JDBC, JDBC Batching, JPA, '
                    'оптимизация SQL-запросов, индексирование'),
    ('Брокеры сообщений', 'Apache Kafka (Producer/Consumer, Streams, Schema Registry), RabbitMQ (базово)'),
    ('Микросервисы', 'Microservices Architecture, Event-Driven Architecture, REST API, OpenAPI 3.0 / '
                     'Swagger, OpenFeign, Spring Cloud Gateway, Service Discovery (Eureka), '
                     'Resilience4j (Retry, Circuit Breaker, Rate Limiter), Dead Letter Queue (DLQ)'),
    ('Кэширование', 'Redis, Caffeine, Spring Cache'),
    ('Тестирование', 'JUnit 5, Mockito, Spring Test, Testcontainers, Selenium WebDriver, TDD, '
                     'Unit / Integration / E2E тесты'),
    ('DevOps / CI/CD', 'Docker, Docker Compose, Kubernetes (kubectl, helm — базово), Jenkins, '
                       'GitLab CI, Git, Bitbucket'),
    ('Мониторинг и логирование', 'Prometheus, Grafana, ELK Stack (Elasticsearch, Logstash, Kibana), '
                                  'Spring Boot Actuator'),
    ('Многопоточность', 'Multithreading, Concurrency, CompletableFuture, ExecutorService, Stream API'),
    ('Архитектура и подходы', 'ООП, SOLID, GoF Design Patterns, Clean Code, Clean Architecture, '
                              'DDD (базово), Code Review, Refactoring'),
    ('Методологии', 'Agile, Scrum, Kanban'),
    ('Инструменты', 'IntelliJ IDEA, Maven, Gradle, Postman, Confluence, Jira'),
    ('Интеграции', 'REST, SOAP/XML, СМЭВ-3, gRPC (базово), WebSocket (базово)'),
    ('AI-инструменты', 'GigaCode, GitHub Copilot — генерация unit-тестов, ускорение разработки'),
]
for label, value in hard_skills_data:
    story.append(two_col(
        [Paragraph(label, s_label)],
        [Paragraph(value, s_body)]
    ))

# Skill tags (hh.ru style gray pills)
skills = [
    'Java 17', 'Java 11', 'Spring Boot', 'Spring Framework', 'Spring MVC', 'Spring Data JPA',
    'Spring Security', 'Spring Cloud', 'Spring Scheduler', 'Spring Test', 'Hibernate',
    'PostgreSQL', 'Oracle', 'Liquibase', 'Flyway', 'JDBC', 'JDBC Batching',
    'Apache Kafka', 'RabbitMQ', 'REST API', 'SOAP/XML', 'OpenAPI 3.0', 'Swagger',
    'OpenFeign', 'Spring Cloud Gateway', 'Resilience4j', 'Dead Letter Queue',
    'Microservices', 'Event-Driven Architecture',
    'Docker', 'Docker Compose', 'Kubernetes', 'Jenkins', 'GitLab CI', 'Git', 'Bitbucket',
    'Prometheus', 'Grafana', 'ELK Stack', 'Spring Boot Actuator',
    'Redis', 'Caffeine', 'Spring Cache',
    'JUnit 5', 'Mockito', 'Testcontainers', 'Selenium WebDriver', 'TDD',
    'Multithreading', 'Concurrency', 'CompletableFuture', 'Stream API', 'Collections Framework',
    'ООП', 'SOLID', 'GoF Design Patterns', 'Clean Code', 'Refactoring', 'Код-ревью',
    'Agile', 'Scrum',
    'IntelliJ IDEA', 'Maven', 'Gradle', 'Postman', 'Confluence', 'Jira',
    'gRPC', 'WebSocket', 'СМЭВ-3', 'GigaCode', 'GitHub Copilot',
]
story.append(two_col(
    [Paragraph('Навыки', s_label)],
    [tag_flow(skills, RCOL)]
))

# Driving
story += [Spacer(1, 6)]
story += section_header('Опыт вождения')
story.append(two_col([], Paragraph('Права категории B', s_body)))

# Additional info / About me
story += section_header('Дополнительная информация')
about_right = [
    Paragraph(
        'Я — Middle Java-разработчик с коммерческим опытом более 4 лет. '
        'Специализируюсь на backend-разработке высоконагруженных систем с использованием стека '
        '<b>Spring Boot, Apache Kafka, PostgreSQL, Hibernate, Docker, Kubernetes</b>. '
        'Опыт построения <b>микросервисной</b> и <b>событийно-ориентированной</b> архитектуры, '
        'интеграций по REST API, SOAP/XML, Kafka.', s_body),
    Paragraph(
        'Придерживаюсь практик <b>чистого кода</b>, активно применяю принципы <b>SOLID</b> '
        'и паттерны проектирования <b>GoF</b>. Имею опыт работы с мониторингом (<b>Prometheus, '
        'Grafana, ELK Stack</b>) и CI/CD пайплайнами (<b>Jenkins, GitLab CI</b>). '
        'Участвую в код-ревью и декомпозиции бизнес-требований.', s_body),
    Paragraph(
        'В работе нацелен на результат и метрики: успешно оптимизировал время выполнения '
        'запросов к БД с 5 минут до 5 секунд, ускорил развертывание легаси-системы в десятки раз. '
        'Легко адаптируюсь к крупным энтерпрайз-проектам, в том числе в банковской сфере. '
        'Готов к сложным задачам по реализации фич и рефакторингу.', s_body),
    Paragraph(
        'Есть опыт внедрения AI-инструментов (<b>GigaCode, GitHub Copilot</b>) '
        'в цикл разработки для ускорения тестирования.', s_body),
]
story.append(two_col([Paragraph('Обо мне', s_label)], about_right))


# Footer on every page
def draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('HH', 8)
    canvas.setFillColor(GRAY_LIGHT)
    if doc.page == 1:
        canvas.drawString(LEFT_M, 1.0*cm, 'Резюме обновлено 7 мая 2026 в 12:17')
    else:
        canvas.drawString(LEFT_M, 1.0*cm, 'Татарников Герман  •  Резюме обновлено 7 мая 2026 в 12:17')
    canvas.restoreState()


doc = SimpleDocTemplate(
    OUT_PATH, pagesize=A4,
    leftMargin=LEFT_M, rightMargin=RIGHT_M,
    topMargin=1.6*cm, bottomMargin=1.6*cm,
    title='Татарников Герман Дмитриевич — Middle Java разработчик',
    author='Татарников Герман Дмитриевич',
)
doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
print(f'OK: {OUT_PATH}')
