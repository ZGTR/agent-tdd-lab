# Extension architecture

Keep policies explicit and dependencies directed toward small application-owned protocols. More patterns do not inherently improve a system: use an abstraction where a real variation or trust boundary exists.

| Pattern | Implementation | Purpose |
| --- | --- | --- |
| Ports and adapters / dependency inversion | Model, Tool, Step, Observer, RefundStore | Replace SDKs and infrastructure without changing domain rules |
| Strategy | Injected Model/Tool/Step and validation callables | Application-specific behavior without conditionals for each vendor |
| Factory registry | StepRegistry | Create fresh stateful agents per execution; avoid conversation leakage across runs |
| Decorator | ValidatedStep | Domain input/output schemas independent of orchestration |
| Composite | Workflow implements Step | Nest workflows and agents through one handoff contract |
| Observer | RunEvent, ResilientObserver, LoggingObserver | Metadata-only lifecycle reporting, isolated from business authorization |
| Unit of work / transactional outbox | SQLite transaction and OutboxDispatcher | Commit local effect and event together; deliver at least once |
| MVC / application service | Immutable models, ExecutionService/CLI controllers, JSON views | Separate presentation and infrastructure from execution policy |

## Register an application

```python
from agent_tdd.adapters.local import ScriptedModel, SystemClock
from agent_tdd.context import Identity
from agent_tdd.harness import Agent
from agent_tdd.runtime import ExecutionService, RunSettings, StepRegistry
from agent_tdd.types import Decision

# In a real application, construct provider clients and tools here.
def build_answer_agent():
    return Agent(ScriptedModel([Decision("finish", output={"answer": 42})]), {})

registry = StepRegistry({"answer": build_answer_agent})
service = ExecutionService(registry, SystemClock())
result = service.run(
    "answer", {"question": "example"}, identity=Identity("tenant", "actor"),
    request_id="stable-business-operation-id", settings=RunSettings(max_model_calls=5),
)
```

The registry snapshots configuration and has no service locator, dynamic import, untrusted plugin loader, or global mutable container. Factories return fresh per-run objects; sharing a stateful SDK model across requests is the application's responsibility. The execution service creates a fresh cancellation/budget context, validates JSON boundaries, and reports stable outcomes. It preserves completed tool receipts when final-output validation fails.

## Domain schema validation

Wrap a Step with `ValidatedStep(input_validator=..., output_validator=...)`. Validators accept a JSON value, return None on success, and raise ValueError for a domain mismatch. For Pydantic or another schema library, adapt that library's validation errors to this contract. Unexpected programming errors propagate and emit a safe `failed` lifecycle event rather than pretending the run completed.

Every workflow node still receives `{"task": original_task, "upstream": declared_outputs}`. Validate that envelope or the relevant task field explicitly. A schema can establish shape, not factual correctness. Failed answers do not reverse effects; inspect authoritative receipts before retrying.

## Lifecycle observation

Inject an Observer through ExecutionService. Events include a generated run ID, a registered application name, phase, elapsed time, model-call count, and stable status. They exclude tenant, actor, request IDs, task payloads, tool receipts, final answers, and exception text. LoggingObserver takes an application-owned logger and does not configure global handlers.

Observer exceptions increment `observer_failures` and do not change run success. Observers are synchronous: keep them nonblocking, or inject a bounded queue/exporter adapter. A blocked observer is still blocked Python code; exception isolation is not hard timeout enforcement. Critical business effects belong in authorized tools and a durable outbox, never in lifecycle observers.

## Explicit retry and concurrency policy

No automatic retry decorator wraps a whole workflow or effectful step. Such a decorator can duplicate effects or hide ambiguous outcomes. Transient tool retries remain bounded by the existing harness, and remote effects must retain provider idempotency keys. Model adapters must count every actual provider request; internal invisible retries can defeat the shared call budget.

The registry/execution service supports independent simultaneous runs. The DAG runner itself remains sequential. For parallel/durable orchestration, adapt an appropriate engine through Step and preserve context, schema, budget, and identity boundaries. OpenTofu and Step Functions implement deployment/lifecycle concerns outside the generic Python domain model.

## Evaluation without framework coupling

`evaluate()` is an application service depending on the `Executor`, `Grader`, and `Clock` protocols. The default `AgentExecutor` composes fresh injected models/tools through the existing execution service; a different workflow or framework replaces only the executor. `ExactGrader` is the initial strategy. Provider clients, lifecycle observers and artifact writers remain separate boundaries; cases never import plugins or code.

Case expectations and scripted decisions are test-only. Only the task and prior observations cross the model port. `Sample` combines the existing `RunResult` with measured usage, so hard policy gates remain separate from a task-quality average. Baseline compatibility is checked before dispatch. Report destinations are checked before inference, and private atomic writes happen after evaluation. Source/prompt/protocol/config/case/grader identities are retained as metadata rather than copies of prompts or task data. Custom graders/executors must emit stable non-sensitive codes and measure all internal requests.

See [evaluation.md](evaluation.md) for the executable setup and the explicit boundary between mocked adapter contracts and live model/account evidence.
