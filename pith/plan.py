"""Turn a project into the edit the window and the exporter share.

The window never decides which seconds get cut. It sends threshold,
padding, and edits. This module recomputes the plan, so the preview and
the export cannot drift apart.
"""

from __future__ import annotations

from pith.captions import group_lines
from pith.ranges import complement_ranges, first_overlap, map_source_to_output, overlap_amount, range_duration
from pith.silence import compute_keeps

CUT_LABELS = {
    "before": "Quiet before the first word",
    "between": "Pause between words",
    "after": "Quiet after the last word",
    "deleted": "A word you removed",
    "segment": "A segment you cut",
}


def _spans(pairs) -> list[tuple[float, float]]:
    spans = []
    for pair in pairs or []:
        if not pair or len(pair) < 2:
            continue
        start, end = float(pair[0]), float(pair[1])
        if end > start:
            spans.append((start, end))
    return spans


def _round(value: float) -> float:
    return round(float(value), 3)


def classify_cut(start: float, end: float, words, forced) -> str:
    length = end - start
    for hole_start, hole_end in forced:
        if overlap_amount(start, end, [(hole_start, hole_end)]) >= 0.5 * length:
            return "segment"
    for word in words:
        if not word.deleted:
            continue
        if overlap_amount(start, end, [(word.start, word.end)]) >= 0.5 * min(length, max(0.01, word.end - word.start)):
            return "deleted"
    active = [word for word in words if not word.deleted]
    if not active:
        return "between"
    active.sort(key=lambda word: word.start)
    if end <= active[0].start + 0.08:
        return "before"
    if start >= active[-1].end - 0.08:
        return "after"
    return "between"


def caption_words(words, keeps) -> list[dict]:
    """Words that survived, with both source times and output times."""

    spoken = []
    for word in sorted(words, key=lambda item: (item.start, item.id)):
        if word.deleted:
            continue
        piece = first_overlap(word.start, word.end, keeps)
        if piece is None:
            continue
        out_start = map_source_to_output(piece[0], keeps)
        out_end = map_source_to_output(piece[1], keeps)
        if out_end < out_start + 0.04:
            out_end = out_start + 0.04
        spoken.append(
            {
                "id": word.id,
                "text": word.text,
                "start": word.start,
                "end": word.end,
                "out_start": out_start,
                "out_end": out_end,
            }
        )
    return spoken


def build_snippets(words, cuts, loud_gaps) -> list[dict]:
    """Transcript order: words, with pause markers dropped in the gaps."""

    ordered = sorted(words, key=lambda word: (word.start, word.id))
    markers = []
    for cut in cuts:
        if cut["reason"] in {"before", "between", "after"}:
            markers.append(("cut", (cut["start"] + cut["end"]) / 2, cut))
    for gap in loud_gaps:
        markers.append(("loud", (gap["start"] + gap["end"]) / 2, gap))
    markers.sort(key=lambda item: item[1])
    snippets = []
    index = 0
    for kind, _mid, payload in markers:
        while index < len(ordered) and ordered[index].start <= _mid:
            snippets.append({"type": "word", "id": ordered[index].id})
            index += 1
        item = {"type": kind}
        item.update(payload)
        snippets.append(item)
    while index < len(ordered):
        snippets.append({"type": "word", "id": ordered[index].id})
        index += 1
    return snippets


def build_plan(project, rms=None, hop: float = 0.02) -> dict:
    duration = float(project.duration or 0)
    if not project.transcript_done:
        keeps = [(0.0, duration)] if duration > 0 else []
        return {
            "ready": False,
            "duration": _round(duration),
            "keeps": [{"start": 0, "end": _round(duration)}] if keeps else [],
            "cuts": [],
            "loud_gaps": [],
            "restored": [],
            "forced": [],
            "words": [],
            "lines": [],
            "snippets": [],
            "energy": rms is not None and len(rms) > 0,
            "note": "Transcribe the clip to find pauses.",
            "stats": {
                "keep_seconds": _round(duration),
                "cut_seconds": 0,
                "cut_count": 0,
            },
        }

    keeps, loud = compute_keeps(
        project.words,
        duration,
        float(project.pause_threshold),
        float(project.padding),
        _spans(project.restored),
        _spans(project.forced_cuts),
        rms=rms,
        hop=hop,
    )
    forced = _spans(project.forced_cuts)
    raw_cuts = complement_ranges(keeps, duration)
    cuts = []
    for start, end in raw_cuts:
        reason = classify_cut(start, end, project.words, forced)
        cuts.append(
            {
                "start": _round(start),
                "end": _round(end),
                "seconds": _round(end - start),
                "reason": reason,
                "label": CUT_LABELS[reason],
            }
        )
    loud_gaps = [
        {
            "start": _round(start),
            "end": _round(end),
            "seconds": _round(end - start),
            "label": "Left in because this gap is not quiet",
        }
        for start, end in loud
    ]
    spoken = caption_words(project.words, keeps)
    lines = []
    for group in group_lines(spoken, project.style.words_per_line):
        lines.append(
            {
                "start": _round(group[0]["start"]),
                "end": _round(group[-1]["end"]),
                "out_start": _round(group[0]["out_start"]),
                "out_end": _round(group[-1]["out_end"]),
                "words": [
                    {
                        "id": word["id"],
                        "text": word["text"],
                        "start": _round(word["start"]),
                        "end": _round(word["end"]),
                        "out_start": _round(word["out_start"]),
                        "out_end": _round(word["out_end"]),
                    }
                    for word in group
                ],
            }
        )
    included = {word["id"] for word in spoken}
    public_words = []
    for word in sorted(project.words, key=lambda item: (item.start, item.id)):
        public_words.append(
            {
                "id": word.id,
                "text": word.text,
                "start": _round(word.start),
                "end": _round(word.end),
                "deleted": word.deleted,
                "included": word.id in included,
                "probability": round(float(word.probability), 2),
            }
        )
    note = ""
    if project.transcript_done and not project.words:
        note = "No speech detected, so nothing was cut."
    elif not cuts:
        note = "No pauses to cut. Lower the threshold if you want it tighter."
    return {
        "ready": True,
        "duration": _round(duration),
        "keeps": [{"start": _round(start), "end": _round(end)} for start, end in keeps],
        "cuts": cuts,
        "loud_gaps": loud_gaps,
        "restored": [
            {"start": _round(start), "end": _round(end), "seconds": _round(end - start)}
            for start, end in _spans(project.restored)
        ],
        "forced": [
            {"start": _round(start), "end": _round(end), "seconds": _round(end - start)}
            for start, end in forced
        ],
        "words": public_words,
        "lines": lines,
        "snippets": build_snippets(project.words, cuts, loud_gaps),
        "energy": rms is not None and len(rms) > 0,
        "note": note,
        "stats": {
            "keep_seconds": _round(range_duration(keeps)),
            "cut_seconds": _round(range_duration(raw_cuts)),
            "cut_count": len(cuts),
        },
        # Full-precision keeps for the exporter. Not rounded, so a cut
        # does not drift by a millisecond on every save.
        "_keeps": keeps,
        "_spoken": spoken,
    }


def public_plan(plan: dict) -> dict:
    """Drop the full-precision fields before sending the plan to the window."""

    return {key: value for key, value in plan.items() if not key.startswith("_")}
