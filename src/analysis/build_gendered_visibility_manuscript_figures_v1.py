#!/usr/bin/env python3
"""Build the main manuscript figure from aggregate frozen-corpus results.

The script reads only public aggregate tables and public manifests. It emits
deterministic SVG and high-resolution PNG figures, a caption/alt-text file, and
an output manifest. No title-level text, source name, URL, record identifier,
or evidence span is read or written.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve()
DEFAULT_ANALYSIS_DIR = (
    ROOT
    / "results/tables/gendered_visibility_paper_corpus_v4_privacy_remediated_analysis_v1"
)
DEFAULT_FREEZE_MANIFEST = (
    ROOT
    / "data/processed/gendered_visibility_paper_corpus_v4_privacy_remediated/manifest.json"
)
DEFAULT_MANUSCRIPT_DECISIONS = (
    ROOT / "paper/sc_paper_v1/第一阶段核心论证决定_2026-08-19.md"
)
DEFAULT_OUTPUT_DIR = ROOT / "paper/figures"

FIGURE_SET_ID = "gendered_visibility_manuscript_figures_v1"
PNG_SCALE = 2

WHITE = "#FFFFFF"
INK = "#1F2933"
MUTED = "#5F6B76"
GRID = "#D8E0E5"
LIGHT = "#F4F7F8"
BLUE = "#2A6F97"
BLUE_LIGHT = "#DDECF3"
TEAL = "#2A9D8F"
TEAL_LIGHT = "#DDF1EE"
GOLD = "#D39A32"
GOLD_LIGHT = "#F6ECD5"
EXCLUDE = "#A8584F"
EXCLUDE_LIGHT = "#F4E5E2"
SLATE = "#657785"

FONT_REGULAR_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)
FONT_BOLD_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return payload


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def metric_row(
    rows: list[dict[str, str]], candidate: str, metric: str
) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if row.get("candidate") == candidate and row.get("metric") == metric
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one aggregate row for {candidate}/{metric}; got {len(matches)}"
        )
    return matches[0]


def count_row(rows: list[dict[str, str]], statistic: str) -> int:
    matches = [row for row in rows if row.get("statistic") == statistic]
    if len(matches) != 1:
        raise ValueError(f"Expected one aggregate row for {statistic}")
    return int(matches[0]["count"])


@dataclass(frozen=True)
class FigureData:
    captured_n: int
    prepared_record_n: int
    unique_deidentified_n: int
    candidate_record_n: int
    candidate_frame_n: int
    corpus_n: int
    historical_valid_output_n: int
    duplicate_contribution_n: int
    probability_track_n: int
    mechanism_track_n: int
    distribution_n: int
    target_visible_n: int
    target_visible_rate: float
    actor_visible_n: int
    actor_visible_rate: float
    target_only_n: int
    actor_only_n: int
    both_visible_n: int
    neither_visible_n: int
    paired_difference: float
    creation_distribution_consistent_n: int
    creation_distribution_n: int
    creation_distribution_rate: float
    distribution_exposure_consistent_n: int
    distribution_exposure_n: int
    distribution_exposure_rate: float
    three_stage_consistent_n: int
    three_stage_n: int
    three_stage_rate: float

    @property
    def excluded_during_cleaning_n(self) -> int:
        return self.captured_n - self.prepared_record_n

    @property
    def exact_duplicate_occurrence_n(self) -> int:
        return self.prepared_record_n - self.unique_deidentified_n

    @property
    def excluded_short_n(self) -> int:
        return self.unique_deidentified_n - self.candidate_frame_n

    @property
    def outside_corpus_n(self) -> int:
        return self.candidate_frame_n - self.corpus_n


def validate_data(data: FigureData) -> None:
    expected = {
        "captured_n": 103_743,
        "prepared_record_n": 91_172,
        "unique_deidentified_n": 89_538,
        "candidate_record_n": 90_727,
        "candidate_frame_n": 89_161,
        "corpus_n": 38_298,
        "historical_valid_output_n": 38_623,
        "duplicate_contribution_n": 325,
        "probability_track_n": 36_303,
        "mechanism_track_n": 1_995,
        "distribution_n": 4_186,
        "target_visible_n": 3_967,
        "actor_visible_n": 1_053,
        "target_only_n": 2_975,
        "actor_only_n": 61,
        "both_visible_n": 992,
        "neither_visible_n": 158,
        "creation_distribution_consistent_n": 1_737,
        "creation_distribution_n": 1_773,
        "distribution_exposure_consistent_n": 3_733,
        "distribution_exposure_n": 3_743,
        "three_stage_consistent_n": 1_661,
        "three_stage_n": 1_697,
    }
    for field, expected_value in expected.items():
        actual = getattr(data, field)
        if actual != expected_value:
            raise ValueError(
                f"Frozen count drift for {field}: {actual} != {expected_value}"
            )

    if data.historical_valid_output_n - data.duplicate_contribution_n != data.corpus_n:
        raise ValueError("Final-text deduplication does not reconcile with the corpus")
    if data.probability_track_n + data.mechanism_track_n != data.corpus_n:
        raise ValueError("Sampling tracks do not reconcile with the corpus")
    if (
        data.target_only_n
        + data.actor_only_n
        + data.both_visible_n
        + data.neither_visible_n
        != data.distribution_n
    ):
        raise ValueError("B1 paired cells do not sum to the opportunity denominator")
    if data.target_only_n + data.both_visible_n != data.target_visible_n:
        raise ValueError("B1 target-visible cells do not reconcile")
    if data.actor_only_n + data.both_visible_n != data.actor_visible_n:
        raise ValueError("B1 actor-visible cells do not reconcile")

    rate_checks = (
        (data.target_visible_rate, data.target_visible_n / data.distribution_n),
        (data.actor_visible_rate, data.actor_visible_n / data.distribution_n),
        (
            data.paired_difference,
            (data.target_only_n - data.actor_only_n) / data.distribution_n,
        ),
        (
            data.creation_distribution_rate,
            data.creation_distribution_consistent_n / data.creation_distribution_n,
        ),
        (
            data.distribution_exposure_rate,
            data.distribution_exposure_consistent_n / data.distribution_exposure_n,
        ),
        (
            data.three_stage_rate,
            data.three_stage_consistent_n / data.three_stage_n,
        ),
    )
    for actual, recomputed in rate_checks:
        if abs(actual - recomputed) > 1e-12:
            raise ValueError(f"Aggregate rate drift: {actual} != {recomputed}")


def validate_manuscript_decisions(decision_text: str) -> None:
    """Require the researcher-approved scope and evidence hierarchy."""

    normalized = " ".join(decision_text.split())
    required_fragments = (
        "状态：研究者已确认",
        "唯一中心结果",
        "第二层结果",
        "敏感性结果",
        "不把跨阶段标签一致性解释为现实人物、媒介文件或事件链的连续性",
    )
    missing = [
        fragment for fragment in required_fragments if fragment not in normalized
    ]
    if missing:
        raise ValueError(
            "Manuscript decision freeze is missing required approved decisions: "
            + "; ".join(missing)
        )


def load_figure_data(
    analysis_dir: Path = DEFAULT_ANALYSIS_DIR,
    freeze_manifest_path: Path = DEFAULT_FREEZE_MANIFEST,
    manuscript_decisions_path: Path = DEFAULT_MANUSCRIPT_DECISIONS,
) -> tuple[FigureData, dict[str, str]]:
    analysis_manifest_path = analysis_dir / "manifest.json"
    candidate_path = analysis_dir / "candidate_metrics.csv"
    descriptives_path = analysis_dir / "corpus_descriptives.csv"
    analysis_manifest = load_json(analysis_manifest_path)
    freeze_manifest = load_json(freeze_manifest_path)
    manuscript_decisions = manuscript_decisions_path.read_text(encoding="utf-8")

    for path in (candidate_path, descriptives_path):
        recorded = analysis_manifest.get("outputs", {}).get(relative(path))
        actual = sha256_file(path)
        if recorded != actual:
            raise ValueError(f"Aggregate input checksum mismatch: {relative(path)}")

    recorded_freeze = analysis_manifest.get("inputs", {}).get(
        relative(freeze_manifest_path)
    )
    actual_freeze = sha256_file(freeze_manifest_path)
    if recorded_freeze != actual_freeze:
        raise ValueError("Frozen corpus manifest checksum mismatch")

    candidates = load_csv(candidate_path)
    descriptives = load_csv(descriptives_path)
    counts = freeze_manifest["counts"]

    target = metric_row(candidates, "B1", "distribution_target_position_visible")
    actor = metric_row(candidates, "B1", "distribution_actor_position_visible")
    paired = metric_row(
        candidates, "B1", "target_minus_actor_visibility_paired_difference"
    )
    c_to_d = metric_row(
        candidates, "C2", "creation_to_distribution_target_position_consistency"
    )
    d_to_e = metric_row(
        candidates, "C2", "distribution_to_exposure_target_position_consistency"
    )
    all_three = metric_row(candidates, "C2", "three_stage_target_position_consistency")

    data = FigureData(
        captured_n=103_743,
        prepared_record_n=int(counts["deidentified_record_rows"]),
        unique_deidentified_n=int(counts["deidentified_unique_texts"]),
        candidate_record_n=int(counts["candidate_record_rows"]),
        candidate_frame_n=int(counts["candidate_unique_texts"]),
        corpus_n=count_row(descriptives, "paper_corpus_records"),
        historical_valid_output_n=int(counts["v1_valid_record_members"]),
        duplicate_contribution_n=(
            int(counts["v1_extra_records_beyond_first"])
            + int(counts.get("v4_extra_contributions_removed", 0))
        ),
        probability_track_n=int(
            freeze_manifest["scope"]["sampling_tracks"]["probability"]
        ),
        mechanism_track_n=int(
            freeze_manifest["scope"]["sampling_tracks"]["mechanism"]
        ),
        distribution_n=int(target["denominator_n"]),
        target_visible_n=int(target["selected_n"]),
        target_visible_rate=float(target["estimate"]),
        actor_visible_n=int(actor["selected_n"]),
        actor_visible_rate=float(actor["estimate"]),
        target_only_n=int(paired["selected_n"]),
        actor_only_n=int(paired["comparison_selected_n"]),
        both_visible_n=int(paired["both_n"]),
        neither_visible_n=int(paired["neither_n"]),
        paired_difference=float(paired["estimate"]),
        creation_distribution_consistent_n=int(c_to_d["selected_n"]),
        creation_distribution_n=int(c_to_d["denominator_n"]),
        creation_distribution_rate=float(c_to_d["estimate"]),
        distribution_exposure_consistent_n=int(d_to_e["selected_n"]),
        distribution_exposure_n=int(d_to_e["denominator_n"]),
        distribution_exposure_rate=float(d_to_e["estimate"]),
        three_stage_consistent_n=int(all_three["selected_n"]),
        three_stage_n=int(all_three["denominator_n"]),
        three_stage_rate=float(all_three["estimate"]),
    )
    validate_data(data)
    provenance_hashes = {
        relative(analysis_manifest_path): sha256_file(analysis_manifest_path),
        relative(candidate_path): sha256_file(candidate_path),
        relative(descriptives_path): sha256_file(descriptives_path),
        relative(freeze_manifest_path): actual_freeze,
    }
    validate_manuscript_decisions(manuscript_decisions)
    return data, {
        **provenance_hashes,
        relative(manuscript_decisions_path): sha256_file(manuscript_decisions_path),
    }


class Canvas:
    """Small shared SVG/Pillow drawing surface for deterministic dual output."""

    def __init__(self, width: int, height: int, title: str, scale: int = PNG_SCALE):
        self.width = width
        self.height = height
        self.scale = scale
        self.image = Image.new("RGB", (width * scale, height * scale), WHITE)
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
                f'aria-labelledby="figure-title">'
            ),
            f'<title id="figure-title">{html.escape(title)}</title>',
            '<rect width="100%" height="100%" fill="#FFFFFF"/>',
        ]
        self._fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}

    def _coord(self, value: float) -> int:
        return round(value * self.scale)

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, bold)
        if key in self._fonts:
            return self._fonts[key]
        candidates = FONT_BOLD_CANDIDATES if bold else FONT_REGULAR_CANDIDATES
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
            font = ImageFont.truetype(name, size * self.scale)
        else:
            font = ImageFont.truetype(str(path), size * self.scale)
        self._fonts[key] = font
        return font

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        fill: str = WHITE,
        stroke: str | None = None,
        stroke_width: float = 2,
        radius: float = 0,
    ) -> None:
        box = tuple(self._coord(value) for value in (x, y, x + width, y + height))
        kwargs: dict[str, Any] = {"fill": fill}
        if stroke:
            kwargs["outline"] = stroke
            kwargs["width"] = max(1, self._coord(stroke_width))
        if radius:
            self.draw.rounded_rectangle(box, radius=self._coord(radius), **kwargs)
        else:
            self.draw.rectangle(box, **kwargs)
        stroke_attr = (
            f' stroke="{stroke}" stroke-width="{stroke_width}"' if stroke else ""
        )
        self.svg.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
            f'rx="{radius}" fill="{fill}"{stroke_attr}/>'
        )

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str = INK,
        width: float = 2,
        dash: tuple[int, int] | None = None,
    ) -> None:
        coords = tuple(self._coord(value) for value in (x1, y1, x2, y2))
        if dash is None:
            self.draw.line(coords, fill=color, width=max(1, self._coord(width)))
            dash_attr = ""
        else:
            dash_length, gap = (self._coord(value) for value in dash)
            total = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            if total:
                ux = (x2 - x1) / total
                uy = (y2 - y1) / total
                current = 0.0
                while current < total:
                    end = min(total, current + dash[0])
                    self.draw.line(
                        (
                            self._coord(x1 + ux * current),
                            self._coord(y1 + uy * current),
                            self._coord(x1 + ux * end),
                            self._coord(y1 + uy * end),
                        ),
                        fill=color,
                        width=max(1, self._coord(width)),
                    )
                    current += (dash_length + gap) / self.scale
            dash_attr = f' stroke-dasharray="{dash[0]} {dash[1]}"'
        self.svg.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="{width}"{dash_attr}/>'
        )

    def polygon(self, points: list[tuple[float, float]], *, fill: str) -> None:
        scaled = [(self._coord(x), self._coord(y)) for x, y in points]
        self.draw.polygon(scaled, fill=fill)
        svg_points = " ".join(f"{x},{y}" for x, y in points)
        self.svg.append(f'<polygon points="{svg_points}" fill="{fill}"/>')

    def arrow(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str = SLATE,
        width: float = 3,
        head: float = 10,
    ) -> None:
        self.line(x1, y1, x2, y2, color=color, width=width)
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            return
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        self.polygon(
            [
                (x2, y2),
                (x2 - ux * head + px * head * 0.55, y2 - uy * head + py * head * 0.55),
                (x2 - ux * head - px * head * 0.55, y2 - uy * head - py * head * 0.55),
            ],
            fill=color,
        )

    def text(
        self,
        x: float,
        y: float,
        content: str,
        *,
        size: int = 24,
        color: str = INK,
        bold: bool = False,
        align: str = "left",
        valign: str = "middle",
    ) -> None:
        svg_anchor = {"left": "start", "center": "middle", "right": "end"}[align]
        svg_baseline = {"top": "hanging", "middle": "middle", "bottom": "auto"}[valign]
        pillow_anchor = {
            ("left", "top"): "lt",
            ("center", "top"): "mt",
            ("right", "top"): "rt",
            ("left", "middle"): "lm",
            ("center", "middle"): "mm",
            ("right", "middle"): "rm",
            ("left", "bottom"): "lb",
            ("center", "bottom"): "mb",
            ("right", "bottom"): "rb",
        }[(align, valign)]
        self.draw.text(
            (self._coord(x), self._coord(y)),
            content,
            font=self._font(size, bold),
            fill=color,
            anchor=pillow_anchor,
        )
        weight = "700" if bold else "400"
        self.svg.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" text-anchor="{svg_anchor}" '
            f'dominant-baseline="{svg_baseline}">{html.escape(content)}</text>'
        )

    def multiline(
        self,
        x: float,
        y: float,
        lines: list[str],
        *,
        size: int = 24,
        color: str = INK,
        bold: bool = False,
        align: str = "left",
        line_height: float = 1.25,
    ) -> None:
        step = size * line_height
        start = y - step * (len(lines) - 1) / 2
        for index, line in enumerate(lines):
            self.text(
                x,
                start + step * index,
                line,
                size=size,
                color=color,
                bold=bold,
                align=align,
            )

    def save(self, svg_path: Path, png_path: Path) -> None:
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text("\n".join([*self.svg, "</svg>"]) + "\n", encoding="utf-8")
        self.image.save(png_path, format="PNG", optimize=True, dpi=(300, 300))


def stage_box(
    canvas: Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    label: str,
    count: int,
    *,
    accent: str = BLUE,
    fill: str = LIGHT,
    unit: str = "records",
) -> None:
    canvas.rect(
        x, y, width, height, fill=fill, stroke=accent, stroke_width=2, radius=12
    )
    canvas.rect(x, y, 9, height, fill=accent, radius=4)
    canvas.text(x + 28, y + 40, label, size=22, bold=True, valign="top")
    canvas.text(x + 28, y + 102, f"{count:,}", size=38, bold=True)
    canvas.text(x + width - 24, y + 104, unit, size=17, color=MUTED, align="right")


def compact_box(
    canvas: Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    lines: list[str],
    *,
    fill: str,
    stroke: str,
    bold_first: bool = True,
) -> None:
    canvas.rect(
        x, y, width, height, fill=fill, stroke=stroke, stroke_width=2, radius=10
    )
    if len(lines) == 1:
        canvas.text(
            x + width / 2,
            y + height / 2,
            lines[0],
            size=23,
            bold=bold_first,
            align="center",
        )
        return
    step = 26
    start = y + height / 2 - step * (len(lines) - 1) / 2
    for index, line in enumerate(lines):
        canvas.text(
            x + width / 2,
            start + step * index,
            line,
            size=21 if index == 0 else 18,
            bold=bold_first and index == 0,
            color=INK if index == 0 else MUTED,
            align="center",
        )


def draw_figure_1(data: FigureData, output_dir: Path) -> tuple[Path, Path]:
    canvas = Canvas(1800, 1000, "Corpus formation and analytical sample")
    stroke = "#111111"
    secondary = "#333333"
    box_x, box_w, center_x = 250, 900, 700

    def plain_box(y: float, height: float, *, stroke_width: float = 2.5) -> None:
        canvas.rect(
            box_x,
            y,
            box_w,
            height,
            fill=WHITE,
            stroke=stroke,
            stroke_width=stroke_width,
            radius=0,
        )

    # Five square-corner boxes form the complete source-to-sample path.
    plain_box(25, 100)
    canvas.text(
        center_x,
        52,
        "Source records",
        size=29,
        color=stroke,
        bold=True,
        align="center",
    )
    canvas.text(
        center_x,
        94,
        f"{data.captured_n:,} records from 10 websites",
        size=30,
        color=stroke,
        align="center",
    )

    canvas.arrow(center_x, 125, center_x, 170, color=stroke, width=2.5, head=11)
    plain_box(170, 115)
    canvas.text(
        center_x,
        196,
        "Prepared corpus",
        size=29,
        color=stroke,
        bold=True,
        align="center",
    )
    canvas.text(
        center_x,
        235,
        f"{data.prepared_record_n:,} records  |  {data.unique_deidentified_n:,} unique texts",
        size=29,
        color=stroke,
        align="center",
    )
    canvas.text(
        center_x,
        266,
        "After cleaning, exclusions, and de-identification",
        size=21,
        color=secondary,
        align="center",
    )

    canvas.arrow(center_x, 285, center_x, 330, color=stroke, width=2.5, head=11)
    plain_box(330, 120)
    canvas.text(
        center_x,
        357,
        "Candidate frame",
        size=29,
        color=stroke,
        bold=True,
        align="center",
    )
    canvas.text(
        center_x,
        397,
        f"{data.candidate_record_n:,} records  |  {data.candidate_frame_n:,} unique texts",
        size=29,
        color=stroke,
        align="center",
    )
    canvas.text(
        center_x,
        430,
        "After semantic-length screening",
        size=21,
        color=secondary,
        align="center",
    )

    # The side box is the complement of the final unique-text sample. It is not
    # presented as a failed coding batch or as simple-random-sample attrition.
    canvas.arrow(1150, 390, 1290, 390, color=stroke, width=2.5, head=11)
    canvas.rect(
        1310,
        330,
        440,
        120,
        fill=WHITE,
        stroke=stroke,
        stroke_width=2.5,
        radius=0,
    )
    canvas.multiline(
        1530,
        365,
        ["Not represented in", "the analytical sample"],
        size=23,
        color=stroke,
        bold=True,
        align="center",
        line_height=1.05,
    )
    canvas.text(
        1530,
        423,
        f"{data.outside_corpus_n:,} unique candidate texts",
        size=23,
        color=stroke,
        align="center",
    )

    canvas.arrow(center_x, 450, center_x, 555, color=stroke, width=2.5, head=11)
    canvas.multiline(
        735,
        503,
        ["Fixed-seed stratified selection", "for v0.3 AI coding"],
        size=22,
        color=secondary,
        align="left",
        line_height=1.05,
    )
    plain_box(555, 140)
    canvas.text(
        center_x,
        582,
        "Valid v0.3 AI outputs",
        size=29,
        color=stroke,
        bold=True,
        align="center",
    )
    canvas.text(
        center_x,
        626,
        f"{data.historical_valid_output_n:,} record-level outputs",
        size=31,
        color=stroke,
        align="center",
    )
    probability_output_n = data.historical_valid_output_n - data.mechanism_track_n
    canvas.text(
        center_x,
        669,
        f"{probability_output_n:,} probability track  |  {data.mechanism_track_n:,} mechanism-enriched",
        size=22,
        color=secondary,
        align="center",
    )

    canvas.arrow(center_x, 695, center_x, 835, color=stroke, width=2.5, head=11)
    canvas.multiline(
        735,
        766,
        [
            "Final-text deduplication",
            f"{data.duplicate_contribution_n:,} extra record contributions removed",
        ],
        size=22,
        color=secondary,
        align="left",
        line_height=1.05,
    )
    plain_box(835, 140, stroke_width=3.5)
    canvas.text(
        center_x,
        862,
        "Analytical sample",
        size=30,
        color=stroke,
        bold=True,
        align="center",
    )
    canvas.text(
        center_x,
        907,
        f"{data.corpus_n:,} unique final texts",
        size=34,
        color=stroke,
        bold=True,
        align="center",
    )
    canvas.text(
        center_x,
        949,
        f"{data.probability_track_n:,} probability track  |  {data.mechanism_track_n:,} mechanism-enriched",
        size=23,
        color=secondary,
        align="center",
    )

    svg_path = output_dir / "figure_1_corpus_formation.svg"
    png_path = output_dir / "figure_1_corpus_formation.png"
    canvas.save(svg_path, png_path)
    return svg_path, png_path


def caption_markdown(data: FigureData) -> str:
    return f"""# Manuscript figure captions and accessibility text

## Figure 1. Corpus formation and analytical sample

Formation of the analytical sample. Two acquisition rounds yielded
{data.captured_n:,} source records. Cleaning, exclusions, and de-identification produced
{data.prepared_record_n:,} prepared record rows. Exact final-text deduplication removed
{data.exact_duplicate_occurrence_n:,} occurrences beyond the first, leaving
{data.unique_deidentified_n:,} unique strings; the semantic-length rule then removed
{data.excluded_short_n:,} unique short texts, leaving a {data.candidate_frame_n:,}-text
candidate frame. Record-level v0.3 coding had already produced
{data.historical_valid_output_n:,} valid outputs through fixed-seed stratified
selection: {data.historical_valid_output_n - data.mechanism_track_n:,} from the
probability track and {data.mechanism_track_n:,} from the pre-specified
mechanism-enriched track. Retaining one label-independent
representative per final-text hash, with the rule reapplied after privacy remediation,
removed {data.duplicate_contribution_n:,} additional
record contributions and produced an analytical sample of {data.corpus_n:,}
unique texts: {data.probability_track_n:,} probability-track texts and
{data.mechanism_track_n:,} mechanism-enriched texts. The other
{data.outside_corpus_n:,} candidate-frame texts were not represented in the analytical
sample. Subsequent results describe the retained sample and are not estimates for the
candidate frame.

**Alt text.** A vertical five-box flow shows {data.captured_n:,} source records,
{data.prepared_record_n:,} prepared records representing
{data.unique_deidentified_n:,} unique texts, a candidate frame of
{data.candidate_record_n:,} records representing {data.candidate_frame_n:,} unique
texts, {data.historical_valid_output_n:,} valid record-level coding outputs, and a
final analytical sample of {data.corpus_n:,} unique texts. The final sample contains
{data.probability_track_n:,} probability-track and {data.mechanism_track_n:,}
mechanism-enriched texts. A single side box reports that {data.outside_corpus_n:,}
unique candidate texts were not represented in the analytical sample.
"""


def render_all(
    analysis_dir: Path = DEFAULT_ANALYSIS_DIR,
    freeze_manifest_path: Path = DEFAULT_FREEZE_MANIFEST,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    manuscript_decisions_path: Path = DEFAULT_MANUSCRIPT_DECISIONS,
) -> dict[str, Any]:
    data, input_hashes = load_figure_data(
        analysis_dir,
        freeze_manifest_path,
        manuscript_decisions_path,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_paths = [*draw_figure_1(data, output_dir)]
    captions_path = output_dir / "figure_captions_v1.md"
    captions_path.write_text(caption_markdown(data), encoding="utf-8")
    output_paths = [*figure_paths, captions_path]

    manifest = {
        "created_at": utc_now(),
        "figure_set_id": FIGURE_SET_ID,
        "status": "complete_aggregate_only_manuscript_figure",
        "privacy": (
            "Outputs contain aggregate counts only; no title text, source name, URL, "
            "record identifier, or evidence span was read or emitted."
        ),
        "scope": (
            "Sample-descriptive figures for the retained 38,298 unique title texts; "
            "not estimates for the candidate frame."
        ),
        "inputs": {relative(SCRIPT): sha256_file(SCRIPT), **input_hashes},
        "parameters": {
            "png_scale": PNG_SCALE,
            "png_dpi_metadata": 300,
            "figure_1_canvas": [1800, 1000],
            "font_family": "Arial with DejaVu Sans fallback",
            "rounding": "display percentages rounded to one decimal",
            "intervals_plotted": False,
        },
        "counts": {
            "initial_capture": data.captured_n,
            "prepared_record_rows": data.prepared_record_n,
            "unique_deidentified_texts": data.unique_deidentified_n,
            "candidate_record_rows": data.candidate_record_n,
            "unique_candidate_frame": data.candidate_frame_n,
            "historical_valid_record_outputs": data.historical_valid_output_n,
            "duplicate_record_contributions_removed": data.duplicate_contribution_n,
            "manuscript_corpus": data.corpus_n,
            "outside_manuscript_corpus": data.outside_corpus_n,
            "probability_track_texts": data.probability_track_n,
            "mechanism_enriched_texts": data.mechanism_track_n,
        },
        "outputs": {relative(path): sha256_file(path) for path in output_paths},
        "api_calls": 0,
        "raw_data_files_read": 0,
        "title_level_fields_read": 0,
    }
    manifest_path = (
        output_dir / "gendered_visibility_manuscript_figures_v1_manifest.json"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument("--freeze-manifest", type=Path, default=DEFAULT_FREEZE_MANIFEST)
    parser.add_argument(
        "--manuscript-decisions", type=Path, default=DEFAULT_MANUSCRIPT_DECISIONS
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = render_all(
        args.analysis_dir,
        args.freeze_manifest,
        args.output_dir,
        args.manuscript_decisions,
    )
    print(
        json.dumps(
            {
                "figure_set_id": manifest["figure_set_id"],
                "status": manifest["status"],
                "outputs": len(manifest["outputs"]),
                "api_calls": manifest["api_calls"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
