"""Small-scale tokenizer comparison on a stratified sample of cleaned titles.

Compares 4 schemes, per docs/research_protocol.md section 4 ("比较不同分词
器或字符n-gram方案... 自定义词典带来的变化"):

  A. char       -- character-level unigrams (no segmentation), the baseline
                   docs/ideation-challenge-insight-tree.md explicitly asks
                   to keep running in parallel rather than betting on one
                   segmenter.
  B. jieba_default -- jieba, default dictionary, precise mode.
  C. jieba_custom  -- jieba, default dictionary + custom_dict.txt
                      (build_custom_dict.py) loaded via suggest_freq/add_word
                      so domain terms are forced as atomic units.
  D. thulac     -- THULAC, a differently-trained segmenter (independent
                   evidence: does a different tool's default model agree
                   with jieba, or is jieba's behavior idiosyncratic?).

Only reads data/interim/04_deduplicated/titles_deduplicated.jsonl (already
produced) and data/interim/05_tokenized/custom_dict.txt (already built).
Writes per-method JSONL outputs and one side-by-side comparison file, all
under data/interim/05_tokenized/, for manual review.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import jieba
import thulac

try:
    from .extract_bracket_content import sha256_of
except ImportError:  # Direct script execution.
    from extract_bracket_content import sha256_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEDUP_PATH = PROJECT_ROOT / "data" / "interim" / "04_deduplicated" / "titles_deduplicated.jsonl"
CUSTOM_DICT_PATH = PROJECT_ROOT / "data" / "interim" / "05_tokenized" / "custom_dict.txt"
OUTPUT_DIR = PROJECT_ROOT / "data" / "interim" / "05_tokenized"

SAMPLE_SIZE_PER_SITE = 75
RANDOM_SEED = 20260731


def load_pool() -> list[dict]:
    rows = []
    with DEDUP_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["is_duplicate_canonical"] and rec["cleaned_title"].strip():
                rows.append(rec)
    return rows


def stratified_sample(rows: list[dict]) -> list[dict]:
    by_site: dict[str, list[dict]] = {}
    for r in rows:
        by_site.setdefault(r["source_site"], []).append(r)
    rng = random.Random(RANDOM_SEED)
    sample = []
    for site, site_rows in sorted(by_site.items()):
        rng.shuffle(site_rows)
        sample.extend(site_rows[:SAMPLE_SIZE_PER_SITE])
    rng.shuffle(sample)
    return sample


def tokenize_char(text: str) -> list[str]:
    return list(text.replace(" ", ""))


def build_jieba_custom():
    jb = jieba.Tokenizer()
    jb.initialize()
    with CUSTOM_DICT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                jb.add_word(parts[0], freq=int(parts[1]))
    return jb


def main() -> None:
    rows = load_pool()
    sample = stratified_sample(rows)

    jieba.initialize()
    jieba_custom = build_jieba_custom()
    thu = thulac.thulac(seg_only=True)

    methods = {}
    for rec in sample:
        text = rec["cleaned_title"]
        methods.setdefault(rec["record_id"], {})

    results_by_method: dict[str, list[dict]] = {"char": [], "jieba_default": [], "jieba_custom": [], "thulac": []}

    for rec in sample:
        text = rec["cleaned_title"]
        base = {"record_id": rec["record_id"], "source_site": rec["source_site"], "text": text}

        results_by_method["char"].append({**base, "tokens": tokenize_char(text)})
        results_by_method["jieba_default"].append({**base, "tokens": list(jieba.cut(text))})
        results_by_method["jieba_custom"].append({**base, "tokens": list(jieba_custom.cut(text))})
        thu_out = thu.cut(text, text=True)
        results_by_method["thulac"].append({**base, "tokens": thu_out.split()})

    for method, results in results_by_method.items():
        out_path = OUTPUT_DIR / f"sample_{method}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # side-by-side comparison file, one block per title, all 4 methods
    side_by_side_path = OUTPUT_DIR / "sample_side_by_side.txt"
    with side_by_side_path.open("w", encoding="utf-8") as f:
        for i in range(len(sample)):
            text = results_by_method["char"][i]["text"]
            site = results_by_method["char"][i]["source_site"]
            f.write(f"[{i}] ({site}) {text}\n")
            for method in ["jieba_default", "jieba_custom", "thulac"]:
                toks = results_by_method[method][i]["tokens"]
                f.write(f"  {method:>14}: {' / '.join(toks)}\n")
            f.write("\n")

    # quantitative summary stats
    def stats(results: list[dict]) -> dict:
        token_counts = [len(r["tokens"]) for r in results]
        all_tokens = [t for r in results for t in r["tokens"]]
        single_char = sum(1 for t in all_tokens if len(t) == 1)
        avg_token_len = sum(len(t) for t in all_tokens) / len(all_tokens)
        return {
            "n_titles": len(results),
            "avg_tokens_per_title": round(sum(token_counts) / len(token_counts), 2),
            "avg_token_length": round(avg_token_len, 2),
            "single_char_token_ratio": round(single_char / len(all_tokens), 3),
            "unique_token_types": len(set(all_tokens)),
        }

    summary = {method: stats(results) for method, results in results_by_method.items()}

    summary_path = OUTPUT_DIR / "comparison_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"sample_size={len(sample)}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for method in results_by_method:
        p = OUTPUT_DIR / f"sample_{method}.jsonl"
        print(f"{method}: {p.relative_to(PROJECT_ROOT)} sha256={sha256_of(p)}")
    print(f"side_by_side={side_by_side_path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
