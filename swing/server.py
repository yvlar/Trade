"""Loopback-only HTTP presentation adapter. No login, credentials or external requests."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from swing.engine import advance, snapshot
from swing.fixtures import synthetic_dataset
from swing.store import Store, encode

STATIC = Path(__file__).with_name("static")


class Worker:
    def __init__(self, store: Store):
        self.store = store
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self.thread and self.thread.is_alive()),
            "error": self.error,
            "mode": "replay local uniquement",
            "broker_enabled": False,
        }

    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.error = None
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, name="swing-local-replay", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        with self.lock:
            self.stop_event.set()
            if self.thread:
                self.thread.join(timeout=12)

    def run(self) -> None:
        try:
            while not self.stop_event.wait(0.5):
                with self.store.transaction() as db:
                    cursor = Store.state(db)["cursor"]
                result = advance(self.store, cursor)
                if result["status"] == "finished":
                    break
        except Exception as exc:
            self.error = str(exc)
            with self.store.transaction() as db:
                Store.event(db, "worker_error", {"message": self.error})
        finally:
            self.stop_event.set()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, store: Store, port: int):
        self.store = store
        self.worker = Worker(store)
        super().__init__(("127.0.0.1", port), Handler)

    def server_close(self) -> None:
        self.worker.stop()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: LocalServer
    server_version = "SwingLocal/0.1"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args: Any) -> None:
        # No user payloads/headers in access logs.
        pass

    def valid_host(self) -> bool:
        values = self.headers.get_all("Host", [])
        return len(values) == 1 and values[0] in (
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        )

    def send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def reply(self, status: int, data: Any) -> None:
        self.send(status, encode(data).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self.valid_host():
            self.reply(403, {"error": "Host local invalide"})
            return
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.reply(403, {"error": "Lecture intersite refusée"})
            return
        if self.path == "/api/state":
            self.reply(200, {**snapshot(self.server.store), "worker": self.server.worker.status()})
        elif self.path == "/api/export":
            with self.server.store.transaction() as db:
                data = {
                    "state": Store.state(db),
                    "mode": "replay historique, jamais réel",
                    "journal": [dict(r) for r in db.execute("SELECT * FROM journal ORDER BY id")],
                    "bars": [dict(r) for r in db.execute("SELECT * FROM bars ORDER BY day,symbol")],
                    "metadata": [dict(r) for r in db.execute("SELECT * FROM metadata")],
                    "assets": [dict(r) for r in db.execute("SELECT * FROM assets")],
                    "batches": [dict(r) for r in db.execute("SELECT * FROM batches")],
                }
            self.reply(200, data)
        elif self.path == "/api/example":
            self.reply(200, synthetic_dataset())
        else:
            paths = {
                "/": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/style.css": ("style.css", "text/css"),
            }
            resource = paths.get(self.path)
            if resource:
                name, kind = resource
                self.send(200, (STATIC / name).read_bytes(), kind + "; charset=utf-8")
            else:
                self.reply(404, {"error": "Page introuvable"})

    def do_POST(self) -> None:
        if not self.valid_host():
            self.reply(403, {"error": "Host local invalide"})
            return
        if (
            self.headers.get_all("Origin", []) != ["http://" + self.headers["Host"]]
            or self.headers.get("X-Swing-Local") != "1"
            or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin")
            or self.headers.get("Content-Type") != "application/json"
        ):
            self.reply(403, {"error": "Protection Origin/CSRF : requête locale JSON requise"})
            return
        try:
            if (
                self.headers.get("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length", [])) != 1
            ):
                raise ValueError("Content-Length unique requis")
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 10_000_000:
                raise ValueError("Corps JSON requis, maximum 10 Mo")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Requête tronquée")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Objet JSON requis")
            result: Any = {"ok": True}
            if self.path == "/api/import":
                result = self.server.store.ingest(payload)
            elif self.path == "/api/fixture":
                if payload:
                    raise ValueError("Le chargement synthétique attend un objet vide")
                result = self.server.store.ingest(synthetic_dataset())
            elif self.path == "/api/settings":
                self.server.store.configure(payload)
            elif self.path == "/api/step":
                if set(payload) != {"expected_cursor"} or not isinstance(
                    payload["expected_cursor"], (str, type(None))
                ):
                    raise ValueError("expected_cursor requis pour l'idempotence")
                result = advance(self.server.store, payload["expected_cursor"])
            elif self.path in ("/api/worker/start", "/api/worker/stop"):
                if payload:
                    raise ValueError("Objet vide requis")
                if self.path.endswith("start"):
                    self.server.worker.start()
                else:
                    self.server.worker.stop()
                result = self.server.worker.status()
            else:
                self.reply(404, {"error": "Action introuvable"})
                return
            self.reply(200, result)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.reply(422, {"error": str(exc)})
        except Exception:
            self.reply(
                500,
                {"error": "Erreur interne ; aucune abstention implicite. Consulter le journal."},
            )

    def do_OPTIONS(self) -> None:
        self.reply(403, {"error": "Aucun accès CORS autorisé"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Atelier Swing — replay local uniquement")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=Path("data/swing.sqlite3"))
    args = parser.parse_args()
    if args.port == 8000 or not 1024 <= args.port <= 65535:
        parser.error("Utiliser un port 1024–65535 libre, distinct de 8000")
    server = LocalServer(Store(args.db), args.port)
    print(f"Atelier Swing : http://127.0.0.1:{args.port} — replay local, worker arrêté", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
