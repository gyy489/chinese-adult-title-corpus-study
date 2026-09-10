"""文件用途: 抓取标题列表页数据, 写入可断点续跑的 MySQL 主存储或兼容文件导出。"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src.collection.mysql_store import MySQLStore
from src.collection.project_paths import (
    DEFAULT_EXPORT_DIR,
    DEFAULT_MYSQL_CONFIG,
    DEFAULT_SITE_CONFIG,
    PROJECT_ROOT,
    resolve_project_path,
)


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
def extract_page_number(url: str) -> int | None:
    patterns = [
        r"/page/(\d+)(?:/|\.html|$)",
        r"[?&]page=(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return int(match.group(1))
    return None


@dataclass
class SiteConfig:
    name: str
    scope: str
    start_urls: list[str]
    item_selectors: list[str]
    next_page_selectors: list[str]
    allowed_domains: list[str]
    exclude_url_patterns: list[str]
    list_url_patterns: list[str] | None = None
    detail_url_patterns: list[str] | None = None
    # "requests" (default) fetches with the plain requests+BeautifulSoup path
    # via fetch_html(). "playwright" renders the page with headless Chromium
    # via fetch_html_playwright() first, for JS single-page apps that return
    # no usable HTML to a plain HTTP GET (for example a Vue app with an empty
    # <div id="app">). Existing sites omit this field and keep "requests"
    # behavior unchanged.
    fetch_mode: str = "requests"


def load_config(config_path: Path) -> list[SiteConfig]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    sites = []
    for site in payload.get("sites", []):
        sites.append(
            SiteConfig(
                name=site["name"],
                scope=str(site.get("scope", "general")),
                start_urls=site.get("start_urls", []),
                item_selectors=site.get("item_selectors", []),
                next_page_selectors=site.get("next_page_selectors", []),
                allowed_domains=site.get("allowed_domains", []),
                exclude_url_patterns=site.get("exclude_url_patterns", []),
                list_url_patterns=site.get("list_url_patterns"),
                detail_url_patterns=site.get("detail_url_patterns"),
                fetch_mode=str(site.get("fetch_mode", "requests")),
            )
        )
    return sites


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
    )
    return session


def fetch_html(session: requests.Session, url: str, timeout: int) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


class PlaywrightFetchError(Exception):
    """Raised when a Playwright-based page render fails (missing dependency,
    browser launch failure, navigation timeout, and so on). Kept as a distinct
    type so callers can catch it alongside requests.RequestException without
    broadening what the existing requests-based fetch path catches."""


def fetch_html_playwright(url: str, timeout: int, wait_after_load_ms: int = 1500) -> str:
    """Render a JS single-page app with headless Chromium and return the
    hydrated HTML. Only sites configured with fetch_mode == "playwright" call
    this; requests-based sites never import playwright at all (lazy import
    below), so a machine without the playwright package installed can still
    run every other site unaffected."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightFetchError(
            "playwright package is not installed; run "
            "'./.venv/bin/python -m pip install playwright' and "
            "'./.venv/bin/python -m playwright install chromium'"
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=USER_AGENT,
                    locale="zh-CN",
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                page.wait_for_timeout(wait_after_load_ms)
                return page.content()
            finally:
                browser.close()
    except PlaywrightFetchError:
        raise
    except Exception as exc:
        raise PlaywrightFetchError(f"playwright render failed for {url}: {exc}") from exc


def fetch_html_for_site(session: requests.Session, site: SiteConfig, url: str, timeout: int) -> str:
    if site.fetch_mode == "playwright":
        return fetch_html_playwright(url, timeout=timeout)
    return fetch_html(session, url, timeout=timeout)


def normalize_title(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_allowed_domain(url: str, allowed_domains: Iterable[str]) -> bool:
    hostname = urlparse(url).hostname or ""
    return hostname in set(allowed_domains)


def matches_any_pattern(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def looks_like_detail_url(url: str, site: SiteConfig) -> bool:
    if not is_allowed_domain(url, site.allowed_domains):
        return False
    if matches_any_pattern(url, site.exclude_url_patterns):
        return False
    if site.list_url_patterns and matches_any_pattern(url, site.list_url_patterns):
        return False
    if site.detail_url_patterns and not matches_any_pattern(url, site.detail_url_patterns):
        return False
    return True


def extract_title_from_anchor(anchor) -> str:
    candidate = anchor.get_text(" ", strip=True)
    if not candidate:
        candidate = anchor.get("title", "")
    if not candidate:
        candidate = anchor.get("aria-label", "")
    if not candidate:
        image = anchor.find("img")
        if image:
            candidate = image.get("alt", "")
    return normalize_title(candidate)


def extract_items_from_page(html: str, page_url: str, site: SiteConfig) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen_urls: set[str] = set()
    crawl_time = datetime.now(timezone.utc).isoformat()

    for selector in site.item_selectors:
        for anchor in soup.select(selector):
            href = anchor.get("href")
            if not href:
                continue

            source_url = urljoin(page_url, href)
            if source_url in seen_urls or not looks_like_detail_url(source_url, site):
                continue

            title = extract_title_from_anchor(anchor)
            if len(title) < 2:
                continue

            seen_urls.add(source_url)
            items.append(
                {
                    "title": title,
                    "source_url": source_url,
                    "source_site": site.name,
                    "listing_url": page_url,
                    "crawl_time": crawl_time,
                }
            )

    if items:
        return items

    for anchor in soup.find_all("a", href=True):
        source_url = urljoin(page_url, anchor["href"])
        if source_url in seen_urls or not looks_like_detail_url(source_url, site):
            continue

        title = extract_title_from_anchor(anchor)
        if len(title) < 6:
            continue
        if title in {"首页", "下一页", "上一页", "搜索", "更多分类"}:
            continue

        seen_urls.add(source_url)
        items.append(
            {
                "title": title,
                "source_url": source_url,
                "source_site": site.name,
                "listing_url": page_url,
                "crawl_time": crawl_time,
            }
        )

    return items


def extract_next_page(html: str, page_url: str, site: SiteConfig) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    current_page_number = extract_page_number(page_url) or 1

    for selector in site.next_page_selectors:
        candidates: list[tuple[int | None, str]] = []
        for anchor in soup.select(selector):
            href = anchor.get("href")
            if not href:
                continue
            next_url = urljoin(page_url, href)
            if next_url == page_url or not is_allowed_domain(next_url, site.allowed_domains):
                continue
            candidates.append((extract_page_number(next_url), next_url))

        numeric_candidates = [
            (page_number, next_url)
            for page_number, next_url in candidates
            if page_number is not None and page_number > current_page_number
        ]
        if numeric_candidates:
            return min(numeric_candidates, key=lambda item: item[0])[1]

        if candidates:
            return candidates[0][1]
    return None


def dedupe_items(items: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item["title"], item["source_url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def write_jsonl(path: Path, items: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_csv(path: Path, items: list[dict]) -> None:
    fieldnames = ["title", "source_url", "source_site", "listing_url", "crawl_time"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(items)


def export_rows_to_output(rows: list[dict[str, str]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "titles.jsonl", rows)
    write_csv(output_dir / "titles.csv", rows)


def crawl_site_to_files(
    session: requests.Session,
    site: SiteConfig,
    max_pages_per_site: int,
    max_items_per_site: int,
    delay: float,
    timeout: int,
) -> list[dict]:
    all_items: list[dict] = []
    visited_pages: set[str] = set()

    for start_url in site.start_urls:
        current_url = start_url
        pages_crawled = 0

        while current_url and pages_crawled < max_pages_per_site:
            if current_url in visited_pages:
                break

            visited_pages.add(current_url)
            pages_crawled += 1
            print(f"[{site.name}] fetching page {pages_crawled}: {current_url}")

            try:
                html = fetch_html_for_site(session, site, current_url, timeout=timeout)
            except (requests.RequestException, PlaywrightFetchError) as exc:
                print(f"[{site.name}] fetch failed: {exc}", file=sys.stderr)
                break

            page_items = extract_items_from_page(html, current_url, site)
            all_items.extend(page_items)
            print(f"[{site.name}] extracted {len(page_items)} items from current page")

            if len(all_items) >= max_items_per_site:
                print(f"[{site.name}] reached max_items_per_site={max_items_per_site}")
                return all_items[:max_items_per_site]

            next_page = extract_next_page(html, current_url, site)
            if not next_page or next_page in visited_pages:
                break

            current_url = next_page
            time.sleep(delay)

    return all_items[:max_items_per_site]


def crawl_site_to_mysql(
    session: requests.Session,
    store: MySQLStore,
    run_id: int,
    site: SiteConfig,
    max_pages_per_site: int,
    max_items_per_site: int,
    delay: float,
    timeout: int,
    max_attempts: int,
    run_progress: dict[str, int],
    target_total_titles: int | None,
    target_new_titles: int | None,
) -> dict[str, int]:
    stats = {
        "pages_fetched": 0,
        "pages_failed": 0,
        "items_extracted": 0,
        "new_titles": 0,
        "existing_titles": 0,
        "next_pages_seen": 0,
    }

    while stats["pages_fetched"] < max_pages_per_site and stats["items_extracted"] < max_items_per_site:
        if target_total_titles and run_progress["current_total_titles"] >= target_total_titles:
            print(f"[{site.name}] reached target_total_titles={target_total_titles}")
            break
        if target_new_titles and run_progress["new_titles"] >= target_new_titles:
            print(f"[{site.name}] reached target_new_titles={target_new_titles}")
            break

        claimed_page = store.claim_next_page(site.name, run_id=run_id, max_attempts=max_attempts)
        if claimed_page is None:
            print(f"[{site.name}] no pending queue page left")
            break

        current_url = str(claimed_page["page_url"])
        print(
            f"[{site.name}] queue fetch {stats['pages_fetched'] + stats['pages_failed'] + 1}: "
            f"{current_url}"
        )

        try:
            html = fetch_html_for_site(session, site, current_url, timeout=timeout)
        except (requests.RequestException, PlaywrightFetchError) as exc:
            stats["pages_failed"] += 1
            store.mark_page_error(claimed_page["id"], run_id=run_id, error_message=str(exc))
            print(f"[{site.name}] fetch failed: {exc}", file=sys.stderr)
            if delay > 0:
                time.sleep(delay)
            continue

        page_items = extract_items_from_page(html, current_url, site)
        new_count, existing_count = store.upsert_titles(page_items, run_id=run_id)
        stats["pages_fetched"] += 1
        stats["items_extracted"] += len(page_items)
        stats["new_titles"] += new_count
        stats["existing_titles"] += existing_count
        run_progress["new_titles"] += new_count
        run_progress["current_total_titles"] += new_count

        next_page = extract_next_page(html, current_url, site)
        if next_page and next_page != current_url:
            store.enqueue_page(
                site_name=site.name,
                page_url=next_page,
                discovered_from=current_url,
                page_number=extract_page_number(next_page),
            )
            stats["next_pages_seen"] += 1

        store.mark_page_success(claimed_page["id"], run_id=run_id)
        print(
            f"[{site.name}] extracted {len(page_items)} items "
            f"(new={new_count}, existing={existing_count})"
        )

        if stats["items_extracted"] >= max_items_per_site:
            print(f"[{site.name}] reached max_items_per_site={max_items_per_site}")
            break
        if target_total_titles and run_progress["current_total_titles"] >= target_total_titles:
            print(f"[{site.name}] reached target_total_titles={target_total_titles}")
            break
        if target_new_titles and run_progress["new_titles"] >= target_new_titles:
            print(f"[{site.name}] reached target_new_titles={target_new_titles}")
            break

        if delay > 0:
            time.sleep(delay)

    return stats


def summarize(items: list[dict]) -> None:
    counter = Counter(item["source_site"] for item in items)
    print("")
    print("Summary")
    for site_name, count in sorted(counter.items()):
        print(f"- {site_name}: {count}")
    print(f"- total: {len(items)}")


def summarize_mysql_stats(
    site_stats: dict[str, dict[str, int]],
    queue_counts_by_site: dict[str, dict[str, int]],
    site_title_counts: dict[str, int],
    selected_total_titles: int,
) -> None:
    print("")
    print("MySQL Summary")
    for site_name, stats in site_stats.items():
        site_queue = queue_counts_by_site.get(site_name, {"pending": 0, "processing": 0, "success": 0, "error": 0})
        print(
            f"- {site_name}: pages_fetched={stats['pages_fetched']}, "
            f"pages_failed={stats['pages_failed']}, items_extracted={stats['items_extracted']}, "
            f"new_titles={stats['new_titles']}, existing_titles={stats['existing_titles']}, "
            f"stored_titles={site_title_counts.get(site_name, 0)}, "
            f"queue(pending={site_queue['pending']}, processing={site_queue['processing']}, "
            f"success={site_queue['success']}, error={site_queue['error']})"
        )
    print(f"- selected_total_titles: {selected_total_titles}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawler for collecting video titles at larger scale.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_SITE_CONFIG.relative_to(PROJECT_ROOT)),
        help="Path to site config JSON, relative to the project root unless absolute.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_EXPORT_DIR.relative_to(PROJECT_ROOT)),
        help="Directory for output files, relative to the project root unless absolute.",
    )
    parser.add_argument("--max-pages-per-site", type=int, default=30)
    parser.add_argument("--max-items-per-site", type=int, default=3000)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--storage", choices=["mysql", "files"], default="mysql")
    parser.add_argument(
        "--mysql-config",
        default=str(DEFAULT_MYSQL_CONFIG.relative_to(PROJECT_ROOT)),
        help="Path to MySQL JSON config, relative to the project root unless absolute.",
    )
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum attempts for a queue page.")
    parser.add_argument("--run-label", default="", help="Optional label written into crawl_runs.")
    parser.add_argument("--sites", default="", help="Optional comma-separated site names.")
    parser.add_argument(
        "--site-scope",
        default="",
        help="Optional site scope filter, for example 'community' or 'general'.",
    )
    parser.add_argument("--skip-export", action="store_true", help="Skip compatibility export after MySQL crawl.")
    parser.add_argument("--requeue-errors", action="store_true", help="Move error queue pages back to pending before crawling.")
    parser.add_argument(
        "--recover-stale-processing-minutes",
        type=int,
        default=120,
        help="Requeue 'processing' pages older than this many minutes before starting.",
    )
    parser.add_argument(
        "--target-total-titles",
        type=int,
        default=0,
        help="Optional stop condition: end the run when total titles in MySQL reach this number.",
    )
    parser.add_argument(
        "--target-new-titles",
        type=int,
        default=0,
        help="Optional stop condition: end the run when this run inserts this many new titles.",
    )
    return parser.parse_args()


def select_sites(all_sites: list[SiteConfig], requested_site_names: list[str], requested_scope: str) -> list[SiteConfig]:
    selected = all_sites
    if requested_scope:
        selected = [site for site in selected if site.scope == requested_scope]
    wanted = set(requested_site_names)
    if wanted:
        selected = [site for site in selected if site.name in wanted]
        missing = sorted(wanted - {site.name for site in selected})
        if missing:
            raise ValueError(f"Unknown site names under current filter: {', '.join(missing)}")
    if requested_scope and not selected:
        raise ValueError(f"No sites matched site scope: {requested_scope}")
    return selected


def run_file_mode(
    session: requests.Session,
    sites: list[SiteConfig],
    output_dir: Path,
    max_pages_per_site: int,
    max_items_per_site: int,
    delay: float,
    timeout: int,
) -> int:
    raw_items: list[dict] = []
    for site in sites:
        site_items = crawl_site_to_files(
            session=session,
            site=site,
            max_pages_per_site=max_pages_per_site,
            max_items_per_site=max_items_per_site,
            delay=delay,
            timeout=timeout,
        )
        raw_items.extend(site_items)

    deduped_items = dedupe_items(raw_items)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "collected_titles.jsonl", raw_items)
    write_jsonl(output_dir / "titles.jsonl", deduped_items)
    write_csv(output_dir / "titles.csv", deduped_items)
    summarize(deduped_items)
    return 0


def run_mysql_mode(
    session: requests.Session,
    sites: list[SiteConfig],
    output_dir: Path,
    mysql_config_path: Path,
    max_pages_per_site: int,
    max_items_per_site: int,
    delay: float,
    timeout: int,
    max_attempts: int,
    run_label: str,
    skip_export: bool,
    requeue_errors: bool,
    recover_stale_processing_minutes: int,
    target_total_titles: int,
    target_new_titles: int,
) -> int:
    store = MySQLStore(mysql_config_path)
    store.ensure_database_and_schema()
    recovered_runs = 0
    site_names = [site.name for site in sites]
    recovered_processing = 0
    if recover_stale_processing_minutes >= 0:
        recovered_runs = store.mark_stale_running_runs_as_interrupted(
            older_than_minutes=recover_stale_processing_minutes,
        )
        print(f"recovered_running_runs={recovered_runs}")
        recovered_processing = store.requeue_stale_processing(
            older_than_minutes=recover_stale_processing_minutes,
            site_names=site_names,
        )
        print(f"recovered_processing_pages={recovered_processing}")
    if requeue_errors:
        requeued = 0
        for site_name in site_names:
            requeued += store.requeue_errors(site_name=site_name)
        print(f"requeued_error_pages={requeued}")

    initial_total_titles = store.get_total_titles(site_names=site_names)
    crawl_config = {
        "sites": site_names,
        "site_scope": sorted({site.scope for site in sites}),
        "max_pages_per_site": max_pages_per_site,
        "max_items_per_site": max_items_per_site,
        "delay": delay,
        "timeout": timeout,
        "max_attempts": max_attempts,
        "recover_stale_processing_minutes": recover_stale_processing_minutes,
        "target_total_titles": target_total_titles or None,
        "target_new_titles": target_new_titles or None,
        "auto_export": not skip_export,
    }
    run_id = store.create_run(
        run_label=run_label or None,
        crawl_config=crawl_config,
        notes="Resumable MySQL-backed crawl run.",
    )

    site_stats: dict[str, dict[str, int]] = {}
    run_progress = {
        "initial_total_titles": initial_total_titles,
        "current_total_titles": initial_total_titles,
        "new_titles": 0,
    }
    try:
        store.seed_start_urls(sites)
        for site in sites:
            if target_total_titles and run_progress["current_total_titles"] >= target_total_titles:
                print(f"global stop: reached target_total_titles={target_total_titles}")
                break
            if target_new_titles and run_progress["new_titles"] >= target_new_titles:
                print(f"global stop: reached target_new_titles={target_new_titles}")
                break
            site_stats[site.name] = crawl_site_to_mysql(
                session=session,
                store=store,
                run_id=run_id,
                site=site,
                max_pages_per_site=max_pages_per_site,
                max_items_per_site=max_items_per_site,
                delay=delay,
                timeout=timeout,
                max_attempts=max_attempts,
                run_progress=run_progress,
                target_total_titles=target_total_titles or None,
                target_new_titles=target_new_titles or None,
            )

        queue_counts = store.get_queue_counts()
        queue_counts_by_site = store.get_queue_counts_by_site(site_names=site_names)
        title_counts = store.get_title_counts()
        site_title_counts = store.get_site_title_counts(site_names=site_names)
        selected_total_titles = store.get_total_titles(site_names=site_names)
        summarize_mysql_stats(
            site_stats,
            queue_counts_by_site=queue_counts_by_site,
            site_title_counts=site_title_counts,
            selected_total_titles=selected_total_titles,
        )

        exported_rows = 0
        if not skip_export:
            export_rows = store.fetch_export_rows(site_names=site_names)
            export_rows_to_output(export_rows, output_dir)
            exported_rows = len(export_rows)
            print(f"exported_rows={exported_rows}")

        summary = {
            "site_stats": site_stats,
            "queue_counts": queue_counts,
            "queue_counts_by_site": queue_counts_by_site,
            "title_counts": title_counts,
            "site_title_counts": site_title_counts,
            "selected_total_titles": selected_total_titles,
            "selected_site_names": site_names,
            "recovered_running_runs": recovered_runs,
            "recovered_processing_pages": recovered_processing,
            "initial_total_titles": initial_total_titles,
            "run_new_titles": run_progress["new_titles"],
            "exported_rows": exported_rows,
        }
        store.finish_run(run_id, status="completed", summary=summary)
    except KeyboardInterrupt:
        summary = {
            "site_stats": site_stats,
            "selected_site_names": site_names,
            "recovered_running_runs": recovered_runs,
            "recovered_processing_pages": recovered_processing,
            "initial_total_titles": initial_total_titles,
            "run_new_titles": run_progress["new_titles"],
            "note": "Interrupted by user.",
        }
        store.finish_run(run_id, status="interrupted", summary=summary)
        store.close()
        raise
    except Exception as exc:
        summary = {
            "site_stats": site_stats,
            "selected_site_names": site_names,
            "recovered_running_runs": recovered_runs,
            "recovered_processing_pages": recovered_processing,
            "initial_total_titles": initial_total_titles,
            "run_new_titles": run_progress["new_titles"],
            "error": str(exc),
        }
        store.finish_run(run_id, status="failed", summary=summary)
        store.close()
        raise

    store.close()
    print(f"run_id={run_id}")
    print(f"mysql_config={mysql_config_path}")
    return 0


def main() -> int:
    args = parse_args()
    config_path = resolve_project_path(args.config)
    output_dir = resolve_project_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    mysql_config_path = resolve_project_path(args.mysql_config)

    all_sites = load_config(config_path)
    requested_sites = [value.strip() for value in args.sites.split(",") if value.strip()]
    sites = select_sites(all_sites, requested_sites, args.site_scope.strip())
    session = build_session()

    if args.storage == "files":
        return run_file_mode(
            session=session,
            sites=sites,
            output_dir=output_dir,
            max_pages_per_site=args.max_pages_per_site,
            max_items_per_site=args.max_items_per_site,
            delay=args.delay,
            timeout=args.timeout,
        )

    return run_mysql_mode(
        session=session,
        sites=sites,
        output_dir=output_dir,
        mysql_config_path=mysql_config_path,
        max_pages_per_site=args.max_pages_per_site,
        max_items_per_site=args.max_items_per_site,
        delay=args.delay,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        run_label=args.run_label,
        skip_export=args.skip_export,
        requeue_errors=args.requeue_errors,
        recover_stale_processing_minutes=args.recover_stale_processing_minutes,
        target_total_titles=args.target_total_titles,
        target_new_titles=args.target_new_titles,
    )


if __name__ == "__main__":
    raise SystemExit(main())
