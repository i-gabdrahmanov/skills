"""Local, registry-driven configuration UI for Forge."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import math
import re
from pathlib import Path
import secrets
from threading import Lock, Thread
from time import monotonic
from urllib.parse import parse_qs, urlsplit
import webbrowser

import config
from _config_loader import MANIFEST_VERSION, find_project_root
from _project import exclusive_lock
from _util import atomic_write, backup, dig

ASSETS = Path(__file__).resolve().parent / "config_ui"
MASK = "••••••"
FILES = {"pipeline": "Проект", "policy": "Проект", "gates": "Гейты",
         "risk": "Установка", "manifest": "Фича"}
LEVELS = [f"R{level}" for level in range(6)]
IDLE_TIMEOUT_SECONDS = 15 * 60


class FieldError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


class Conflict(ValueError):
    pass


def reject_constant(value):
    raise ValueError(f"недопустимое число JSON {value}")


def finite_float(value):
    number = float(value)
    return number if math.isfinite(number) else reject_constant(value)


def read_document(path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=reject_constant, parse_float=finite_float)
        if not isinstance(data, dict):
            raise ValueError("ожидался объект JSON")
    except ValueError as exc:
        raise ValueError(f"{path}: повреждённый JSON. {exc.__class__.__name__}") from exc
    return data, sha256(raw).hexdigest()


class Editor:
    def __init__(self, project):
        self.project = project
        self.params = config.load_registry()
        self.entries = {entry["id"]: entry for entry in self.params}
        self.pending = None

    def contained(self, path):
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project):
            raise ValueError(f"Путь выходит за пределы проекта: {path}")
        if path.is_symlink():
            raise ValueError(f"Ожидался обычный файл: {path}")
        return path

    def features(self):
        base = self.project / "ground" / "statements"
        return {str(path.parent.relative_to(base)): path
                for path in sorted(base.glob("*/*/manifest.json"))
                if path.is_file() and path.resolve().is_relative_to(self.project)}

    def target(self, file_key, feature):
        if file_key == "manifest":
            path = self.features().get(feature)
            if path is None:
                raise ValueError("Выберите существующую фичу")
        else:
            path = config.resolve_file(self.project, file_key)
        return self.contained(path)

    def snapshot(self, file_key, target):
        data, revision = read_document(target)
        legacy_data = legacy_revision = None
        if file_key in {"pipeline", "policy", "manifest"} and (
                data is None or file_key == "manifest"):
            legacy_data, legacy_revision = read_document(self.contained(config._legacy_pipeline_path(self.project)))
        if file_key == "manifest" and (data is None or data.get("version") != MANIFEST_VERSION):
            raise ValueError(f"{target}: требуется manifest версии {MANIFEST_VERSION}")
        return data, legacy_data, [str(target), revision, legacy_revision]

    @staticmethod
    def effective(entry, target, data, legacy):
        if entry["file"] in {"pipeline", "policy"} and data is None:
            return config.setting_value(entry, legacy, "pipeline.json (legacy)")
        return config.setting_value(entry, data, Path(target).name, legacy)

    def catalog(self, feature):
        features = self.features()
        if feature and feature not in features:
            raise ValueError("Фича отсутствует. Обновите список")
        manifest = features.get(feature)
        documents = {}
        legacy = {}
        errors = {}
        revisions = {}
        for file_key in {entry["file"] for entry in self.params}:
            if file_key == "manifest" and manifest is None:
                continue
            try:
                target = self.target(file_key, feature)
                documents[file_key], legacy[file_key], revisions[file_key] = self.snapshot(file_key, target)
            except (ValueError, OSError) as exc:
                errors[file_key] = str(exc)
        resolver = config.phase_resolver()
        rows = []
        for entry in self.params:
            file_key = entry["file"]
            row = dict(entry, scope=FILES[file_key], error=errors.get(file_key))
            if file_key not in revisions:
                row.update(value=None, source="Выберите фичу" if not row["error"] else "Ошибка чтения",
                           editable=False)
            else:
                value, source = self.effective(entry, revisions[file_key][0],
                                               documents[file_key], legacy[file_key])
                row.update(value=MASK if entry["sensitive"] and value is not None else value,
                           source=source, target=revisions[file_key][0],
                           editable=documents[file_key] is not None or file_key == "gates")
            if entry["id"] == "inputs.mode" and documents.get("manifest"):
                skill = documents["manifest"].get("skill")
                row["unavailable"] = {mode: "Требуется фича одного из типов: " + ", ".join(sorted(skills))
                                      for mode, skills in resolver._MODE_TO_SKILLS.items()
                                      if mode in entry["enum"] and skill not in skills}
            if entry["sensitive"]:
                row["default"] = None
            rows.append(row)
        policy = documents.get("pipeline") or {}
        risk = documents.get("risk") or {}
        phases = {p["id"]: dict(p) for p in resolver.DEFAULT_PHASES}
        overrides = policy.get("phases_override")
        for override in overrides if isinstance(overrides, list) else []:
            if isinstance(override, dict) and isinstance(override.get("id"), str):
                phases.setdefault(override["id"], {}).update(override)
        live = config._live_run(self.project)
        return dict(project=str(self.project), features=list(features), selected=feature, rows=rows,
                    revisions=revisions, errors=errors, phases=list(phases.values()),
                    policy_ready=documents.get("pipeline") is not None,
                    risk_ready=documents.get("risk") is not None, levels=LEVELS,
                    risk_lists={key: risk.get(key, []) for key in sorted(config._RISK_LIST_KEYS)},
                    caps=risk.get("agent_caps", {}), live=live)

    def operations(self, changes, feature):
        if not isinstance(changes, list) or not 1 <= len(changes) <= len(self.params):
            raise ValueError("Выберите хотя бы одно изменение")
        result = []
        seen = set()
        for change in changes:
            op = change["op"]
            if op == "set":
                entry = self.entries.get(change["id"])
                if entry is None or entry["id"] in seen:
                    raise ValueError("Неизвестная или повторная настройка")
                seen.add(entry["id"])
                value = change["value"]
                if isinstance(value, str) and entry["type"] == "float":
                    value = value.replace(",", ".")
                skill, _, slug = feature.partition("/")
                args = argparse.Namespace(id=entry["id"], value=value, skill=skill,
                                          feature=slug, confirm=True, dry_run=False)
                result.append((entry["file"], args, entry))
                continue
            if len(changes) != 1:
                raise ValueError("Подтвердите структурную операцию отдельно")
            if op == "phase" and change["action"] in {"enable", "disable", "add"}:
                phase_id = change["phase_id"].strip()
                if not phase_id:
                    raise ValueError("Укажите ID фазы")
                args = argparse.Namespace(action=change["action"], phase_id=phase_id,
                                          enabled_by=change.get("enabled_by") or None,
                                          skill=change.get("skill") or None,
                                          gates=change.get("gates", "").split(),
                                          desc=change.get("desc"), after=change.get("after"))
                result.append(("pipeline", args, None))
            elif op == "risk" and change["action"] in {"list-add", "list-remove", "cap-set"}:
                if not change["key"] or not change["value"]:
                    raise ValueError("Заполните ключ и значение")
                if change["action"] == "list-add" and change["key"] == "pii_patterns":
                    try:
                        re.compile(change["value"])
                    except re.error as exc:
                        raise ValueError(f"Некорректное регулярное выражение. {exc}") from exc
                args = argparse.Namespace(action=change["action"], key=change["key"],
                                          value=change["value"], confirm=True)
                result.append(("risk", args, None))
            else:
                raise ValueError("Неизвестная операция")
        if len({file_key for file_key, _, _ in result}) != 1:
            raise ValueError("Подтвердите изменения отдельно для каждого файла")
        return result

    def apply(self, operations, target, revision, *, preview):
        file_key = operations[0][0]
        data, legacy, current = self.snapshot(file_key, target)
        if current != revision:
            raise Conflict("Файл изменился. Перечитайте настройки и проверьте черновик")
        original = data
        if data is None:
            if file_key != "gates":
                raise ValueError(f"{target}: сначала инициализируйте конфигурацию штатной командой Forge")
            data = config.gates_skeleton(self.params)

        saved = json.dumps(data, ensure_ascii=False, allow_nan=False)
        effects = []
        diff = []
        for _, args, entry in operations:
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                if entry is not None:
                    args.dry_run = preview
                    old, _ = self.effective(entry, target, original, legacy)
                    code = config.cmd_set(self.project, self.params, args, staged=data)
                    path = entry["path"]
                    new = dig(data, path)[1]
                    if code == 0 and path == "inputs.mode":
                        error = config.phase_resolver()._validate_mode_skill(data)
                        if error:
                            raise FieldError(entry["id"], error)
                else:
                    cmd = config.cmd_phase if file_key == "pipeline" else config.cmd_risk
                    path = ("phases_override" if file_key == "pipeline" else
                            "agent_caps" if args.action == "cap-set" else args.key)
                    old = json.loads(json.dumps(data.get(path)))
                    code = cmd(self.project, self.params, args, staged=data,
                               preview=preview, deferred=effects)
                    new = data.get(path)
            sensitive = bool(entry and entry["sensitive"])
            if code:
                message = json.loads(stdout.getvalue())
                if sensitive and not message.get("approval_key"):
                    message = {"error": "Проверьте формат чувствительного значения"}
                details = [message.get(key) for key in ("error", "reason", "human", "hint")]
                message = " ".join(detail for detail in details if detail)
                if entry:
                    raise FieldError(entry["id"], message)
                raise ValueError(message)
            diff.append(dict(id=entry["id"] if entry else path, path=path,
                             old=MASK if sensitive else old, new=MASK if sensitive else new))
        if not preview and (original is None or
                            json.dumps(data, ensure_ascii=False, allow_nan=False) != saved):
            self.contained(self.project / "ground" / "config-helper" / "backups")
            backup(target, self.project)
            atomic_write(target, data)
            for consent, what in effects:
                config._spend_structure_consent(self.project, consent, what)
        return diff

    def preview(self, request):
        feature = request.get("feature", "")
        ops = self.operations(request["changes"], feature)
        file_key = ops[0][0]
        target = self.target(file_key, feature)
        revision = request["revision"]
        diff = self.apply(ops, target, revision, preview=True)
        token = secrets.token_urlsafe(24)
        self.pending = (token, ops, feature, revision)
        return dict(token=token, file=str(target), diff=diff)

    def save(self, request):
        if self.pending is None or request["token"] != self.pending[0]:
            raise Conflict("Откройте предварительный просмотр заново")
        _, ops, feature, revision = self.pending
        self.pending = None
        target = self.target(ops[0][0], feature)
        with exclusive_lock(self.contained(target.with_suffix(".lock"))):
            self.apply(ops, target, revision, preview=False)
        return {"saved": True}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *_args):
        pass

    def send(self, content, kind, status=200):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(content)

    def api(self, response, status=200):
        self.send(json.dumps(response, ensure_ascii=False, allow_nan=False).encode(),
                  "application/json; charset=utf-8", status)

    def allowed(self, *, write=False):
        host = f"127.0.0.1:{self.server.server_port}"
        return self.headers.get("Host") == host and (
            not write or (self.headers.get("Origin") == f"http://{host}"
                          and self.headers.get("X-Forge-Session") == self.server.token))

    def do_GET(self):
        if not self.allowed():
            return self.api({"error": "Недопустимый адрес"}, 403)
        url = urlsplit(self.path)
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                  "/app.css": ("app.css", "text/css")}
        if url.path in assets:
            name, kind = assets[url.path]
            return self.send((ASSETS / name).read_bytes(), f"{kind}; charset=utf-8")
        if url.path == "/api/catalog":
            try:
                feature = parse_qs(url.query).get("feature", [""])[0]
                with self.server.editor_lock:
                    catalog = self.server.editor.catalog(feature)
                return self.api(dict(catalog, session=self.server.token))
            except (ValueError, OSError) as exc:
                return self.api({"error": str(exc)}, 400)
        return self.api({"error": "Страница отсутствует"}, 404)

    def do_POST(self):
        if not self.allowed(write=True):
            return self.api({"error": "Сессия недействительна. Обновите страницу"}, 403)
        actions = {"/api/preview": self.server.editor.preview, "/api/save": self.server.editor.save}
        if self.path not in actions:
            return self.api({"error": "Операция отсутствует"}, 404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.headers.get_content_type() != "application/json" or not 0 < size <= 262144:
                raise ValueError("Ожидался JSON размером до 256 КиБ")
            request = json.loads(self.rfile.read(size), parse_constant=reject_constant, parse_float=finite_float)
            with self.server.editor_lock:
                response = actions[self.path](request)
        except FieldError as exc:
            return self.api({"error": str(exc), "field": exc.field}, 400)
        except Conflict as exc:
            return self.api({"error": str(exc)}, 409)
        except (KeyError, TypeError, AttributeError):
            return self.api({"error": "Неверные поля запроса"}, 400)
        except (ValueError, OSError) as exc:
            return self.api({"error": str(exc)}, 400)
        self.api(response)


class ConfigServer(ThreadingHTTPServer):
    def __init__(self, address, handler):
        self.idle_expired = False
        self._activity_lock = Lock()
        self._last_activity = monotonic()
        self._active_requests = 0
        super().__init__(address, handler)

    def process_request_thread(self, request, client_address):
        with self._activity_lock:
            expired = self.idle_expired
            if not expired:
                self._active_requests += 1
        if expired:
            self.shutdown_request(request)
            return
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._activity_lock:
                self._last_activity = monotonic()
                self._active_requests -= 1

    def service_actions(self):
        with self._activity_lock:
            if (self.idle_expired or self._active_requests
                    or monotonic() - self._last_activity < IDLE_TIMEOUT_SECONDS):
                return
            self.idle_expired = True
        # shutdown() waits for serve_forever() to exit, so call it from another thread.
        Thread(target=self.shutdown, daemon=True).start()


def bind_server():
    while True:
        server = ConfigServer(("127.0.0.1", 0), Handler)
        if server.server_port not in {8080, 8084}:
            return server
        server.server_close()


def open_browser(url):
    try:
        webbrowser.open(url)
    except (webbrowser.Error, OSError):
        pass


def main():
    parser = argparse.ArgumentParser(description="Локальный конфигуратор Forge")
    parser.add_argument("--project", type=Path, help="Корень проекта Forge")
    parser.add_argument("--no-browser", action="store_true", help="Только напечатать URL")
    args = parser.parse_args()
    cwd = Path.cwd().resolve()
    project = args.project.resolve() if args.project else find_project_root(cwd)
    if project is None:
        project = next((p for p in (cwd, *cwd.parents)
                        if (p / ".gigacode" / "extensions").is_dir()), None)
    if project is None or not project.is_dir():
        parser.error("Укажите каталог проекта через --project")
    with bind_server() as server:
        server.editor = Editor(project)
        server.editor_lock = Lock()
        server.token = secrets.token_urlsafe(32)
        url = f"http://127.0.0.1:{server.server_port}/"
        print(url, flush=True)
        if not args.no_browser:
            Thread(target=open_browser, args=(url,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        if server.idle_expired:
            print(f"forge-config: сервер остановлен после {IDLE_TIMEOUT_SECONDS // 60} минут без запросов.")


if __name__ == "__main__":
    main()
