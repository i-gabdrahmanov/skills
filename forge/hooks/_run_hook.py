#!/usr/bin/env python3
"""_run_hook.py — запуск хука forge из extension'а только в СВОЁМ проекте.

hooks/hooks.json extension'а зовёт каждый хук так:

    <python> -X utf8 <ext>/hooks/_run_hook.py <ext>/hooks/<хук>.py

Зачем. Extension подключён к рантайму ссылкой из ~/.gigacode/extensions, а реестр ссылок — общий
на пользователя. Установщик включает extension только для своего workspace, но правила включения
рантайма покрывают не всё: `disable --scope user` пишет «выключен под $HOME», и проект ВНЕ $HOME
(другой диск, общий каталог) видит extension включённым; форк GigaCode может вести себя иначе
стокового qwen. Без этой проверки хуки чужого проекта (pii-boundary, destructive-blocker…)
резали бы работу в репо, где forge вообще не стоит. Рабочий каталог сессии рантайм передаёт
хуку и как cwd процесса, и как QWEN_PROJECT_DIR; не внутри корня проекта этого extension'а —
выход 0 молча («не моё»).

Хук исполняется в ЭТОМ же процессе (runpy, как `python <хук>.py`): тот же stdin, `__file__`,
`sys.path[0]` = hooks/, коды выхода. Сам модуль — stdlib-only и без синтаксиса новее 3.9: он
грузится раньше бандла. Сбой проверки «чужой ли проект» = считать своим (хук запускается):
ошибка здесь не должна молча снимать enforcement. Нет файла хука — exit 2 с причиной, как у
`python <нет файла>` (рантайм читает 2 как «блокировать»: громко, а не тихий пропуск).
"""
import os
import runpy
import sys
from pathlib import Path

_PROJECT_ENV = ("QWEN_PROJECT_DIR", "GIGACODE_PROJECT_DIR", "CLAUDE_PROJECT_DIR", "GEMINI_PROJECT_DIR")


def install_root(here: Path):
    """Корень проекта extension'а: <root>/.gigacode/extensions/<kind>/hooks/_run_hook.py → <root>.
    Вне такой раскладки (source-репо, старая установка) — None: проверять нечего."""
    try:
        if here.parents[1].name and here.parents[2].name == "extensions" and here.parents[3].name == ".gigacode":
            return here.parents[4]
    except IndexError:
        pass
    return None


def is_foreign(root, workdir: str) -> bool:
    if root is None or not workdir:
        return False
    r = os.path.realpath(str(root))
    w = os.path.realpath(workdir)
    if os.name == "nt":
        r, w = r.lower(), w.lower()
    return not (w == r or w.startswith(r.rstrip("\\/") + os.sep))


def session_dir() -> str:
    for k in _PROJECT_ENV:
        if os.environ.get(k):
            return os.environ[k]
    return os.getcwd()


def main(argv) -> None:
    if len(argv) < 2:
        print("[forge] _run_hook.py: не передан хук — hooks/hooks.json собран неверно; "
              "перезапусти deploy-local.sh extension'а", file=sys.stderr)
        sys.exit(2)
    hook = Path(argv[1])
    try:
        foreign = is_foreign(install_root(Path(__file__).resolve()), session_dir())
    except Exception:  # noqa: BLE001 — сбой проверки не снимает enforcement
        foreign = False
    if foreign:
        sys.exit(0)
    if not hook.is_file():
        print(f"[forge] хук не найден: {hook} — перезапусти deploy-local.sh extension'а", file=sys.stderr)
        sys.exit(2)
    sys.argv = [str(hook)] + list(argv[2:])
    runpy.run_path(str(hook), run_name="__main__")


if __name__ == "__main__":
    main(sys.argv)
