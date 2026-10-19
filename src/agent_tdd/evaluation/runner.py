"""Sequential evaluation service with explicit evidence, comparison, and release outcomes."""

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agent_tdd.context import Clock
from agent_tdd.evaluation.contracts import Executor, Gates, Grade, Grader, Sample, Suite
from agent_tdd.types import RunResult, Status

ERROR_STATUSES = {Status.MODEL_ERROR, Status.EXECUTION_ERROR, Status.CANCELLED, Status.DEADLINE}


def _baseline_rate(
    baseline: dict[str, Any], comparison: dict[str, Any], rows: list[dict[str, Any]]
) -> float:
    if (
        type(baseline.get("schema_version")) is not int
        or baseline.get("schema_version") != 1
        or baseline.get("comparison") != comparison
    ):
        raise ValueError("baseline has incompatible cases, split, repeats, or grader")
    previous = baseline.get("runs")
    if not isinstance(previous, list) or len(previous) != len(rows):
        raise ValueError("baseline runs are missing")
    expected = [(r["case_id"], r["repeat"]) for r in rows]
    actual: list[tuple[str, int]] = []
    for row in previous:
        if not isinstance(row, dict) or type(row.get("quality_passed")) is not bool:
            raise ValueError("invalid baseline run")
        case_id, repeat = row.get("case_id"), row.get("repeat")
        if not isinstance(case_id, str) or type(repeat) is not int:
            raise ValueError("invalid baseline identity")
        actual.append((case_id, repeat))
    if actual != expected:
        raise ValueError("baseline run identities differ")
    if baseline.get("passed") is not True:
        raise ValueError("baseline must have passed its release gates")
    return sum(1 for row in previous if row["quality_passed"]) / len(previous)


def evaluate(
    suite: Suite,
    executor: Executor,
    grader: Grader,
    clock: Clock,
    *,
    provenance: dict[str, str],
    gates: Gates | None = None,
    split: str = "release",
    repeats: int = 1,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gates = gates or Gates()
    if split not in ("dev", "release") or type(repeats) is not int or not 1 <= repeats <= 100:
        raise ValueError("invalid split or repeat count")
    cases = [case for case in suite.cases if case.split == split]
    if not cases or len(cases) * repeats > 10_000:
        raise ValueError("selected suite must contain 1–10000 runs")
    comparison = {
        "suite_sha256": suite.digest,
        "split": split,
        "repeats": repeats,
        "grader_version": grader.version,
    }
    identities = [
        {"case_id": case.id, "repeat": repeat} for case in cases for repeat in range(1, repeats + 1)
    ]
    if baseline is not None:
        previous_provenance = baseline.get("provenance")
        if not isinstance(previous_provenance, dict) or previous_provenance.get(
            "suite_kind"
        ) != provenance.get("suite_kind"):
            raise ValueError("baseline and candidate must have the same evidence kind")
    baseline_rate = (
        _baseline_rate(baseline, comparison, identities) if baseline is not None else None
    )
    rows: list[dict[str, Any]] = []
    requests = 0
    total_tokens = 0
    cost = 0.0
    tokens_known = True
    cost_known = True
    reasons: set[str] = set()
    poisoned = False
    requests_complete = True
    for case in cases:
        for repeat in range(1, repeats + 1):
            started = clock.monotonic()
            remaining = gates.max_requests - requests
            blocked = (
                poisoned
                or remaining <= 0
                or (
                    gates.max_total_tokens is not None
                    and (not tokens_known or total_tokens >= gates.max_total_tokens)
                )
                or (
                    gates.max_estimated_cost_usd is not None
                    and (not cost_known or cost >= gates.max_estimated_cost_usd)
                )
            )
            try:
                sample = (
                    Sample(RunResult(Status.EXECUTION_ERROR, error_code="suite_budget_exhausted"))
                    if blocked
                    else executor.run(case, request_id=str(uuid4()), max_requests=remaining)
                )
            except Exception:
                # Grader/executor defects are missing evidence, never a passing/omitted row.
                sample = Sample(RunResult(Status.EXECUTION_ERROR, error_code="evaluation_error"))
                poisoned = True
                requests_complete = False
            try:
                grade = grader.grade(case, sample)
            except Exception:
                grade = Grade(False, failures=("grader_error",))
                reasons.add("grader_errors")
            elapsed = max(0, clock.monotonic() - started)
            usage = sample.usage
            requests += usage.requests
            if usage.input_tokens is None or usage.output_tokens is None:
                tokens_known = False
            else:
                total_tokens += usage.input_tokens + usage.output_tokens
            if usage.estimated_cost_usd is None:
                cost_known = False
            else:
                cost += usage.estimated_cost_usd
            error = sample.result.status in ERROR_STATUSES
            if error:
                reasons.add("execution_errors")
            if grade.policy_violations:
                reasons.add("policy_violations")
            if elapsed > gates.max_latency_seconds:
                reasons.add("latency_limit")
            rows.append(
                {
                    "case_id": case.id,
                    "split": case.split,
                    "tags": list(case.tags),
                    "repeat": repeat,
                    "status": sample.result.status.value,
                    "quality_passed": grade.quality_passed and not error,
                    "policy_violations": list(grade.policy_violations),
                    "failures": list(grade.failures),
                    "elapsed_seconds": elapsed,
                    "usage": asdict(usage),
                    "model_calls": sample.result.model_calls,
                }
            )
    quality_rate = sum(row["quality_passed"] for row in rows) / len(rows)
    if quality_rate < gates.min_pass_rate:
        reasons.add("quality_floor")
    if requests > gates.max_requests:
        reasons.add("request_limit")
    if gates.max_total_tokens is not None and (
        not tokens_known or total_tokens > gates.max_total_tokens
    ):
        reasons.add("token_limit_or_missing_usage")
    if gates.max_estimated_cost_usd is not None and (
        not cost_known or cost > gates.max_estimated_cost_usd
    ):
        reasons.add("cost_limit_or_missing_usage")
    if baseline_rate is not None and baseline_rate - quality_rate > gates.max_regression + 1e-12:
        reasons.add("quality_regression")
    slices = {}
    for tag in sorted({tag for row in rows for tag in row["tags"]}):
        selected = [row for row in rows if tag in row["tags"]]
        slices[tag] = {
            "runs": len(selected),
            "pass_rate": sum(row["quality_passed"] for row in selected) / len(selected),
            "policy_violations": sum(bool(row["policy_violations"]) for row in selected),
        }
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "suite": suite.name,
        "suite_version": suite.version,
        "comparison": comparison,
        "provenance": dict(provenance),
        "gates": asdict(gates),
        "passed": not reasons,
        "gate_failures": sorted(reasons),
        "summary": {
            "runs": len(rows),
            "pass_rate": quality_rate,
            "baseline_pass_rate": baseline_rate,
            "requests": requests if requests_complete else None,
            "known_requests": requests,
            "total_tokens": total_tokens if tokens_known else None,
            "estimated_cost_usd": cost if cost_known else None,
            "policy_violations": sum(bool(row["policy_violations"]) for row in rows),
        },
        "slices": slices,
        "runs": rows,
    }
