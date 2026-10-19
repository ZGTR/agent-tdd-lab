# Adapting any agent, workflow, or harness

The stable API is deliberately small. The core only imports the Python standard library. An adapter chooses whether this library owns the loop (`Model` + `Tool`) or your existing framework owns it (`Step`). Keep SDK dependencies in your application, lock their versions, and test their provider contracts separately.

## Model port: use the generic loop

Implement `decide(task, history, *, timeout_seconds) -> Decision`.

- `task` is a JSON value and `history` contains tool outcomes. Both are isolated copies.
- Return `Decision("tool", tool="name", arguments={...})` for one tool call, or `Decision("finish", output=...)` for a final result.
- Convert vendor tool-call IDs, message formats, streaming fragments, parallel calls, and JSON parsing into this interface. This minimal port allows **one decision per turn**; it does not pretend all vendor protocols are identical.
- Validate the provider response, cap response size and tokens, and pass the remaining timeout to the provider SDK. Normalize known provider errors to `ModelError`. The generic controller rejects invalid decisions and unknown tools before dispatch.
- Construct fresh stateful model adapters for each conversation. `ScriptedModel` consumes its script and is intentionally single-use, not a shared concurrent service.
- Do not pass secrets, host approvals, or authentication credentials to the model.

## Tool port: define application effects

```python
from agent_tdd.context import RunContext
from agent_tdd.types import JsonObject, JsonValue, ToolError

class AddTool:
    def execute(
        self, arguments: JsonObject, context: RunContext, *, timeout_seconds: float
    ) -> JsonValue:
        if set(arguments) != {"left", "right"}:
            raise ToolError("invalid_arguments")
        left, right = arguments["left"], arguments["right"]
        if type(left) is not int or type(right) is not int:
            raise ToolError("invalid_arguments")
        return left + right
```

Register with `Agent(model, {"add": AddTool()})`. Tools validate their own schemas and authorization. Inject storage, clocks, clients, and approval authorities through constructors. `ToolError(code)` rejects the run; `ToolError(code, retryable=True)` informs the model and permits another attempt within the same budgets. A retry uses the original operation key and never synthesizes a new identity or permission.

Unexpected tool exceptions propagate rather than being disguised as success. Host code should record a failed run, release resources, and reconcile uncertain external effects; don't blindly retry a whole effectful run.

## Step port: keep your existing harness

Implement `run(task, context) -> RunResult`, then place the adapter into a `Node`.

| Existing system | Adapter boundary | Checks to keep outside model reasoning |
| --- | --- | --- |
| LangGraph | Step wraps the compiled graph invocation | Separate thread IDs, checkpoint persistence, interrupt/resume, exact authorization at the service |
| PydanticAI | Step wraps `Agent.run_sync` or your async bridge | Output schemas, dependency injection, usage limits, policy |
| CrewAI | Step wraps a Flow or crew kickoff | Flow state, typed handoffs, budget and real tool effects |
| Strands / Bedrock | Step wraps the Strands agent; or Model wraps provider inference | IAM/model access, error mapping, request deadlines, tool authorization |
| Custom agents or functions | Implement Step directly | Explicit outcomes, cancellation, task/result schema |
| Another workflow | Workflow already implements Step | Shared context and budgets; no hidden identity escalation |

A Step adapter is responsible for accounting its internal calls against `context.budget`, propagating cancellation, and honoring remaining time. A framework call hidden inside a Step is **not automatically intercepted** by this package. Count each model request at the actual invocation boundary. `RunResult.model_calls` reports work; budget reservation is the enforcement mechanism.

Workflow output maps node names to outputs. Node input contains the original task and only its declared dependencies. Add application-specific validators to a Step wrapper for typed handoffs. Do not treat a schema-valid handoff as evidence that the model's claims are true.

## Pub/sub contract

Subscribe startup-configured handlers to topic names. The dispatcher reads pending events, publishes them, then acknowledges them. It stops on the first exception so an undelivered event remains visible. It does not catch errors and silently discard them.

A consumer that writes externally must durably deduplicate by `event.id`. A publisher adapter for SNS/SQS, Kafka, or another broker must return only after the broker accepts the event and must preserve the ID. Multiple dispatchers may publish duplicates; this implementation has no distributed lease or per-subscriber acknowledgment.
