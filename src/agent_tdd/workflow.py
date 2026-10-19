"""Composable DAG orchestration. Nodes may be agents, workflows, or ordinary steps."""

from copy import deepcopy
from dataclasses import dataclass

from agent_tdd.context import RunContext
from agent_tdd.ports import Step
from agent_tdd.types import JsonObject, JsonValue, Observation, RunResult, Status, validate_json


@dataclass(frozen=True)
class Node:
    name: str
    step: Step
    depends_on: tuple[str, ...] = ()


class Workflow:
    """Stable, sequential topological execution; fan-out topology does not imply parallelism."""

    def __init__(self, nodes: list[Node]) -> None:
        by_name = {node.name: node for node in nodes}
        if not nodes or len(by_name) != len(nodes) or any(not n.name.strip() for n in nodes):
            raise ValueError("workflow requires unique nonempty nodes")
        pending = list(nodes)
        ordered: list[Node] = []
        done: set[str] = set()
        for node in nodes:
            if any(dep not in by_name for dep in node.depends_on):
                raise ValueError("unknown dependency")
        while pending:
            ready = [n for n in pending if set(n.depends_on) <= done]
            if not ready:
                raise ValueError("workflow contains a cycle")
            ordered.extend(ready)
            done.update(n.name for n in ready)
            pending = [n for n in pending if n.name not in done]
        self._nodes = tuple(ordered)

    def run(self, task: JsonValue, context: RunContext) -> RunResult:
        validate_json(task)
        outputs: JsonObject = {}
        calls = 0
        observations: list[Observation] = []
        for node in self._nodes:
            if context.cancelled.is_set():
                return RunResult(Status.CANCELLED, outputs, tuple(observations), calls)
            if context.budget.remaining_seconds() <= 0:
                return RunResult(Status.DEADLINE, outputs, tuple(observations), calls)
            handoff: JsonObject = {
                "task": task,
                "upstream": {name: outputs[name] for name in node.depends_on},
            }
            result = node.step.run(deepcopy(handoff), context)
            calls += result.model_calls
            observations.extend(result.observations)
            if result.status != Status.COMPLETED:
                return RunResult(
                    Status.UPSTREAM_FAILED,
                    outputs,
                    tuple(observations),
                    calls,
                    f"{node.name}:{result.status}",
                )
            validate_json(result.output)
            outputs[node.name] = deepcopy(result.output)
        return RunResult(Status.COMPLETED, outputs, tuple(observations), calls)
