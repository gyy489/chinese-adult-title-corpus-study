"""Rule 008: normalize CJK Radical / CJK Radicals Supplement codepoints back
to the standard Hanzi they visually mimic.

Discovered during manual tokenizer-comparison review (not by any earlier
automated audit pass): 1,765 records (7.0% of the corpus), all from
`source_02`, use Unicode "radical" symbols (U+2E80-2EFF / U+2F00-2FDF --
e.g. U+2F14 CJK RADICAL ONE "⼀" instead of U+4E00 "一") in place of the
ordinary Hanzi. These are visually near-identical but are a DIFFERENT
Unicode block, so every general-purpose tool (jieba, THULAC, and the human
eye skimming quickly) either treats them as unknown characters or silently
misreads them. This is likely a font/text-processing artifact on the source
site rather than anything introduced by this project's scraper (out of
scope to trace further here).

Fix: `unicodedata.normalize("NFKC", text)` resolves 85.8% of the 1,765
affected records completely; the remaining 23 distinct codepoints have no
NFKC decomposition (they are radicals, not compatibility characters, so
Unicode doesn't map them) and need an explicit table, built here by
inspecting real context for each of the 23 residual characters in
data/raw/titles.jsonl (e.g. "⻝" appears in "买零⻝" = "买零食", confirming
食, not the bound radical form 饣).

This is purely a character-level normalization -- it does not reinterpret,
guess, or fill in missing text, only maps one Unicode representation of the
same character to its standard form. Applied on top of the current
data/interim/02_normalized output and written back to the same file, same
pattern as rules 004-006.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
NORMALIZED_PATH = PROJECT_ROOT / "data" / "interim" / "02_normalized" / "titles_bracket_cleaned.jsonl"

# Verified against real usage context in data/raw/titles.jsonl (see module
# docstring); each entry is (Unicode radical name, target Hanzi).
RESIDUAL_RADICAL_MAP = {
    "⺠": "民",  # CJK RADICAL CIVILIAN         -- 民宿
    "⻁": "虎",  # CJK RADICAL TIGER             -- 白虎
    "⻄": "西",  # CJK RADICAL WEST TWO          -- 东西
    "⻅": "见",  # CJK RADICAL C-SIMPLIFIED SEE  -- 见到/撞见
    "⻆": "角",  # CJK RADICAL SIMPLIFIED HORN   -- 海角/视角
    "⻉": "贝",  # CJK RADICAL C-SIMPLIFIED SHELL -- 宝贝
    "⻋": "车",  # CJK RADICAL C-SIMPLIFIED CART -- 开车
    "⻓": "长",  # CJK RADICAL C-SIMPLIFIED LONG -- 长腿
    "⻔": "门",  # CJK RADICAL C-SIMPLIFIED GATE -- 出门/敲门
    "⻘": "青",  # CJK RADICAL BLUE              -- 青春
    "⻚": "页",  # CJK RADICAL C-SIMPLIFIED LEAF -- 首页
    "⻛": "风",  # CJK RADICAL C-SIMPLIFIED WIND -- 风险/雄风
    "⻜": "飞",  # CJK RADICAL C-SIMPLIFIED FLY  -- 双飞/飞机
    "⻝": "食",  # CJK RADICAL EAT ONE           -- 零食
    "⻢": "马",  # CJK RADICAL C-SIMPLIFIED HORSE -- 马桶/骑马
    "⻣": "骨",  # CJK RADICAL BONE              -- 骨头
    "⻤": "鬼",  # CJK RADICAL GHOST             -- 骗人的鬼
    "⻥": "鱼",  # CJK RADICAL C-SIMPLIFIED FISH -- 钓鱼
    "⻦": "鸟",  # CJK RADICAL C-SIMPLIFIED BIRD -- 大鸟
    "⻨": "麦",  # CJK RADICAL SIMPLIFIED WHEAT  -- 开麦（直播用语）
    "⻩": "黄",  # CJK RADICAL SIMPLIFIED YELLOW -- 黄片/黄网
    "⻬": "齐",  # CJK RADICAL C-SIMPLIFIED EVEN -- 齐插/齐开
    "⻰": "龙",  # CJK RADICAL C-SIMPLIFIED DRAGON -- 一龙二凤
}


CJK_RADICALS_SUPPLEMENT = range(0x2E80, 0x2F00)
KANGXI_RADICALS = range(0x2F00, 0x2FE0)


def normalize_text(text: str) -> str:
    # Per-character, and ONLY inside the two radical blocks -- a blanket
    # unicodedata.normalize("NFKC", text) over the whole string was tried
    # first and rejected: it also silently converts full-width Chinese
    # punctuation (， 。 （ ） ？ ：) to half-width ASCII punctuation, which
    # is normal, correct Chinese typography, not an error, and changing it
    # would be an unrelated, undocumented side effect on ~7,600 records
    # instead of the ~1,765 that actually have the radical-variant problem.
    out = []
    for ch in text:
        cp = ord(ch)
        if cp in CJK_RADICALS_SUPPLEMENT or cp in KANGXI_RADICALS:
            fixed = unicodedata.normalize("NFKC", ch)
            if fixed == ch:
                fixed = RESIDUAL_RADICAL_MAP.get(ch, ch)
            out.append(fixed)
        else:
            out.append(ch)
    return "".join(out)


def main() -> None:
    raw_sha256 = sha256_of(RAW_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if raw_sha256 != manifest["sha256"]:
        raise SystemExit("SHA-256 mismatch against data/raw/manifest.json; refusing to run.")

    rows = []
    with NORMALIZED_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    n_changed = 0
    out_rows = []
    for rec in rows:
        original = rec["cleaned_title"]
        normalized = normalize_text(original)
        if normalized != original:
            n_changed += 1
        out_rows.append(
            {
                **rec,
                "cleaned_title": normalized,
                "rule_id": rec["rule_id"] + "+cjk_radical_normalize",
                "rule_versions": {
                    **rec.get("rule_versions", {}),
                    "cjk_radical_variant_normalize": "v1.0-2026-07-31",
                },
            }
        )

    with NORMALIZED_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"input_sha256={raw_sha256}")
    print(f"total_records={len(out_rows)}")
    print(f"records_changed={n_changed}")
    print(f"output={NORMALIZED_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(NORMALIZED_PATH)}")


if __name__ == "__main__":
    main()
