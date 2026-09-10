"""Rules 001->002->004->005->006->008->012->013->014, applied to the
round1+round2 combined dedup layer
(data/interim/10_combined_deduplicated/titles_combined_deduplicated.jsonl,
103,743 records: 25,191 round1 + 78,552 round2).

This is NOT a reimplementation. Every rule's actual logic is imported
UNCHANGED from round1's own scripts (extract_bracket_content.py,
clean_titles_bracket_content.py, draft_bracket_coding.py,
brand_and_code_rule.py, apply_brand_code_rule.py,
apply_text_cleanup_rules.py, normalize_cjk_radical_variants.py,
apply_freetext_marketing_cleanup.py, apply_date_stamp_cleanup.py,
apply_truncation_marker_cleanup.py) and re-applied here to `original_title`
for every record in the wider round1+round2 pool, in the exact same order
round1 applies them (001 -> 002 -> 004 -> 005 -> 006 -> 008 -> 012 -> 013 ->
014). Rules 003/007/009/011/015/016 are explicitly out of scope this pass
(007/016 already computed by earlier scripts in this same directory; 003 is
a retracted no-op; 009/011/015 are record-level/prefix rules the user
deliberately deferred to a later pass -- see docs/research_log.md 2026-08-03
entry).

CRITICAL INVARIANT this script exists to satisfy: recomputing the pipeline
here for a round1 record must reproduce, byte-for-byte, the `cleaned_title`
round1 already has on disk in
data/interim/02_normalized/titles_bracket_cleaned.jsonl (equivalently
data/interim/04_deduplicated/titles_deduplicated.jsonl -- the two are
verified identical for `cleaned_title` on all 25,191 records). This is
verified separately by tests/test_full_cleanup_pipeline_combined.py, not
asserted inline here (asserting it here would make partial/debugging runs
impossible), but the design keeps it easy to check: every rule function
below with a "round1" code path is the literal imported round1 function,
never a hand-copied reimplementation.

round2 supplementary extensions (round2 records only, never round1):
audited directly against real round2 title text (see docs/cleaning_rules.md
rule 001 and rule 012 "round2 扩展说明" sections for the full audit
methodology, sample sizes, and reasoning behind every addition below).
Two extensions, both scoped to `data_round == "round2"` ONLY:

1. Rule 001 (bracket classification): round2 (specifically source_07/source_05) uses
   a 12th bracket style, white lenticular brackets "〖〗", to wrap creator
   nicknames the same way round1's already-covered 『』/「」 do -- round1's
   extract_bracket_content.py
   BRACKET_PATTERNS list never needed this bracket style because round1's
   corpus barely uses it (8 raw occurrences across 25,191 titles). Adding
   〖〗 to the shared BRACKET_PATTERNS list would retroactively change
   round1's own frozen cleaned_title for those handful of records, which
   would break the invariant above -- so this is applied ONLY when
   data_round == "round2", via BRACKET_PATTERNS_ROUND2 below, reusing
   classify()/resolve_nesting() unchanged.
2. Rule 012 (freetext marketing/decorative cleanup): 26 marketing-hype
   words found at meaningful frequency in round2 (封神/巅峰/劲爆/天花板/
   高能/神级/炸裂/必看/稀缺/爆棚/爆炸/满分/年度/绝版/顶流/无水印/原版/
   自购/私藏/热销/神作/超值/秒杀/惊爆/绝色/原创) and 5 decorative
   separator characters not covered by round1's EMOJI_RE (‖ U+2016, ▌
   U+258C, ㊙ U+3299, ／ U+FF0F fullwidth solidus, ｜ U+FF5C fullwidth
   vertical bar -- all confirmed via manual sample review to function as
   pure visual separators/hype adjectives, never unique content
   information). Every one of these also has a nonzero hit count in
   round1's OWN raw data (checked individually -- see cleaning_rules.md),
   so adding them to the shared FREETEXT_MARKETING_TERMS/EMOJI_RE constants
   would likewise retroactively change round1's frozen cleaned_title.
   Applied ONLY when data_round == "round2", as a pre-pass immediately
   before calling round1's own unmodified clean_freetext().

A third round2-only, but SITE-scoped (not data_round-scoped) extension was
added 2026-08-03, ahead of rule 001, not alongside the two above: source_08 AI
rewrite-prompt residue cleanup (`apply_source_08_ai_residue_cleanup.
clean_source_08_ai_residue()`). 58 source_08 records have `original_title` values
that are themselves LLM prompt-and-response scaffolding ("[draft text]。
重写后的标题： [real title]（注：...)") rather than a clean title -- see
docs/cleaning_rules.md rule 017. This runs on `rec["source_site"] ==
"source_08"` records only (round1 has no source_08 site, so this is inherently
round2-only) and is a no-op elsewhere. Unlike the two extensions above, it
produces the WORKING TEXT the 001-014 chain runs on (not a shared-constant
addition), so it necessarily runs first; `original_title` itself is left
untouched in the output record.

2026-08-03 also fixed a pre-existing bug affecting BOTH round1 and round2:
"福利姬" (a real identity/occupation term, not a marketing superlative) was
being shredded by the "福利" marketing-term strip in both bracket
classification (draft_bracket_coding.classify(), imported by rule 001 above)
and freetext marketing cleanup (apply_freetext_marketing_cleanup.
clean_freetext(), imported by rule 012 above). Both fixes are compound-word
protections made directly in round1's own, shared, unmodified-in-every-other-
respect source files -- see docs/cleaning_rules.md rules 001 and 012 for the
fix itself. Because this changes shared logic rather than adding a
round2-only branch, it also changes round1's OWN frozen `cleaned_title` for
every affected record -- round1's `data/interim/02_normalized/` outputs were
regenerated first, and the "byte-for-byte round1 invariant" above was
re-verified against that NEW round1 baseline (not the pre-fix one) after
this script re-ran. See docs/research_log.md 2026-08-03 "福利姬" entry.

Writes: overwrites
data/interim/10_combined_deduplicated/titles_combined_deduplicated.jsonl in
place, adding (never removing) fields: `title_after_bracket_rule`,
`cleaned_title`, `kept_spans`, `removed_spans`, `skipped_due_to_overlap`,
`removed_brand_code_spans`, `contains_cjk`, `rule_id`, `rule_versions`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREPROCESSING_DIR = Path(__file__).resolve().parent
if str(PREPROCESSING_DIR) not in sys.path:
    sys.path.insert(0, str(PREPROCESSING_DIR))

from extract_bracket_content import BRACKET_PATTERNS, sha256_of  # noqa: E402
from clean_titles_bracket_content import (  # noqa: E402
    KEEP_CATEGORIES,
    find_all_spans,
    resolve_nesting,
)
from clean_titles_bracket_content import WHITESPACE_RE as BRACKET_WHITESPACE_RE  # noqa: E402
from draft_bracket_coding import classify  # noqa: E402
from apply_brand_code_rule import strip_brand_and_code  # noqa: E402
from apply_text_cleanup_rules import (  # noqa: E402
    CJK_RE,
    apply_rule_004,
    apply_rule_005,
)
from apply_text_cleanup_rules import WHITESPACE_RE as TEXT_CLEANUP_WHITESPACE_RE  # noqa: E402
from normalize_cjk_radical_variants import normalize_text  # noqa: E402
from apply_freetext_marketing_cleanup import (  # noqa: E402
    FREETEXT_MARKETING_TERMS,
    clean_freetext,
)
from apply_date_stamp_cleanup import clean_date_stamps  # noqa: E402
from apply_truncation_marker_cleanup import clean_truncation_marker  # noqa: E402
from apply_source_08_ai_residue_cleanup import clean_source_08_ai_residue  # noqa: E402

COMBINED_DIR = PROJECT_ROOT / "data" / "interim" / "10_combined_deduplicated"
COMBINED_PATH = COMBINED_DIR / "titles_combined_deduplicated.jsonl"

# --- round2-only supplementary extensions, see module docstring ---

BRACKET_PATTERNS_ROUND2 = list(BRACKET_PATTERNS) + [
    ("〖〗", re.compile(r"〖([^〖〗]*)〗")),
]

ROUND2_EXTRA_MARKETING_TERMS = [
    "封神", "巅峰", "劲爆", "天花板", "高能", "神级", "炸裂", "必看", "稀缺",
    "爆棚", "爆炸", "满分", "年度", "绝版", "顶流", "无水印", "原版", "自购",
    "私藏", "热销", "超值", "秒杀", "惊爆", "绝色", "原创",
    # NOTE: "神作" was tested and REJECTED (not just skipped) -- see
    # docs/cleaning_rules.md rule 012 round2 扩展说明 "反例" note. Checked
    # all 13 distinct round2 titles containing “神作”: 12/13 are genuine
    # standalone hype, but 1/13 is a false-positive substring collision
    # straddling “大神” (identity word, kept) + “作品” (ordinary
    # word) -- stripping "神作" there would fuse "大" and "品" into a
    # meaningless two-character remnant. Same class of mistake round1's
    # own rule 002 caught and fixed for bare "天美"/"蜜桃" (see rule 002
    # docstring) -- dropped rather than accepting the collision.
]
ROUND2_DECORATIVE_SEPARATOR_RE = re.compile("[‖▌㊙／｜]")  # ‖ ▌ ㊙ ／ ｜

RULE_ID_CHAIN = (
    "bracket_content_categorization+brand_and_code_rule+text_cleanup_rules_004_005"
    "+cjk_radical_normalize+freetext_marketing_cleanup+date_stamp_cleanup"
    "+truncation_marker_cleanup"
)
RULE_VERSIONS = {
    "bracket_content_categorization": "v1.7+default-exclude-2026-08-03",
    "brand_and_code_rule": "v1.5-2026-08-04",
    "promotional_prefix_strip": "v1.0-2026-07-31",
    "html_entity_unescape": "v1.0-2026-07-31",
    "cjk_radical_variant_normalize": "v1.0-2026-07-31",
    "freetext_marketing_cleanup": "v1.1-2026-08-03",
    "date_stamp_cleanup": "v1.0-2026-07-31",
    "truncation_marker_cleanup": "v1.0-2026-07-31",
}
ROUND2_SUPPLEMENTARY_VERSION = "v1.0-2026-08-03"
SOURCE_08_AI_RESIDUE_CLEANUP_VERSION = "v1.0-2026-08-03"


def _find_all_spans_for(title: str, data_round: str) -> list[tuple[int, int, str, str]]:
    patterns = BRACKET_PATTERNS if data_round == "round1" else BRACKET_PATTERNS_ROUND2
    spans = []
    for style, pattern in patterns:
        for m in pattern.finditer(title):
            content = m.group(1).strip()
            if content == "":
                continue
            spans.append((m.start(0), m.end(0), style, content))
    spans.sort(key=lambda s: s[0])
    return spans


def apply_rule_001(title: str, data_round: str) -> dict:
    """Mirrors clean_titles_bracket_content.clean_title() exactly, with the
    only difference being which bracket-pattern list is used to find spans
    (round1 unchanged; round2 also recognizes "〖〗"). Nesting resolution
    and category classification are the imported, unmodified round1
    functions in both cases."""
    raw_spans = _find_all_spans_for(title, data_round)
    spans, crossing_overlap = resolve_nesting(raw_spans)
    if crossing_overlap:
        return {"cleaned_title": title, "kept_spans": [], "removed_spans": [], "skipped_due_to_overlap": True}

    pieces, kept, removed = [], [], []
    last_end = 0
    for start, end, style, content in spans:
        category, _matched_rule = classify(content)
        pieces.append(title[last_end:start])
        if category in KEEP_CATEGORIES:
            pieces.append(title[start:end])
            kept.append({"bracket_style": style, "content": content, "category": category})
        else:
            removed.append({"bracket_style": style, "content": content, "category": category})
        last_end = end
    pieces.append(title[last_end:])

    cleaned = BRACKET_WHITESPACE_RE.sub(" ", "".join(pieces)).strip()
    return {"cleaned_title": cleaned, "kept_spans": kept, "removed_spans": removed, "skipped_due_to_overlap": False}


def apply_rule_012(text: str, data_round: str) -> str:
    """round1 rows: literally round1's own clean_freetext(), unmodified.
    round2 rows: strip the 26 round2-only marketing words and replace the
    5 round2-only decorative separator characters with a space (same
    "space, not empty string" rule round1 uses for emoji, so segments on
    either side don't fuse together), THEN hand off to the same unmodified
    clean_freetext() for everything else (base marketing terms, emoji,
    quality suffixes, edge-punctuation trim loop)."""
    if data_round == "round2":
        for term in ROUND2_EXTRA_MARKETING_TERMS:
            text = text.replace(term, "")
        text = ROUND2_DECORATIVE_SEPARATOR_RE.sub(" ", text)
    return clean_freetext(text)


def process_record(rec: dict) -> dict:
    title = rec["original_title"]
    data_round = rec["data_round"]

    # --- source_08 AI-residue cleanup (round2 only, site-scoped), see
    #     apply_source_08_ai_residue_cleanup.py. Runs BEFORE rule 001 -- it is a
    #     no-op on ordinary titles (SOURCE_08_AI_RESIDUE_SIGNAL_RE gate) and
    #     only rewrites the ~58 source_08 records whose original_title itself
    #     contains leaked LLM rewrite-prompt scaffolding, so the rest of the
    #     001-014 chain runs on the real title instead of the prompt residue.
    #     `rec["original_title"]` in the OUTPUT record is left untouched;
    #     only the working text fed into the rest of the chain changes. ---
    working_title = title
    if rec.get("source_site") == "source_08":
        working_title = clean_source_08_ai_residue(title)

    # --- rule 001 ---
    r001 = apply_rule_001(working_title, data_round)
    title_after_bracket_rule = r001["cleaned_title"]

    # --- rule 002 ---
    cleaned, removed_brand_code_spans = strip_brand_and_code(title_after_bracket_rule)

    # --- rules 004 + 005 (+ whitespace normalize, matching
    #     apply_text_cleanup_rules.py's main loop exactly) ---
    cleaned, _hit_004 = apply_rule_004(cleaned)
    cleaned, _hit_005 = apply_rule_005(cleaned)
    cleaned = TEXT_CLEANUP_WHITESPACE_RE.sub(" ", cleaned).strip()

    # --- rule 006 (informational flag only, computed off original_title) ---
    contains_cjk = bool(CJK_RE.search(title))

    # --- rule 008 ---
    cleaned = normalize_text(cleaned)

    # --- rule 012 ---
    cleaned = apply_rule_012(cleaned, data_round)

    # --- rule 013 ---
    cleaned = clean_date_stamps(cleaned)

    # --- rule 014 ---
    cleaned = clean_truncation_marker(cleaned)

    # NOTE on the empty-title safety net (rule 018): it is deliberately NOT
    # applied here. This function's `cleaned_title` must stay byte-for-byte
    # identical to round1's own frozen rules-001-014 output in
    # data/interim/02_normalized/titles_bracket_cleaned.jsonl (the "round1
    # invariant" tests/test_full_cleanup_pipeline_combined.py checks) --
    # exactly the same reason rule 015 (organizer-prefix removal) is its own
    # separate downstream step for round1 rather than a retroactive edit to
    # this shared 001-014 chain. The empty-title fallback is applied at the
    # true final, analysis-ready stage of EACH pipeline instead: round1's
    # remove_organizer_prefix_noise.py (after its own rule 015 has run) and
    # combined's export_combined_review_and_analysis_views.py (when building
    # titles_for_analysis_combined.csv, since rule 015 itself is explicitly
    # out of scope for the combined layer -- see module docstring above).
    rule_versions = dict(RULE_VERSIONS)
    rule_id = RULE_ID_CHAIN
    if data_round == "round2":
        rule_versions["round2_supplementary_cleanup"] = ROUND2_SUPPLEMENTARY_VERSION
        rule_id = rule_id + "+round2_supplementary_cleanup"
    if rec.get("source_site") == "source_08":
        rule_versions["source_08_ai_residue_cleanup"] = SOURCE_08_AI_RESIDUE_CLEANUP_VERSION
        rule_id = rule_id + "+source_08_ai_residue_cleanup"

    return {
        **rec,
        "title_after_bracket_rule": title_after_bracket_rule,
        "cleaned_title": cleaned,
        "kept_spans": r001["kept_spans"],
        "removed_spans": r001["removed_spans"],
        "removed_brand_code_spans": removed_brand_code_spans,
        "skipped_due_to_overlap": r001["skipped_due_to_overlap"],
        "contains_cjk": contains_cjk,
        "rule_id": rule_id,
        "rule_versions": rule_versions,
    }


def main() -> None:
    rows = []
    with COMBINED_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    n_total = 0
    n_by_round = {"round1": 0, "round2": 0}
    n_changed_by_rule = {
        "000_source_08_ai_residue": 0,
        "001_bracket": 0,
        "002_brand_code": 0,
        "004_promo_prefix": 0,
        "005_html_unescape": 0,
        "008_radical_normalize": 0,
        "012_freetext_marketing": 0,
        "013_date_stamp": 0,
        "014_truncation_marker": 0,
    }
    n_became_empty = 0

    out_rows = []
    for rec in rows:
        n_total += 1
        n_by_round[rec["data_round"]] += 1
        title = rec["original_title"]

        working_title = title
        if rec.get("source_site") == "source_08":
            working_title = clean_source_08_ai_residue(title)
        if working_title != title:
            n_changed_by_rule["000_source_08_ai_residue"] += 1

        r001 = apply_rule_001(working_title, rec["data_round"])
        if r001["kept_spans"] or r001["removed_spans"]:
            n_changed_by_rule["001_bracket"] += 1
        stage1 = r001["cleaned_title"]

        stage2, removed_brand_code_spans = strip_brand_and_code(stage1)
        if removed_brand_code_spans:
            n_changed_by_rule["002_brand_code"] += 1

        stage3, hit_004 = apply_rule_004(stage2)
        if hit_004:
            n_changed_by_rule["004_promo_prefix"] += 1
        stage3b, hit_005 = apply_rule_005(stage3)
        if hit_005:
            n_changed_by_rule["005_html_unescape"] += 1
        stage3c = TEXT_CLEANUP_WHITESPACE_RE.sub(" ", stage3b).strip()

        stage4 = normalize_text(stage3c)
        if stage4 != stage3c:
            n_changed_by_rule["008_radical_normalize"] += 1

        stage5 = apply_rule_012(stage4, rec["data_round"])
        if stage5 != stage4:
            n_changed_by_rule["012_freetext_marketing"] += 1

        stage6 = clean_date_stamps(stage5)
        if stage6 != stage5:
            n_changed_by_rule["013_date_stamp"] += 1

        stage7 = clean_truncation_marker(stage6)
        if stage7 != stage6:
            n_changed_by_rule["014_truncation_marker"] += 1

        if title.strip() and not stage7:
            n_became_empty += 1
        # (empty-title fallback is intentionally NOT applied here -- see the
        # NOTE in process_record() above; it runs later, at each pipeline's
        # own true final/analysis-ready stage.)

        rule_versions = dict(RULE_VERSIONS)
        rule_id = RULE_ID_CHAIN
        if rec["data_round"] == "round2":
            rule_versions["round2_supplementary_cleanup"] = ROUND2_SUPPLEMENTARY_VERSION
            rule_id = rule_id + "+round2_supplementary_cleanup"
        if rec.get("source_site") == "source_08":
            rule_versions["source_08_ai_residue_cleanup"] = SOURCE_08_AI_RESIDUE_CLEANUP_VERSION
            rule_id = rule_id + "+source_08_ai_residue_cleanup"

        out_rows.append(
            {
                **rec,
                "title_after_bracket_rule": stage1,
                "cleaned_title": stage7,
                "kept_spans": r001["kept_spans"],
                "removed_spans": r001["removed_spans"],
                "removed_brand_code_spans": removed_brand_code_spans,
                "skipped_due_to_overlap": r001["skipped_due_to_overlap"],
                "contains_cjk": bool(CJK_RE.search(title)),
                "rule_id": rule_id,
                "rule_versions": rule_versions,
            }
        )

    with COMBINED_PATH.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"total_records={n_total}")
    print(f"by_round={n_by_round}")
    print("records_changed_per_rule:")
    for rule, cnt in n_changed_by_rule.items():
        print(f"  {rule}: {cnt}")
    print(f"records_became_empty_after_full_pipeline={n_became_empty}")
    print(f"output={COMBINED_PATH.relative_to(PROJECT_ROOT)} sha256={sha256_of(COMBINED_PATH)}")


if __name__ == "__main__":
    main()
