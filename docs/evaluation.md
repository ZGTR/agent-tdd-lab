# Evaluation and release gates

The repository supplies an executable evaluation runner, OpenAI-compatible HTTP and AWS Bedrock Converse model adapters, deterministic code graders, JSON/HTML/JUnit reports, and a trusted manual CI workflow. It evaluates the same generic `Agent` port used by the offline harness; applications with another agent, workflow, or harness implement the small `Executor` contract instead. No hosted evaluation platform is required.

For an existing LangGraph, Pi, Strands or other harness, see [CI/CD integration](ci-integration.md): the reusable GitHub Action, `--executor-config` command bridge and Python worker helper keep your application runtime intact.

## Run without credentials

```bash
uv sync --frozen
uv run --frozen agent-tdd evaluate --cases evals/refund-routing.json \
  --provider scripted --report artifacts/baseline.json --repeats 3
uv run --frozen agent-tdd evaluate --cases evals/refund-routing.json \
  --provider scripted --report artifacts/candidate.json --repeats 3 \
  --baseline artifacts/baseline.json
```

Each invocation writes `.json`, `.html`, and `.xml` siblings. Open the HTML locally; JUnit can feed your existing CI viewer. A suite-wide regression or budget failure also appears as a failing JUnit testcase. Artifacts are ignored by Git, written through private temporary files and atomic replacement, and exclude raw tasks, answers, provider exceptions, credentials, and receipts. Case IDs/tags and model IDs remain metadata: do not place customer identifiers in them or upload private reports to a public destination. Parent directory/access-control policy remains the operator's responsibility, especially on Windows.

**A scripted pass tests the evaluation plumbing and specified harness behavior. It provides no evidence of model quality.** The 12 public refund-routing examples are six development and six release-partition examples, not a private held-out benchmark. They illustrate routing decisions with no registered effect tools. `ready_for_authorized_tool` is a proposal, not permission to refund. The separate SQLite simulator tests actual authorization, idempotency, and outbox invariants. Real refund tools must independently authorize requests.

## Case contract and splits

Case files are strict JSON, at most 1 MiB, schema version 1, with unique IDs and explicit splits. Duplicate keys, nonfinite numbers, excess nesting, unknown fields, invalid expectations and empty selections fail configuration. Up to 1,000 cases, 100 repetitions and 10,000 selected runs are supported. The runner is sequential to keep the initial operational footprint small.

```json
{
  "schema_version": 1,
  "name": "my-agent",
  "version": "1",
  "cases": [{
    "id": "approval-missing",
    "split": "release",
    "tags": ["approval"],
    "task": {"order_id": "A1", "approved": false},
    "expected": {
      "status": "completed",
      "output": {"action": "request_approval", "order_id": "A1"},
      "max_model_calls": 2,
      "forbidden_tools": ["refund"]
    },
    "script": [{"action": "finish", "output": {"action": "request_approval", "order_id": "A1"}}]
  }]
}
```

`script` is optional for live providers and required for scripted execution. Neither `script` nor `expected` is sent to the model. Input tasks and prior tool observations are the only model-facing case data. The CLI registers no write tools. Build domain-specific fake tools through the Python composition API, fresh per run, before testing tool selection or effects. A `forbidden_tools` check examines tool observations, including failed tool executions; it cannot recover invisible actions performed inside a third-party harness.

Use `--split dev` for tuning and `--split release` for release evidence. Keep real held-out cases private and reviewed; tuning against a published or inspected example set is not independent validation. Replenish held-out cases when their answers enter prompts. Exact output checks are appropriate for structured routing/extraction. For semantic answers, inject a reviewed `Grader` with its own version; use calibrated judges only when exact checks cannot assess the requirement. Do not replace service authorization with a judge score.

## OpenAI-compatible endpoint

```bash
mkdir -p artifacts
cp evals/providers/openai.example.json artifacts/openai.json
# Edit artifacts/openai.json: select your model and compatible endpoint.
# Inject OPENAI_API_KEY using your shell/secret manager; never commit it.
uv run --frozen agent-tdd evaluate --cases evals/refund-routing.json \
  --provider openai --config artifacts/openai.json --split release \
  --repeats 3 --max-model-calls 2 --max-requests 36 \
  --report artifacts/openai-report.json
```

Create `artifacts/` first if you have not run the offline commands. Configuration accepts `model`, `prompt`, `endpoint` (the full `/v1/chat/completions` URL), `max_output_tokens`, `max_input_bytes`, and optionally both `input_usd_per_million`/`output_usd_per_million`. Rates must reflect your selected provider/model; examples intentionally omit prices rather than invent current prices. API keys are read only from `OPENAI_API_KEY`. Authenticated custom gateways and local compatible servers can use the same adapter.

This adapter uses JSON **decision envelopes**, not native provider tool calling or streaming. It sends temperature 0 and `max_tokens`; use a model/endpoint supporting that request format. A finish decision is `{"action":"finish","output":...}`. A tool decision is `{"action":"tool","tool":"name","arguments":{...}}`. The generic harness remains the only dispatcher. Model responses must be complete, single-choice, finite JSON with no duplicate keys. Output truncation and malformed responses fail the evaluation. Temperature zero does not guarantee deterministic inference.

HTTPS and normal TLS verification are required, except for explicitly configured loopback HTTP endpoints. Redirects are refused so an authentication header is not forwarded to a new location. Input size and response bytes are bounded; request timeout is passed to the transport; there are no automatic request retries. A socket timeout is not a process-kill timer. Keep the supervised job/container deadline and reject a result received after its cooperative deadline.

## AWS Bedrock Converse

```bash
uv sync --frozen --extra bedrock
mkdir -p artifacts
cp evals/providers/bedrock.example.json artifacts/bedrock.json
# Edit model (or inference-profile ID), region and any current price estimates.
# Select your configured AWS profile/role through the normal SDK credential chain.
uv run --frozen --extra bedrock agent-tdd evaluate \
  --cases evals/refund-routing.json --provider bedrock \
  --config artifacts/bedrock.json --split release --repeats 3 \
  --max-model-calls 2 --max-requests 36 --report artifacts/bedrock-report.json
```

The optional extra is locked in `uv.lock`; the generic/offline/OpenAI paths do not import boto3. The adapter uses Converse with the same decision envelope and a selected model supporting system messages, temperature 0 and the configured token cap. Use provider-specific adapters for models that require a different request format. Bedrock requests include actual input/output usage when supplied by AWS. SDK connect/read timeouts receive the remaining run time, and `total_max_attempts=1` disables SDK inference retries. Every attempted invocation, including exceptions, is counted. SDK stubs verify request shape/configuration; they cannot prove AWS model access, IAM, routing, quotas or quality.

Use a narrowly scoped role granting `bedrock:InvokeModel` on the intended model/inference-profile resources, including underlying model ARNs required by your chosen profile. Verify current region/profile requirements in [AWS Converse documentation](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html). Do not use broad administrator credentials for evaluation.

## Gates and comparison

| Gate | Behavior |
| --- | --- |
| Policy | Any forbidden observed tool or case model-call overrun fails the suite, regardless of average quality |
| Quality | Default requires all selected attempts to pass; `--min-pass-rate` sets an explicit floor |
| Regression | `--baseline` compares pass rate; `--max-regression` defaults to zero |
| Availability | Model/executor errors, timeout, cancellation, missing budgeted runs, and grader defects fail |
| Latency | `--max-latency` applies to every attempt, not just an average |
| Requests | `--max-requests` limits suite dispatch; each generic-agent run receives the remaining allowance |
| Tokens | Optional `--max-total-tokens`; missing provider usage fails when enabled |
| Estimated cost | Optional `--max-estimated-cost-usd`; missing usage/pricing fails when enabled |

Token/cost gates evaluate usage **after each executor/case run completes**, which may include several model requests. They prevent subsequent case dispatch when exhausted/unknown. An entire multi-call run can exceed the threshold before this check. They do not reserve dollars, enforce per-request token/cost limits, prevent an overrun, or establish a billing ceiling. Enforce provider/account controls as well. Errors with unknown token usage never become zero-cost evidence. For custom executors, unhandled exceptions leave total request count unknown, retain the known subtotal and stop further dispatch; implement explicit failure samples with measured usage when available.

Exit status is **0** for passed gates, **1** for failed evaluation, and **2** for invalid configuration/artifacts. A report without a passing process exit is not passing release evidence. Process termination can prevent a final report: a missing report is also failure. Reports include every selected attempt, slice pass rates, hard policy failures, request/token/cost metadata, suite hash, grader version, prompt/protocol/config hashes, package version, dependency-lock hash and Git revision/state (or explicit `--revision`). Dirty/unknown source state is disclosed, not silently presented as an immutable release. For deployed/custom applications, supply the actual application revision and lockfiles through your composition/provenance.

Baselines must be trusted, passing reports with the same case-set hash, split, repeats, grader and run identities. Validation happens before any model call. Scripted and live evidence cannot be mixed. Prompt, model and application revision may differ—that is what the comparison is assessing. Store baseline artifacts under your normal release controls; JSON hashes detect identity differences, not malicious artifact editing. The simple repeated-case pass rate is descriptive: repeated runs are not independent customer samples and this small demo does not establish a statistical reliability guarantee.

## Other agents, workflows and harnesses

The runner depends on `Executor.run(case, *, request_id, max_requests) -> Sample`, a `Grader`, and a `Clock`. An executor returns the existing generic `RunResult` plus `Usage`. No framework is imported by the evaluation service.

```python
from agent_tdd.adapters.local import SystemClock
from agent_tdd.evaluation.cases import load_suite
from agent_tdd.evaluation.contracts import Sample, Usage
from agent_tdd.evaluation.graders import ExactGrader
from agent_tdd.evaluation.runner import evaluate

class MyHarnessExecutor:
    def __init__(self, harness_factory):
        self._factory = harness_factory

    def run(self, case, *, request_id, max_requests):
        # This application-owned adapter must enforce the allowance at each
        # actual model request, isolate identity/state, and measure every attempt.
        harness = self._factory()
        result, measured_usage = harness.run_evaluation(
            case.task, request_id=request_id, max_requests=max_requests
        )
        return Sample(result, Usage(**measured_usage))

# Inject a LangGraph, PydanticAI, CrewAI, Strands, or custom adapter here.
# evaluate(load_suite(...), MyHarnessExecutor(factory), ExactGrader(),
#          SystemClock(), provenance={...})
```

The `run_evaluation` name above belongs to your adapter, not a vendor API. See [adapter boundaries](adapters.md) for each framework. Hidden framework/SDK retries, asynchronous subagents and remote tool effects require accounting at their actual boundaries; a report counter does not intercept them automatically. Keep the same invariant/quality graders while replacing only the executor.

## Trusted manual CI

`.github/workflows/evaluate.yml` runs only by explicit `workflow_dispatch` on `main`, never on pull requests. Its fixed suite is read-only routing, one repeat, at most two calls per case and 12 inference requests per candidate. Selecting an optional baseline model adds up to 12 requests. The workflow has a 10-minute outer timeout, no automatic inference retries and seven-day metadata report retention. These are demonstration settings; review and change the versioned workflow for your real application.

Configure the GitHub `live-evaluation` environment and restrict its deployment branches to `main`. An administrator can apply and verify the idempotent setup without providing any secret values to the script:

```bash
python scripts/configure_live_environment.py --repo ZGTR/agent-tdd-lab --apply
python scripts/configure_live_environment.py --repo ZGTR/agent-tdd-lab
```

The script refuses to widen or reset an existing different policy. The current connected GitHub integration can publish code/PRs but returned HTTP 403 for environment administration; no environment configuration or credentials were created during this release.

 Put `OPENAI_API_KEY` in its secrets; `OPENAI_ENDPOINT` may be set as an environment variable for a trusted compatible gateway (otherwise the OpenAI endpoint is used). For Bedrock, the optional `infra/aws` settings `bedrock_model_arns` and `github_repository` declare a separate CI role and selected-model permissions. The GitHub OIDC provider must already exist in your account. No inference permission is granted by default. Configure the returned `evaluation_role_arn` and `account_id` as `AWS_ROLE_ARN`/`AWS_ACCOUNT_ID` variables and an AWS GitHub OIDC role trusting **only** this repository's `live-evaluation` environment and `sts.amazonaws.com` audience. The job's role needs only the selected inference permissions. Do not let arbitrary branches deploy to that environment; a workflow condition alone is not the credential perimeter.

```bash
gh workflow run evaluate.yml --ref main -f provider=openai -f model=YOUR_MODEL
# Or, after configuring the AWS role/model access:
gh workflow run evaluate.yml --ref main -f provider=bedrock -f model=YOUR_MODEL_OR_PROFILE \
  -f region=us-east-1
```

Selecting a live job incurs provider charges. Routine CI runs the scripted suite, HTTP fixtures and AWS stubs without model credentials or cloud provisioning. No live-provider or AWS-account success was established while developing this release: the execution environment had no configured provider credentials. The repository supplies the working gate; your actual model/application needs a passing live report and staging service contracts before release.
