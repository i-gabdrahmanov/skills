"""pull.py — daiver_pixso_pull: экспорт кадра из файла → slim + сырой рядом + регистрация в треке.

Pixso MCP принадлежит пользователю (токен в его клиенте), сервер daiver другие MCP вызывать не
может — поэтому экспорт приходит ФАЙЛОМ: агент просит Pixso MCP сохранить DSL кадра в репо (или
человек экспортирует сам) и передаёт путь. Сырой DSL в контекст модели не попадает ни на каком шаге
(INV-B3): инструмент читает файл сам и отвечает числами и предупреждениями.

Что делает вызов: проверяет, что это Pixso DSL (по структуре — сжатие тут не критерий: кадр,
выгруженный с одним компонентом, и так без библиотеки); худеет (slim.py); пишет
`visual-requirements.pixso.json` (slim) и `visual-requirements.pixso.raw.json` (минифицированный
сырой — свидетельство на случай исправления slim; сбой его записи — предупреждение, не отказ);
регистрирует кадр в track.yaml с хешем требования. Повторный pull того же экспорта ничего не меняет.

Второй кадр трека (`frame=<имя>`) заводится тем же вызовом и переносит семью первого в `frames/<имя>/` (`layout.migrate`, спека §4.2): в ответе `layout` и строка «LAYOUT — …»;
два кадра трека на одном frameUrl — ошибка именования, до любых изменений на диске.

Ревизия (задача 6.3, INV-B15, G3). СОБРАННЫЙ кадр (все секции build-state `done` — `status.frame_built`, то же правило, что у лестницы) новый экспорт
НЕ перезаписывает: семья кадра целиком уходит в `versions/<n>/` (`baseline.retire`), манифест получает запись версии и `baseline`, растр уходит с
версией, подпись плана снимается сама (в lock входит хеш требования, а плана кадра больше нет). Ответ — `mode: "revision"`, `version` и
схожесть с собранной версией с числом регионов (канон новой версии считается в памяти; не вышло — предупреждение, регионы покажет
`daiver_frame_baseline` после `daiver_normalize`) и строка «REVISION — …». Недостроенный кадр (есть секции не `done`) перезаписывается как раньше:
у ревизии база остаётся прежней, у первой сборки сверка build-state пометит stale секции, чей хеш сдвинулся. Кадр-состояние (`stateOf`) не
версионируется никогда: его сборка — дельта экрана. Тот же экспорт поверх собранного (тот же хеш) ничего не меняет — ревизии без изменения
нет (отклонение от буквы INV-G3, см. README). Повреждённый build-state — отказ: собран ли кадр, не понять.
`mode` в ответе: new | unchanged | overwrite | revision.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import baseline as BL
from .. import config as CFG
from .. import layout as LAY
from .. import states as STS
from .. import status as ST
from .. import track as TR
from ..ingest import pixso as PX
from . import dsl as D
from . import slim as S

URL_RE = re.compile(r"^https?://\S+$")
MAX_SOURCE_BYTES = 256 * 1024 * 1024
ALREADY_SLIM_RATIO = 1.2
STALE_ARTIFACTS = ("plan.json", "plan.md", "resolutions.json", "plan.lock.json")


class PullError(ValueError):
    """Вход не годится или кадр нельзя перезаписать — с причиной и тем, что делать."""


def _rel(root: Path, p: Path) -> str:
    return p.resolve().relative_to(Path(root).resolve()).as_posix()


def _read_source(path: Path) -> Any:
    if not path.is_file():
        raise PullError(f"файла {path.name} нет: сохраните DSL кадра в репо (Pixso MCP или экспорт "
                        f"пользователя) и передайте путь в dslPath")
    size = path.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise PullError(f"{path.name}: {size // (1 << 20)} МБ — больше {MAX_SOURCE_BYTES // (1 << 20)} МБ, "
                        f"это не экспорт одного кадра")
    try:
        return json.loads(path.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PullError(f"{path.name}: не JSON ({e}); ожидается DSL-экспорт Pixso (файл целиком)")


def _drop_new_frame(directory: Path) -> None:
    """Сбой переезда: файлы только что записанного нового кадра убираются (манифест его не знает), пустой `frames/` — тоже."""
    shutil.rmtree(str(directory), ignore_errors=True)
    try:
        directory.parent.rmdir()
    except OSError:
        pass


def pull(root: Path, track: str, dsl_path: Path, frame_url: Any, frame: Optional[str] = None,
         now: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root)
    try:
        TR.check_track_name(track)
        frame_name = TR.check_frame_name(frame or "main")
    except TR.TrackError as e:
        raise PullError(str(e))
    if not (isinstance(frame_url, str) and URL_RE.match(frame_url)):
        raise PullError("frameUrl обязателен — ссылка на кадр в Pixso (https://…); от неё строятся ссылки "
                        "на узлы в отчётах. URL не выдумывается: его даёт пользователь")
    doc = _read_source(Path(dsl_path))
    bytes_in = Path(dsl_path).stat().st_size
    try:
        slim_doc, report = S.slim(doc)
    except D.DslError as e:
        raise PullError(f"{e}. Это не Pixso-экспорт (или не тот формат: плоский список узлов без "
                        f"pixTreeDslNodes пока не поддержан; Figma-экспорты — отдельная задача)")

    try:
        manifest = TR.load(root, track)
    except TR.TrackError as e:
        raise PullError(str(e))
    created = manifest is None
    if created:
        manifest = TR.new_manifest(track, created=now)
    existing = next((f for f in manifest["frames"] if f["name"] == frame_name), None)
    migrate_from = None
    if existing is None and manifest["frames"]:
        same = next((f["name"] for f in manifest["frames"] if f.get("frameUrl") == frame_url), None)
        if same is not None:
            raise PullError(f"кадр «{same}» этого трека уже смотрит на тот же frameUrl: два кадра одной фичи на одном URL — ошибка именования "
                            f"(тот же кадр под другим именем, или у «{frame_name}» другая ссылка); ничего не изменено")
        if len(manifest["frames"]) == 1 and manifest["frames"][0].get("dir") == ".":
            migrate_from = manifest["frames"][0]
            try:
                LAY.check_target(root, track, migrate_from)
            except LAY.LayoutError as e:
                raise PullError(str(e))
    slim_text = S.dumps(slim_doc)
    sha = hashlib.sha256(slim_text.encode("utf-8")).hexdigest()
    prev_req = (existing or {}).get("requirement") or {}
    unchanged = (existing is not None and prev_req.get("sha256") == sha
                 and existing.get("frameUrl") == frame_url
                 and TR.frame_paths(root, manifest, frame_name)["requirement"].is_file())
    revision = False
    if existing is not None and not unchanged and not existing.get("stateOf"):
        bs_path = TR.frame_paths(root, manifest, frame_name)["buildState"]
        built, why = ST.frame_built(bs_path)
        if why:
            raise PullError(f"кадр «{frame_name}»: {bs_path.name} не читается ({why}) — собран ли кадр, не понять, а собранный новый экспорт "
                            f"не перезаписывает. Верни файл из git или удали его (сборка начнётся с первой секции) и повтори pull; ничего не изменено")
        revision = built and bool(prev_req)

    warnings: List[str] = list(report["warnings"])
    notes: List[str] = []
    invalidated: List[str] = []
    stale: List[str] = []
    migration: Optional[LAY.Migration] = None
    layout_text: Optional[str] = None
    retired: Optional[BL.Retirement] = None
    if revision:
        try:
            retired = BL.retire(root, manifest, existing, now or TR.now_iso())
        except BL.BaselineError as e:
            raise PullError(str(e))
        BL.record(existing, retired)
        notes.append(retired.message(frame_name))
    # путь кадра считается после регистрации в манифесте (раскладка dir — его дело)
    scratch = TR.upsert_frame(manifest, frame_name, frameUrl=frame_url) if existing is None else existing
    paths = TR.frame_paths(root, manifest, frame_name)
    req_path, raw_path = paths["requirement"], paths["raw"]
    req_path.parent.mkdir(parents=True, exist_ok=True)
    if not unchanged:
        try:
            req_path.write_text(slim_text, encoding="utf-8")
        except OSError as e:
            if retired is not None:
                retired.rollback()
            raise PullError(f"требование не записано ({e.strerror or e}); прежняя версия возвращена на место")
    raw_saved = raw_path.is_file() and unchanged
    raw_bytes = raw_path.stat().st_size if raw_saved else 0
    if not raw_saved:
        try:
            raw_text = S.dumps(doc)
            raw_path.write_text(raw_text, encoding="utf-8")
            raw_saved, raw_bytes = True, len(raw_text.encode("utf-8"))
        except OSError as e:
            warnings.append(f"сырой экспорт рядом не сохранён ({e}) — pull продолжен; если понадобится "
                            f"исправлять slim, экспорт придётся выгрузить заново")

    if not unchanged:
        if prev_req and prev_req.get("sha256") != sha and retired is None:
            canon = paths["canonical"]
            if canon.is_file():
                canon.unlink()
                invalidated.append(canon.name)
            stale = [n for n in STALE_ARTIFACTS if (paths["dir"] / n).is_file()]
            if stale:
                warnings.append("требование изменилось, а артефакты поверх него остались: " + ", ".join(stale)
                                + " — они устарели (план придётся пересмотреть и подписать заново)")
        TR.upsert_frame(manifest, frame_name, frameUrl=frame_url, requirement={
            "kind": "pixso", "path": req_path.relative_to(paths["dir"]).as_posix(), "sha256": sha,
            "pulledAt": now or TR.now_iso()})
        if migrate_from is not None:
            try:
                migration = LAY.migrate(root, track, migrate_from)
            except LAY.LayoutError as e:
                _drop_new_frame(paths["dir"])
                raise PullError(str(e))
        try:
            TR.save(root, manifest)
        except (TR.TrackError, OSError) as e:
            if migration is not None:
                migration.rollback()
                _drop_new_frame(paths["dir"])
            if retired is not None:
                for p in (req_path, raw_path):
                    if p.is_file():
                        p.unlink()
                retired.rollback()
            raise PullError(str(e))
        if migration is not None:
            LAY.rewrite_paths(root, track, migration)
            LAY.rebuild_report(root, manifest, migration)
            layout_text = migration.message(track, frame_name)
            notes.append(layout_text)
            warnings.extend(migration.warnings)

    for other in TR.list_tracks(root):
        if other == track:
            continue
        om, _ = TR.load_safe(root, other)
        for f in (om or {}).get("frames") or []:
            if f.get("frameUrl") == frame_url:
                warnings.append(f"этот кадр уже тянут в треке «{other}» (кадр «{f['name']}»): два трека на "
                                f"один URL — ошибка наименования, одна фича должна жить в одном треке")

    bytes_out = len(slim_text.encode("utf-8"))
    ratio = round(bytes_in / bytes_out, 2) if bytes_out else None
    if ratio is not None and ratio < ALREADY_SLIM_RATIO and not report["diagnostics"]:
        notes.append(f"сжатие ×{ratio}: экспорт уже без лишней библиотеки (кадр выгружен вместе с нужными "
                     f"компонентами) — для маленького кадра это нормально")
    stats = dict(report["stats"], bytesIn=bytes_in, bytesRaw=raw_bytes, bytesOut=bytes_out, ratio=ratio)
    out: Dict[str, Any] = {
        "track": track, "frame": frame_name, "created": created, "unchanged": unchanged,
        "dir": _rel(root, paths["dir"]),
        "requirement": {"path": _rel(root, req_path), "kind": "pixso", "sha256": sha, "bytes": bytes_out},
        "raw": {"path": _rel(root, raw_path), "saved": raw_saved, "bytes": raw_bytes},
        "ratio": ratio, "stats": stats, "diagnostics": report["diagnostics"],
        "warnings": warnings, "notes": notes, "invalidated": invalidated, "stale": stale,
    }
    if migration is not None:
        out["layout"] = {"message": layout_text, "movedFrame": migration.frame, "moved": migration.moved, "rewritten": migration.rewritten}
    out["mode"] = "new" if existing is None else ("unchanged" if unchanged else ("revision" if retired is not None else "overwrite"))
    if retired is not None:
        out["version"] = retired.n
        out["revision"] = _revision_view(root, retired, slim_doc, sha, warnings)
    return out


def _revision_view(root: Path, retired: BL.Retirement, slim_doc: Any, sha: str, warnings: List[str]) -> Dict[str, Any]:
    """Схожесть новой версии с собранной и число регионов — по канону новой версии, построенному в памяти (на диск его пишет daiver_normalize)."""
    view: Dict[str, Any] = {"version": retired.n, "moved": list(retired.moved)}
    base = retired.target / TR.CANONICAL_FILE
    try:
        from .. import canon as C
        base_env = C.read_canonical(base)
        infer = bool(CFG.load(root)["config"]["layout"]["infer"])
        new_env = PX.normalize_doc(slim_doc, source_sha256=sha, infer_layout=infer)
        res = STS.compare(base_env, new_env, f"v{retired.n}", "new", gate=False)
    except (OSError, ValueError, RecursionError, D.DslError) as e:
        warnings.append(f"схожесть с собранной версией не посчитана ({str(e)[:160]}): регионы изменения покажет daiver_frame_baseline после "
                        f"daiver_normalize")
        return view
    view["similarity"] = res["similarity"]
    view["regions"] = {k: v for k, v in res["counts"].items() if k != "shared"}
    if res.get("_mismatch"):
        warnings.append("новая версия почти целиком другая (" + "; ".join(res["_mismatch"]) + "): это всё ещё ревизия собранного экрана; если на "
                        "самом деле это другой экран — заведи его отдельным кадром")
    return view
