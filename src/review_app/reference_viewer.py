"""Local-only, read-only viewer for the sealed AI labels in the 400-record audit."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import webbrowser
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/interim/72_gendered_visibility_human_review_400_v1"
QUEUE = DATA / "private/blind_queue.csv"
REFERENCE = DATA / "private/sealed_reference.jsonl"
CODEBOOK = ROOT / "config/gendered_visibility_human_review_400_v1.json"
MANIFEST = DATA / "manifest.json"
STATIC = Path(__file__).with_name("reference_static")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


class ReferenceDataset:
    """Validate and join the private queue with its sealed frozen AI outputs."""

    def __init__(
        self,
        queue_path: Path = QUEUE,
        reference_path: Path = REFERENCE,
        codebook_path: Path = CODEBOOK,
        manifest_path: Path = MANIFEST,
    ) -> None:
        self.queue_path = Path(queue_path)
        self.reference_path = Path(reference_path)
        self.codebook_path = Path(codebook_path)
        self.manifest_path = Path(manifest_path)
        self._verify_frozen_inputs()

        with self.queue_path.open(encoding="utf-8", newline="") as handle:
            queue = list(csv.DictReader(handle))
        references = [
            json.loads(line)
            for line in self.reference_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(queue) != 400 or len(references) != 400:
            raise ValueError("AI参考查看台必须严格包含400条队列与400条密封输出")
        if len({row["blind_id"] for row in queue}) != 400:
            raise ValueError("盲ID必须唯一")
        reference_by_id = {row["blind_id"]: row["reference_output"] for row in references}
        if len(reference_by_id) != 400 or set(reference_by_id) != {
            row["blind_id"] for row in queue
        }:
            raise ValueError("密封AI输出与400条盲审队列不一致")

        codebook = json.loads(self.codebook_path.read_text(encoding="utf-8"))
        option_sets = codebook.get("option_sets", {})
        fields: list[dict[str, Any]] = []
        reference_keys = set(next(iter(reference_by_id.values())))
        for source_field in codebook["fields"]:
            if source_field["id"] not in reference_keys:
                continue
            field = deepcopy(source_field)
            if "options" not in field and field.get("option_set"):
                field["options"] = deepcopy(option_sets[field["option_set"]])
            fields.append(field)
        if len(fields) != 16 or {field["id"] for field in fields} != reference_keys:
            raise ValueError("密封AI字段与审核代码本不一致")

        option_ids = {
            field["id"]: {option["id"] for option in field.get("options", [])}
            for field in fields
        }
        records: list[dict[str, Any]] = []
        for position, row in enumerate(sorted(queue, key=lambda item: int(item["position"])), 1):
            output = reference_by_id[row["blind_id"]]
            if set(output) != reference_keys:
                raise ValueError("密封AI记录字段不完整")
            invalid = {
                field_id: value
                for field_id, value in output.items()
                if value not in option_ids[field_id]
            }
            if invalid:
                raise ValueError(f"密封AI记录存在无效代码: {sorted(invalid)}")
            records.append(
                {
                    "position": position,
                    "blind_id": row["blind_id"],
                    "title": row["deidentified_title"],
                    "reference_output": output,
                }
            )
        self.fields = fields
        self.records = records

    def _verify_frozen_inputs(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        expected = {**manifest.get("inputs", {}), **manifest.get("outputs", {})}
        for path in (self.queue_path, self.reference_path, self.codebook_path):
            key = relative(path)
            if key not in expected or sha256(path) != expected[key]:
                raise RuntimeError(f"冻结输入SHA-256不一致: {key}")

    def metadata(self) -> dict[str, Any]:
        return {
            "app": "gendered_visibility_ai_reference_viewer",
            "title": "作者专用：400条冻结AI标注查看台",
            "total": len(self.records),
            "fields": self.fields,
            "warning": "这些是冻结模型输出，不是人工金标准。禁止向两位盲审员展示。",
            "read_only": True,
        }

    def record(self, position: int) -> dict[str, Any]:
        if not 1 <= position <= len(self.records):
            raise ValueError(f"位置必须在1到{len(self.records)}之间")
        return self.records[position - 1]

    def search(
        self,
        title_query: str = "",
        field_id: str = "",
        value: str = "",
    ) -> list[dict[str, Any]]:
        query = title_query.strip().casefold()
        field_ids = {field["id"] for field in self.fields}
        if field_id and field_id not in field_ids:
            raise ValueError("未知筛选字段")
        if value:
            if not field_id:
                raise ValueError("选择筛选值前必须先选择字段")
            field = next(item for item in self.fields if item["id"] == field_id)
            if value not in {option["id"] for option in field["options"]}:
                raise ValueError("未知筛选值")
        matches = []
        for record in self.records:
            if query and query not in record["title"].casefold():
                continue
            if field_id and value and record["reference_output"][field_id] != value:
                continue
            matches.append(
                {
                    "position": record["position"],
                    "blind_id": record["blind_id"],
                    "title": record["title"],
                    "field_id": field_id or None,
                    "value": record["reference_output"].get(field_id)
                    if field_id
                    else None,
                }
            )
        return matches


class Handler(BaseHTTPRequestHandler):
    dataset: ReferenceDataset

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
        return self.headers.get("Host", "").split(":")[0] in {"127.0.0.1", "localhost"}

    def do_GET(self) -> None:
        if not self.local_request():
            self.send_json({"error": "local access only"}, 403)
            return
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/api/health":
                self.send_json({"ok": True, "read_only": True})
                return
            if parsed.path == "/api/meta":
                self.send_json(self.dataset.metadata())
                return
            if parsed.path == "/api/reference":
                position = int(query.get("position", [1])[0])
                self.send_json({"record": self.dataset.record(position)})
                return
            if parsed.path == "/api/search":
                matches = self.dataset.search(
                    query.get("q", [""])[0],
                    query.get("field", [""])[0],
                    query.get("value", [""])[0],
                )
                self.send_json({"total": len(matches), "records": matches})
                return
            relative_path = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            target = (STATIC / relative_path).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                self.send_json({"error": "not found"}, 404)
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type == "application/javascript":
                content_type += "; charset=utf-8"
            self.send_headers(200, content_type)
            self.wfile.write(target.read_bytes())
        except Exception as error:  # noqa: BLE001
            self.send_json({"error": str(error)}, 400)

    def do_POST(self) -> None:
        self.send_json({"error": "read-only viewer"}, 405)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--queue", type=Path, default=QUEUE)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    parser.add_argument("--codebook", type=Path, default=CODEBOOK)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    Handler.dataset = ReferenceDataset(
        args.queue, args.reference, args.codebook, args.manifest
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"AI reference viewer (author-only, read-only): {url}")
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
