"""General, site-agnostic safety net against a fully (or near-fully) emptied
`cleaned_title`.

If a record's FINAL `cleaned_title` -- after every other cleaning rule in the
chain has already run -- is empty or shorter than `MIN_KEPT_LENGTH`
characters while its `original_title` is not itself empty, that is a signal
that the cleaning chain stripped the record's ENTIRE informative content as
noise, not evidence the record genuinely has no content. This module is
deliberately imported and invoked as the LAST step of each of the two
independent pipelines in this project (round1: `remove_organizer_prefix_
noise.py`, after rule 015 -- the last rule that still mutates `cleaned_title`
for round1 -- has already run; round2/combined:
`apply_full_cleanup_pipeline_combined.py`, after rule 014, the last rule in
that chain, has already run), so it always sees whichever `cleaned_title`
each pipeline itself treats as final.

Found via (see docs/cleaning_rules.md rule 018 and docs/research_log.md
2026-08-04 entry): source_07 and source_08 titles are frequently wrapped ENTIRELY in
a single "《》" bracket pair; when that bracket's content does not happen to
contain any KINSHIP_TERMS/SCENARIO_ACT_TERMS/MARKETING_TERMS/REGIONAL_TERMS
keyword, `draft_bracket_coding.classify()` falls through to "undetermined",
which `clean_titles_bracket_content.KEEP_CATEGORIES` excludes under the v1.3
default-exclude policy -- stripping the record's only text down to nothing.
But the underlying failure mode is GENERAL, not specific to those two sites
or that one code path -- audited examples found in the full round1+round2
corpus during this fix also include: an entire title that is nothing but a
brand name + catalog code (rule 002 leaves nothing behind); a bracket whose
content is only a regional/marketing word ("欧美精选"); and round1's own
rule 015 (`remove_organizer_prefix_noise.py`) stripping an organizer-added
prefix + trailing number that, for a handful of records, turns out to BE the
entire title (a "newly_empty" case that script already counted but never
protected against). Rather than special-case every site or every rule that
can produce this, this module is deliberately generic: it only looks at the
before/after title strings, not which rule or site caused the emptying, so
it also covers failure modes not yet discovered.

Fallback preference order, applied only when triggered:
  1. Bracket content pulled directly from `original_title` -- in the
     observed failure mode the entire meaningful title text usually WAS
     inside a bracket pair, so this is the more surgical recovery: it
     restores what the bracket said without re-admitting brand names / promo
     boilerplate that sat OUTSIDE the bracket and were correctly stripped
     for an unrelated, still-valid reason.
  2. The complete, untouched `original_title` -- used when there is no
     bracket content to recover (or the bracket content itself is still
     empty/too short), e.g. rule 015's organizer-prefix case, which has no
     brackets involved at all.

Every record this fires on is marked `empty_title_fallback_applied=True` and
`empty_title_fallback_source` ("bracket_content" / "full_original_title") so
it can be found and audited later; the pre-fallback (empty/near-empty) value
is preserved in `cleaned_title_before_empty_title_fallback` for full
reversibility -- the same convention every other rule in this pipeline
already follows (e.g. rule 015's own `cleaned_title_before_organizer_
prefix_rule`).
"""

from __future__ import annotations

import re

try:
    from .extract_bracket_content import BRACKET_PATTERNS
except ImportError:  # Direct script execution.
    from extract_bracket_content import BRACKET_PATTERNS

# Same 12th bracket style apply_full_cleanup_pipeline_combined.py adds for
# round2 records only (source_07/source_05 use white lenticular brackets "〖〗" for
# creator handles/nicknames the same way round1's covered styles do).
# Included here UNCONDITIONALLY (not gated on data_round) because this
# fallback is intentionally round/site-agnostic by design -- see module
# docstring. This does not retroactively change any round1 record's
# `cleaned_title` computed by the earlier rules (those are untouched); it
# only widens what THIS module can recover FROM, and only for records that
# already reached this fallback with an empty/near-empty cleaned_title.
# Verified against the full round1+round2 corpus: zero round1 records both
# use "〖〗" AND trigger this fallback, so in practice this addition only
# ever matters for round2 records -- see research_log.md 2026-08-04 entry.
FALLBACK_BRACKET_PATTERNS = list(BRACKET_PATTERNS) + [
    ("〖〗", re.compile(r"〖([^〖〗]*)〗")),
]

MIN_KEPT_LENGTH = 2

WHITESPACE_RE = re.compile(r"\s+")


def _extract_bracket_fallback_text(original_title: str) -> str:
    """Pull every bracket span's content out of `original_title` (any of the
    12 known bracket styles, nesting/overlap aside -- unlike rule 001 this is
    a best-effort salvage operation, not a precise reversible transform, so
    it does not need resolve_nesting()'s crossing-overlap safety net) and
    join them in left-to-right order with a single space."""
    spans = []
    for _style, pattern in FALLBACK_BRACKET_PATTERNS:
        for m in pattern.finditer(original_title):
            content = m.group(1).strip()
            if content:
                spans.append((m.start(0), content))
    spans.sort(key=lambda s: s[0])
    return WHITESPACE_RE.sub(" ", " ".join(c for _, c in spans)).strip()


def apply_empty_title_fallback(
    original_title: str, cleaned_title: str, min_len: int = MIN_KEPT_LENGTH
) -> dict:
    """Return a dict with the (possibly fallback-rescued) `cleaned_title`
    plus audit fields. A no-op (fallback NOT applied, `cleaned_title` passed
    through unchanged) unless `cleaned_title` is empty or shorter than
    `min_len` characters (after stripping whitespace) while `original_title`
    itself is not."""
    original_title = original_title or ""
    cleaned_title = cleaned_title or ""

    if not original_title.strip() or len(cleaned_title.strip()) >= min_len:
        return {
            "cleaned_title": cleaned_title,
            "cleaned_title_before_empty_title_fallback": "",
            "empty_title_fallback_applied": False,
            "empty_title_fallback_source": "",
        }

    bracket_text = _extract_bracket_fallback_text(original_title)
    if len(bracket_text) >= min_len:
        fallback_text, source = bracket_text, "bracket_content"
    else:
        fallback_text, source = original_title.strip(), "full_original_title"

    return {
        "cleaned_title": fallback_text,
        "cleaned_title_before_empty_title_fallback": cleaned_title,
        "empty_title_fallback_applied": True,
        "empty_title_fallback_source": source,
    }
