#!/usr/bin/env python3
"""_shell_targets.py — цели записи и удаления в shell-команде (общий разбор хуков).

Один разбор на все хуки, которые решают по ЦЕЛИ записи: state-write-guard (control-plane),
tdd-guard и eval-guard (src/main до RED/EDD), sod-enforcer (пути роли). Каждый из них раньше
смотрел только на Write/Edit, и `cat > src/main/java/Foo.java <<EOF` на фазе RED проходил
все хуки (боевой прогон v0.4.6, L-17b) — обход гейта одной shell-командой.

Best-effort по природе (в shell тысяча способов записать файл), но гард отличает «файл, в
который пишут» от «файл, который читают/исполняют»: путь исполняемого скрипта, аргументы-входы
и `2>&1` целями не являются. Обёртки (`sudo`, `env`, `timeout`, `nice`, `VAR=x`) снимаются,
`sh -c '<скрипт>'` и `eval` раскрываются на один уровень.

Модуль намеренно stdlib-only и без синтаксиса новее 3.9: его грузит state-write-guard, у
которого нет других форж-зависимостей.
"""
from __future__ import annotations

import posixpath
import re
import shlex

_CMD_SEP_RE = re.compile(r"\|\||&&|[;|&\n]")
_REDIR_TOK_RE = re.compile(r"^[0-9]*&?(>>?|<>)(.*)$")
# назначение — последний аргумент. ln/link: `ln -sf /dev/null …/manifest.json` подменял
# манифест пустышкой без единого «пишущего» глагола (боевой прогон v0.4.6, E-10).
_COPY_CMDS = ("cp", "mv", "install", "rsync", "ln", "link")
# пишут во все свои файлы всегда. mkfifo/mknod: FIFO на месте policy.json/manifest.json вешал
# КАЖДЫЙ вызов gate-guard (читатели открывают файл блокирующим open) — таймаут хука, и
# enforcement снят на всю сессию (E-07).
_MULTI_TARGET_CMDS = ("tee", "truncate", "mkfifo", "mknod")
# Удаление/обнуление/перенос/«подкрутка mtime» — отдельный класс целей, разбирается
# unlink_targets. Это самый дешёвый способ снять enforcement: без manifest.json фазовая
# машина не резолвится и ВСЕ хуки становятся noop, а `touch` чужого манифеста перехватывает
# «активную фичу» (её резолвят по самому свежему mtime). У `mv` сюда идёт ИСТОЧНИК —
# назначение остаётся обычной записью через _COPY_CMDS.
_UNLINK_CMDS = ("rm", "unlink", "shred", "srm", "touch", "mv")
_INPLACE_CMDS = ("sed", "perl", "ruby")    # пишут в файл ТОЛЬКО с -i (иначе поток на stdout)
# Строчные редакторы пишут в свой файл-аргумент без всякого -i (E-11).
_EDITOR_CMDS = ("ed", "ex")
# Загрузчики пишут в файл из опции: `curl -o f`, `wget -O f`, `openssl … -out f` (E-12).
_OUTPUT_OPTS = {"curl": ("-o", "--output"), "wget": ("-O", "--output-document"),
                "openssl": ("-out",)}
# inline-python: пишущий/удаляющий вызов в тексте команды. Есть такой — целями считаем ВСЕ
# строковые литералы команды (какой из них путь, из shell не разобрать; лучше перебдеть).
# Раньше список знал только open('w')/.write*/shutil.copy|move: `os.remove(manifest)`,
# `os.replace(tmp, policy.json)`, `shutil.copyfile`, `copytree`, `rmtree('ground')`,
# `Path(…).unlink()` снимали control-plane мимо всех хуков (E-09, L-7d).
_PY_WRITE_RE = re.compile(
    r"open\s*\([^)]*['\"]\s*,\s*['\"][awx+]|\.write(?:_text|_bytes)?\s*\(|\.touch\s*\("
    r"|\bshutil\.\w+\s*\(|\bos\.(?:remove|unlink|rmdir|removedirs|rename|replace|renames"
    r"|truncate|mkfifo|mknod|symlink|link)\s*\(|\.(?:unlink|rmdir|rename|symlink_to"
    r"|hardlink_to|link_to)\s*\(|\b(?:rmtree|copyfile|copytree|copy2)\s*\("
    # node -e / deno eval: fs.writeFileSync, appendFile, copyFile, rename, rm, unlink, createWriteStream
    r"|\b(?:writeFile|appendFile|copyFile|rename|rm|rmdir|unlink|truncate|symlink|cp)(?:Sync)?\s*\("
    r"|\bcreateWriteStream\s*\("
)
# Программа awk пишет в файл своим `print … > "файл"` — без редиректа оболочки (аудит 2026-11-07).
_AWK_CMDS = ("awk", "gawk", "mawk", "nawk")
_AWK_REDIR_RE = re.compile(r'>>?\s*"([^"]+)"')
# Патч пишет в файлы, названные в нём самом: `+++ b/<путь>` (и `--- a/<путь>` для удаления).
# `git apply`/`patch` — естественный для модели способ применить дифф, и до 2026-11-07 он писал в
# src/main мимо всех гейтов на фазе RED.
_PATCH_PATH_RE = re.compile(r"^(?:\+\+\+|---)\s+(?:[ab]/)?([^\t\n]+?)\s*(?:\t.*)?$", re.M)

# Префиксы, за которыми идёт настоящая команда. Гард смотрел на argv[0] сегмента, и
# `sudo tee ground/policy.json`, `echo x | sudo tee …`, `sudo rm ground/policy.json`
# проходили все хуки (боевой прогон v0.4.6, track C F-1). Значения их опций пропускаем.
_WRAPPER_OPTS = {
    "sudo": ("-u", "-g", "-p", "-C", "-U", "-r", "-t", "-T", "-h", "-D"),
    "doas": ("-u", "-C"), "env": ("-u", "-C", "-S"), "nohup": (), "command": (),
    "exec": ("-a",), "nice": ("-n",), "ionice": ("-c", "-n", "-p"),
    "timeout": ("-s", "-k", "--signal", "--kill-after"), "stdbuf": ("-i", "-o", "-e"),
    "time": ("-f", "-o"),
}
_ASSIGN_RE = re.compile(r"^[A-Za-z_]\w*=")
_SHELLS = ("sh", "bash", "zsh", "dash", "ksh")


def _tokens(seg: str) -> list[str]:
    try:
        return shlex.split(seg, posix=True)
    except ValueError:  # незакрытая кавычка — грубая токенизация
        return re.findall(r"[^\s'\"]+", seg)


def _strip_wrappers(toks: list[str]) -> list[str]:
    """argv без ведущих `VAR=x`, `sudo -u u`, `env -i`, `timeout 10`, `nice -n 5`, …"""
    while toks:
        if _ASSIGN_RE.match(toks[0]):
            toks = toks[1:]
            continue
        name = posixpath.basename(toks[0])
        if name not in _WRAPPER_OPTS:
            return toks
        i = 1
        while i < len(toks):
            t = toks[i]
            if t == "--":
                i += 1
                break
            if t.startswith("-") and len(t) > 1:
                i += 2 if t in _WRAPPER_OPTS[name] else 1
            elif name == "env" and _ASSIGN_RE.match(t):
                i += 1
            elif name == "timeout" and t[:1].isdigit():
                i += 1                     # DURATION, дальше — команда
                break
            else:
                break
        toks = toks[i:]
    return toks


def _segments(cmd: str, depth: int = 0):
    """argv сегментов команды: обёртки сняты, `sh -c '<скрипт>'` и `eval` раскрыты на один
    уровень. Без раскрытия `sh -c "echo x > ground/policy.json"` был одним токеном-строкой,
    и цель редиректа внутри не видел никто (track C K08)."""
    for seg in _CMD_SEP_RE.split(cmd.replace(">|", ">")):
        if not seg.strip():
            continue
        toks = _strip_wrappers(_tokens(seg))
        if not toks:
            continue
        yield toks
        if depth:
            continue
        name = posixpath.basename(toks[0])
        if name in _SHELLS and "-c" in toks[1:]:
            i = toks.index("-c", 1)
            if i + 1 < len(toks):
                yield from _segments(toks[i + 1], depth + 1)
        elif name == "eval" and len(toks) > 1:
            yield from _segments(" ".join(toks[1:]), depth + 1)


def _option_targets(name: str, argv: list[str]) -> list[str]:
    """Цель из опции вывода: `-o f`, `--output f`, `--output=f`, кластер `-sSo f`."""
    opts = _OUTPUT_OPTS.get(name)
    if not opts:
        return []
    out = []
    for i, a in enumerate(argv[1:], 1):
        nxt = argv[i + 1] if i + 1 < len(argv) else ""
        for o in opts:
            if a == o or (len(o) == 2 and re.fullmatch(r"-[A-Za-z]+", a) and a.endswith(o[1])):
                if nxt:
                    out.append(nxt)
            elif a.startswith(o + "="):
                out.append(a[len(o) + 1:])
    return out


def _py_literals(cmd: str) -> list[str]:
    """Строковые литералы команды, если в ней пишущий/удаляющий python-вызов. Одинарные и
    двойные — отдельно: в `python3 -c "shutil.rmtree('ground')"` внешний литерал — весь код,
    и внутренний 'ground' иначе не виден (CP-паттерн каталога якорится на конец пути)."""
    if not _PY_WRITE_RE.search(cmd):
        return []
    return re.findall(r"'([^']+)'", cmd) + re.findall(r'"([^"]+)"', cmd)


def _patch_paths(text: str) -> list[str]:
    return [m for m in _PATCH_PATH_RE.findall(text or "") if m and m != "/dev/null"]


def _read_patch(path: str, cwd: str) -> str:
    import os
    try:
        p = path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path)
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read(4 * 1024 * 1024)
    except (OSError, ValueError):
        return ""


def _git_targets(argv: list[str], cmd: str, cwd: str) -> list[str]:
    """Пути, которые пишет git-подкоманда: apply/am — файлы патча; checkout/restore — pathspec."""
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i >= len(argv):
        return []
    sub, args = argv[i], argv[i + 1:]
    if sub in ("apply", "am"):
        if "--check" in args or "--stat" in args or "--numstat" in args:
            return []
        out = _patch_paths(cmd)                        # патч прямо в команде (heredoc)
        for a in args:
            if not a.startswith("-"):
                out += _patch_paths(_read_patch(a, cwd))
        return out
    if sub in ("checkout", "restore"):
        if sub == "restore" and "--staged" in args and not ({"--worktree", "-W"} & set(args)):
            return []
        if "--" in args:
            return [a for a in args[args.index("--") + 1:] if a]
        pos, skip = [], False
        for a in args:
            if skip:
                skip = False
                continue
            if a in ("-s", "--source", "-b", "-B", "--orphan", "--conflict", "--pathspec-from-file"):
                skip = True
                continue
            if not a.startswith("-"):
                pos.append(a)
        return pos if sub == "restore" else pos[1:]   # checkout <ветка> [пути…]
    return []


def write_targets(cmd: str, cwd: str = "") -> list[str]:
    """Пути, в которые команда ПИШЕТ (best-effort). Путь исполняемого скрипта, аргументы-входы
    и `2>&1` целями не считаются. `cwd` — каталог сессии: от него читается файл патча."""
    out: list[str] = _py_literals(cmd)
    for toks in _segments(cmd):
        name0 = posixpath.basename(toks[0]) if toks else ""
        if name0 == "git":
            out += _git_targets(toks, cmd, cwd)
        elif name0 == "patch" and "--dry-run" not in toks:
            src = ""
            for i, t in enumerate(toks):
                if t in ("-i", "--input") and i + 1 < len(toks):
                    src = toks[i + 1]
                elif t.startswith("--input="):
                    src = t.split("=", 1)[1]
                elif t == "<" and i + 1 < len(toks):
                    src = toks[i + 1]
                elif t.startswith("<") and len(t) > 1 and not t.startswith("<<"):
                    src = t[1:]
            out += _patch_paths(_read_patch(src, cwd) if src else cmd)
        elif name0 in _AWK_CMDS:
            out += _AWK_REDIR_RE.findall(" ".join(toks[1:]))
        # редиректы: `> f`, `>>f`, `1> f`, `&> f` (но не `2>&1` и не fd-номер)
        redirect_idx = set()
        for i, t in enumerate(toks):
            m = _REDIR_TOK_RE.match(t)
            if not m:
                continue
            redirect_idx.add(i)
            rest = m.group(2)
            if not rest and i + 1 < len(toks):
                rest = toks[i + 1]
                # цель редиректа — НЕ аргумент команды: иначе у `cp src <cp-файл> > /dev/null`
                # последним аргументом cp оказывался /dev/null, и настоящее назначение копии
                # (control-plane) не проверялось вовсе — дыра в гарде.
                redirect_idx.add(i + 1)
            if rest and not rest.startswith("&") and not rest.isdigit():
                out.append(rest)
        argv = [t for i, t in enumerate(toks) if i not in redirect_idx]
        if not argv:
            continue
        name = posixpath.basename(argv[0])
        files = [a for a in argv[1:] if not a.startswith("-")]
        if name in _COPY_CMDS and files:
            out.append(files[-1])          # назначение copy/move/ln — последний аргумент
        elif name in _MULTI_TARGET_CMDS or name in _EDITOR_CMDS:
            out += files
        elif name in _INPLACE_CMDS and any(a.startswith("-i") for a in argv[1:]):
            out += files
        out += _option_targets(name, argv)
        for a in argv:
            if a.startswith("of="):        # dd of=<file>
                out.append(a[3:])
    return [t for t in out if t]


def unlink_targets(cmd: str) -> list[str]:
    """Пути, которые команда УДАЛЯЕТ/обнуляет/уносит (rm, unlink, shred, srm, touch,
    mv-источник, удаляющие вызовы inline-python).

    Отдельно от _write_targets, потому что проверяются по более узкому множеству: живой
    control-plane. Архив завершённых прогонов (ground/archive/) под это не попадает —
    его уборка легитимна, гейты из него ничего не читают."""
    out: list[str] = _py_literals(cmd)     # rmtree('ground') — каталог, CP-паттерн его не видит
    for toks in _segments(cmd):
        name = posixpath.basename(toks[0])
        if name not in _UNLINK_CMDS:
            continue
        files = [a for a in toks[1:] if not a.startswith("-")]
        if name == "mv":
            files = files[:-1]             # последний операнд mv — назначение, не источник
        out += files
    return [t for t in out if t]


_RM_CMDS = ("rm", "unlink", "shred", "srm")


def rm_targets(cmd: str) -> list[str]:
    """Пути, которые команда именно УДАЛЯЕТ (rm/unlink/shred/srm) — без mv и touch."""
    out: list[str] = []
    for toks in _segments(cmd):
        if posixpath.basename(toks[0]) in _RM_CMDS:
            out += [a for a in toks[1:] if a and not a.startswith("-")]
    return out
