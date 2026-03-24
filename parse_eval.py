"""
MS 114 Peer Evaluation PDF Score Parser
========================================
Handles all known student selection styles:

  1. Highlight (any color — green, yellow, purple, etc.)
       → A filled rectangle of any non-background color sits behind the digit.
  2. Font color change
       → The selected digit is in a span whose text color differs from black.
  3. Bold
       → The selected digit is in a span whose flags indicate bold weight.
  4. Delete-all-but-one
       → The student deleted all other numbers; the scale span contains only
         a bare digit (e.g. "4").
  5. Any combination of the above (e.g. highlight + bold).

Core idea: "visual outlier detection"
--------------------------------------
For the full 1–10 scale, parse the row into DigitTokens (one per number).
Each token gets a VisualSignature: background highlight color, font color,
bold flag. The token whose signature differs from all others is the selection.

Usage:
    python parse_eval.py --path /path/to/evals --section 105
    python parse_eval.py --path /path/to/evals --section 106

Dependencies:
    pip install pymupdf
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, stdev
from typing import Optional

GREEN = "\033[32m"
RED   = "\033[31m"
RESET = "\033[0m"

import fitz  # PyMuPDF

from rosters import SECTION_105, SECTION_106


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# These are section-header labels — they appear on the form but do NOT
# correspond to a scoreable metric. Hard-coded per the form design.
SECTION_HEADERS = {"Content", "Presentation", "Audience Engagement"}

METRIC_LABELS = [
    "Course Themes",
    "Scholarly Observations",
    "Preparedness",
    "Coordination",
    "Clear Voices",
    "Too much reading of text",
    "Audience Engagement",
    "Slides: cluttered with text",
]

# Lower scores are worse on these metrics
INVERTED_METRICS = {"Too much reading of text", "Slides: cluttered with text"}

# Regex: full scale present  e.g.  1—2—3—4—5—6—7—8—9—10
FULL_SCALE_RE = re.compile(
    r"1\s*[—\-]\s*2\s*[—\-]\s*3\s*[—\-]\s*4\s*[—\-]\s*5\s*[—\-]\s*"
    r"6\s*[—\-]\s*7\s*[—\-]\s*8\s*[—\-]\s*9\s*[—\-]\s*10"
)
# Regex: lone number (delete-style)
LONE_NUMBER_RE = re.compile(r"^([1-9]|10)$")

# x-coordinate: everything to the right of this is the scale column
SCALE_COLUMN_X = 180

# Maximum width of a per-digit selection highlight rect.
# Wider rects are section-header backgrounds.
MAX_HIGHLIGHT_RECT_WIDTH = 60  # pts


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class VisualSignature:
    """Describes how a digit token looks on the page."""
    bg_color: Optional[tuple] = None   # fill color of any overlapping highlight rect
    font_color: int = 0                # packed RGB int; 0 = black (default)
    is_bold: bool = False              # span-level bold flag

    def is_default(self) -> bool:
        """True if the token looks like a plain, unstyled digit."""
        return self.bg_color is None and self.font_color == 0 and not self.is_bold

    def __eq__(self, other):
        return (
            self.bg_color == other.bg_color
            and self.font_color == other.font_color
            and self.is_bold == other.is_bold
        )


@dataclass
class DigitToken:
    """One number (1–10) in the scale with its bounding box and visual style."""
    value: int
    x0: float
    y0: float
    x1: float
    y1: float
    sig: VisualSignature = field(default_factory=VisualSignature)


@dataclass
class EvalResult:
    reviewer_name: str = "Unknown"
    topic: str = "Unknown"
    scores: dict = field(default_factory=dict)         # metric → int
    score_methods: dict = field(default_factory=dict)  # metric → str
    good_point: str = ""
    murky_point: str = ""
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

def span_text(span: dict) -> str:
    """Reconstruct text from a rawdict span (rawdict mode has no 'text' key)."""
    return "".join(ch["c"] for ch in span.get("chars", []))


def is_bold_span(span: dict) -> bool:
    """Return True if the span has the bold font flag set (bit 4 = 16)."""
    return bool(span.get("flags", 0) & 16)


def is_background_rect(rect: fitz.Rect, fill: tuple) -> bool:
    """
    Return True if this filled rect is a page background / section header band.
    Selection highlights are narrow; background bands span most of the page width.
    """
    r, g, b = fill
    # White / near-white
    if r > 0.92 and g > 0.92 and b > 0.92:
        return True
    # Achromatic gray (section header shading)
    if abs(r - g) < 0.06 and abs(g - b) < 0.06:
        return True
    # Very wide → it's a background band, not a per-digit highlight
    if (rect.x1 - rect.x0) > MAX_HIGHLIGHT_RECT_WIDTH:
        return True
    return False


def get_selection_rects(page: fitz.Page) -> list[tuple[fitz.Rect, tuple]]:
    """
    Return all (rect, fill_rgb) pairs that look like per-digit selection highlights.
    """
    results = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is None:
            continue
        rect = d["rect"]
        if not is_background_rect(rect, fill):
            results.append((rect, fill))
    return results


def rects_overlap(ax0, ay0, ax1, ay1, bx0, by0, bx1, by1, tol: float = 1.5) -> bool:
    return ax0 < bx1 + tol and ax1 > bx0 - tol and ay0 < by1 + tol and ay1 > by0 - tol


# ---------------------------------------------------------------------------
# Scale parsing — build DigitTokens from a row
# ---------------------------------------------------------------------------

def build_digit_tokens(
    page: fitz.Page,
    row_y0: float,
    row_y1: float,
    selection_rects: list[tuple[fitz.Rect, tuple]],
) -> list[DigitToken]:
    """
    Parse the 1–10 scale on a given row into a list of DigitTokens.

    Why span-level signals work for bold/color:
        When a student bolds or recolors one number in Google Docs,
        the export splits that number into its own span. So instead of
        one span "1—2—3—...—10", we get three spans:
            span A (normal):  "1—2—3—"
            span B (bold):    "4"
            span C (normal):  "—5—6—...—10"
        Reading the span's flags/color per character lets us detect this.

    Why spatial overlap works for highlight rects:
        Highlights are filled vector rectangles drawn behind the text.
        We test each DigitToken's bbox against all selection rects.
    """
    rawdict = page.get_text("rawdict")

    # Collect all spans in the scale column for this row
    relevant_spans = []
    for block in rawdict["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sx0, sy0, sx1, sy1 = span["bbox"]
                if not (row_y0 - 3 <= sy0 and sy1 <= row_y1 + 3):
                    continue
                if sx1 < SCALE_COLUMN_X:
                    continue
                if not any(ch["c"].isdigit() for ch in span.get("chars", [])):
                    continue
                relevant_spans.append(span)

    if not relevant_spans:
        return []

    # Walk all chars in left-to-right order, pairing each with its parent span
    all_chars_with_span = []
    for span in sorted(relevant_spans, key=lambda s: s["bbox"][0]):
        for ch in span.get("chars", []):
            all_chars_with_span.append((ch, span))

    # Group consecutive digit chars into number tokens (so "1","0" → token 10)
    tokens: list[DigitToken] = []
    i = 0
    while i < len(all_chars_with_span):
        ch, span = all_chars_with_span[i]
        if not ch["c"].isdigit():
            i += 1
            continue

        digit_chars = [(ch, span)]
        j = i + 1
        # A separator (em-dash, hyphen, zero-width space) ends the current token
        while j < len(all_chars_with_span):
            nch, nspan = all_chars_with_span[j]
            if nch["c"].isdigit():
                digit_chars.append((nch, nspan))
                j += 1
            else:
                break  # hit a non-digit (separator or letter)

        value_str = "".join(dc["c"] for dc, _ in digit_chars)
        try:
            value = int(value_str)
        except ValueError:
            i = j if j > i else i + 1
            continue

        if not 1 <= value <= 10:
            i = j if j > i else i + 1
            continue

        # Bounding box = union over all chars in this token
        x0 = min(dc["bbox"][0] for dc, _ in digit_chars)
        y0 = min(dc["bbox"][1] for dc, _ in digit_chars)
        x1 = max(dc["bbox"][2] for dc, _ in digit_chars)
        y1 = max(dc["bbox"][3] for dc, _ in digit_chars)

        # Visual signature
        sig = VisualSignature()
        sig.font_color = digit_chars[0][1].get("color", 0)
        sig.is_bold = any(is_bold_span(s) for _, s in digit_chars)
        # Use digit center-point rather than full bbox overlap so that a highlight
        # rect whose left edge merely touches the prior digit's right edge (a common
        # artifact with Google Docs colored highlights) is not falsely attributed.
        digit_cx = (x0 + x1) / 2
        digit_cy = (y0 + y1) / 2
        for rect, fill in selection_rects:
            if (rect.x0 - 2 <= digit_cx <= rect.x1 + 2 and
                    rect.y0 - 2 <= digit_cy <= rect.y1 + 2):
                sig.bg_color = fill
                break

        tokens.append(DigitToken(value=value, x0=x0, y0=y0, x1=x1, y1=y1, sig=sig))
        i = j if j > i else i + 1

    return tokens


# ---------------------------------------------------------------------------
# Score extraction: find the visual outlier
# ---------------------------------------------------------------------------

def extract_score_from_tokens(tokens: list[DigitToken]) -> tuple[Optional[int], str]:
    """
    Given DigitTokens for one metric row, return (score, method_description).

    Logic:
      - 1 token  → delete-style (all others removed)
      - exactly 1 non-default token → that's the selection; report which signals fired
      - multiple non-default tokens pointing to same value → same selection via multiple signals
      - otherwise → ambiguous or no signal
    """
    if not tokens:
        return None, "no_tokens"

    if len(tokens) == 1:
        return tokens[0].value, "delete_style"

    non_default = [t for t in tokens if not t.sig.is_default()]

    if not non_default:
        return None, "no_signal"

    # All non-default tokens point to the same value → unambiguous
    values = {t.value for t in non_default}
    if len(values) == 1:
        t = non_default[0]
        methods = []
        if t.sig.bg_color is not None:
            methods.append("highlight")
        if t.sig.font_color != 0:
            methods.append("font_color")
        if t.sig.is_bold:
            methods.append("bold")
        return t.value, "+".join(methods) if methods else "unknown"

    # Multiple different values flagged → genuinely ambiguous
    return None, f"ambiguous(values={sorted(values)})"


# ---------------------------------------------------------------------------
# Field row detection
# ---------------------------------------------------------------------------

def normalize_label(s: str) -> str:
    # Strip zero-width spaces (U+200B) Google Docs injects at span boundaries
    # when colored highlights are applied, then strip normal whitespace.
    return s.replace("\u200b", "").strip().rstrip("?:").lower()


def detect_field_rows(page: fitz.Page) -> list[dict]:
    """
    Find the y-band for each scoreable metric row.

    "Audience Engagement" appears twice — once as a SECTION_HEADER and once
    as a metric row with a scale. We resolve this by preferring the occurrence
    whose y-band contains scale text.
    """
    words = page.get_text("words")  # (x0, y0, x1, y1, text, ...)

    # y-positions of scale text (for tie-breaking)
    scale_y_centers: set[float] = set()
    for wx0, wy0, wx1, wy1, wtext, *_ in words:
        clean = wtext.replace("\u200b", "").strip()
        if FULL_SCALE_RE.search(wtext) or LONE_NUMBER_RE.match(clean):
            scale_y_centers.add(round((wy0 + wy1) / 2, 0))

    def has_nearby_scale(y0: float, y1: float) -> bool:
        mid = (y0 + y1) / 2
        return any(abs(sy - mid) < 20 for sy in scale_y_centers)

    all_matches: dict[str, list[dict]] = {lbl: [] for lbl in METRIC_LABELS}

    for label in METRIC_LABELS:
        label_tokens = label.split()
        n = len(label_tokens)
        norm_first = normalize_label(label_tokens[0])

        for i, (wx0, wy0, wx1, wy1, wtext, *_) in enumerate(words):
            if normalize_label(wtext) != norm_first:
                continue
            if i + n > len(words):
                continue
            candidate = " ".join(words[j][4] for j in range(i, i + n))
            if label.lower() not in candidate.lower():
                continue
            all_matches[label].append({"label": label, "y0": wy0 - 3, "y1": wy1 + 3})

    result = []
    for label in METRIC_LABELS:
        matches = all_matches[label]
        if not matches:
            continue
        if len(matches) == 1:
            result.append(matches[0])
            continue
        # Prefer the occurrence adjacent to scale text
        best = next((m for m in matches if has_nearby_scale(m["y0"], m["y1"])), None)
        if best is None:
            best = max(matches, key=lambda m: m["y0"])
        result.append(best)

    return result


# ---------------------------------------------------------------------------
# Qualitative text extraction
# ---------------------------------------------------------------------------

def extract_qualitative(page_texts: list[str]) -> tuple[str, str]:
    full = "\n".join(page_texts)
    good_m = re.search(
        r"Good\s+point:\s*\n(.*?)(?=Murky\s+point:|$)", full, re.DOTALL | re.IGNORECASE
    )
    murky_m = re.search(
        r"Murky\s+point:\s*(.*?)(?=\f|$)", full, re.DOTALL | re.IGNORECASE
    )
    return (
        good_m.group(1).strip() if good_m else "",
        murky_m.group(1).strip() if murky_m else "",
    )


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------

def parse_evaluation(pdf_path: str) -> EvalResult:
    result = EvalResult()
    doc = fitz.open(pdf_path)
    page = doc[0]

    # Metadata — use [^\n]* so we never bleed into the next line if fields are blank
    page0_text = page.get_text()
    name_m = re.search(r"Reviewer\s+Name:[ \t]*([^\n]*)", page0_text)
    topic_m = re.search(r"Discussion\s+Leader\s+Topic:[ \t]*([^\n]*)", page0_text)
    name_val = name_m.group(1).strip() if name_m else ""
    topic_val = topic_m.group(1).strip() if topic_m else ""
    result.reviewer_name = name_val if name_val else "BLANK"
    result.topic = topic_val if topic_val else "BLANK"

    # Pre-compute selection rects once for the whole page
    selection_rects = get_selection_rects(page)

    # Score each metric
    for row in detect_field_rows(page):
        label = row["label"]
        tokens = build_digit_tokens(page, row["y0"], row["y1"], selection_rects)
        score, method = extract_score_from_tokens(tokens)

        if score is not None:
            result.scores[label] = score
            result.score_methods[label] = method
        else:
            result.warnings.append(
                f"Could not extract score for '{label}' — method={method}"
            )

    # Qualitative (may span pages)
    all_texts = [doc[i].get_text() for i in range(len(doc))]
    result.good_point, result.murky_point = extract_qualitative(all_texts)

    # Flag completely blank / unfilled submissions
    if not result.scores:
        result.warnings.insert(0, "INCOMPLETE SUBMISSION — no scores found. Form may have been submitted blank.")

    return result


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_result(r: EvalResult):
    print("=" * 62)
    print(f"  Reviewer : {r.reviewer_name}")
    print(f"  Topic    : {r.topic}")
    print("=" * 62)
    print(f"\n  {'Metric':<38} {'Score':>5}  Method")
    print("  " + "-" * 58)
    for label in METRIC_LABELS:
        score = r.scores.get(label, "???")
        method = r.score_methods.get(label, "—")
        inv = " ↓" if label in INVERTED_METRICS else ""
        print(f"  {label:<38} {str(score):>5}  [{method}]{inv}")

    if r.warnings:
        print("\n  ⚠ Warnings:")
        for w in r.warnings:
            print(f"    - {w}")

    if r.good_point:
        print(f"\n  Good Point:\n    {r.good_point}")
    if r.murky_point:
        print(f"\n  Murky Point:\n    {r.murky_point}")
    print()


# ---------------------------------------------------------------------------
# Multi-file processing
# ---------------------------------------------------------------------------

def extract_student_lastname_from_filename(filename: str) -> Optional[str]:
    """
    Extract student last name from filename.
    Expected format: "lastnamefirstname_ID_..." or similar.
    Returns lowercase lastname, or None if cannot parse.
    """
    # Remove .pdf extension
    base = filename.lower().replace(".pdf", "").strip()
    
    # Common pattern: lastname_firstname_numbers_description
    # Try to extract the first token before underscore
    parts = base.split("_")
    if not parts:
        return None
    
    first_part = parts[0]
    
    # The first part typically contains lastname+firstname concatenated
    # We need a heuristic: usually last name is shorter or comes first.
    # For now, assume the whole first_part is lastname+firstname
    # and we'll do a fuzzy match against known students
    
    # For now, just return the first underscore-separated token
    # This will be matched against rosters
    return first_part if first_part else None


def get_student_roster(section: int) -> set:
    """Get the set of lowercase student last names for a section."""
    if section == 105:
        return SECTION_105
    elif section == 106:
        return SECTION_106
    else:
        raise ValueError(f"Unknown section: {section}")


def filename_matches_roster(filename: str, roster: set) -> bool:
    """
    Check if filename indicates a student in the roster.
    Extracts lastname from filename and checks membership.
    """
    lastname = extract_student_lastname_from_filename(filename)
    if lastname is None:
        return False
    
    # Check if extracted name matches any roster entry
    # Use fuzzy matching: check if lastname is contained in or contains roster names
    for roster_name in roster:
        # Exact match
        if lastname == roster_name:
            return True
        # Substring match (handles cases like "aguirregutierrez" vs "aguirre")
        if roster_name in lastname or lastname in roster_name:
            return True
    
    return False


def find_evaluation_files(directory: Path, section: int) -> list[Path]:
    """
    Scan directory for PDF files matching section roster.
    Returns list of valid PDF paths.
    """
    roster = get_student_roster(section)
    valid_files = []
    
    for pdf_path in sorted(directory.glob("*.pdf")):
        if filename_matches_roster(pdf_path.name, roster):
            valid_files.append(pdf_path)
    
    return valid_files


def aggregate_results(results: list[EvalResult]) -> dict:
    """
    Aggregate multiple EvalResult objects into section-level statistics.
    Returns dict mapping metric names to (count, mean, stdev, min, max).
    """
    if not results:
        return {}
    
    aggregates = {}
    
    for label in METRIC_LABELS:
        scores = [r.scores.get(label) for r in results if label in r.scores]
        scores = [s for s in scores if s is not None]
        
        if not scores:
            continue
        
        count = len(scores)
        avg = mean(scores) if scores else 0
        sd = stdev(scores) if len(scores) > 1 else 0
        minimum = min(scores)
        maximum = max(scores)
        
        aggregates[label] = {
            "count": count,
            "mean": avg,
            "stdev": sd,
            "min": minimum,
            "max": maximum,
        }
    
    return aggregates


def print_aggregates(section: int, aggregates: dict, total_files: int, processed_files: int):
    """Print section-level aggregate statistics."""
    print("\n")
    print("=" * 70)
    print(f"  SECTION {section} AGGREGATE STATISTICS")
    print(f"  Files processed: {processed_files} / {total_files}")
    print("=" * 70)
    print(f"\n  {'Metric':<38} {'N':>3}  {'Mean':>6}  {'SD':>6}  {'Min':>3}  {'Max':>3}")
    print("  " + "-" * 66)
    
    for label in METRIC_LABELS:
        if label not in aggregates:
            continue
        
        agg = aggregates[label]
        inv = " ↓" if label in INVERTED_METRICS else ""
        print(
            f"  {label:<38} {agg['count']:>3}  "
            f"{agg['mean']:>6.2f}  {agg['stdev']:>6.2f}  "
            f"{agg['min']:>3}  {agg['max']:>3}{inv}"
        )
    
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse MS 114 peer evaluation PDFs"
    )
    parser.add_argument(
        "--path",
        required=True,
        type=Path,
        help="Path to a single PDF file, or a directory containing evaluation PDFs"
    )
    parser.add_argument(
        "--section",
        type=int,
        choices=[105, 106],
        help="Section number (105 or 106) — required when --path is a directory"
    )

    args = parser.parse_args()

    # ── Single-file mode ──────────────────────────────────────────────────────
    if args.path.is_file():
        if not args.path.suffix.lower() == ".pdf":
            print(f"Error: {args.path} is not a PDF file", file=sys.stderr)
            sys.exit(1)
        result = parse_evaluation(str(args.path))
        print_result(result)
        sys.exit(0)

    # ── Directory mode ────────────────────────────────────────────────────────
    if not args.path.is_dir():
        print(f"Error: {args.path} is not a file or directory", file=sys.stderr)
        sys.exit(1)

    if args.section is None:
        print("Error: --section is required when --path is a directory", file=sys.stderr)
        sys.exit(1)

    # Find matching files
    print(f"Scanning {args.path} for section {args.section} students...")
    pdf_files = find_evaluation_files(args.path, args.section)

    if not pdf_files:
        print(f"No evaluation files found for section {args.section}")
        sys.exit(0)

    print(f"Found {len(pdf_files)} evaluation files. Processing...\n")

    # Parse each file
    results = []
    skipped = 0

    for pdf_path in pdf_files:
        try:
            result = parse_evaluation(str(pdf_path))

            # Skip completely blank submissions
            if not result.scores:
                print(f"  {RED}⊘ SKIPPED (blank): {pdf_path.name}{RESET}")
                skipped += 1
                continue

            results.append(result)
            print(f"  {GREEN}✓ {pdf_path.name}{RESET}")
        except Exception as e:
            print(f"  {RED}✗ ERROR reading {pdf_path.name}: {e}{RESET}")
            skipped += 1

    # Compute aggregates
    aggregates = aggregate_results(results)
    
    # Print results
    print_aggregates(args.section, aggregates, len(pdf_files), len(results))
    
    if skipped > 0:
        print(f"Note: {skipped} file(s) skipped (blank or error)")
