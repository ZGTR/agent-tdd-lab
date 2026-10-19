"""Code graders keep hard policy failures separate from task quality."""

from agent_tdd.evaluation.cases import fingerprint
from agent_tdd.evaluation.contracts import Case, Grade, Sample


class ExactGrader:
    version = "exact-v1"

    def grade(self, case: Case, sample: Sample) -> Grade:
        result, expected = sample.result, case.expected
        failures: list[str] = []
        violations: list[str] = []
        if result.status.value != expected["status"]:
            failures.append("status_mismatch")
        if "output" in expected and fingerprint(result.output) != fingerprint(expected["output"]):
            failures.append("output_mismatch")
        if "error_code" in expected and result.error_code != expected["error_code"]:
            failures.append("error_code_mismatch")
        limit = expected.get("max_model_calls")
        if isinstance(limit, int) and max(result.model_calls, sample.usage.requests) > limit:
            violations.append("model_call_limit")
        forbidden = expected.get("forbidden_tools", [])
        if isinstance(forbidden, list) and any(o.tool in forbidden for o in result.observations):
            violations.append("forbidden_tool_observed")
        return Grade(not failures, tuple(violations), tuple(failures))
