"""Rule 012: strip marketing words, emoji, and quality/resolution suffixes
from the FULL title text -- not just bracket content.

Rule 001 already strips these same marketing words (精品/精选/福利/极品/...)
when they appear INSIDE brackets (see MARKETING_TERMS in
draft_bracket_coding.py), but that rule's scope is bracket content only. The
user found these words leaking through in large volume in the free-flowing
text OUTSIDE brackets too -- checked against
data/interim/04_deduplicated/titles_for_analysis.csv before writing this
rule: 极品 appears 2,074 times (8.5% of the analysis dataset), 福利 734,
精品 546, 最新 477, 力荐 401, 合集 351, 顶级 344, plus a long tail. This is
a much bigger gap than the emoji/quality-suffix issues found in the same
pass, and is fixed the same way rule 004 fixed the non-bracket "重磅福利>"
prefix: apply the SAME kind of stripping, just to the whole string instead
of only inside brackets.

Four sub-fixes, all validated by simulating on titles_for_analysis.csv
before being written here (see docs/cleaning_rules.md rule 012):

1. FREETEXT_MARKETING_TERMS -- the rule 001 MARKETING_TERMS list plus terms
   found leaking specifically in free text (最新/新品/震撼/上线), stripped
   as literal substrings anywhere in the title.
2. Emoji removal -- ❤️✅⚫️⭐⚡ etc. used as decorative bullets/separators
   (396 records). Range covers the emoji blocks plus variation selectors
   (U+FE00-FE0F) and zero-width joiners (U+200D) -- an earlier version that
   only stripped the base emoji codepoint left a stray invisible mark
   behind (e.g. "❤️" is U+2764 + U+FE0F; stripping only U+2764 left U+FE0F
   rendering as a visible dangling mark).
3. Quality/resolution suffix removal -- "_HD"/"高清"/"超清"/"720P"/"4K"
   etc. (99 records).
4. "国产av"/"国产AV" phrase removal (7 records) -- functions as a
   genre/category label here, not organic content, same treatment as the
   regional-origin+quality compounds already handled inside brackets.

Edge-punctuation cleanup after stripping is deliberately ASYMMETRIC: the
leading edge strips any punctuation (titles never genuinely start with
punctuation, so this is always safe), but the TRAILING edge only strips
connector-style characters (- _ · | 丨 > ： : and whitespace), NOT
sentence-final marks (。！？) -- an earlier version stripped trailing "？"
unconditionally and would have deleted a real sentence-final question mark
even though nothing was removed near it. The one accepted residual
imperfection from this asymmetry: a template tail can leave a trailing "!"
behind since "!" is treated as
sentence-final, not connector, punctuation -- documented as a known
limitation rather than special-cased, because guessing "was this ! really
part of the sentence or part of the removed tag" is not reliably decidable
from the string alone.

Applied on top of the current data/interim/02_normalized output and written
back to the same file, same pattern as rules 004-006 and 008.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "titles.jsonl"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
NORMALIZED_PATH = PROJECT_ROOT / "data" / "interim" / "02_normalized" / "titles_bracket_cleaned.jsonl"

# rule 001's MARKETING_TERMS (draft_bracket_coding.py) plus terms found
# leaking in free text specifically (validated against
# titles_for_analysis.csv before being added here).
FREETEXT_MARKETING_TERMS = [
    "精品", "精选", "福利", "推荐", "重磅", "独家", "优质", "资源", "合集",
    "专属", "限量", "首发", "特辑", "珍藏", "VIP", "会员", "免费", "今日",
    "爆款", "热门", "人气", "好片", "订制", "力荐", "首推", "全网", "顶级",
    "极品", "最新", "新品", "震撼", "上线",
]

EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U0000FE00-\U0000FE0F\U0000200D☀-➿⬀-⯿]"
)
# lookahead/lookbehind, not \b: Python's \w (and therefore \b) treats CJK
# ideographs as word characters, so \b fails to fire between e.g. "K" and
# an immediately-following "高" with no separator -- found via "4K高清"
# ("4K" was silently left unstripped because \b couldn't fire before "高").
QUALITY_SUFFIX_RE = re.compile(
    r"(?<![A-Za-z0-9])[_\-]?(HD|4K|FHD|1080P|720P|超清|高清)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
GUOCHAN_AV_RE = re.compile(r"[国國][产產][Aa][Vv]")
LEADING_EDGE_RE = re.compile(r"^[\-_·丨|>：:.。!！?？,，\s]+")
TRAILING_EDGE_RE = re.compile(r"[\-_·丨|>：:\s]+$")
WHITESPACE_RE = re.compile(r"\s+")


def clean_freetext(text: str) -> str:
    for term in FREETEXT_MARKETING_TERMS:
        if term == "福利":
            # Compound-word protection (2026-08-03, see docs/cleaning_rules.md
            # rule 012 "福利姬" fix): "福利姬" is a real research-relevant
            # identity/occupation category (a person, typically a student, who
            # films/sells sexually explicit self-produced content for money),
            # not a marketing superlative -- the user explained this
            # directly, with real round1 examples of "极品福利姬，可甜可盐
            # 女神" getting shredded to a lone "姬" once "福利" was blindly
            # stripped as a substring. Only protect the exact "福利姬"
            # sequence (negative lookahead, not a broader "starts with 福利"
            # guess) -- every other occurrence of "福利" (bare, or followed
            # by anything else, e.g. the "福利>" promo prefix or standalone
            # "福利") keeps being stripped exactly as before. This does not
            # remove "福利" from FREETEXT_MARKETING_TERMS -- it only changes
            # how THIS one term is matched.
            text = re.sub(r"福利(?!姬)", "", text)
        else:
            text = text.replace(term, "")
    text = GUOCHAN_AV_RE.sub("", text)
    # replace with a space, not "" -- emoji are frequently used as visual
    # separators between otherwise-unrelated segments, and deleting them
    # outright silently glues the two sides together (observed when an
    # alphabetic fragment and digits fused into a string that then looked like
    # a fake production code, "XM21", to the code-detection check).
    text = EMOJI_RE.sub(" ", text)
    # looped: back-to-back markers with no separator (e.g. "4K高清") only
    # fully clear after a second pass -- see QUALITY_SUFFIX_RE comment above.
    prev_q = None
    while prev_q != text:
        prev_q = text
        text = QUALITY_SUFFIX_RE.sub("", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    prev = None
    while prev != text:
        prev = text
        text = LEADING_EDGE_RE.sub("", text)
        text = TRAILING_EDGE_RE.sub("", text)
        text = text.strip()
    return text


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
    n_became_empty = 0
    out_rows = []
    for rec in rows:
        before = rec["cleaned_title"]
        after = clean_freetext(before)
        if after != before:
            n_changed += 1
        if before.strip() and not after:
            n_became_empty += 1
        out_rows.append(
            {
                **rec,
                "cleaned_title": after,
                "rule_id": rec["rule_id"] + "+freetext_marketing_cleanup",
                "rule_versions": {
                    **rec.get("rule_versions", {}),
                    "freetext_marketing_cleanup": "v1.1-2026-08-03",
                },
            }
        )

    with NORMALIZED_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"input_sha256={raw_sha256}")
    print(f"total_records={len(out_rows)}")
    print(f"records_changed={n_changed}")
    print(f"records_became_empty={n_became_empty}")
    print(f"output={NORMALIZED_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(NORMALIZED_PATH)}")


if __name__ == "__main__":
    main()
