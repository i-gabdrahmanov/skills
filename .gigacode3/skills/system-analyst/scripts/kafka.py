"""Парсер Kafka producers/consumers/topics в Java/Kotlin исходниках."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Импортируем общие хелперы из соседних модулей.
from common import in_skipped_dir  # noqa: E402
from endpoints import _balanced, _find_method_signature, _strip_comments  # noqa: E402


@dataclass
class KafkaConsumer:
    class_name: str
    method: str
    topics: list[str]
    file: str
    group_id: str = ""


@dataclass
class KafkaProducer:
    class_name: str
    method: str        # java-метод, внутри которого вызывается send
    topics: list[str]
    file: str


@dataclass
class KafkaModel:
    consumers: list[KafkaConsumer] = field(default_factory=list)
    producers: list[KafkaProducer] = field(default_factory=list)

    @property
    def topics(self) -> set[str]:
        t: set[str] = set()
        for c in self.consumers:
            t.update(c.topics)
        for p in self.producers:
            t.update(p.topics)
        return t

    @property
    def empty(self) -> bool:
        return not self.consumers and not self.producers


_TOPIC_CONST_RE = re.compile(
    r"static\s+final\s+String\s+([A-Z_][A-Z0-9_]*)\s*=\s*\"([^\"]+)\""
)

_CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_]\w*)")


def _resolve_topic(raw: str, constants: dict[str, str]) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    # Литерал
    m = re.fullmatch(r'"([^"]*)"', raw)
    if m:
        return m.group(1)
    # Имя константы вида CLASS.CONST или CONST
    m = re.fullmatch(r"(?:\w+\.)?([A-Z_][A-Z0-9_]*)", raw)
    if m:
        # Резолвим из локальных констант файла; иначе (кросс-файловая константа) —
        # сохраняем символически как const:<NAME>, чтобы не терять топик в recall.
        return constants.get(m.group(1)) or f"const:{m.group(1)}"
    return ""


def _collect_topics_from_args(args: str, constants: dict[str, str]) -> list[str]:
    """Из аргументов @KafkaListener(topics = ...) извлечь список топиков."""
    topics: list[str] = []
    # topics = "x" или topics = {"a", "b"}
    m = re.search(r"topics\s*=\s*\{([^}]*)\}", args)
    if m:
        for raw in re.split(r",", m.group(1)):
            t = _resolve_topic(raw, constants)
            if t:
                topics.append(t)
    else:
        m = re.search(r"topics\s*=\s*([^,)]+)", args)
        if m:
            t = _resolve_topic(m.group(1), constants)
            if t:
                topics.append(t)
    # topicPattern = "..."
    m = re.search(r'topicPattern\s*=\s*"([^"]+)"', args)
    if m:
        topics.append(f"pattern:{m.group(1)}")
    return topics


def _enclosing_class(text: str, pos: int) -> str:
    """Имя последнего class ... перед позицией pos."""
    last = ""
    for m in _CLASS_RE.finditer(text):
        if m.start() < pos:
            last = m.group(1)
        else:
            break
    return last


def _enclosing_method(text: str, pos: int) -> str:
    """Найти имя метода, в теле которого лежит pos."""
    # Грубая эвристика: ищем последнюю сигнатуру `\w+\s*\([^)]*\)\s*\{` перед pos,
    # у которой парная `}` идёт ПОСЛЕ pos.
    name = ""
    sig_re = re.compile(r"([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:throws[^{;]+)?\{")
    for m in sig_re.finditer(text):
        if m.end() > pos:
            break
        # Проверим, что blok ещё открыт на позиции pos
        brace = m.end() - 1
        close = _balanced(text, brace, "{", "}")
        if close > pos:
            name = m.group(1)
    return name


def _topic_constants(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _TOPIC_CONST_RE.finditer(text)}


def parse_file(path: Path) -> tuple[list[KafkaConsumer], list[KafkaProducer]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []
    if (
        "@KafkaListener" not in raw
        and "KafkaTemplate" not in raw
        and "@SendTo" not in raw
    ):
        return [], []
    text = _strip_comments(raw)
    constants = _topic_constants(text)
    consumers: list[KafkaConsumer] = []
    producers: list[KafkaProducer] = []

    # --- Consumers via @KafkaListener ---
    for m in re.finditer(r"@KafkaListener\b", text):
        j = m.end()
        args = ""
        k = j
        while k < len(text) and text[k].isspace():
            k += 1
        if k < len(text) and text[k] == "(":
            close = _balanced(text, k, "(", ")")
            if close > 0:
                args = text[k:close]
                j = close
        # Найти сигнатуру следующего метода (с учётом возможных других аннотаций)
        is_kotlin = path.suffix == ".kt"
        sig = _find_method_signature(text, j, kotlin=is_kotlin)
        method = sig[3] if sig else ""
        topics = _collect_topics_from_args(args, constants)
        if not topics:
            continue
        group = ""
        gm = re.search(r'groupId\s*=\s*"([^"]+)"', args)
        if gm:
            group = gm.group(1)
        consumers.append(
            KafkaConsumer(
                class_name=_enclosing_class(text, m.start()),
                method=method,
                topics=topics,
                file=str(path),
                group_id=group,
            )
        )

    # --- Producers via kafkaTemplate.send("topic", ...) ---
    # Также ловим вообще <что угодно>.send("topic", ...) если в файле есть KafkaTemplate.
    send_pattern = re.compile(
        r"\b([A-Za-z_]\w*)\s*\.\s*send\s*\(\s*([^,)]+)"
    )
    has_kafka_template = "KafkaTemplate" in text
    for m in send_pattern.finditer(text):
        receiver = m.group(1)
        # Эвристика: receiver должен быть похож на kafka template
        rcv_lower = receiver.lower()
        if not has_kafka_template and "kafka" not in rcv_lower and "template" not in rcv_lower:
            continue
        topic = _resolve_topic(m.group(2), constants)
        if not topic:
            continue
        producers.append(
            KafkaProducer(
                class_name=_enclosing_class(text, m.start()),
                method=_enclosing_method(text, m.start()),
                topics=[topic],
                file=str(path),
            )
        )

    # --- Producers via @SendTo ---
    for m in re.finditer(r"@SendTo\b", text):
        j = m.end()
        args = ""
        k = j
        while k < len(text) and text[k].isspace():
            k += 1
        if k < len(text) and text[k] == "(":
            close = _balanced(text, k, "(", ")")
            if close > 0:
                args = text[k:close]
                j = close
        topic = _resolve_topic(re.sub(r"[(){}]", "", args).strip(), constants)
        if not topic:
            continue
        is_kotlin = path.suffix == ".kt"
        sig = _find_method_signature(text, j, kotlin=is_kotlin)
        method = sig[3] if sig else ""
        producers.append(
            KafkaProducer(
                class_name=_enclosing_class(text, m.start()),
                method=method,
                topics=[topic],
                file=str(path),
            )
        )
    return consumers, producers


def scan(root: Path) -> KafkaModel:
    model = KafkaModel()
    root = root.resolve()
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if in_skipped_dir(root, path):
            continue
        if path.suffix not in (".java", ".kt"):
            continue
        c, p = parse_file(path)
        model.consumers.extend(c)
        model.producers.extend(p)
    model.consumers.sort(key=lambda c: (c.class_name, c.method))
    model.producers.sort(key=lambda p: (p.class_name, p.method))
    return model
