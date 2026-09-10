"""Serve the local-only exact-span privacy review desk."""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .privacy_span_store import DATA, MANIFEST, QUEUE, PrivacySpanReviewStore

STATIC = Path(__file__).with_name("privacy_span_static")


class Handler(BaseHTTPRequestHandler):
    store: PrivacySpanReviewStore
    token = ""

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_headers(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'",
        )
        self.end_headers()

    def send_json(self, value: object, status: int = 200) -> None:
        self.send_headers(status, "application/json; charset=utf-8")
        self.wfile.write(json.dumps(value, ensure_ascii=False).encode())

    def local_request(self) -> bool:
        return self.headers.get("Host", "").split(":")[0] in {"127.0.0.1", "localhost"}

    def do_GET(self) -> None:
        if not self.local_request():
            self.send_json({"error": "local access only"}, 403)
            return
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/api/health":
                self.send_json({"ok": True, "local_only": True})
                return
            if parsed.path == "/api/meta":
                self.send_json({**self.store.metadata(), "request_token": self.token})
                return
            if parsed.path == "/api/summary":
                self.send_json(self.store.summary())
                return
            if parsed.path == "/api/record":
                self.send_json(
                    {
                        "record": self.store.record(
                            int(query.get("position", [1])[0]),
                            next_unreviewed=query.get("next_unreviewed", ["0"])[0]
                            == "1",
                            after_position=int(query.get("after_position", [0])[0]),
                        )
                    }
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
        if not self.local_request():
            self.send_json({"error": "local access only"}, 403)
            return
        if self.headers.get("X-Review-App-Token") != self.token:
            self.send_json({"error": "invalid token"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 100_000:
                raise ValueError("request too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/review":
                self.send_json(
                    self.store.save(
                        str(body["span_review_id"]),
                        str(body.get("action", "")),
                        body.get("spans", []),
                        str(body.get("note", "")),
                        str(body.get("status", "draft")),
                        int(body.get("duration_ms", 0)),
                    )
                )
                return
            if self.path == "/api/export":
                self.send_json({"export_path": str(self.store.export())})
                return
            self.send_json({"error": "not found"}, 404)
        except Exception as error:  # noqa: BLE001
            self.send_json({"error": str(error)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewer-id", default="researcher_span_reviewer_1")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--db", type=Path, default=DATA / "private/span_review.sqlite3")
    parser.add_argument("--queue", type=Path, default=QUEUE)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--init-only", action="store_true")
    args = parser.parse_args()

    store = PrivacySpanReviewStore(args.reviewer_id, args.db, args.queue, args.manifest)
    store.initialize()
    if args.init_only:
        print(f"Initialized {store.db_path}")
        return
    Handler.store = store
    Handler.token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Exact privacy-span review: {url}")
    print("仅绑定本机127.0.0.1；Ctrl-C停止。")
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
