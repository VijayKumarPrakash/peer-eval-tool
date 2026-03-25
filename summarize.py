"""
Qualitative summarization for MS 114 peer evaluations.

Collects good points, murky points, and per-anchor item-level comments
(keyed by metric label or section header label) from all EvalResult objects
in a batch, then calls the Claude API to produce a per-dimension prose summary.

Usage:
    from summarize import summarize_qualitative
    print(summarize_qualitative(list_of_eval_results))
"""

import os
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()


def summarize_qualitative(results: list, inverted_metrics: set = None) -> str:
    """
    Synthesize qualitative feedback from a batch of EvalResult objects into a
    per-dimension prose summary.

    Dimensions come from three sources:
      - item_comments keys  (metric labels like "Course Themes", or section
        header labels like "Content" if a student wrote there instead)
      - overall good_point text across all evaluations
      - overall murky_point text across all evaluations

    Each dimension with sufficient data gets a 1-2 sentence summary.

    Args:
        results: All EvalResult objects for the batch (one DL group).
        inverted_metrics: Set of metric label strings where a LOW score is
            better (e.g. "Too much reading of text", "Slides: cluttered with
            text"). Passed to the LLM so it interprets comments correctly.

    Returns:
        Formatted multi-line string with one labeled section per dimension.

    Raises:
        RuntimeError: If ANTHROPIC_API_KEY is not set or anthropic not installed.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set.\n"
            "Add it to your .env file:  ANTHROPIC_API_KEY=sk-ant-..."
        )

    # Collect per-dimension data across all evaluators
    item_comments_by_label: dict[str, list[str]] = defaultdict(list)
    for r in results:
        for label, comment in r.item_comments.items():
            if comment.strip():
                item_comments_by_label[label].append(comment.strip())

    good_points = [r.good_point.strip() for r in results if r.good_point.strip()]
    murky_points = [r.murky_point.strip() for r in results if r.murky_point.strip()]

    total = (
        sum(len(v) for v in item_comments_by_label.values())
        + len(good_points)
        + len(murky_points)
    )
    if total < 2:
        return "(Insufficient qualitative feedback to summarize.)"

    # Build prompt blocks — one per dimension
    blocks = []
    active_dimensions = []

    for label, comments in item_comments_by_label.items():
        lines = "\n".join(f"  - {c}" for c in comments)
        blocks.append(f"[{label}] ({len(comments)} comment(s)):\n{lines}")
        active_dimensions.append(label)

    if good_points:
        lines = "\n".join(f"  - {p}" for p in good_points)
        blocks.append(f"[Good Points] ({len(good_points)} response(s)):\n{lines}")
        active_dimensions.append("Good Points")

    if murky_points:
        lines = "\n".join(f"  - {p}" for p in murky_points)
        blocks.append(f"[Murky Points] ({len(murky_points)} response(s)):\n{lines}")
        active_dimensions.append("Murky Points")

    feedback_block = "\n\n".join(blocks)
    format_example = "\n".join(f"{d}: [1-2 sentence summary]" for d in active_dimensions)

    inverted_note = ""
    if inverted_metrics:
        names = " and ".join(f'"{m}"' for m in sorted(inverted_metrics))
        inverted_note = (
            f"\nIMPORTANT: The metrics {names} are scored inversely — "
            "a LOW score (e.g. 1–3) means the presenter did WELL on that dimension "
            "(e.g. did not read from slides much; slides were not cluttered). "
            "Interpret any comments about these metrics accordingly.\n"
        )

    prompt = (
        "You are summarizing peer feedback for a discussion leader group in a "
        "university media studies course.\n"
        f"{inverted_note}\n"
        "Below is the verbatim written feedback from evaluators, organized by "
        "evaluation dimension:\n\n"
        f"{feedback_block}\n\n"
        "Write a 1-2 sentence summary for each dimension. "
        "Base your summary EXCLUSIVELY on the written text above — do not infer, "
        "assume, or extrapolate beyond what evaluators explicitly wrote. "
        "Do not use the dimension label names as evidence of anything; only the "
        "actual comment text counts. If comments for a dimension are sparse or "
        "ambiguous, reflect that uncertainty rather than filling in gaps.\n\n"
        "Format your response exactly as shown, with one dimension per line:\n\n"
        f"{format_example}\n\n"
    )

    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "The 'anthropic' package is not installed.\n"
            "Run:  pip install anthropic"
        )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()
