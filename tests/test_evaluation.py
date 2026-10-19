"""Release gates must reject missing evidence, hide payloads, and avoid duplicate paid calls."""

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agent_tdd.adapters.local import SystemClock
from agent_tdd.evaluation.cases import load_suite, read_object
from agent_tdd.evaluation.contracts import Case, Gates, Sample, Suite, Usage
from agent_tdd.evaluation.executors import AgentExecutor, ScriptedEvaluationModel
from agent_tdd.evaluation.graders import ExactGrader
from agent_tdd.evaluation.reports import write_reports
from agent_tdd.evaluation.runner import evaluate
from agent_tdd.types import Decision, Observation, RunResult, Status


def case(**overrides):
    values = dict(
        id="one",
        split="release",
        task={"secret": "task-private"},
        expected={"status": "completed", "output": {"secret": "answer-private"}},
        tags=("quality",),
        script=(Decision("finish", output={"secret": "answer-private"}),),
    )
    return Case(**(values | overrides))


def suite(*cases):
    return Suite("example", "1", cases or (case(),), "cases-hash")


class FakeExecutor:
    def __init__(self, sample=None, error=False):
        self.sample = sample or Sample(
            RunResult(Status.COMPLETED, {"secret": "answer-private"}), Usage(1, 10, 2, 0.02)
        )
        self.error = error
        self.calls = 0

    def run(self, case, *, request_id, max_requests):
        assert max_requests > 0
        assert request_id
        self.calls += 1
        if self.error:
            raise RuntimeError("secret-provider-payload")
        return self.sample


def run(executor=None, **kwargs):
    return evaluate(
        kwargs.pop("suite", suite()),
        executor or FakeExecutor(),
        ExactGrader(),
        SystemClock(),
        provenance={"suite_kind": "live", "model": "candidate"},
        **kwargs,
    )


def test_scripted_real_harness_fresh_each_repeat_and_metadata_only(tmp_path):
    s = load_suite(Path("evals/refund-routing.json"))
    report = evaluate(
        s,
        AgentExecutor(ScriptedEvaluationModel, SystemClock()),
        ExactGrader(),
        SystemClock(),
        provenance={"suite_kind": "scripted"},
        repeats=3,
    )
    assert report["passed"] and len(report["runs"]) == 18
    assert report["summary"]["requests"] == 18
    write_reports(tmp_path / "private" / "run.json", report)
    assert ET.parse(tmp_path / "private" / "run.xml").getroot().get("failures") == "0"
    if os.name != "nt":
        assert (tmp_path / "private" / "run.json").stat().st_mode & 0o777 == 0o600


def test_reports_hide_tasks_answers_exceptions_and_escape_html(tmp_path):
    report = run(suite=suite(case(id="<unsafe>")))
    write_reports(tmp_path / "run.json", report)
    for extension in ("json", "html", "xml"):
        content = (tmp_path / f"run.{extension}").read_text()
        assert "task-private" not in content and "answer-private" not in content
        assert "<unsafe>" not in content or extension == "json"
    assert "&lt;unsafe&gt;" in (tmp_path / "run.html").read_text()
    assert not list(tmp_path.glob(".agent-tdd-*"))
    with pytest.raises(ValueError):
        write_reports(tmp_path / "run.txt", report)


def test_policy_failures_cannot_be_averaged_away():
    expected = {"status": "completed", "max_model_calls": 0, "forbidden_tools": ["refund"]}
    result = RunResult(
        Status.COMPLETED, observations=(Observation("refund", False),), model_calls=1
    )
    report = run(
        FakeExecutor(Sample(result, Usage(1, 0, 0, 0))),
        suite=suite(case(expected=expected)),
        gates=Gates(min_pass_rate=0),
    )
    assert report["summary"]["pass_rate"] == 1
    assert not report["passed"] and report["gate_failures"] == ["policy_violations"]
    assert report["runs"][0]["policy_violations"] == ["model_call_limit", "forbidden_tool_observed"]


def test_quality_status_and_error_code_and_exact_json_types():
    sample = Sample(RunResult(Status.REJECTED, {"value": True}, error_code="wrong"))
    grade = ExactGrader().grade(
        case(expected={"status": "completed", "output": {"value": 1}, "error_code": "expected"}),
        sample,
    )
    assert grade.failures == ("status_mismatch", "output_mismatch", "error_code_mismatch")
    assert not grade.quality_passed


def test_budget_exhaustion_records_missing_runs_and_stops_dispatch():
    executor = FakeExecutor()
    report = run(executor, repeats=3, gates=Gates(max_requests=1))
    assert executor.calls == 1
    assert len(report["runs"]) == 3
    assert report["summary"]["requests"] == 1
    assert not report["passed"] and "execution_errors" in report["gate_failures"]


@pytest.mark.parametrize(
    "gates,usage,reason",
    [
        (Gates(max_total_tokens=5), Usage(1, 10, 2, 0.02), "token_limit_or_missing_usage"),
        (Gates(max_total_tokens=5), Usage(1), "token_limit_or_missing_usage"),
        (Gates(max_estimated_cost_usd=0.01), Usage(1, 10, 2, 0.02), "cost_limit_or_missing_usage"),
        (Gates(max_estimated_cost_usd=0.01), Usage(1), "cost_limit_or_missing_usage"),
        (Gates(max_requests=1), Usage(2), "request_limit"),
    ],
)
def test_usage_gates_unknown_or_exceeded(gates, usage, reason):
    executor = FakeExecutor(
        Sample(RunResult(Status.COMPLETED, {"secret": "answer-private"}), usage)
    )
    report = run(executor, repeats=2, gates=gates)
    assert not report["passed"] and reason in report["gate_failures"]
    assert executor.calls == 1


def test_executor_exception_does_not_leak_or_continue_paid_calls():
    executor = FakeExecutor(error=True)
    report = run(executor, repeats=2)
    assert executor.calls == 1
    assert not report["passed"] and "secret-provider-payload" not in json.dumps(report)
    assert len(report["runs"]) == 2


def test_grader_exception_preserves_usage_and_fails_closed():
    class Broken:
        version = "broken"

        def grade(self, case, sample):
            raise RuntimeError("secret grader")

    report = evaluate(suite(), FakeExecutor(), Broken(), SystemClock(), provenance={})
    assert not report["passed"] and report["summary"]["requests"] == 1
    assert "grader_errors" in report["gate_failures"]
    assert "secret grader" not in json.dumps(report)


def test_latency_gates_and_junit_suite_failure(tmp_path):
    class Clock:
        def __init__(self):
            self.tick = 0

        def monotonic(self):
            self.tick += 10
            return self.tick

        def time(self):
            return 0

    report = evaluate(
        suite(),
        FakeExecutor(),
        ExactGrader(),
        Clock(),
        provenance={},
        gates=Gates(max_latency_seconds=1),
    )
    assert report["gate_failures"] == ["latency_limit"]
    write_reports(tmp_path / "out.json", report)
    root = ET.parse(tmp_path / "out.xml").getroot()
    assert root.get("tests") == "2" and root.get("failures") == "1"


def test_baseline_comparison_allows_model_or_prompt_change_but_rejects_mismatch_before_calls():
    baseline = run()
    candidate = FakeExecutor(Sample(RunResult(Status.COMPLETED, {"wrong": 1}), Usage(1)))
    report = run(candidate, baseline=baseline, gates=Gates(min_pass_rate=0))
    assert report["gate_failures"] == ["quality_regression"]
    assert report["summary"]["baseline_pass_rate"] == 1
    for mutation in (
        {"comparison": {}},
        {"runs": []},
        {"runs": [{"quality_passed": "true"}]},
        {"runs": [{"quality_passed": True, "case_id": "different", "repeat": 1}]},
        {"runs": [{"quality_passed": True, "case_id": "one", "repeat": "1"}]},
        {"passed": False},
        {"provenance": {"suite_kind": "scripted"}},
    ):
        executor = FakeExecutor()
        with pytest.raises(ValueError):
            run(executor, baseline=baseline | mutation)
        assert executor.calls == 0


@pytest.mark.parametrize(
    "kwargs",
    [{"repeats": 0}, {"repeats": True}, {"split": "unknown"}, {"suite": suite(case(split="dev"))}],
)
def test_invalid_selection_is_not_vacuous_pass(kwargs):
    with pytest.raises(ValueError):
        run(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_requests": True},
        {"min_pass_rate": float("nan")},
        {"max_regression": 2},
        {"max_latency_seconds": 0},
        {"max_total_tokens": 0},
        {"max_estimated_cost_usd": float("inf")},
    ],
)
def test_invalid_gate_settings(kwargs):
    with pytest.raises(ValueError):
        Gates(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"requests": -1},
        {"input_tokens": True},
        {"output_tokens": -1},
        {"estimated_cost_usd": float("nan")},
        {"estimated_cost_usd": -0.1},
    ],
)
def test_invalid_usage(kwargs):
    with pytest.raises(ValueError):
        Usage(**kwargs)


def manifest():
    return {
        "schema_version": 1,
        "name": "test",
        "version": "1",
        "cases": [
            {
                "id": "one",
                "split": "release",
                "task": {},
                "expected": {"status": "completed"},
                "script": [{"action": "finish", "output": 42}],
            }
        ],
    }


def test_case_loading_strict_and_bounded(tmp_path):
    path = tmp_path / "cases.json"
    for raw in ("[]", '{"name":1,"name":2}', '{"bad":NaN}', '{"value":' + "[" * 1000):
        path.write_text(raw)
        with pytest.raises((ValueError, RecursionError)):
            read_object(path)
    path.write_text(" " * 100)
    with pytest.raises(ValueError):
        read_object(path, limit=10)
    data = manifest()
    mutations = [
        {"schema_version": True},
        {"name": "bad name"},
        {"cases": []},
        {"cases": [{}]},
        {"cases": [data["cases"][0] | {"extra": 1}]},
        {"cases": data["cases"] * 2},
    ]
    for mutation in mutations:
        path.write_text(json.dumps(data | mutation))
        with pytest.raises(ValueError):
            load_suite(path)
    changes = [
        {"split": "unknown"},
        {"expected": {}},
        {"expected": {"status": "bogus"}},
        {"expected": {"status": "completed", "max_model_calls": True}},
        {"expected": {"status": "completed", "error_code": 42}},
        {"tags": "bad"},
        {"tags": ["bad name"]},
        {"script": "bad"},
        {"script": [{"action": "bogus"}]},
        {"script": [{"action": "tool", "arguments": []}]},
        {"script": [{"action": "finish", "extra": 42}]},
    ]
    for mutation in changes:
        path.write_text(json.dumps(data | {"cases": [data["cases"][0] | mutation]}))
        with pytest.raises(ValueError):
            load_suite(path)
    path.write_text(json.dumps(data))
    loaded = load_suite(path)
    assert loaded.cases[0].script == (Decision("finish", output=42),)
    assert len(loaded.digest) == 64


def test_agent_executor_failure_and_script_requirement():
    with pytest.raises(ValueError):
        ScriptedEvaluationModel(case(script=()))

    class ExplodingModel:
        def decide(self, *a, **k):
            raise RuntimeError("vendor secret")

        def usage(self):
            return Usage(1)

    sample = AgentExecutor(lambda case: ExplodingModel(), SystemClock()).run(
        case(), request_id="one", max_requests=1
    )
    assert sample.result.status == Status.EXECUTION_ERROR
    assert sample.usage.requests == 1


def test_bad_baseline_provenance_or_schema_rejected_before_dispatch():
    baseline = run()
    for mutation in ({"provenance": None}, {"schema_version": True}):
        executor = FakeExecutor()
        with pytest.raises(ValueError):
            run(executor, baseline=baseline | mutation)
        assert executor.calls == 0


def test_report_destination_preflight_private_temp_and_existing_directory(tmp_path):
    from agent_tdd.evaluation.reports import prepare_reports

    path = tmp_path / "private" / "candidate.json"
    prepare_reports(path)
    assert not path.exists() and not list(path.parent.glob(".agent-tdd-*"))
    path.with_suffix(".html").mkdir()
    with pytest.raises(ValueError):
        prepare_reports(path)
    with pytest.raises(ValueError):
        prepare_reports(tmp_path / "bad.txt")
