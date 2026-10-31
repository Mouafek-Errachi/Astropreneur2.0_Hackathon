"""Learns the scoring weights from past hires, so the score reflects what actually predicted success here
(performance review and Jira tickets done).

Only the weights are learned: the score stays the same explainable formula (required, nice, experience), and
resume text can still not set its own score. The input is limited to job-related facts plus an optional group
column used only for the bias check. Learned weights are a proposal: HR approves them in the approval queue."""
import csv
import io
import math
from dataclasses import dataclass

from backend.agents.ranker import DEFAULT_WEIGHTS, features
from backend.agents.schemas import CandidateProfile

# Anything else (name, age, gender, photo, address...) is refused: the model must not see it.
REQUIRED_COLUMNS = {"skills", "years_experience", "jira_done", "performance"}
OPTIONAL_COLUMNS = {"group"}  # never a feature: only used to compare selection rates between groups
MAX_BYTES = 1_000_000
MAX_ROWS = 2000
MIN_ROWS = 30
MIN_PER_OUTCOME = 5
SELECTED_AT = 70  # a "strong" score in ranking.bucket()
FOUR_FIFTHS = 0.8  # adverse-impact rule: a group's selection rate below 80% of the best group's fails
PERFORMANCE_SHARE = 0.5  # success = 50% performance review + 50% Jira tickets done (ranked among colleagues)


class LearnError(ValueError):
    pass


@dataclass
class Record:
    features: dict
    outcome: int
    group: str = ""
    success: float = 0.0  # 0-100, the score the outcome is derived from


def read_csv(data: bytes, required: list[str], nice: list[str], min_years: float) -> list[Record]:
    """One row per past hire: skills and years of experience at hiring time, Jira tickets done, and the
    performance review out of 100. The outcome is computed, see _outcomes()."""
    if len(data) > MAX_BYTES:
        raise LearnError(f"File too large (max {MAX_BYTES // 1000} KB)")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LearnError("The file must be UTF-8 CSV")
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.strip().lower() for c in reader.fieldnames or []}
    if extra := cols - REQUIRED_COLUMNS - OPTIONAL_COLUMNS:
        raise LearnError(f"Unexpected column(s): {', '.join(sorted(extra))}. Only skills, years_experience, "
                         "jira_done, performance and group are accepted, so no personal data reaches the model.")
    if missing := REQUIRED_COLUMNS - cols:
        raise LearnError(f"Missing column(s): {', '.join(sorted(missing))}")
    records, results = [], []
    for line, row in enumerate(reader, start=2):
        if len(records) >= MAX_ROWS:
            raise LearnError(f"Too many rows (max {MAX_ROWS})")
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        try:
            years = float(row["years_experience"] or 0)
        except ValueError:
            raise LearnError(f"Line {line}: years_experience must be a number")
        if not 0 <= years <= 60:
            raise LearnError(f"Line {line}: years_experience must be between 0 and 60")
        try:
            jira, perf = int(row["jira_done"] or 0), float(row["performance"])
        except ValueError:
            raise LearnError(f"Line {line}: jira_done must be a whole number and performance a number")
        if not 0 <= jira <= 100_000 or not 0 <= perf <= 100:
            raise LearnError(f"Line {line}: jira_done must be 0 or more and performance between 0 and 100")
        skills = [s for s in row["skills"].replace(";", ",").split(",") if s.strip()]
        profile = CandidateProfile(skills=skills[:80], years_experience=years)
        records.append(Record(features(profile, required, nice, min_years), 0, row.get("group", "")[:40]))
        results.append((jira, perf))
    _outcomes(records, results)
    return records


def _outcomes(records: list[Record], results: list[tuple[int, float]]) -> None:
    """success = performance and Jira tickets done, 50/50. Jira counts are turned into a 0-100 rank among the
    colleagues in the file, so the scale does not depend on the team. Good outcome = success at or above the
    median: the better half of the file."""
    if not records:
        return
    counts = [j for j, _ in results]
    for r, (jira, perf) in zip(records, results):
        rank = (sum(c < jira for c in counts) + 0.5 * (counts.count(jira) - 1)) / max(len(counts) - 1, 1)
        r.success = round(PERFORMANCE_SHARE * perf + (1 - PERFORMANCE_SHARE) * 100 * rank, 1)
    median = sorted(r.success for r in records)[(len(records) - 1) // 2]
    for r in records:
        r.outcome = int(r.success >= median)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination for the small (at most 4x4) Newton step."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c and m[c][c]:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if m[i][i] else 0.0 for i in range(n)]


def fit_logistic(x: list[list[float]], y: list[int], l2: float = 1.0, iters: int = 30) -> list[float]:
    """Ridge logistic regression by Newton's method. Returns [intercept, coef...]. The penalty keeps small
    datasets from producing extreme weights; the intercept is not penalised."""
    rows = [[1.0] + r for r in x]
    k = len(rows[0])
    beta = [0.0] * k
    for _ in range(iters):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for r, t in zip(rows, y):
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, sum(b * v for b, v in zip(beta, r))))))
            w = p * (1 - p)
            for i in range(k):
                grad[i] += (p - t) * r[i]
                for j in range(k):
                    hess[i][j] += w * r[i] * r[j]
        for i in range(1, k):
            grad[i] += l2 * beta[i]
            hess[i][i] += l2
        step = _solve(hess, grad)
        beta = [b - s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-6:
            break
    return beta


def total(f: dict, weights: dict) -> float:
    """Same formula as scorer.score(): weighted average over the criteria the job defines."""
    active = {k: w for k, w in weights.items() if f.get(k) is not None and w > 0}
    s = sum(active.values())
    return 100 * sum(w * f[k] for k, w in active.items()) / s if s else 0.0


def selection_rates(records: list[Record], weights: dict) -> dict:
    out = {}
    for g in sorted({r.group for r in records}):
        members = [r for r in records if r.group == g]
        out[g] = {"n": len(members), "rate": round(sum(total(r.features, weights) >= SELECTED_AT for r in members)
                                                   / len(members), 3)}
    return out


def impact_ratio(rates: dict) -> float:
    best = max(v["rate"] for v in rates.values())
    return round(min(v["rate"] for v in rates.values()) / best, 3) if best else 1.0


def learn(records: list[Record], current: dict | None = None) -> dict:
    current = {**DEFAULT_WEIGHTS, **(current or {})}
    if len(records) < MIN_ROWS:
        raise LearnError(f"Need at least {MIN_ROWS} past hires to learn from, got {len(records)}. "
                         "Keep the current weights.")
    good = sum(r.outcome for r in records)
    if min(good, len(records) - good) < MIN_PER_OUTCOME:
        raise LearnError(f"Need at least {MIN_PER_OUTCOME} good and {MIN_PER_OUTCOME} not-good outcomes, "
                         f"got {good} and {len(records) - good}")
    keys = [k for k in DEFAULT_WEIGHTS if records[0].features[k] is not None]
    if not keys:
        raise LearnError("This job defines no criteria to weight")
    beta = fit_logistic([[r.features[k] for k in keys] for r in records], [r.outcome for r in records])
    coef = dict(zip(keys, beta[1:]))
    positive = {k: max(c, 0.0) for k, c in coef.items()}
    if not sum(positive.values()):
        raise LearnError("None of the criteria predicts a good outcome in this data. Keep the current weights.")
    learned = {k: round(100 * v / sum(positive.values())) for k, v in positive.items()}
    top = max(learned, key=learned.get)
    learned[top] += 100 - sum(learned.values())  # rounding: the weights add up to exactly 100
    weights = {**current, **learned}  # criteria the job does not define keep their value (they are ignored)

    predicted = [1 / (1 + math.exp(-(beta[0] + sum(coef[k] * r.features[k] for k in keys)))) >= 0.5 for r in records]
    report = {
        "weights": weights,
        "previous_weights": current,
        "learned_from": len(records),
        "good_outcomes": good,
        "outcome_rule": {"performance_share": round(100 * PERFORMANCE_SHARE),
                         "jira_share": round(100 * (1 - PERFORMANCE_SHARE)),
                         "good_if_success_at_least": min(r.success for r in records if r.outcome)},
        "coefficients": {k: round(c, 3) for k, c in coef.items()},
        "accuracy": round(sum(p == bool(r.outcome) for p, r in zip(predicted, records)) / len(records), 3),
        "baseline_accuracy": round(max(good, len(records) - good) / len(records), 3),
    }
    groups = {r.group for r in records if r.group}
    if len(groups) >= 2 and all(r.group for r in records):
        new, old = selection_rates(records, weights), selection_rates(records, current)
        ratio = impact_ratio(new)
        report["fairness"] = {"checked": True, "selected_at": SELECTED_AT, "rule": "four-fifths",
                              "rates": {g: {"n": new[g]["n"], "before": old[g]["rate"], "after": new[g]["rate"]}
                                        for g in new},
                              "impact_ratio_before": impact_ratio(old), "impact_ratio": ratio,
                              "passed": ratio >= FOUR_FIFTHS}
    else:
        report["fairness"] = {"checked": False,
                              "reason": "Add a group column (filled on every row, at least 2 groups) to run the "
                                        "bias check"}
    return report
