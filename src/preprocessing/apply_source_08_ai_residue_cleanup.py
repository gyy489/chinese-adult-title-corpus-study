"""round2-only rule (source_08 site): strip LLM rewrite-prompt scaffolding text
that leaked into `original_title` at the source.

Discovered while auditing round2 bracket content for rule 001 (see
docs/cleaning_rules.md rule 001 "round2 扩展说明", 2026-08-03 entry): source_08's
backend apparently rewrites/generates titles with an LLM, and in a minority
of records the raw prompt-and-response exchange itself -- not just the final
title -- got scraped into the `title` field. The general shape is:

    [original draft text the LLM was given] 。 重写后的标题： [the actual
    title the site meant to publish] （注：...meta-commentary...）

i.e. noise BEFORE a "rewritten title" marker, the real title, then optional
parenthetical "（注：...)" annotations (and occasionally more than one
marker/annotation pair chained back to back, or the annotation embedding the
marker itself with the real title following the annotation instead of the
marker). This rule keeps only the real title and discards the rest.

**Validated against the full, exact set of affected records, not a sample.**
`SOURCE_08_AI_RESIDUE_SIGNAL_RE` below (used both as the gate for whether to run
extraction at all, and as the discovery query) was checked against all 7,399
source_08 records in `data/interim/10_combined_deduplicated/
titles_combined_deduplicated.jsonl` (2026-08-03 snapshot) and matched exactly
**58** records (0.78% of the site) -- matching the count independently found
during the rule 001 round2 audit ("58 条", see cleaning_rules.md). All 58 were
read individually and the extraction below was iterated until every one
produced a clean result (verified: none of the 58 outputs still contain any
of the marker/annotation vocabulary below). This is NOT the same 21-title set
noted separately in the rule 001 log entry where source_08 titles look
AI-generated (verbose, repetitive hype-word lists) but carry no explicit
scaffolding marker -- the user explicitly decided not to chase that broader,
undecidable "style" pattern; this rule only removes titles with an unambiguous
literal marker/annotation trace.

Distinct marker phrasings actually observed across the 58 (not an exhaustive
a-priori guess -- this is the full list, see MARKER_ALTERNATIVES): "重写后的
标题"/"重写后标题"/"重写后的标题名"（optionally followed by a parenthetical
qualifier like "（不显示标签）"/"（35个字）"）/"重写后的描述"/"最终输出"/
"最终答案"/"修正后输出"/"修改后的版本"/"以下是符合要求的重写标题名"/"只输出
重写后的标题名且不显示标签" (this last one has no colon -- the real title
follows directly after a space. Annotation blocks open with
"（注"/"(注意" or, in one case, "（修正后，" (a second, differently-worded
annotation chained after a first "（注...)" one in the same title) -- both
trigger words are covered by ANNOTATION_OPEN below.

Three structural variants required different handling, all implemented here:

1. **Marker directly followed by the real title** (the majority: ~40/58) --
   e.g. "...重写后的标题： 真正的标题". Straightforward: take the text after
   the LAST such marker.
2. **Marker embedded INSIDE an annotation, real title follows the annotation
   instead** -- e.g. "...（注：根据要求，只输出重写后的标题名且不显示标签）
   真正的标题" (no separate marker before the real title; the marker is only
   mentioned, in passing, as part of the note explaining what was done).
   Handled by treating the position right after every annotation's closing
   "）" as an additional candidate split point, not just marker endpoints.
3. **Chained/repeated marker+annotation pairs, sometimes ending in a
   genuinely truncated fragment** (the raw source text itself cuts off
   mid-word for 2 of the 58, e.g. "...最终输出： 地铁少" -- nothing more
   follows in `original_title`, this is a data-truncation artifact at the
   source, not something this function can recover). Handled by always
   preferring the RIGHTMOST candidate split point whose resulting tail is
   both non-trivial (>=6 characters) and not itself just another marker
   label with nothing real after it, falling back through earlier
   marker/annotation occurrences until one qualifies.

**Known accepted residual limitation** (8/58 records): when a title contains
an early, more verbose "draft" attempt immediately followed by a shorter
"final" restatement with NO marker or annotation separating the two (the
annotation, if any, comes only at the very end and refers back to "the
rewritten title above" without repeating it), there is no reliable
machine-checkable boundary between the draft and the final restatement.
Rather than guess (and risk cutting off real content -- an earlier version of
this function that tried a "trailing comma-run" heuristic for this case was
caught, during validation, cutting a real final title in half), this function
falls back to keeping BOTH the draft and the restatement concatenated. Every
marker/annotation/meta-commentary PHRASE is still fully removed in these
cases (verified: none of the 58 outputs contain scaffolding vocabulary) --
only the redundant-but-real draft text is left in, which is a strictly safer
failure mode than deleting real content. Affected examples are documented in
the validation run (`docs/cleaning_rules.md` rule 017).
"""

from __future__ import annotations

import re

# --- gate: only run extraction on titles that actually show a literal
# scaffolding trace. Validated to match exactly 58/7,399 source_08 records in
# the 2026-08-03 snapshot -- see module docstring. ---
SOURCE_08_AI_RESIDUE_SIGNAL_RE = re.compile(
    "|".join(
        re.escape(s)
        for s in [
            "重写后", "最终输出", "最终答案", "修正后", "修改后的版本",
            "（注", "(注", "请注意", "以下是符合", "只输出", "根据您的要求",
            "根据要求", "已经删除了", "已去除特殊字符", "拓展为", "禁止出现",
            "字数", "个字", "融入标签", "融入2个标签", "融入两个标签", "标签名",
        ]
    )
)

# --- annotation blocks: "（注：...)" / "(注意：...)" / "（修正后，...)" --
# always pure meta-commentary noise, wherever they occur in the string.
_ANNOTATION_OPEN = r"[（(]\s*(?:注[意]?|修正后)[：:，,]?"
_CLOSED_ANNOTATION_RE = re.compile(_ANNOTATION_OPEN + r"[^）)]*[）)]")
# an annotation opened but never closed before the string ends -- the raw
# source data is truncated (see docstring point 3); drop the dangling
# fragment rather than leave a half-written note in the output.
_TRAILING_UNCLOSED_ANNOTATION_RE = re.compile(_ANNOTATION_OPEN + r"[^）)]*$")
# an unbracketed trailing meta-commentary sentence ("请注意，我已经..."),
# stops at the first sentence-final "。" so it does not eat real content that
# happens to follow later in the same string (see "呼伦贝尔学院" case in the
# validation run, where this phrase appears mid-string, not at the end).
_TRAILING_QINGZHUYI_RE = re.compile(r"请注意，我已经[^。]*。?")

_MARKER_ALTERNATIVES = [
    "只输出重写后的标题名且不显示标签",
    "以下是符合要求的重写标题名",
    "最终重写后的标题名",
    "重写后的标题名",
    "重写后的标题",
    "重写后标题",
    "重写后的描述",
    "最终输出",
    "最终答案",
    "修正后输出",
    "修改后的版本",
]
_MARKER_RE = re.compile(
    "(?:" + "|".join(re.escape(m) for m in _MARKER_ALTERNATIVES) + ")"
    r"(?:[（(][^）)]*[）)])?"  # optional qualifier right after the label, e.g. "（不显示标签）"
    r"\s*[：:，,]?\s*"
)
_MARKER_ONLY_RE = re.compile(
    r"^(?:" + "|".join(re.escape(m) for m in _MARKER_ALTERNATIVES) + r")$"
)
_WHITESPACE_RE = re.compile(r"\s+")

_MIN_TAIL_LEN = 6  # a real title candidate must be at least this long


def _strip_annotations(text: str) -> tuple[str, list[int]]:
    """Replace every closed annotation block with a single space (never
    delete-and-fuse -- mirrors round1's emoji-handling precedent), tracking
    the end offset of each replacement in the NEW string. Those offsets are
    additional candidate split points: the real title sometimes follows an
    annotation directly, with no separate marker (structural variant 2 in
    the module docstring)."""
    out: list[str] = []
    gap_ends: list[int] = []
    last = 0
    for m in _CLOSED_ANNOTATION_RE.finditer(text):
        out.append(text[last:m.start()])
        out.append(" ")
        gap_ends.append(sum(len(p) for p in out))
        last = m.end()
    out.append(text[last:])
    return "".join(out), gap_ends


def _clean_tail(text: str, pos: int) -> str:
    tail = text[pos:].strip()
    # peel any marker label(s) the tail itself STARTS with -- happens when
    # the chosen split point is immediately followed by another, separately
    # matched marker occurrence.
    m = _MARKER_RE.match(tail)
    while m:
        tail = tail[m.end():].strip()
        m = _MARKER_RE.match(tail)
    # strip a DANGLING trailing marker occurrence: one whose own tail is too
    # short to be a real title (a truncated or superseded later rewrite
    # attempt appended after the real one -- structural variant 3).
    while True:
        ms = list(_MARKER_RE.finditer(tail))
        if not ms:
            break
        last_m = ms[-1]
        after = tail[last_m.end():].strip()
        if len(after) < _MIN_TAIL_LEN:
            tail = tail[:last_m.start()].rstrip()
        else:
            break
    return _WHITESPACE_RE.sub(" ", tail).strip()


def clean_source_08_ai_residue(title: str) -> str:
    """Strip LLM rewrite-prompt scaffolding from a source_08 title. A no-op
    (returns `title` unchanged) unless SOURCE_08_AI_RESIDUE_SIGNAL_RE matches --
    callers should still gate on `source_site == "source_08"` themselves (this
    function does not know the site), since the signal words alone are not
    guaranteed unique to source_08 residue in a wider corpus."""
    if not SOURCE_08_AI_RESIDUE_SIGNAL_RE.search(title):
        return title

    text = title
    text = _TRAILING_UNCLOSED_ANNOTATION_RE.sub("", text)
    text = _TRAILING_QINGZHUYI_RE.sub("", text)
    text, gap_ends = _strip_annotations(text)

    marker_ends = [m.end() for m in _MARKER_RE.finditer(text)]
    candidates = sorted(set(gap_ends) | set(marker_ends))

    for pos in reversed(candidates):
        tail = _clean_tail(text, pos)
        if len(tail) >= _MIN_TAIL_LEN and not _MARKER_ONLY_RE.match(tail):
            return tail

    # No candidate split point produced a usable tail (e.g. a trailing
    # marker label with nothing after it, right after an annotation, and no
    # earlier marker either -- see module docstring "known accepted residual
    # limitation"). Fall back to everything before the last candidate, with
    # marker labels stripped out of it, rather than dropping real content.
    if candidates:
        head = text[:candidates[-1]].strip()
        head = _MARKER_RE.sub(" ", head).strip()
        return _WHITESPACE_RE.sub(" ", head).strip()

    return _WHITESPACE_RE.sub(" ", text).strip()
