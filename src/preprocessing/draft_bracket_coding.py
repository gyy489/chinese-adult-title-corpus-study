"""Draft (not final) categorization of bracket contents for human review.

Reads data/interim/01_profile/bracket_contents.tsv (already extracted,
read-only from the raw snapshot) and proposes a category for each unique
bracket-content string:

  structural_metadata   -- duration / video-count / resolution / episode marker
  content_descriptor    -- kinship, relationship, scenario, or act vocabulary
  marketing_boilerplate -- promotional/quality language with no content info
  mixed                 -- contains BOTH content and marketing vocabulary
                           (needs manual splitting, e.g. "全球首发妇女乱伦")
  undetermined          -- none of the keyword/regex rules fired

This is a draft coding aid, not a cleaning rule: nothing here is deleted or
applied to data/raw or data/processed. A human must confirm or correct the
suggested_category column before it can become a real cleaning rule under
docs/research_protocol.md section 3.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_TSV = PROJECT_ROOT / "data" / "interim" / "01_profile" / "bracket_contents.tsv"
OUTPUT_CSV = PROJECT_ROOT / "data" / "interim" / "01_profile" / "bracket_content_coding_draft.csv"
OUTPUT_ORGANIZED_TXT = PROJECT_ROOT / "data" / "interim" / "01_profile" / "bracket_contents_only.txt"

# --- structural metadata: matched against the WHOLE bracket content ---
STRUCTURAL_PATTERNS = [
    re.compile(r"^\d{1,3}:\d{2}$"),          # 05:27  duration
    re.compile(r"^\d+[Vv]$"),                # 6V 12V  clip/part count
    re.compile(r"^\d{3,4}[Pp]$"),            # 1080P 720P
    re.compile(r"^(4K|HD|FHD|高清|超清)$"),
    re.compile(r"^[上中下]$"),               # episode marker
    re.compile(r"^\d+$"),                    # bare number
    re.compile(r"^第?\d+[集话部弹]$"),
]

# --- content-descriptor vocabulary (kinship/relationship + scenario/act) ---
KINSHIP_TERMS = [
    "妈", "母", "爸", "父", "兄", "哥", "弟", "姐", "妹", "嫂", "叔", "伯",
    "姑", "姨", "舅", "爷", "奶", "岳", "婆", "媳", "继母", "继父", "干爹",
    "干妈", "女儿", "儿子", "老婆", "老公", "丈夫", "妻子", "小姨子", "小舅子",
    "师生", "同学", "老师", "员工", "老板", "秘书", "同事", "邻居",
]
SCENARIO_ACT_TERMS = [
    "乱伦", "偷拍", "泄密", "自拍", "约炮", "约啪", "足交", "口交", "颜射",
    "按摩", "探花", "外围", "偷情", "露出", "野战", "车震", "内射", "潮吹",
    "肛交", "SM", "调教", "勾引", "强奸", "迷奖", "迷药", "强迫", "凌辱",
    "按摩店", "浴室", "厕所", "办公室", "教室", "宿舍",
]

# Compound identity/occupation terms that must survive as whole units even
# though they contain a MARKETING_TERMS substring -- see docs/cleaning_rules.md
# rule 001 "福利姬" fix (2026-08-03). "福利姬" ("welfare girl") is a real
# research-relevant identity category (a person, typically a student, who
# films/sells sexually explicit self-produced content for money), not a
# marketing superlative like "极品"/"精品" -- the user explained this
# directly. Naively, bracket content equal to or containing "福利姬" would
# be misclassified as marketing_boilerplate (has_marketing=["福利"],
# has_content=[]) and discarded whole under the v1.3 default-exclude policy,
# destroying the identity term. Listing "福利姬" here as content gives it
# the same protection full kinship/scenario compounds like "继母"/"按摩店"
# already have: whenever CONTENT_TERMS matches, the classify() branch below
# folds straight to a KEEP category (content_descriptor/content_tag_4char)
# regardless of any co-occurring MARKETING_TERMS hit -- same precedent as
# v1.1's "mixed word" handling for e.g. "极品探花". Bare "福利" (not
# followed by "姬") is NOT added here and keeps being classified as
# marketing_boilerplate exactly as before -- this is a narrow compound-word
# protection, not a removal of "福利" from MARKETING_TERMS.
IDENTITY_ROLE_TERMS = ["福利姬"]

CONTENT_TERMS = KINSHIP_TERMS + SCENARIO_ACT_TERMS + IDENTITY_ROLE_TERMS

# --- marketing/promotional boilerplate (no content information) ---
MARKETING_TERMS = [
    "精品", "精选", "福利", "推荐", "重磅", "独家", "优质", "资源", "合集",
    "专属", "限量", "首发", "特辑", "珍藏", "VIP", "会员", "免费", "今日",
    "爆款", "热门", "人气", "好片", "订制", "力荐", "首推",
    "全网", "顶级", "极品",
]

# Regional/origin words: real descriptive information (where the content is
# claimed to be from) but also function as a marketing/exoticism cue. Kept
# as its own open category rather than folded into content or marketing --
# see docs/cleaning_rules.md for the unresolved judgment call.
REGIONAL_TERMS = ["国产", "欧美", "日韩", "海外", "国内", "亚洲", "东南亚", "本土"]

# Creator/series handles: proper nouns (usernames, cosplay/series brand
# names) that are neither content-descriptive vocabulary nor promotional
# language. Only the high-precision Latin-script heuristic is automated;
# Chinese-name-like handles are left in
# `undetermined` for human judgment rather than guessed at.
HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")

# v1.2: within content_descriptor, exactly-4-character entries turned out to
# be dominated by a combinatorial tag template (relationship/descriptor word
# + act/genre word, e.g. 兄妹+乱伦, 真实+偷拍, 极品+探花) rather than organic
# title prose -- length==4 terms are 33% of unique content_descriptor terms
# but 55% of their total occurrences, vs. a flat ~1-2x average elsewhere.
# These are split into their own bucket: useful as a categorical tag, but not
# analyzed as natural-language title text. See docs/cleaning_rules.md rule 001.
TAG_LENGTH = 4

# v1.6 (2026-08-03): the content_tag_4char bucket above was found to also be
# discarding the single most direct evidence for this project's core research
# variable ("how do titles turn family/occupational identity into different
# harm scripts"). content_tag_4char is NOT in clean_titles_bracket_content's
# KEEP_CATEGORIES, so anything routed there is stripped from cleaned_title
# entirely -- and the "relationship word + act/genre word" template that
# earned this bucket its name (see v1.2 note above) turns out to include
# exactly the kinship+harm compounds (母子乱伦/父女乱伦/兄妹乱伦/...) this
# research question is about. Manual audit (KINSHIP_TERMS cross-checked
# term-by-term against cleaned_title) found 187 round1 + 70 round2 records
# whose 4-char kinship tag left literally no trace in cleaned_title.
#
# The same audit also confirmed the bucket is NOT uniformly wrong: a
# separate set of 4-character creator/streamer handles or nicknames are not narrated
# relationships, and correctly belong in content_tag_4char (excluded).
#
# Fix: a 4-char span is only rescued out of content_tag_4char into
# content_descriptor (i.e. kept in cleaned_title like any other free-text
# content descriptor) when it contains BOTH (a) a KINSHIP_TERMS word AND (b)
# a word from the precise HARM_RELATIONSHIP_VERBS list below. Handle-type
# tags (母/姐/哥/... paired only with a bare surname/nickname/生活场景 word,
# no harm-relationship verb) still fall through to content_tag_4char
# unchanged -- this is a narrow, targeted override, not a loosening of the
# length==4 rule itself or a re-litigation of the v1.2 rationale.
#
# Known, accepted residual gap (deliberately NOT chased by expanding this
# list): uncommon differently-structured phrases such as "父债女偿" (父+女
# +债+偿-- no HARM_RELATIONSHIP_VERBS hit) or "与父同行" (父+同行 -- no
# hit) will NOT be rescued by this rule and remain in content_tag_4char
# (excluded). This is an intentional, bounded precision-over-recall choice,
# not an oversight -- see docs/cleaning_rules.md rule 001 v1.6.
# v1.7 (2026-08-04): added "强奸"/"强上"/"睡奸" -- three more harm-relationship
# verbs the user identified by name (see docs/cleaning_rules.md rule 001 v1.7
# update). Same mechanism as v1.6: only widens which VERB half of the
# kinship+harm pair counts, does not touch KINSHIP_TERMS or the override
# logic itself. Confirmed against the full round1+round2 corpus that this
# rescues "强奸大嫂"/"强奸表妹"/"强上亲姐"/"睡奸嫂子"-type 4-char tags into
# content_descriptor (kept) exactly like the v1.6 verbs already do for
# "乱伦"/"偷情"/etc.
HARM_RELATIONSHIP_VERBS = [
    "乱伦", "偷情", "淫乱", "通奸", "出轨", "迷奸", "绿帽", "淫秽", "苟合",
    "私通", "偷欢", "母狗", "骚货", "强奸", "强上", "睡奸",
]


def _kinship_harm_override(content: str) -> tuple[bool, str, str]:
    """Return (True, matched_kinship_term, matched_harm_term) iff `content`
    contains at least one KINSHIP_TERMS word AND at least one
    HARM_RELATIONSHIP_VERBS word. See the v1.6 note above TAG_LENGTH."""
    kinship_hits = [t for t in KINSHIP_TERMS if t in content]
    harm_hits = [t for t in HARM_RELATIONSHIP_VERBS if t in content]
    if kinship_hits and harm_hits:
        return True, kinship_hits[0], harm_hits[0]
    return False, "", ""


def _split_by_length(content: str, note: str) -> tuple[str, str]:
    if len(content) == TAG_LENGTH:
        overridden, kinship_hit, harm_hit = _kinship_harm_override(content)
        if overridden:
            return "content_descriptor", f"{note} kinship_harm_override=({kinship_hit}+{harm_hit})"
        return "content_tag_4char", note
    return "content_descriptor", note


def classify(content: str) -> tuple[str, str]:
    for pat in STRUCTURAL_PATTERNS:
        if pat.match(content):
            return "structural_metadata", f"regex:{pat.pattern}"

    if HANDLE_RE.match(content):
        return "creator_or_series_handle", "latin_script_token"

    has_content = [t for t in CONTENT_TERMS if t in content]
    has_marketing = [t for t in MARKETING_TERMS if t in content]
    has_regional = [t for t in REGIONAL_TERMS if t in content]

    if has_content and (has_marketing or has_regional):
        # v1.1: no longer kept as a separate "mixed" bucket -- the content
        # word is real signal, so fold straight into content_descriptor and
        # just keep the marketing/regional words visible in matched_rule for
        # anyone who later wants to strip them at the token level.
        base_note = f"content={has_content[:3]} (also_marketing={has_marketing[:2]} also_regional={has_regional[:2]})"
        return _split_by_length(content, base_note)
    if has_content:
        return _split_by_length(content, f"content={has_content[:3]}")
    if has_regional:
        return "regional_origin", f"regional={has_regional[:3]}"
    if has_marketing:
        return "marketing_boilerplate", f"marketing={has_marketing[:3]}"
    return "undetermined", ""


def main() -> None:
    rows = []
    with INPUT_TSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for r in reader:
            category, matched = classify(r["content"])
            rows.append(
                {
                    "count": int(r["count"]),
                    "content": r["content"],
                    "suggested_category": category,
                    "matched_rule": matched,
                    "example_site": r["example_site"],
                    "example_title": r["example_title"],
                    "human_confirmed_category": "",  # for the user to fill in
                }
            )

    rows.sort(key=lambda r: (-r["count"], r["content"]))

    # --- organized plain-content file, replacing the earlier flat list ---
    SECTION_TITLES = {
        "content_descriptor": "内容描述——自由文本（保留，进入语言分析；含原混合词，词内仍可能夹杂促销/地域修饰词，见 matched_rule 列）",
        "content_tag_4char": "内容描述——4字类目标签词（建议拆到独立分类字段，不进入标题正文的语言分析，见 docs/cleaning_rules.md 规则001 v1.2）",
        "regional_origin": "地域来源词——未定（可能是内容信息，也可能是促销/猎奇话术，见 docs/cleaning_rules.md）",
        "structural_metadata": "结构元数据——时长/作品数/分辨率/集数（建议拆到独立字段，不进入文本分析）",
        "creator_or_series_handle": "创作者/系列代号（建议拆到独立字段，不进入文本分析）",
        "marketing_boilerplate": "促销套话（建议从内容分析文本中剔除）",
        "undetermined": "待人工判断——自动规则未命中，交给你自己看",
    }
    SECTION_ORDER = [
        "content_descriptor",
        "content_tag_4char",
        "regional_origin",
        "structural_metadata",
        "creator_or_series_handle",
        "marketing_boilerplate",
        "undetermined",
    ]
    with OUTPUT_ORGANIZED_TXT.open("w", encoding="utf-8") as f:
        for cat in SECTION_ORDER:
            section_rows = [r for r in rows if r["suggested_category"] == cat]
            f.write(f"===== {SECTION_TITLES[cat]}（{len(section_rows)} 项）=====\n")
            for r in section_rows:
                f.write(r["content"] + "\n")
            f.write("\n")

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "count",
                "content",
                "suggested_category",
                "matched_rule",
                "example_site",
                "example_title",
                "human_confirmed_category",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    from collections import Counter

    by_category_terms = Counter(r["suggested_category"] for r in rows)
    by_category_occurrences = Counter()
    for r in rows:
        by_category_occurrences[r["suggested_category"]] += r["count"]
    total_occ = sum(r["count"] for r in rows)

    print(f"unique_terms={len(rows)}  total_occurrences={total_occ}")
    print("--- by unique term count ---")
    for cat, n in by_category_terms.most_common():
        print(f"  {cat}: {n} terms")
    print("--- by total occurrences (weighted) ---")
    for cat, n in by_category_occurrences.most_common():
        print(f"  {cat}: {n} occurrences ({round(100*n/total_occ,1)}%)")
    print(f"output_csv: {OUTPUT_CSV.relative_to(PROJECT_ROOT)}")
    print(f"output_organized_txt: {OUTPUT_ORGANIZED_TXT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
