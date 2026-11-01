"""Insights agent: a short narrative about workforce metrics that were already computed in code.

The input is trusted, aggregated, name-free data (orchestration/analytics.py `insight_metrics`). The agent only writes
sentences. Every number it writes is checked against the metrics it cites: an observation quoting a number that is not
in them is dropped, and a headline that fails the check is replaced by a fixed one. So the narrative can be wrong in
tone, but not in its figures.
"""
import json
import re

from backend.agents.llm import complete_json
from backend.agents.schemas import InsightsDraft

TASK = (
    "You summarise workforce analytics for an HR team. The data is a list of metrics as `key: value` and a list of "
    "flags (teams that may be under-resourced, with the rule that fired). Write `headline`: one sentence of at most "
    "120 characters on the most important point. Write up to 5 `observations`, one sentence each, ordered by "
    "importance; in `metric_keys` list the keys whose values the sentence quotes. Quote numbers exactly as they appear "
    "in the data (a key ending in _pct is a percentage). Use no other numbers, no names of people (there are none in "
    "the data), and no advice about individuals. Write in English."
)
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def _allowed(values: list[float]) -> list[float]:
    out = []
    for v in values:
        out += [v, round(v), round(v, 1)]
    return out


def numbers_ok(text: str, values: list[float]) -> bool:
    """Every number in the text matches one of the values (as written, or rounded to 0 or 1 decimal)."""
    allowed = _allowed(values)
    return all(any(abs(float(n) - a) < 0.051 for a in allowed) for n in NUMBER.findall(text))


def check(draft: InsightsDraft, metrics: dict) -> tuple[str, list[dict], int]:
    """(headline, kept observations, number dropped). Keys must exist; numbers must come from the cited keys."""
    kept, dropped = [], 0
    for o in draft.observations:
        if o.metric_keys and all(k in metrics for k in o.metric_keys) and numbers_ok(o.text, [metrics[k] for k in o.metric_keys]):
            kept.append({"text": o.text, "metric_keys": o.metric_keys})
        else:
            dropped += 1
    headline = draft.headline if len(draft.headline) <= 160 and numbers_ok(draft.headline, list(metrics.values())) else ""
    return headline, kept, dropped


def template_headline(flags: dict[str, list[str]], names: dict[str, str]) -> str:
    flagged = [names.get(t, t) for t, f in flags.items() if f]
    if not flagged:
        return "No team is flagged as under-resourced."
    return f"{', '.join(flagged)} {'is' if len(flagged) == 1 else 'are'} flagged as under-resourced."


def _fmt(v: float) -> str:
    return f"{v:g}"


def _mock(metrics: dict, flags: dict[str, list[str]], names: dict[str, str]) -> dict:
    """Rule-based narrative: the most severe flag first, then the gaps and the approval queue."""
    obs = []
    ranked = sorted(flags.items(), key=lambda kv: -len(kv[1]))
    for team, f in ranked:
        name = names.get(team, team)
        if not f:
            continue
        key = f"team.{name}.utilisation_pct"
        if key in metrics:
            obs.append({"text": f"{name} is at {_fmt(metrics[key])}% of its capacity.", "metric_keys": [key]})
        gap_key = f"team.{name}.gap_fte"
        if metrics.get(gap_key):
            obs.append({"text": f"{name} has {_fmt(metrics[gap_key])} FTE of open skill gaps.", "metric_keys": [gap_key]})
    if metrics.get("company.single_points_of_failure"):
        k = "company.single_points_of_failure"
        obs.append({"text": f"{_fmt(metrics[k])} required skill(s) depend on a single person.", "metric_keys": [k]})
    if metrics.get("pipeline.pending_approvals"):
        k, h = "pipeline.pending_approvals", "pipeline.oldest_approval_hours"
        obs.append({"text": f"{_fmt(metrics[k])} actions wait for approval; the oldest has waited {_fmt(metrics[h])} hours.",
                    "metric_keys": [k, h]})
    worst = next((names.get(t, t) for t, f in ranked if f), None)
    headline = f"{worst} needs attention first: {flags[next(t for t, f in ranked if f)][0]}." if worst else \
        "No team is flagged as under-resourced."
    if not numbers_ok(headline, list(metrics.values())):  # a threshold in the flag text is not a metric
        headline = template_headline(flags, names)
    return {"headline": headline[:160], "observations": obs[:5]}


def summarise(metrics: dict, flags: dict[str, list[str]], names: dict[str, str]) -> dict:
    data = {"metrics": metrics, "flags": {names.get(t, t): f for t, f in flags.items() if f}}
    draft, fallback = complete_json(TASK, InsightsDraft, lambda: _mock(metrics, flags, names),
                                    trusted_context=json.dumps(data, indent=1))
    headline, kept, dropped = check(draft, metrics)
    return {"headline": headline or template_headline(flags, names), "observations": kept, "dropped": dropped,
            "llm_fallback": fallback}
