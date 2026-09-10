# Unequal Target–Actor Visibility in Chinese-Language Adult-Video Titles

[![Release safety and reproducibility](https://github.com/gyy489/chinese-adult-title-metadata-study/actions/workflows/ci.yml/badge.svg)](https://github.com/gyy489/chinese-adult-title-metadata-study/actions/workflows/ci.yml)

Public research companion for an AI-assisted quantitative content analysis of
Chinese-language adult-video title metadata. This repository presents released
aggregate results, the frozen annotation contract, and deterministic programs
that verify and reproduce the public result summaries and figures.

[中文说明](README.zh-CN.md)

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

- 18 aggregate CSV tables covering corpus formation, primary and supporting
  results, sensitivity checks, cross-model diagnostics, and human review.
- The frozen v0.3 annotation prompt, codebook, and 42-field JSON Schema.
- Deterministic, standard-library Python programs that validate the release and
  regenerate public summaries and SVG figures.
- Automated tests and a GitHub Actions workflow.

The manuscript itself is not distributed here. No raw data or row-level title
data are included.

## Reproduce the public results

Python 3.11 or later is sufficient; the public verification layer has no
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

## Released results

- [Aggregate tables](results/tables/README.md)
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
