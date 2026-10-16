"""component_map.py — карта компонентов `.daiver/component-map.yaml`: «узел макета → компонент библиотеки».

Спека §5.2 (схема и инварианты карты), §10.4–10.5 (карта читается до fuzzy-резолва, правило дописывается
только после «да»), §13.5 п.4 (импорты не изобретаются); INV-B17, C7, F9, D3.

Что делает
  • `lookup(root, figmaName, variants?)` — первое, что резолв делает с кандидатом. Попадание в правило —
    вердикт high без чтения библиотеки; `import.statement` ответа вставляется в код ДОСЛОВНО.
  • `append(root, track, rule)` — единственный способ дописать правило. Только по согласию `ui-map-<KEY>`
    (запись record_approval в ground/approvals.jsonl, проверяет forge_events.approval); одно «да» — одно
    правило (согласие тратится записью, как у rollback и override). Файл не заменяется целиком: правило
    вставляется ТЕКСТОМ в конец блока `rules:`, комментарии, порядок и переводы строк остаются на месте;
    результат перед записью разбирается заново и сверяется с исходной картой (прежние правила не
    изменились, новое — ровно принятое), запись атомарна (tmp + replace) под замком.

Что читается строго (опечатка в карте — громкая ошибка, а не молча пустой список правил)
  Схема закрыта: неизвестный ключ верхнего уровня, `library`, `icons`, правила или `import` — проблема.
  Правило проверяется целиком, все нарушения называются ОДНИМ сообщением. Битая карта не применяется
  (lookup и append отказывают с перечнем проблем): «карта по догадке» хуже, чем остановка.

Правила сопоставления (допущения помечены «не проверено данными»: образца боевой карты у нас нет, только
текст спеки; имена для тестов — из образца «Modal»)
  • Имя в `match` без учёта регистра и разделителей (§6.4 figmaName): NFKC → casefold → только буквы и цифры,
    поэтому «Modal Header», «modal-header», «ModalHeader» и «MODAL_HEADER» — один ключ.
  • `match`: строка-имя, список имён (many-to-one) или `/regex/` (в списке regex не допускается). Regex
    ищется (`search`, как `test` в JS) в имени КАК ЕСТЬ — регистр захватов `$1…$9` сохраняется — и чувствителен
    к регистру, если не дописан флаг `i` (других флагов нет). Не проверено данными: спека регистр regex не
    называет.
  • Приоритет: точное имя/список выигрывает у любого regex независимо от места в файле (частное сильнее
    общего); regex — по порядку в файле, первый сработавший. Одно имя в двух правилах — дубликат (тот же
    результат) или конфликт (другой) — явная ошибка и при чтении файла, и при append.
  • `outcome`: component (нужен component; icon нельзя), local (component необязателен — имя помогает матчеру
    verify; без props/import), ignore (только match), icon (нужен icon; component и import нельзя). Поле,
    которое исходу не принадлежит, — ошибка, а не молчаливо проигнорированное.
  • Импорт. Путь — per-rule `import.path` либо `library.import` (легаси-алиас `importPath` — то же значение).
    Только bare specifier: относительный путь зависит от файла, куда вставляется statement. Формы:
    `import { Badge } from "@your/ui";`; `named` ≠ имени компонента → `import { Named as Badge } from …`;
    `default: true` → `import Badge from "…";` (`named` при этом либо опущен, либо равен имени, как в примере
    спеки); `{name}` в `import.path` заменяется именем компонента (шаблон путей «один компонент — один
    файл»). Составное имя `Form.Item` импортирует корень `Form`. Не проверено данными: спека называет
    `default: true # или default-export через шаблон {name}`, но не раскрывает механику.
  • Пути нет вообще → `import: null` и `importNote`: основной пакет не угадывается.
  • Иконки — из пакета иконок (`icons.import.path`), НЕ из основного, даже если значения совпадают; пути
    иконок нет → `import: null`. С `wrapper` иконка — `<Icon name="…"/>` (component = wrapper.component,
    props.name = имя в наборе); без — отдельный компонент пакета. Явное правило `outcome: icon` задаёт имя в
    наборе окончательно; `wrapper.nameMap` («имя иконки макета → имя в наборе») — таблица для имён БЕЗ
    правила (явное сильнее неявного). Не проверено данными.
  • `variants` lookup принимает и проверяет, но в сопоставлении не использует: пропы задаёт правило (спека не
    описывает связи вариантов с поиском по карте). Не проверено данными.
  • Файл карты — `.daiver/` + `library.mapFile` конфига (по умолчанию `./component-map.yaml`); вне `.daiver/`
    карта лежать не может.

Автономный прогон карту не пишет (INV-D3): в нём некому дать согласие. Явный запрет по режиму прогона
(`run.json`, директива) — задача 7.1.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config as C
from . import track as TR
from . import yamlite as Y

HOOKS = Path(__file__).resolve().parents[4] / "hooks"

OUTCOMES = ("component", "local", "ignore", "icon")
FALLBACKS = ("ask", "placeholder")
CONSENT_PREFIX = "ui-map"

TOP_KEYS = ("library", "icons", "rules")
LIBRARY_KEYS = ("name", "import", "importPath")
ICONS_KEYS = ("import", "fallback")
ICONS_IMPORT_KEYS = ("path", "wrapper")
WRAPPER_KEYS = ("component", "nameMap")
RULE_KEYS = ("match", "outcome", "component", "icon", "props", "import")
IMPORT_KEYS = ("path", "named", "default")
_APPLIES = {"component": ("match", "outcome", "component", "props", "import"),
            "local": ("match", "outcome", "component"),
            "ignore": ("match", "outcome"),
            "icon": ("match", "outcome", "icon", "props")}

MAX_NAME = 200
MAX_PATTERN = 200
MAX_LIST = 50
MAX_TEXT = 200
MAX_ICON = 100
MAX_VARIANTS = 30

_REGEX_LITERAL = re.compile(r"^/(.+)/([A-Za-z]*)$", re.DOTALL)
_RULES_LINE = re.compile(r"^rules[ \t]*:(.*)$")
_CAP = re.compile(r"\$(\d)")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SPEC = re.compile(r"^[A-Za-z0-9@#._/+-]+$")
_PROP_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_:-]*$")
_ICON_NAME = re.compile(r"^\w[\w ./:-]*$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_FORCE_QUOTE = frozenset('$\\"\'/,[]{}#:')


class MapError(ValueError):
    """Карта не разобрана или не проходит проверку, правило отвергнуто, нет согласия — одним сообщением."""


# ── имена ────────────────────────────────────────────────────────────────────

def name_key(name: Any) -> str:
    """Ключ имени без учёта регистра и разделителей: NFKC → casefold → только буквы и цифры (§6.4)."""
    if not isinstance(name, str):
        return ""
    folded = unicodedata.normalize("NFKC", name).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def consent_key(track: str) -> str:
    """Ключ согласия на правило в карту: `ui-map-<KEY>` (класс согласий с цитатой пользователя)."""
    return f"{CONSENT_PREFIX}-{track}"


def _kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "булево"
    if isinstance(v, (int, float)):
        return "число"
    if isinstance(v, str):
        return "строка"
    if isinstance(v, list):
        return "список"
    if isinstance(v, dict):
        return "объект"
    return type(v).__name__


def _short_match(match: Any) -> str:
    if isinstance(match, str):
        s = match.strip()
        return s if len(s) <= 40 else s[:37] + "…"
    if isinstance(match, list):
        items = [x.strip() for x in match if isinstance(x, str)]
        shown = ", ".join(items[:3]) + (", …" if len(items) > 3 else "")
        return shown if len(shown) <= 60 else shown[:57] + "…"
    return ""


_CASES = {"nom": ("правило", "новое правило"), "prep": ("правиле", "новом правиле"),
          "ins": ("правилом", "новым правилом")}


def _where(index: int, match: Any, case: str = "nom") -> str:
    word, fresh = _CASES[case]
    head = f"{word} #{index}" if index else fresh
    shown = _short_match(match)
    return f"{head} «{shown}»" if shown else head


# ── проверка значений ────────────────────────────────────────────────────────

def _template_problem(value: str, field: str, groups: Optional[int], is_regex: bool) -> Optional[str]:
    """`$1…$9` — захваты regex-match; другого `$` нет. groups=None — regex не разобрался, диапазон не судим."""
    refs = [int(n) for n in _CAP.findall(value)]
    if "$" in _CAP.sub("", value):
        return f"{field}: «$» — только как захват $1…$9"
    if refs and not is_regex:
        return f"{field}: захват ${refs[0]} в правиле без regex-match"
    if groups is not None:
        for n in refs:
            if n < 1 or n > groups:
                return f"{field}: захват ${n} — в regex групп: {groups}"
    return None


def _component_problem(name: str) -> Optional[str]:
    parts = name.split(".")
    if not all(_IDENT.match(p) for p in parts):
        return "не имя компонента (латиница, цифры, «_»; через точку — составной, вроде Form.Item)"
    if len(parts) == 1 and "a" <= parts[0][0] <= "z":
        return "JSX читает имя с маленькой буквы как тег HTML — у компонента оно с заглавной"
    return None


def _path_problem(value: str, templated: bool = False) -> Optional[str]:
    probe = _CAP.sub("X", value.replace("{name}", "X")) if templated else value
    if not probe or not _SPEC.match(probe):
        return "не путь пакета (латиница, цифры, @ # . _ / + -; без пробелов, кавычек и скобок)"
    if probe.startswith((".", "/")) or ".." in probe.split("/"):
        return ("относительный путь зависит от файла, в который вставляется statement, — нужен путь пакета "
                "(@scope/pkg, pkg/sub)")
    return None


def _icon_text_problem(probe: str) -> Optional[str]:
    if not probe or len(probe) > MAX_ICON or not _ICON_NAME.match(probe):
        return "имя иконки: буквы, цифры, пробел и . / : _ - (до 100 символов), без кавычек и скобок"
    return None


def _icon_component_problem(probe: str) -> Optional[str]:
    if "." in probe:
        return "иконка без wrapper — отдельный компонент пакета иконок: имя без точки"
    return _component_problem(probe)


# ── правило ──────────────────────────────────────────────────────────────────

class Rule:
    """Проверенное правило. `match` хранится как написано (строка или список), `keys` — ключи имён."""

    __slots__ = ("index", "match", "kind", "keys", "names", "regex", "pattern", "flags", "groups",
                 "outcome", "component", "icon", "props", "imp")

    def __init__(self, **kw: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def label(self, case: str = "nom") -> str:
        return _where(self.index, self.match, case)

    def effect(self) -> Dict[str, Any]:
        """Что правило даёт (без того, по чему оно ищется): по нему различаются дубликат и конфликт."""
        return {"outcome": self.outcome, "component": self.component, "icon": self.icon,
                "props": dict(self.props), "import": dict(self.imp) if self.imp else None}

    def as_dict(self) -> Dict[str, Any]:
        """Правило в каноническом порядке ключей — так оно пишется в файл и возвращается в ответах."""
        d: Dict[str, Any] = {"match": list(self.match) if isinstance(self.match, list) else self.match,
                             "outcome": self.outcome}
        if self.component is not None:
            d["component"] = self.component
        if self.icon is not None:
            d["icon"] = self.icon
        if self.props:
            d["props"] = dict(self.props)
        if self.imp:
            d["import"] = dict(self.imp)
        return d


def _name_entry(s: str, where: str, problems: List[str], keys: List[str], names: List[str]) -> None:
    if len(s) > MAX_NAME or _CTRL.search(s):
        problems.append(f"{where}: имя «{_short_match(s)}» слишком длинное или с управляющими символами")
        return
    key = name_key(s)
    if not key:
        problems.append(f"{where}: имя «{s}» не содержит букв и цифр — по нему нечего сопоставлять")
    elif key in keys:
        problems.append(f"{where}: имя «{s}» повторяет «{names[keys.index(key)]}» "
                        f"(регистр и разделители не различаются)")
    else:
        keys.append(key)
        names.append(s)


def _parse_match(raw: Dict[str, Any], where: str, problems: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"kind": None, "match": None, "keys": [], "names": [], "regex": None,
                           "pattern": None, "flags": "", "groups": None}
    m = raw.get("match")
    if m is None:
        problems.append(f"{where}: нет поля match (имя, список имён или /regex/)")
    elif isinstance(m, str):
        s = m.strip()
        lit = _REGEX_LITERAL.match(s)
        if not s:
            problems.append(f"{where}: match пуст")
        elif lit:
            out["kind"], out["match"] = "regex", s
            pattern, flags = lit.group(1), lit.group(2)
            out["pattern"], out["flags"] = pattern, flags
            if len(pattern) > MAX_PATTERN:
                problems.append(f"{where}: regex длиннее {MAX_PATTERN} символов")
            elif flags not in ("", "i"):
                problems.append(f"{where}: флаг regex «{flags}» не поддерживается (допустим только i)")
            else:
                try:
                    rx = re.compile(pattern, re.IGNORECASE if flags == "i" else 0)
                    out["regex"], out["groups"] = rx, rx.groups
                except re.error as e:
                    problems.append(f"{where}: regex /{pattern}/ не компилируется: {e}")
        else:
            out["kind"], out["match"] = "name", s
            _name_entry(s, where, problems, out["keys"], out["names"])
    elif isinstance(m, list):
        out["kind"] = "names"
        items: List[str] = []
        if not m:
            problems.append(f"{where}: match — пустой список")
        elif len(m) > MAX_LIST:
            problems.append(f"{where}: в match больше {MAX_LIST} имён — раздели правило")
        else:
            for item in m:
                if not isinstance(item, str) or not item.strip():
                    problems.append(f"{where}: в match-списке только непустые строки-имена (число, true, null — "
                                    f"в кавычках), получено: {_kind(item)}")
                elif _REGEX_LITERAL.match(item.strip()):
                    problems.append(f"{where}: regex «{item.strip()}» в списке имён не допускается — "
                                    f"regex пишется отдельным правилом")
                else:
                    items.append(item.strip())
                    _name_entry(item.strip(), where, problems, out["keys"], out["names"])
        out["match"] = items
    else:
        problems.append(f"{where}: match — строка (имя или /regex/) или список имён, получено: {_kind(m)}")
    return out


def _check_str(v: Any, field: str, where: str, problems: List[str]) -> Optional[str]:
    if not isinstance(v, str) or not v.strip():
        problems.append(f"{where}: {field} — непустая строка, получено: {_kind(v)}")
        return None
    return v.strip()


def _check_props(pv: Any, where: str, outcome: str, wrapper: bool, groups: Optional[int], is_regex: bool,
                 problems: List[str]) -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    if not isinstance(pv, dict):
        problems.append(f"{where}: props — объект {{проп: значение}}, получено: {_kind(pv)}")
        return props
    for k, x in pv.items():
        if not isinstance(k, str) or not _PROP_KEY.match(k):
            problems.append(f"{where}: props: «{k}» — не имя пропа")
        elif outcome == "icon" and wrapper and k == "name":
            problems.append(f"{where}: props.name у иконки с wrapper задаёт поле icon")
        elif isinstance(x, bool):
            props[k] = x
        elif isinstance(x, (int, float)):
            if math.isfinite(x):
                props[k] = x
            else:
                problems.append(f"{where}: props.{k}: число должно быть конечным")
        elif isinstance(x, str):
            if len(x) > MAX_TEXT or _CTRL.search(x):
                problems.append(f"{where}: props.{k}: строка длиннее {MAX_TEXT} символов или с переводом строки")
            else:
                t = _template_problem(x, f"props.{k}", groups, is_regex)
                if t:
                    problems.append(f"{where}: {t}")
                else:
                    props[k] = x
        else:
            problems.append(f"{where}: props.{k}: значение — строка, число или true/false, получено: {_kind(x)}")
    return props


def _check_import(iv: Any, component: Optional[str], where: str, groups: Optional[int], is_regex: bool,
                  problems: List[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(iv, dict) or not iv:
        problems.append(f"{where}: import — объект {{path?, named?, default?}}, получено: "
                        f"{'пустой объект' if isinstance(iv, dict) else _kind(iv)}")
        return None
    for k in iv:
        if k not in IMPORT_KEYS:
            problems.append(f"{where}: import: неизвестное поле «{k}» (допустимы: {', '.join(IMPORT_KEYS)})")
    out: Dict[str, Any] = {}
    if "path" in iv:
        p = _check_str(iv["path"], "import.path", where, problems)
        if p is not None:
            t = _template_problem(p, "import.path", groups, is_regex)
            q = _path_problem(p, templated=True)
            if t or q:
                problems.append(f"{where}: {t or 'import.path «' + p + '»: ' + str(q)}")
            else:
                out["path"] = p
    if "named" in iv:
        n = _check_str(iv["named"], "import.named", where, problems)
        if n is not None:
            t = _template_problem(n, "import.named", groups, is_regex)
            if t:
                problems.append(f"{where}: {t}")
            elif not _IDENT.match(_CAP.sub("X", n)):
                problems.append(f"{where}: import.named «{n}» — имя экспорта: латиница, цифры, «_»")
            else:
                out["named"] = n
    if "default" in iv:
        if isinstance(iv["default"], bool):
            out["default"] = iv["default"]
        else:
            problems.append(f"{where}: import.default — true или false (шаблон {{name}} пишется в import.path), "
                            f"получено: {_kind(iv['default'])}")
    if out.get("default") is True and "named" in out and component is not None:
        root = component.split(".")[0]
        if out["named"] != root:
            problems.append(f"{where}: import.default: true с named «{out['named']}» ≠ имени компонента «{root}» — "
                            f"у default-импорта выбирать нечего: убери named или сделай его равным имени")
    return out or None


def check_rule(raw: Any, index: int, wrapper: bool) -> Tuple[Optional[Rule], List[str]]:
    """Правило целиком → (Rule, []) либо (None, все нарушения). index=0 — новое правило (ещё без места)."""
    if not isinstance(raw, dict):
        head = f"правило #{index}" if index else "новое правило"
        return None, [f"{head}: ожидается объект {{match, outcome, …}}, получено: {_kind(raw)}"]
    where = _where(index, raw.get("match"))
    problems: List[str] = []
    for k in raw:
        if k not in RULE_KEYS:
            problems.append(f"{where}: неизвестное поле «{k}» (допустимы: {', '.join(RULE_KEYS)})")
    pm = _parse_match(raw, where, problems)
    is_regex = pm["kind"] not in ("name", "names")      # match не разобрался — захваты не судим строго
    groups: Optional[int] = pm["groups"]

    outcome = raw.get("outcome")
    if outcome is None:
        problems.append(f"{where}: нет поля outcome ({' | '.join(OUTCOMES)})")
    elif outcome not in OUTCOMES:
        problems.append(f"{where}: outcome «{outcome}» — ожидается {' | '.join(OUTCOMES)}")
        outcome = None
    if outcome in OUTCOMES:
        for f in ("component", "icon", "props", "import"):
            if f in raw and f not in _APPLIES[outcome]:
                problems.append(f"{where}: outcome «{outcome}» не принимает поле «{f}»")
        need = {"component": "component", "icon": "icon"}.get(outcome)
        if need and raw.get(need) is None:
            problems.append(f"{where}: outcome «{outcome}» требует поле «{need}»")
    else:
        outcome = None

    component: Optional[str] = None
    if outcome in ("component", "local") and raw.get("component") is not None:
        c = _check_str(raw["component"], "component", where, problems)
        if c is not None:
            t = _template_problem(c, "component", groups, is_regex)
            q = _component_problem(_CAP.sub("X", c))
            if t or q:
                problems.append(f"{where}: {t or 'component «' + c + '»: ' + str(q)}")
            else:
                component = c

    icon: Optional[str] = None
    if outcome == "icon" and raw.get("icon") is not None:
        c = _check_str(raw["icon"], "icon", where, problems)
        if c is not None:
            t = _template_problem(c, "icon", groups, is_regex)
            probe = _CAP.sub("X", c)
            q = _icon_text_problem(probe) if wrapper else _icon_component_problem(probe)
            if t or q:
                problems.append(f"{where}: {t or 'icon «' + c + '»: ' + str(q)}")
            else:
                icon = c

    props: Dict[str, Any] = {}
    if outcome in ("component", "icon") and "props" in raw:
        props = _check_props(raw["props"], where, outcome, wrapper, groups, is_regex, problems)

    imp: Optional[Dict[str, Any]] = None
    if outcome == "component" and "import" in raw:
        imp = _check_import(raw["import"], component, where, groups, is_regex, problems)

    if problems or outcome is None:
        return None, problems
    return Rule(index=index, match=pm["match"], kind=pm["kind"], keys=pm["keys"], names=pm["names"],
                regex=pm["regex"], pattern=pm["pattern"], flags=pm["flags"], groups=pm["groups"],
                outcome=outcome, component=component, icon=icon, props=props, imp=imp), []


# ── карта целиком ────────────────────────────────────────────────────────────

def _effect_diff(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    parts: List[str] = []
    for k in ("outcome", "component", "icon", "props", "import"):
        if a[k] != b[k]:
            parts.append(f"{k}: {a[k] or '—'} ≠ {b[k] or '—'}" if k in ("outcome", "component", "icon") else k)
    return ", ".join(parts)


def _clash(rule: Rule, other: Rule, what: str) -> str:
    if rule.effect() == other.effect():
        return f"{rule.label()}: {what} уже есть в {other.label('prep')} с тем же результатом — дубликат"
    return (f"{rule.label()}: {what} уже занято {other.label('ins')} с ДРУГИМ результатом "
            f"({_effect_diff(rule.effect(), other.effect())}) — конфликт; существующее правило append не меняет, "
            f"его правит человек")


def _subst(template: str, caps: Tuple[Optional[str], ...]) -> str:
    """`$n` → n-я группа regex (не участвовавшая или пустая — пустая строка: годность итога судит проверка имени)."""
    def rep(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        return (caps[n - 1] if 1 <= n <= len(caps) else None) or ""
    return _CAP.sub(rep, template)


class Parsed:
    """Разобранная и проверенная карта (без обращения к диску)."""

    def __init__(self) -> None:
        self.library_name: Optional[str] = None
        self.library_import: Optional[str] = None
        self.icons_path: Optional[str] = None
        self.wrapper: Optional[str] = None
        self.name_map: Dict[str, Tuple[str, str]] = {}      # ключ имени → (как написано, имя в наборе)
        self.icons_fallback: str = "ask"
        self.rules: List[Rule] = []
        self.problems: List[str] = []
        self.by_key: Dict[str, Rule] = {}
        self.by_regex: Dict[Tuple[str, str], Rule] = {}

    def head(self) -> Tuple[Any, ...]:
        """Всё, кроме правил: append не вправе это менять."""
        return (self.library_name, self.library_import, self.icons_path, self.wrapper,
                dict(self.name_map), self.icons_fallback)

    def clashes(self, rule: Rule) -> List[str]:
        """Дубликаты и конфликты нового правила с уже принятыми (по имени и по тексту regex)."""
        grouped: Dict[int, Tuple[Rule, List[str]]] = {}
        order: List[int] = []
        for k, shown in zip(rule.keys, rule.names):
            other = self.by_key.get(k)
            if other is not None:
                if id(other) not in grouped:
                    grouped[id(other)] = (other, [])
                    order.append(id(other))
                grouped[id(other)][1].append(shown)
        out = [_clash(rule, grouped[o][0], "имя " + ", ".join(f"«{s}»" for s in grouped[o][1])) for o in order]
        if rule.kind == "regex":
            other_rx = self.by_regex.get((rule.pattern, rule.flags))
            if other_rx is not None:
                out.append(_clash(rule, other_rx, f"regex {rule.match}"))
        return out

    def register(self, rule: Rule) -> None:
        for k in rule.keys:
            self.by_key.setdefault(k, rule)
        if rule.kind == "regex":
            self.by_regex.setdefault((rule.pattern, rule.flags), rule)
        self.rules.append(rule)

    # ── поиск ────────────────────────────────────────────────────────────────

    def find(self, name: str) -> Dict[str, Any]:
        key = name_key(name)
        if not key:
            raise MapError(f"figmaName «{name}» не содержит букв и цифр — по нему нечего искать")
        rule = self.by_key.get(key)
        caps: Tuple[Optional[str], ...] = ()
        if rule is None:
            for r in self.rules:                       # regex — по порядку в файле, первый сработавший
                if r.kind == "regex":
                    m = r.regex.search(name)
                    if m:
                        rule, caps = r, m.groups()
                        break
        if rule is not None:
            return self._rule_hit(rule, caps)
        mapped = self.name_map.get(key)
        if mapped is not None:
            orig, target = mapped
            return self._icon_hit(target, {}, {"kind": "nameMap", "match": orig}, "icons.nameMap")
        return {"hit": False}

    def _rule_hit(self, rule: Rule, caps: Tuple[Optional[str], ...]) -> Dict[str, Any]:
        where = rule.label()

        def sub(t: str) -> str:
            return _subst(t, caps)

        def subs(props: Dict[str, Any]) -> Dict[str, Any]:
            return {k: (sub(v) if isinstance(v, str) else v) for k, v in props.items()}

        info = {"index": rule.index, "kind": rule.kind, "match": rule.match}
        if rule.outcome == "icon":
            return self._icon_hit(sub(rule.icon), subs(rule.props), info, where)
        out: Dict[str, Any] = {"hit": True, "outcome": rule.outcome}
        note: Optional[str] = None
        if rule.outcome == "component":
            comp = sub(rule.component)
            self._final_component(comp, where)
            imp, note = self._component_import(rule, comp, sub, where)
            out.update({"component": comp, "props": subs(rule.props), "import": imp})
        elif rule.outcome == "local":
            comp = sub(rule.component) if rule.component else None
            if comp is not None:
                self._final_component(comp, where)
            out.update({"component": comp, "props": {}, "import": None})
        else:                                           # ignore
            out.update({"component": None, "props": {}, "import": None})
        if note:
            out["importNote"] = note
        out["rule"] = info
        out["confidence"] = "high"
        return out

    @staticmethod
    def _final_component(comp: str, where: str) -> None:
        q = _component_problem(comp)
        if q:
            raise MapError(f"{where}: после подстановки захватов получилось «{comp}» — {q}")

    def _component_import(self, rule: Rule, comp: str, sub: Any, where: str
                          ) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        binding = comp.split(".")[0]
        imp = rule.imp or {}
        if imp.get("path"):
            path = sub(imp["path"]).replace("{name}", binding)
            q = _path_problem(path)
            if q:
                raise MapError(f"{where}: import.path после подстановки «{path}» — {q}")
        else:
            path = self.library_import
        if not path:
            return None, ("путь импорта не задан ни в правиле, ни в library.import — основной пакет не "
                          "угадывается: спроси пользователя")
        named = sub(imp["named"]) if imp.get("named") else binding
        if not _IDENT.match(named):
            raise MapError(f"{where}: import.named после подстановки «{named}» — не имя экспорта")
        if imp.get("default") is True:
            if named != binding:
                raise MapError(f"{where}: default-импорт: named «{named}» ≠ имени компонента «{binding}»")
            stmt = f'import {binding} from "{path}";'
        elif named == binding:
            stmt = f'import {{ {binding} }} from "{path}";'
        else:
            stmt = f'import {{ {named} as {binding} }} from "{path}";'
        return {"statement": stmt, "path": path}, None

    def _icon_hit(self, name: str, props: Dict[str, Any], info: Dict[str, Any], where: str) -> Dict[str, Any]:
        q = _icon_text_problem(name) if self.wrapper else _icon_component_problem(name)
        if q:
            raise MapError(f"{where}: иконка «{name}» — {q}")
        if self.wrapper:
            comp, binding = self.wrapper, self.wrapper
            out_props = dict([("name", name)] + list(props.items()))
        else:
            comp, binding, out_props = name, name, dict(props)
        out: Dict[str, Any] = {"hit": True, "outcome": "icon", "component": comp, "icon": name, "props": out_props}
        if self.icons_path:
            out["import"] = {"statement": f'import {{ {binding} }} from "{self.icons_path}";',
                             "path": self.icons_path}
        else:
            out["import"] = None
            out["importNote"] = ("путь иконок (icons.import.path) не задан — иконки живут в своём пакете, основной "
                                 "для них не подставляется: спроси пользователя")
        out["rule"] = info
        out["confidence"] = "high"
        return out


def _read_library(v: Any, p: Parsed) -> None:
    if v is None:
        return
    if not isinstance(v, dict):
        p.problems.append(f"library: ожидается объект, получено: {_kind(v)}")
        return
    for k in v:
        if k not in LIBRARY_KEYS:
            p.problems.append(f"library: неизвестное поле «{k}» (допустимы: {', '.join(LIBRARY_KEYS)})")
    name = v.get("name")
    if name is not None:
        if isinstance(name, str):
            p.library_name = name
        else:
            p.problems.append(f"library.name: строка, получено: {_kind(name)}")
    paths: Dict[str, str] = {}
    for k in ("import", "importPath"):
        x = v.get(k)
        if x is None:
            continue
        q = _path_problem(x.strip()) if isinstance(x, str) else f"ожидается строка, получено: {_kind(x)}"
        if q:
            p.problems.append(f"library.{k}: {q}")
        else:
            paths[k] = x.strip()
    if len(set(paths.values())) > 1:
        p.problems.append("library.import и library.importPath (легаси-алиас того же) расходятся")
    p.library_import = paths.get("import") or paths.get("importPath")


def _read_icons(v: Any, p: Parsed) -> None:
    if v is None:
        return
    if not isinstance(v, dict):
        p.problems.append(f"icons: ожидается объект, получено: {_kind(v)}")
        return
    for k in v:
        if k not in ICONS_KEYS:
            p.problems.append(f"icons: неизвестное поле «{k}» (допустимы: {', '.join(ICONS_KEYS)})")
    fb = v.get("fallback")
    if fb is not None:
        if fb in FALLBACKS:
            p.icons_fallback = fb
        else:
            p.problems.append(f"icons.fallback: «{fb}» — ожидается {' | '.join(FALLBACKS)}")
    imp = v.get("import")
    if imp is None:
        return
    if not isinstance(imp, dict):
        p.problems.append(f"icons.import: ожидается объект, получено: {_kind(imp)}")
        return
    for k in imp:
        if k not in ICONS_IMPORT_KEYS:
            p.problems.append(f"icons.import: неизвестное поле «{k}» (допустимы: {', '.join(ICONS_IMPORT_KEYS)})")
    path = imp.get("path")
    if path is not None:
        q = _path_problem(path.strip()) if isinstance(path, str) else f"ожидается строка, получено: {_kind(path)}"
        if q:
            p.problems.append(f"icons.import.path: {q}")
        else:
            p.icons_path = path.strip()
    wr = imp.get("wrapper")
    if wr is None:
        return
    if not isinstance(wr, dict):
        p.problems.append(f"icons.import.wrapper: ожидается объект, получено: {_kind(wr)}")
        return
    for k in wr:
        if k not in WRAPPER_KEYS:
            p.problems.append(f"icons.import.wrapper: неизвестное поле «{k}» (допустимы: {', '.join(WRAPPER_KEYS)})")
    comp = wr.get("component")
    if not isinstance(comp, str) or not comp.strip():
        p.problems.append("icons.import.wrapper.component обязателен (имя set-компонента, например Icon)")
    else:
        q = _component_problem(comp.strip())
        if q:
            p.problems.append(f"icons.import.wrapper.component «{comp.strip()}»: {q}")
        else:
            p.wrapper = comp.strip()
    nm = wr.get("nameMap")
    if nm is None:
        return
    if not isinstance(nm, dict):
        p.problems.append(f"icons.import.wrapper.nameMap: ожидается объект {{имя в макете: имя в наборе}}, "
                          f"получено: {_kind(nm)}")
        return
    for k, x in nm.items():
        key = name_key(k)
        if not key:
            p.problems.append(f"icons.import.wrapper.nameMap: ключ «{k}» не содержит букв и цифр")
        elif not isinstance(x, str) or _icon_text_problem(x.strip()):
            p.problems.append(f"icons.import.wrapper.nameMap: «{k}» → {x!r}: имя в наборе — "
                              f"{_icon_text_problem(x.strip()) if isinstance(x, str) else 'строка'}")
        elif key in p.name_map:
            p.problems.append(f"icons.import.wrapper.nameMap: «{k}» повторяет «{p.name_map[key][0]}» "
                              f"(регистр и разделители не различаются)")
        else:
            p.name_map[key] = (str(k), x.strip())


def _read_rules(v: Any, p: Parsed) -> None:
    if v is None:
        return
    if not isinstance(v, list):
        p.problems.append(f"rules: ожидается список правил, получено: {_kind(v)}")
        return
    for i, raw in enumerate(v, 1):
        rule, problems = check_rule(raw, i, wrapper=bool(p.wrapper))
        p.problems.extend(problems)
        if rule is None:
            continue
        p.problems.extend(p.clashes(rule))
        p.register(rule)


def parse_text(text: str) -> Parsed:
    """Текст карты → разобранная карта; всё, что не так, — в `problems` (не бросает)."""
    p = Parsed()
    try:
        raw = Y.loads(text)
    except Y.YamlError as e:
        p.problems.append(f"YAML не разобран ({e})")
        return p
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        p.problems.append("корень файла — не словарь (ожидаются ключи library, icons, rules)")
        return p
    for k in raw:
        if k not in TOP_KEYS:
            p.problems.append(f"{k}: неизвестный ключ верхнего уровня (допустимы: {', '.join(TOP_KEYS)})")
    _read_library(raw.get("library"), p)
    _read_icons(raw.get("icons"), p)
    _read_rules(raw.get("rules"), p)
    return p


# ── файл ─────────────────────────────────────────────────────────────────────

class MapFile:
    """Карта на диске: путь, текст как есть (BOM и переводы строк сохраняются), разбор."""

    def __init__(self, root: Path, path: Path, rel: str) -> None:
        self.root, self.path, self.rel = root, path, rel
        self.exists = False
        self.text = ""
        self.bom = False
        self.parsed: Optional[Parsed] = None

    def require_valid(self) -> Parsed:
        assert self.parsed is not None
        if self.parsed.problems:
            raise MapError(_problems_message(self.rel, self.parsed.problems))
        return self.parsed


def _problems_message(rel: str, problems: List[str]) -> str:
    body = "; ".join(f"{i}) {t}" for i, t in enumerate(problems[:8], 1))
    more = f"; …и ещё {len(problems) - 8}" if len(problems) > 8 else ""
    return (f"карта {rel} некорректна ({len(problems)}): {body}{more}. Файл правит человек — daiver_map_append "
            f"чужие правила не чинит; пока карта не исправлена, она не применяется: скажи об этом пользователю "
            f"и не подставляй правило «по догадке»")


def _map_path(root: Path) -> Tuple[Path, str]:
    """Файл карты: `.daiver/` + library.mapFile конфига; вне `.daiver/` карта лежать не может."""
    rel = C.load(root)["config"]["library"]["mapFile"]
    p = Path(rel)
    if (p.is_absolute() or ".." in p.parts or "\\" in rel or rel.startswith("~")
            or re.match(r"^[A-Za-z]:", rel)):
        raise MapError(f"library.mapFile «{rel}»: только относительный путь внутри .daiver/, без «..», «~» "
                       f"и обратных слэшей")
    base = (root / C.CONFIG_REL.parent).resolve()
    full = (base / p).resolve()
    try:
        full.relative_to(base)
    except ValueError:
        raise MapError(f"library.mapFile «{rel}»: карта обязана лежать внутри .daiver/")
    shown = Path(os.path.normpath((C.CONFIG_REL.parent / p).as_posix())).as_posix()
    return full, shown


def load(root: Any) -> MapFile:
    """Карту с диска. Нет файла — `exists=False`; нечитаемый файл — MapError; проблемы содержимого — в parsed."""
    root = Path(root)
    path, rel = _map_path(root)
    mf = MapFile(root, path, rel)
    if not path.exists():
        return mf
    if not path.is_file():
        raise MapError(f"{rel}: это не файл")
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise MapError(f"{rel}: не читается ({e})")
    mf.bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        mf.text = (raw[3:] if mf.bom else raw).decode("utf-8")
    except UnicodeDecodeError:
        raise MapError(f"{rel}: файл не в UTF-8")
    mf.exists = True
    mf.parsed = parse_text(mf.text)
    return mf


# ── lookup ───────────────────────────────────────────────────────────────────

def _name_arg(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MapError("figmaName: непустая строка — имя компонента из канона (component.figmaName)")
    s = value.strip()
    if len(s) > MAX_NAME or _CTRL.search(s):
        raise MapError(f"figmaName: не длиннее {MAX_NAME} символов и без управляющих символов")
    return s


def _variants_arg(v: Any) -> None:
    if v is None:
        return
    if not isinstance(v, dict):
        raise MapError("variants: объект {ключ: значение} — варианты узла из канона (component.variants)")
    if len(v) > MAX_VARIANTS:
        raise MapError(f"variants: больше {MAX_VARIANTS} вариантов")
    for k, x in v.items():
        if not isinstance(k, str) or not k.strip() or isinstance(x, (dict, list)) or x is None \
                or (isinstance(x, str) and len(x) > MAX_ICON):
            raise MapError("variants: ключ — непустая строка, значение — короткая строка, число или true/false")


def lookup(root: Any, figma_name: Any, variants: Any = None) -> Dict[str, Any]:
    """Правило карты для имени: `{hit: true, outcome, component, props, import, rule, confidence: "high"}`
    либо `{hit: false}`. Битая карта — MapError (она не применяется), нет файла — промах с пояснением."""
    name = _name_arg(figma_name)
    _variants_arg(variants)
    mf = load(root)
    if not mf.exists:
        return {"hit": False, "note": f"карты нет ({mf.rel}) — её создаёт daiver_init; решай по библиотеке "
                                      f"(ds_lookup / storybook-mcp)"}
    res = mf.require_valid().find(name)
    if not res["hit"]:
        res["note"] = "правила в карте нет — решай по библиотеке (ds_lookup / storybook-mcp)"
    return res


# ── append ───────────────────────────────────────────────────────────────────

def _hooks() -> Tuple[Any, Any]:
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    import forge_events as FE  # noqa: WPS433 — журнал согласий forge (hooks/ рядом со skills/)
    import _project as P       # noqa: WPS433
    return FE, P


def _split_eol(line: str) -> Tuple[str, str]:
    for eol in ("\r\n", "\n", "\r"):
        if line.endswith(eol):
            return line[:-len(eol)], eol
    return line, ""


def _indent_unit(lines: List[str]) -> int:
    for ln in lines:
        body = _split_eol(ln)[0]
        s = body.strip()
        if s and not s.startswith("#"):
            ind = len(body) - len(body.lstrip(" "))
            if ind > 0:
                return ind if ind <= 8 else 2
    return 2


def _scalar(v: Any) -> str:
    if isinstance(v, str) and any(c in _FORCE_QUOTE for c in v):
        return Y._quote(v)
    return Y.dump_scalar(v)


def _render_rule(rule: Rule, d: int, gap: int, unit: int) -> List[str]:
    """Строки правила в стиле списка файла: тире на отступе d, ключи — под первым ключом (флоу — как в спеке)."""
    kc = d + 1 + gap
    lines: List[str] = []

    def put(text: str, extra: int = 0) -> None:
        lines.append((" " * d + "-" + " " * gap if not lines else " " * (kc + extra)) + text)

    m = rule.match
    if isinstance(m, list):
        put("match: [" + ", ".join(_scalar(x) for x in m) + "]")
    elif rule.kind == "regex":
        put("match: " + Y._quote(m))
    else:
        put("match: " + _scalar(m))
    put("outcome: " + rule.outcome)
    if rule.component is not None:
        put("component: " + _scalar(rule.component))
    if rule.icon is not None:
        put("icon: " + _scalar(rule.icon))
    if rule.props:
        put("props: { " + ", ".join(f"{Y._dump_key(k)}: {_scalar(v)}" for k, v in rule.props.items()) + " }")
    if rule.imp:
        put("import:")
        for k in IMPORT_KEYS:
            if k in rule.imp:
                put(f"{k}: {_scalar(rule.imp[k])}", 2)
    return lines


def _splice(text: str, rule: Rule) -> Tuple[str, List[str]]:
    """Вставить правило ТЕКСТОМ в конец блока `rules:`: всё остальное в файле остаётся как было."""
    crlf = text.count("\r\n")
    nl = "\r\n" if crlf > text.count("\n") - crlf else "\n"
    lines = text.splitlines(keepends=True)
    unit = _indent_unit(lines)
    start: Optional[int] = None
    for i, ln in enumerate(lines):
        if _RULES_LINE.match(_split_eol(ln)[0]):
            start = i
            break
    if start is None:                                   # блока rules ещё нет — в конец файла
        tail = list(lines)
        if tail and not _split_eol(tail[-1])[1]:
            tail[-1] += nl
        sep = [""] if tail and _split_eol(tail[-1])[0].strip() else []
        new = ["rules:"] + _render_rule(rule, unit, 1, unit)
        return "".join(tail) + "".join(x + nl for x in sep + new), new
    body, eol = _split_eol(lines[start])
    code = Y.strip_comment(body)
    value = _RULES_LINE.match(code).group(1).strip()
    if value in ("[]", "null", "~", "Null", "NULL"):    # `rules: []` из шаблона init → блок; комментарий остаётся
        lines[start] = "rules:" + body[len(code):] + eol
    elif value:
        raise MapError("rules записан не блоком списка (например, потоком в одной строке) — append такой файл не "
                       "переписывает: приведите rules к блочному списку (это правка человека)")
    last = start
    for j in range(start + 1, len(lines)):
        b = _split_eol(lines[j])[0]
        s = b.strip()
        if not s or s.startswith("#"):
            continue
        if len(b) - len(b.lstrip(" ")) == 0 and not (s == "-" or s.startswith("- ")):
            break
        last = j
    d, gap = unit, 1
    for j in range(start + 1, last + 1):
        m = re.match(r"^( *)-( +)\S", _split_eol(lines[j])[0])
        if m:
            d, gap = len(m.group(1)), len(m.group(2))
            break
    if not _split_eol(lines[last])[1]:
        lines[last] += nl
    new = _render_rule(rule, d, gap, unit)
    return "".join(lines[:last + 1] + [x + nl for x in new] + lines[last + 1:]), new


def _verify(old: Parsed, new_text: str, rule: Rule) -> None:
    """Результат вставки разбирается заново: прежнее не изменилось, добавлено ровно принятое правило."""
    new = parse_text(new_text)
    if new.problems:
        raise MapError("внутренняя ошибка вставки: результат не проходит проверку ("
                       + "; ".join(new.problems[:3]) + ") — файл не тронут")
    if (len(new.rules) != len(old.rules) + 1 or new.head() != old.head()
            or [r.as_dict() for r in new.rules[:-1]] != [r.as_dict() for r in old.rules]
            or new.rules[-1].as_dict() != rule.as_dict()):
        raise MapError("внутренняя ошибка вставки: результат отличается от принятого правила или меняет прежние — "
                       "файл не тронут")


class _Prepared:
    __slots__ = ("mf", "rule", "inserted", "new_text")

    def __init__(self, mf: MapFile, rule: Rule, inserted: List[str], new_text: str) -> None:
        self.mf, self.rule, self.inserted, self.new_text = mf, rule, inserted, new_text


def _prepare(root: Path, rule_raw: Any) -> _Prepared:
    mf = load(root)
    if not mf.exists:
        raise MapError(f"карты нет ({mf.rel}) — её создаёт daiver_init; append файл не создаёт")
    parsed = mf.require_valid()
    rule, problems = check_rule(rule_raw, 0, wrapper=bool(parsed.wrapper))
    if rule is not None:
        problems = problems + parsed.clashes(rule)
    if problems:
        body = "; ".join(f"{i}) {t}" for i, t in enumerate(problems, 1))
        raise MapError(f"правило не принято ({len(problems)}): {body}. Файл не тронут, согласие не потрачено")
    rule.index = len(parsed.rules) + 1
    new_text, inserted = _splice(mf.text, rule)
    _verify(parsed, new_text, rule)
    return _Prepared(mf, rule, inserted, new_text)


def _no_consent(root: Path, key: str) -> str:
    script = (HOOKS.parent / "skills" / "pipeline-state" / "scripts" / "record_approval.py").as_posix()
    return (f"нет согласия пользователя: approval-маркера «{key}» нет либо он уже потрачен (одно «да» — одно "
            f"правило). Порядок: (1) покажи пользователю правило (daiver_map_append с dryRun:true печатает его "
            f"YAML) и спроси прямо; (2) ТОЛЬКО после явного «да»: python3 {script} --project {Path(root).as_posix()} "
            f"--key {key} --approved-by user --reason \"<какое правило добавлено>\" "
            f"--evidence \"<ответ пользователя дословно>\"; (3) повтори вызов. Файл карты не тронут")


def _write_atomic(mf: MapFile, new_text: str) -> None:
    data = (("﻿" if mf.bom else "") + new_text).encode("utf-8")
    tmp = mf.path.with_name(mf.path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        try:
            shutil.copymode(str(mf.path), str(tmp))
        except OSError:
            pass
        os.replace(str(tmp), str(mf.path))
    except OSError as e:
        raise MapError(f"запись {mf.rel} не удалась ({e}); файл не тронут, согласие не потрачено")
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def append(root: Any, track: Any, rule: Any, dry_run: bool = False) -> Dict[str, Any]:
    """Дописать ОДНО правило в карту. dry_run — проверить и показать YAML, ничего не пишет и согласия не требует."""
    try:
        track = TR.check_track_name(track)
    except TR.TrackError as e:
        raise MapError(str(e))
    root = Path(root)
    FE, P = _hooks()
    key = consent_key(track)
    if dry_run:
        prep = _prepare(root, rule)
        return {"ok": True, "dryRun": True, "file": prep.mf.rel, "index": prep.rule.index,
                "rule": prep.rule.as_dict(), "yaml": "\n".join(prep.inserted),
                "consent": {"key": key, "granted": FE.approval(root, key) is not None},
                "next": "покажи пользователю yaml и спроси прямо; только после явного «да» запиши согласие "
                        f"({key}, его ответ дословно в --evidence) и повтори вызов без dryRun"}
    with P.exclusive_lock(P.ground_dir(root) / "component-map.lock"):
        prep = _prepare(root, rule)                    # свежие данные под замком
        if FE.approval(root, key) is None:
            raise MapError(_no_consent(root, key))
        _write_atomic(prep.mf, prep.new_text)
        FE.revoke_approval(root, key, reason=f"согласие потрачено: daiver_map_append "
                                             f"{_short_match(prep.rule.match)}")
    return {"ok": True, "file": prep.mf.rel, "index": prep.rule.index, "rule": prep.rule.as_dict(),
            "yaml": "\n".join(prep.inserted),
            "consent": f"«{key}» потрачено: следующее правило потребует нового «да»"}
