"""SVG-рендерер для sequence- и component-диаграмм.

Строит SVG напрямую из AST (Controller/Endpoint/KafkaModel),
не парся PlantUML. Без внешних зависимостей: только stdlib.

Зачем:
- IDEA Markdown preview нативно рендерит ![](svg) ссылки.
- GitHub тоже рендерит SVG из MD.
- Нет нужды в Java/plantuml.jar/Graphviz/Node.js.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Union

from endpoints import Controller
from kafka import KafkaModel

# ── визуальные константы ─────────────────────────────────────────────────────

FONT_FAMILY = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
    "'Helvetica Neue', Arial, sans-serif"
)
FONT_SIZE = 13
LABEL_FONT_SIZE = 12
MARGIN = 20

# sequence
HEADER_H = 34
LIFELINE_TOP_PAD = 16
LIFELINE_BOTTOM_PAD = 16
MSG_ROW_H = 38
SECTION_H = 26
SECTION_GAP = 8
MIN_PARTICIPANT_GAP = 60
PARTICIPANT_PAD_X = 14
ACTOR_HEIGHT = 30

# component / flowchart
BOX_W_MIN = 90
BOX_PAD_X = 20
BOX_H = 44
COL_GAP = 150
ROW_GAP = 24
TOPIC_R = 12  # corner radius for queue

# colors (стилистика, близкая к default-теме PlantUML/Mermaid)
C_BOX_FILL = "#fefefe"
C_BOX_STROKE = "#6c7079"
C_HEADER_FILL = "#e6e9ef"
C_LIFELINE = "#9aa0a6"
C_SECTION_FILL = "#f4f5f7"
C_SECTION_STROKE = "#6c7079"
C_ARROW = "#2e3338"
C_ARROW_DASHED = "#5f6368"
C_TEXT = "#1c1e21"
C_NOTE_BG = "#fff7c2"
C_NOTE_BORDER = "#c8a90d"
C_TOPIC_FILL = "#dde8ff"
C_TOPIC_STROKE = "#3b6fb6"
C_PRODUCER_FILL = "#e6f4e6"
C_PRODUCER_STROKE = "#3b8b3b"
C_CONSUMER_FILL = "#fdecec"
C_CONSUMER_STROKE = "#b35454"


# ── утилиты ──────────────────────────────────────────────────────────────────


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# Эмпирическая оценка ширины строки в пикселях для sans-serif.
# Реальный движок (Cairo/Skia) дал бы точно, но stdlib не имеет
# доступа к метрикам шрифта, поэтому считаем через таблицу
# приближённых ширин на 13px.
_GLYPH_WIDTH_13 = {
    "i": 4, "l": 4, "I": 4, "j": 4, ".": 4, ",": 4, ";": 4, ":": 4,
    "f": 6, "t": 6, "r": 6, "1": 7,
    "M": 11, "W": 12, "m": 12, "w": 11,
}
_DEFAULT_GLYPH_13 = 8


def text_width(text: str, font_size: int = FONT_SIZE) -> int:
    base = sum(_GLYPH_WIDTH_13.get(c, _DEFAULT_GLYPH_13) for c in text)
    return int(base * font_size / 13)


# ── общие SVG-примитивы ──────────────────────────────────────────────────────


def _svg_open(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" '
        f'font-family="{FONT_FAMILY}" font-size="{FONT_SIZE}" '
        f'fill="{C_TEXT}">'
    )


def _rect(x: int, y: int, w: int, h: int, fill: str, stroke: str, rx: int = 4) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" '
        f'fill="{fill}" stroke="{stroke}" />'
    )


def _text(x: int, y: int, text: str, *, anchor: str = "middle", weight: str = "normal",
          size: int = FONT_SIZE, color: str = C_TEXT) -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-size="{size}" font-weight="{weight}" fill="{color}">{_escape(text)}</text>'
    )


def _line(x1: int, y1: int, x2: int, y2: int, color: str, dashed: bool = False) -> str:
    extra = ' stroke-dasharray="5 4"' if dashed else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        f'stroke="{color}" stroke-width="1"{extra} />'
    )


def _arrow_head(x: int, y: int, direction: str, color: str) -> str:
    if direction == "right":
        pts = f"{x},{y} {x-9},{y-5} {x-9},{y+5}"
    else:
        pts = f"{x},{y} {x+9},{y-5} {x+9},{y+5}"
    return f'<polygon points="{pts}" fill="{color}" />'


# ── sequence-диаграмма ──────────────────────────────────────────────────────


@dataclass
class Participant:
    id: str
    label: str
    kind: str = "participant"  # "actor" | "participant" | "queue"
    x: int = 0
    width: int = 0


@dataclass
class SeqSection:
    title: str
    y: int = 0


@dataclass
class SeqMessage:
    src: str
    dst: str
    label: str
    style: str = "solid"  # "solid" | "dashed"
    y: int = 0


SeqEvent = Union[SeqSection, SeqMessage]


def _participant_width(p: Participant) -> int:
    return max(text_width(p.label) + PARTICIPANT_PAD_X * 2, 80)


def _layout_participants(
    participants: list[Participant], events: list[SeqEvent]
) -> int:
    """Расставить participants по X. Вернуть полную ширину диаграммы."""
    for p in participants:
        p.width = _participant_width(p)

    # Минимальный зазор между соседними lifeline'ами по широчайшему сообщению,
    # которое проходит через соответствующий промежуток.
    n = len(participants)
    id_to_idx = {p.id: i for i, p in enumerate(participants)}
    gap_min: list[int] = [MIN_PARTICIPANT_GAP] * max(0, n - 1)
    for ev in events:
        if not isinstance(ev, SeqMessage):
            continue
        si = id_to_idx.get(ev.src)
        di = id_to_idx.get(ev.dst)
        if si is None or di is None or si == di:
            continue
        lo, hi = sorted((si, di))
        # ширина текста + запас на стрелку
        label_w = text_width(ev.label, LABEL_FONT_SIZE) + 30
        per_gap = max(MIN_PARTICIPANT_GAP, label_w // (hi - lo))
        for k in range(lo, hi):
            if per_gap > gap_min[k]:
                gap_min[k] = per_gap

    x = MARGIN
    for i, p in enumerate(participants):
        p.x = x + p.width // 2
        x += p.width
        if i + 1 < n:
            x += gap_min[i]
    return x + MARGIN


def _layout_events(events: list[SeqEvent], y_start: int) -> int:
    y = y_start
    for ev in events:
        if isinstance(ev, SeqSection):
            y += SECTION_GAP
            ev.y = y
            y += SECTION_H + SECTION_GAP
        else:
            ev.y = y + MSG_ROW_H // 2
            y += MSG_ROW_H
    return y


def _draw_participant_header(p: Participant, y: int) -> list[str]:
    """Верхний (или нижний) бокс участника."""
    parts: list[str] = []
    if p.kind == "actor":
        # человечек слева, прямоугольник справа? Проще: круг + палочки.
        cx = p.x
        cy = y + 12
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="6" fill="{C_HEADER_FILL}" stroke="{C_BOX_STROKE}" />'
        )
        parts.append(_line(cx, cy + 6, cx, cy + 20, C_BOX_STROKE))
        parts.append(_line(cx - 7, cy + 12, cx + 7, cy + 12, C_BOX_STROKE))
        parts.append(_line(cx, cy + 20, cx - 6, cy + 30, C_BOX_STROKE))
        parts.append(_line(cx, cy + 20, cx + 6, cy + 30, C_BOX_STROKE))
        parts.append(_text(cx, y + HEADER_H + 14, p.label, weight="bold"))
        return parts
    if p.kind == "queue":
        # цилиндр (две дуги + прямоугольник)
        x1 = p.x - p.width // 2
        w = p.width
        h = HEADER_H
        rx = 10
        parts.append(
            f'<path d="M {x1} {y+rx} '
            f'A {w/2} {rx} 0 0 1 {x1+w} {y+rx} '
            f'L {x1+w} {y+h-rx} '
            f'A {w/2} {rx} 0 0 1 {x1} {y+h-rx} Z" '
            f'fill="{C_TOPIC_FILL}" stroke="{C_TOPIC_STROKE}" />'
        )
        parts.append(
            f'<path d="M {x1} {y+rx} A {w/2} {rx} 0 0 0 {x1+w} {y+rx}" '
            f'fill="none" stroke="{C_TOPIC_STROKE}" />'
        )
        parts.append(_text(p.x, y + h // 2 + 5, p.label, weight="bold"))
        return parts
    # обычный participant
    x1 = p.x - p.width // 2
    parts.append(_rect(x1, y, p.width, HEADER_H, C_HEADER_FILL, C_BOX_STROKE))
    parts.append(_text(p.x, y + HEADER_H // 2 + 5, p.label, weight="bold"))
    return parts


def render_sequence_svg(
    participants: list[Participant],
    events: list[SeqEvent],
) -> str:
    """Сгенерировать SVG для sequence-диаграммы."""
    if not participants:
        return _svg_open(100, 40) + "</svg>"
    width = _layout_participants(participants, events)
    y_after_headers = MARGIN + HEADER_H + LIFELINE_TOP_PAD
    y_end_events = _layout_events(events, y_after_headers)
    height = y_end_events + LIFELINE_BOTTOM_PAD + HEADER_H + MARGIN

    parts: list[str] = [_svg_open(width, height)]
    # background (transparent)
    # lifelines
    lifeline_y1 = MARGIN + HEADER_H
    lifeline_y2 = height - MARGIN - HEADER_H
    for p in participants:
        parts.append(_line(p.x, lifeline_y1, p.x, lifeline_y2, C_LIFELINE, dashed=True))

    # top + bottom headers
    for p in participants:
        parts.extend(_draw_participant_header(p, MARGIN))
        parts.extend(_draw_participant_header(p, height - MARGIN - HEADER_H))

    by_id = {p.id: p for p in participants}

    for ev in events:
        if isinstance(ev, SeqSection):
            x1 = MARGIN
            x2 = width - MARGIN
            parts.append(_rect(x1, ev.y, x2 - x1, SECTION_H, C_SECTION_FILL, C_SECTION_STROKE))
            parts.append(_text((x1 + x2) // 2, ev.y + SECTION_H // 2 + 5, ev.title, weight="bold"))
        else:
            src = by_id.get(ev.src)
            dst = by_id.get(ev.dst)
            if src is None or dst is None:
                continue
            color = C_ARROW if ev.style == "solid" else C_ARROW_DASHED
            dashed = ev.style != "solid"
            if src.id == dst.id:
                # self-message
                cx = src.x
                y = ev.y
                loop_w = 40
                parts.append(_line(cx, y - 6, cx + loop_w, y - 6, color, dashed=dashed))
                parts.append(_line(cx + loop_w, y - 6, cx + loop_w, y + 6, color, dashed=dashed))
                parts.append(_line(cx + loop_w, y + 6, cx + 6, y + 6, color, dashed=dashed))
                parts.append(_arrow_head(cx + 6, y + 6, "left", color))
                parts.append(_text(cx + loop_w + 6, y, ev.label, anchor="start", size=LABEL_FONT_SIZE))
                continue
            direction = "right" if src.x < dst.x else "left"
            # сжать концы, чтобы стрелка не лезла внутрь бокса соседа
            x1 = src.x
            x2 = dst.x - (9 if direction == "right" else -9)
            parts.append(_line(x1, ev.y, x2, ev.y, color, dashed=dashed))
            parts.append(_arrow_head(x2, ev.y, direction, color))
            mid = (src.x + dst.x) // 2
            parts.append(_text(mid, ev.y - 6, ev.label, size=LABEL_FONT_SIZE))

    parts.append("</svg>")
    return "\n".join(parts)


# ── публичные обёртки от AST к sequence-SVG ─────────────────────────────────


def endpoint_sequence_svg(controller: Controller) -> str:
    participants: list[Participant] = [Participant("Client", "Client", "actor")]
    participants.append(Participant("Ctrl", controller.class_name))
    used: dict[str, str] = {}
    for e in controller.endpoints:
        for field_name, _ in e.calls:
            if field_name in controller.dependencies:
                used[field_name] = controller.dependencies[field_name]
    alias_for_field: dict[str, str] = {}
    used_ids = {"Client", "Ctrl"}
    for field_name, type_name in used.items():
        alias = type_name
        if alias in used_ids:
            alias = f"{type_name}_{field_name}"
        used_ids.add(alias)
        alias_for_field[field_name] = alias
        participants.append(Participant(alias, type_name))

    events: list[SeqEvent] = []
    for e in controller.endpoints:
        events.append(SeqSection(title=f"{e.http_method} {e.path}"))
        param_summary = ", ".join(p.name for p in e.params)
        msg = e.method_name + (f"({param_summary})" if param_summary else "()")
        events.append(SeqMessage("Client", "Ctrl", msg))
        for field_name, call_method in e.calls:
            alias = alias_for_field.get(field_name)
            if not alias:
                continue
            events.append(SeqMessage("Ctrl", alias, f"{call_method}()"))
            events.append(SeqMessage(alias, "Ctrl", "", style="dashed"))
        ret = e.return_type.replace("ResponseEntity<", "").rstrip(">") or "void"
        events.append(SeqMessage("Ctrl", "Client", ret, style="dashed"))
    return render_sequence_svg(participants, events)


def kafka_sequence_svg(model: KafkaModel) -> str:
    by_topic_p: dict[str, list[tuple[str, str]]] = {}
    by_topic_c: dict[str, list[tuple[str, str]]] = {}
    for p in model.producers:
        for t in p.topics:
            by_topic_p.setdefault(t, []).append((p.class_name, p.method))
    for c in model.consumers:
        for t in c.topics:
            by_topic_c.setdefault(t, []).append((c.class_name, c.method))
    topics = sorted(set(by_topic_p) | set(by_topic_c))
    producers_seen: set[str] = set()
    consumers_seen: set[str] = set()
    for t in topics:
        for cls, _ in by_topic_p.get(t, []):
            if cls:
                producers_seen.add(cls)
        for cls, _ in by_topic_c.get(t, []):
            if cls:
                consumers_seen.add(cls)
    participants: list[Participant] = []
    for cls in sorted(producers_seen):
        participants.append(Participant(cls, cls))
    participants.append(Participant("Kafka", "Kafka", "queue"))
    for cls in sorted(consumers_seen):
        if cls in producers_seen:
            continue
        participants.append(Participant(cls, cls))

    events: list[SeqEvent] = []
    for t in topics:
        events.append(SeqSection(title=t))
        for cls, method in by_topic_p.get(t, []):
            if not cls:
                continue
            label = f"{method}()" if method else "send"
            events.append(SeqMessage(cls, "Kafka", f"send({label})"))
        for cls, method in by_topic_c.get(t, []):
            if not cls:
                continue
            handler = f"{method}()" if method else "onMessage"
            events.append(SeqMessage("Kafka", cls, handler))
    return render_sequence_svg(participants, events)


# ── component-диаграмма Kafka ───────────────────────────────────────────────


@dataclass
class CompNode:
    id: str
    label: str
    kind: str  # "producer" | "topic" | "consumer"
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = BOX_H


@dataclass
class CompEdge:
    src: str
    dst: str
    label: str = ""


def _node_width(label: str) -> int:
    return max(text_width(label) + BOX_PAD_X * 2, BOX_W_MIN)


def _draw_node(n: CompNode) -> list[str]:
    parts: list[str] = []
    x1 = n.x - n.w // 2
    if n.kind == "topic":
        # cylinder
        rx = 10
        parts.append(
            f'<path d="M {x1} {n.y+rx} '
            f'A {n.w/2} {rx} 0 0 1 {x1+n.w} {n.y+rx} '
            f'L {x1+n.w} {n.y+n.h-rx} '
            f'A {n.w/2} {rx} 0 0 1 {x1} {n.y+n.h-rx} Z" '
            f'fill="{C_TOPIC_FILL}" stroke="{C_TOPIC_STROKE}" />'
        )
        parts.append(
            f'<path d="M {x1} {n.y+rx} A {n.w/2} {rx} 0 0 0 {x1+n.w} {n.y+rx}" '
            f'fill="none" stroke="{C_TOPIC_STROKE}" />'
        )
    elif n.kind == "producer":
        parts.append(_rect(x1, n.y, n.w, n.h, C_PRODUCER_FILL, C_PRODUCER_STROKE, rx=6))
    else:
        parts.append(_rect(x1, n.y, n.w, n.h, C_CONSUMER_FILL, C_CONSUMER_STROKE, rx=6))
    parts.append(_text(n.x, n.y + n.h // 2 + 5, n.label, weight="bold"))
    return parts


def _edge_geometry(src: CompNode, dst: CompNode) -> Tuple[int, int, int, int, str]:
    sx = src.x + src.w // 2 if dst.x > src.x else src.x - src.w // 2
    dx = dst.x - dst.w // 2 if dst.x > src.x else dst.x + dst.w // 2
    sy = src.y + src.h // 2
    dy = dst.y + dst.h // 2
    direction = "right" if dx > sx else "left"
    return sx, sy, dx, dy, direction


def _draw_edge_line(src: CompNode, dst: CompNode) -> List[str]:
    sx, sy, dx, dy, direction = _edge_geometry(src, dst)
    tip_x = dx - 9 if direction == "right" else dx + 9
    return [
        f'<line x1="{sx}" y1="{sy}" x2="{tip_x}" y2="{dy}" '
        f'stroke="{C_ARROW}" stroke-width="1" />',
        _arrow_head(dx, dy, direction, C_ARROW),
    ]


def _draw_edge_label(src: CompNode, dst: CompNode, label: str, t: float = 0.5) -> List[str]:
    """Лейбл стрелки. Параметр t (0..1) — позиция вдоль линии от src к dst.

    Якорь текста выбирается так, чтобы лейбл вырастал «наружу» от ближайшей
    точки крепления и не перекрывал ноду рядом.
    """
    if not label:
        return []
    sx, sy, dx, dy, _ = _edge_geometry(src, dst)
    px = int(sx + (dx - sx) * t)
    py = int(sy + (dy - sy) * t)
    text_w = text_width(label, LABEL_FONT_SIZE)
    pad_x = 5
    pad_y = 3
    if t < 0.45:
        anchor = "start"
        rect_x = px + 2
    elif t > 0.55:
        anchor = "end"
        rect_x = px - text_w - 2 - pad_x * 2
    else:
        anchor = "middle"
        rect_x = px - text_w // 2 - pad_x
    if anchor == "start":
        text_x = px + 2 + pad_x
    elif anchor == "end":
        text_x = px - 2 - pad_x
    else:
        text_x = px
    return [
        f'<rect x="{rect_x}" y="{py - 10 - pad_y}" '
        f'width="{text_w + pad_x*2}" height="{16 + pad_y*2}" rx="3" '
        f'fill="#ffffff" stroke="none" />',
        _text(text_x, py + 4, label, anchor=anchor, size=LABEL_FONT_SIZE),
    ]


def kafka_component_svg(model: KafkaModel) -> str:
    if model.empty:
        return _svg_open(100, 40) + "</svg>"

    all_producer_names = {p.class_name for p in model.producers if p.class_name}
    all_consumer_names = {c.class_name for c in model.consumers if c.class_name}
    # Сервис, который и produce, и consume, помещаем в правую колонку (consumer);
    # стрелка produce от него к topic пойдёт обратно (справа налево).
    both = all_producer_names & all_consumer_names
    producer_only_names = sorted(all_producer_names - both)
    consumer_names = sorted(all_consumer_names)
    topic_names = sorted(model.topics)

    producers = [CompNode(n, n, "producer") for n in producer_only_names]
    topics = [CompNode(t, t, "topic") for t in topic_names]
    consumers = [
        CompNode(n, n, "consumer") for n in consumer_names
    ]

    for n in producers + topics + consumers:
        n.w = _node_width(n.label)

    col1_w = max((n.w for n in producers), default=0)
    col2_w = max((n.w for n in topics), default=0)
    col3_w = max((n.w for n in consumers), default=0)

    col1_x = MARGIN + col1_w // 2 if producers else MARGIN
    col2_x = col1_x + col1_w // 2 + COL_GAP + col2_w // 2 if producers else MARGIN + col2_w // 2
    col3_x = col2_x + col2_w // 2 + COL_GAP + col3_w // 2 if consumers else col2_x

    total_rows = max(len(producers), len(topics), len(consumers), 1)
    total_h = total_rows * BOX_H + (total_rows - 1) * ROW_GAP

    def _place_column(items: list[CompNode], col_x: int) -> None:
        n = len(items)
        if n == 0:
            return
        col_h = n * BOX_H + (n - 1) * ROW_GAP
        offset = MARGIN + (total_h - col_h) // 2
        for i, node in enumerate(items):
            node.x = col_x
            node.y = offset + i * (BOX_H + ROW_GAP)

    _place_column(producers, col1_x)
    _place_column(topics, col2_x)
    _place_column(consumers, col3_x)

    width_cells = (
        (col1_w + COL_GAP if producers else 0)
        + col2_w
        + (COL_GAP + col3_w if consumers else 0)
    )
    width = MARGIN * 2 + width_cells
    height = MARGIN * 2 + total_h

    service_by_name: dict = {}
    for n in producers + consumers:
        service_by_name[n.id] = n
    topic_by_name: dict = {n.id: n for n in topics}

    # edges: (src_node, dst_node, label, label_t).
    # label_t = позиция лейбла вдоль линии (0..1). Прижимаем все лейблы
    # к ноде topic — так они разносятся по вертикали и не наслаиваются.
    edges: list = []
    seen: set = set()
    for p in model.producers:
        if not p.class_name:
            continue
        src = service_by_name.get(p.class_name)
        for t in p.topics:
            dst = topic_by_name.get(t)
            if src is None or dst is None:
                continue
            key = (p.class_name, "->t", t)
            if key in seen:
                continue
            seen.add(key)
            edges.append((src, dst, "produce", 0.75))
    for c in model.consumers:
        if not c.class_name:
            continue
        dst = service_by_name.get(c.class_name)
        for t in c.topics:
            src = topic_by_name.get(t)
            if src is None or dst is None:
                continue
            key = (t, "->c", c.class_name)
            if key in seen:
                continue
            seen.add(key)
            label = "consume"
            if c.group_id:
                label += f" ({c.group_id})"
            edges.append((src, dst, label, 0.3))

    parts: list = [_svg_open(width, height)]
    # Порядок отрисовки (z-order снизу вверх):
    # 1. линии и стрелочные наконечники
    # 2. ноды (бокс перекрывает все линии, проходящие сквозь него)
    # 3. лейблы стрелок (поверх всего — гарантированно читаемые)
    for src, dst, _, _ in edges:
        parts.extend(_draw_edge_line(src, dst))
    for n in producers + topics + consumers:
        parts.extend(_draw_node(n))
    for src, dst, lbl, t in edges:
        parts.extend(_draw_edge_label(src, dst, lbl, t=t))
    parts.append("</svg>")
    return "\n".join(parts)
