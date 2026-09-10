#!/usr/bin/env python3
"""Parse the local synthetic page through the production collection engine."""

from __future__ import annotations

import json
from pathlib import Path

from src.collection.crawler import extract_items_from_page, extract_next_page, load_config


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    site = load_config(ROOT / "config" / "sites.public.example.json")[0]
    html = (ROOT / "examples" / "synthetic" / "collection_page.html").read_text(
        encoding="utf-8"
    )
    page_url = site.start_urls[0]
    payload = {
        "records": extract_items_from_page(html, page_url, site),
        "next_page": extract_next_page(html, page_url, site),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
