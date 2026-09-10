"""Run the local-only formal blind-review web application."""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .store import CODEBOOK, QUEUE, ReviewStore, data_dir_for_queue

STATIC = Path(__file__).with_name("static")


class Handler(BaseHTTPRequestHandler):
    store: ReviewStore
    token = ""

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_headers(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def send_json(self, value: object, status: int = 200) -> None:
        self.send_headers(status, "application/json; charset=utf-8")
        self.wfile.write(json.dumps(value, ensure_ascii=False).encode())

    def local_request(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] in {
            "127.0.0.1",
            "localhost",
        }

    def do_GET(self) -> None:
        if not self.local_request():
            self.send_json({"error": "local access only"}, 403)
            return
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/api/health":
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/meta":
                self.send_json({**self.store.metadata(), "request_token": self.token})
                return
            if parsed.path == "/api/record":
                record = self.store.record(
                    int(query.get("position", [1])[0]),
                    query.get("next_unreviewed", ["0"])[0] == "1",
                )
                self.send_json({"record": record})
                return
            if parsed.path == "/api/summary":
                self.send_json(self.store.summary())
                return
            if parsed.path == "/api/search":
                self.send_json(
                    self.store.search_reviewed(
                        query.get("field", [""])[0],
                        query.get("option", [""])[0],
                        query.get("title", [""])[0],
                    )
                )
                return
            relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            target = (STATIC / relative).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                self.send_json({"error": "not found"}, 404)
                return
            content_type = (
                mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            )
            if (
                content_type.startswith("text/")
                or content_type == "application/javascript"
            ):
                content_type += "; charset=utf-8"
            self.send_headers(200, content_type)
            self.wfile.write(target.read_bytes())
        except Exception as error:  # noqa: BLE001
            self.send_json({"error": str(error)}, 400)

    def do_POST(self) -> None:
        if self.headers.get("X-Review-App-Token") != self.token:
            self.send_json({"error": "invalid token"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/annotation":
                result = self.store.save(
                    str(body["record_id"]),
                    body.get("annotation", {}),
                    str(body.get("status", "draft")),
                    int(body.get("duration_ms", 0)),
                )
                self.send_json(result)
                return
            if self.path == "/api/export":
                self.send_json({"export_path": str(self.store.export())})
                return
            self.send_json({"error": "not found"}, 404)
        except Exception as error:  # noqa: BLE001
            self.send_json({"error": str(error)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coder-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--queue", type=Path, default=QUEUE)
    parser.add_argument("--codebook", type=Path, default=CODEBOOK)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--init-only", action="store_true")
    args = parser.parse_args()

    data_dir = data_dir_for_queue(args.queue)
    default_db = data_dir / "coders" / f"review_{args.coder_id}.sqlite3"
    store = ReviewStore(args.coder_id, args.db or default_db, args.queue, args.codebook)
    store.initialize()
    if args.init_only:
        print(f"Initialized {store.db_path}")
        return

    Handler.store = store
    Handler.token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Blind review: {url} coder={args.coder_id}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
