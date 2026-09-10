# Unequal Target–Actor Visibility in Chinese-Language Adult-Video Titles

[![Release safety and reproducibility](https://github.com/gyy489/chinese-adult-title-corpus-study/actions/workflows/ci.yml/badge.svg)](https://github.com/gyy489/chinese-adult-title-corpus-study/actions/workflows/ci.yml)

Public research companion for an AI-assisted quantitative content analysis of
Chinese-language adult-video title metadata. This repository exposes the final
research pipeline node by node: collection, cleaning, de-identification,
sampling, annotation validation, final deduplication, privacy remediation,
human-review aggregation, analysis, and fail-closed publication.

[中文说明](README.md)

## Abstract

Adult-video titles are public-facing metadata that may organize visibility
within distribution narratives by selectively presenting some participant
positions while leaving others unexpressed. Although pornography research has
extensively examined gender representation, objectification, and agency,
participant visibility in Chinese-language adult-video titles remains
understudied. This study used quantitative content analysis to examine the
allocation of visibility between targets and actors. The analytical sample
comprised 38,298 unique final de-identified title texts selected through a
pre-specified, fixed-seed stratified procedure with a smaller
mechanism-enriched component. A 42-field coding instrument supported
AI-assisted analysis, and the primary question was assessed among 4,186
eligible titles. At the distribution stage, visibility was not evenly allocated
but was concentrated in the target position: targets were visible in 3,967
titles (94.77%), whereas actors were visible in only 1,053 (25.16%), a
difference of 69.61 percentage points. In a separate analysis of
distribution-target composition, strictly feminine-coded positions formed the
largest category (64.55%). The findings document, within this retained
analytical sample, an unequal pattern of participant visibility and show how a
relational target–actor comparison can complement gender-word-frequency
analysis. Adult-video titles cannot establish video content or participants'
actual identities; future research can test the pattern's generalizability and
interpretive consequences across platforms, languages, and audiences.

## Project at a glance

| Item | Released description |
| --- | ---: |
| Source records collected | 103,743 |
| Unique texts in the candidate frame | 89,161 |
| Valid record-level AI outputs | 38,623 |
| Final unique analytical texts | 38,298 |
| Probability-track texts | 36,303 |
| Mechanism-enriched texts | 1,995 |
| Eligible texts for the primary comparison | 4,186 |
| Annotation fields | 42 |
| Independently reviewed titles used to assess AI accuracy | 400 |

The analytical unit is one unique final de-identified title text, not a source
record, film, scene, event, or person. Results describe the retained analytical
sample and are not population estimates for all Chinese-language adult-video
titles.

## What is included

- The final configurable collection engine: selectors, pagination, domain
  guards, HTTP/Playwright modes, resumption, MySQL storage, and export.
- An auditable preprocessing engine, semantic-length gate, exact-final-text
  deduplication, and public-safe synthetic rule configuration.
- Fixed-seed stratified sampling and a separately labelled
  mechanism-enriched component.
- The complete frozen v0.3 prompt, codebook, 42-field JSON Schema, and layered
  semantic/evidence validators.
- Exact-span privacy remediation, mandatory post-remediation deduplication, and
  a direct-locator release gate.
- Human-review agreement code, aggregate statistical checks, 18 released CSV
  tables, two deterministic SVG figures, checksums, tests, and CI.
- Final aggregate manifests for every consequential stage—not old drafts,
  retries, handoffs, or run diaries.

Start with the **[pipeline map](PIPELINE.md)**, then inspect the
[stage manifests](artifacts/stages/README.md) or run the synthetic demos. The
manuscript, real titles, raw data, and title-level outputs are not distributed.

## Repository tour

| Area | What it demonstrates |
| --- | --- |
| [`src/collection/`](src/collection/) | Reusable acquisition, parsing, persistence, resumption, and export code |
| [`src/preprocessing/`](src/preprocessing/) | Ordered transformations, provenance-preserving audits, length screening, deduplication |
| [`src/sampling/`](src/sampling/) | Stable-hash two-track sample construction |
| [`src/annotation/`](src/annotation/) | Frozen 42-field contract and runtime validation |
| [`src/privacy/`](src/privacy/) | Exact-span remediation and direct-locator gates |
| [`src/human_review/`](src/human_review/) | Two-coder and human-versus-AI agreement aggregation |
| [`src/analysis/`](src/analysis/) | Public estimators and table consistency checks |
| [`artifacts/stages/`](artifacts/stages/) | Final aggregate state of each private-data pipeline node |
| [`examples/synthetic/`](examples/synthetic/) | Harmless inputs for exercising restricted-input stages |
| [`results/`](results/) | Released aggregate tables, figures, summaries, and checksums |

## Reproduce the public results

Python 3.11 or later is sufficient. The aggregate verification layer has no
third-party runtime dependencies.

```bash
python scripts/verify_release.py
python scripts/reproduce_results.py --check
python scripts/build_manifest.py --check
python -m unittest discover -s tests -v
```

To regenerate the checked-in summaries and figures:

```bash
python scripts/reproduce_results.py
python scripts/build_manifest.py
```

The public programs reproduce and verify released summaries from aggregate
tables. Full title-level re-analysis requires the restricted research corpus,
which is not distributed.

To exercise the complete pipeline interfaces on synthetic inputs, install the
small optional public environment and run the demos:

```bash
python -m pip install -r requirements-public.txt
python -m scripts.demo_collection
python -m scripts.demo_preprocessing
python -m scripts.demo_sampling
python -m scripts.validate_annotation_contract
python -m scripts.demo_privacy_gate
python -m scripts.demo_human_review
```

## Released results

- [Aggregate tables](results/tables/README.md)
- [Final pipeline map and disclosure matrix](PIPELINE.md)
- [Final stage manifests](artifacts/stages/README.md)
- [Corpus-formation figure](results/figures/corpus_flow.svg)
- [Target–actor visibility figure](results/figures/target_actor_visibility.svg)
- [Machine-readable headline summary](results/reproduced/headline_metrics.json)
- [Release manifest and checksums](results/release_manifest.json)

## Privacy and availability

Public artifacts intentionally exclude title text, per-title hashes, record
identifiers, source names and URLs, evidence spans, model responses, and human
review records. See [Data availability](DATA_AVAILABILITY.md) and
[Privacy and release boundary](PRIVACY_AND_ETHICS.md).

## Research status and author

The associated manuscript is under journal review. Repository materials are a
public research companion rather than the manuscript or a public dataset.

**Ziyang Gu** · [ORCID 0009-0002-9998-1568](https://orcid.org/0009-0002-9998-1568)
