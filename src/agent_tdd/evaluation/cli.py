"""Evaluation CLI composition: trusted config, optional providers, private artifacts."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent_tdd.adapters.local import SystemClock
from agent_tdd.adapters.providers import PROTOCOL, BedrockModel, OpenAIModel, ProviderSettings
from agent_tdd.evaluation.cases import fingerprint, load_suite, read_object
from agent_tdd.evaluation.command import CommandExecutor, CommandSettings
from agent_tdd.evaluation.contracts import Executor, Gates
from agent_tdd.evaluation.executors import AgentExecutor, ScriptedEvaluationModel
from agent_tdd.evaluation.graders import ExactGrader
from agent_tdd.evaluation.reports import prepare_reports, write_reports
from agent_tdd.evaluation.runner import evaluate
from agent_tdd.runtime import RunSettings


def add_parser(sub: Any) -> None:
    parser = sub.add_parser("evaluate", help="versioned cases, provider adapters and release gates")
    parser.add_argument("--cases", type=Path, required=True)
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--provider", choices=["scripted", "openai", "bedrock"])
    execution.add_argument(
        "--executor-config", type=Path, help="trusted command adapter JSON for any harness/language"
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--split", choices=["dev", "release"], default="release")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--revision", help="application commit/artifact identity; inferred from Git"
    )
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-model-calls", type=int, default=8)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--max-latency", type=float, default=60)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--max-estimated-cost-usd", type=float)
    parser.add_argument("--min-pass-rate", type=float, default=1)
    parser.add_argument("--max-regression", type=float, default=0)


def _revision(override: str | None) -> tuple[str, str]:
    if override:
        if len(override) > 200 or not override.strip():
            raise ValueError("invalid revision")
        return override, "asserted"
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=5, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            stderr=subprocess.DEVNULL,
            timeout=5,
            text=True,
        )
        return revision, "dirty" if dirty else "clean"
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unknown"


def run(args: argparse.Namespace) -> int:
    try:
        if args.report.suffix != ".json":
            raise ValueError("report path must end in .json")
        inputs = [
            path
            for path in (args.cases, args.config, args.baseline, args.executor_config)
            if path is not None
        ]
        outputs = {
            args.report.with_suffix(suffix).resolve() for suffix in (".json", ".html", ".xml")
        }
        if any(path.resolve() in outputs for path in inputs):
            raise ValueError("report must not overwrite an input or baseline")
        suite = load_suite(args.cases)
        gates = Gates(
            args.min_pass_rate,
            args.max_regression,
            args.max_latency,
            args.max_requests,
            args.max_total_tokens,
            args.max_estimated_cost_usd,
        )
        settings = RunSettings(args.max_model_calls, args.timeout)
        clock = SystemClock()
        provider_name = args.provider or "scripted"
        if args.executor_config is not None and args.config is not None:
            raise ValueError("provider config and executor config cannot be combined")
        config = read_object(args.config) if args.config else {}
        provenance = {
            "provider": "command" if args.executor_config else provider_name,
            "package_version": importlib.metadata.version("agent-tdd-lab"),
        }
        revision, state = _revision(args.revision)
        provenance.update(
            revision=revision,
            revision_state=state,
            suite_kind="scripted" if provider_name == "scripted" else "live",
        )
        executor: Executor
        if args.executor_config is not None:
            command_config = read_object(args.executor_config)
            command = CommandSettings.from_config(command_config)
            executor = CommandExecutor(command, settings)
            provenance.update(
                suite_kind=command.suite_kind,
                adapter_version=command.adapter_version,
                executor_config_sha256=fingerprint(command_config),
            )
        elif provider_name == "scripted":
            if config:
                raise ValueError("scripted provider does not accept provider configuration")
            selected = [case for case in suite.cases if case.split == args.split]
            if any(not case.script for case in selected):
                raise ValueError("scripted provider requires explicit scripts")
            executor = AgentExecutor(ScriptedEvaluationModel, clock, settings=settings)
            provenance.update(model="scripted", prompt_sha256="none", config_sha256="none")
        else:
            required = {"model", "prompt", "endpoint" if provider_name == "openai" else "region"}
            allowed = required | {
                "max_output_tokens",
                "max_input_bytes",
                "input_usd_per_million",
                "output_usd_per_million",
            }
            if not required <= set(config) or set(config) - allowed:
                raise ValueError("missing or unknown provider configuration fields")
            if any(not isinstance(config[key], str) for key in required):
                raise ValueError("model, prompt and endpoint/region must be strings")
            provider = ProviderSettings(
                **{key: value for key, value in config.items() if key not in {"endpoint", "region"}}
            )
            provenance.update(
                model=provider.model,
                prompt_sha256=fingerprint(provider.prompt),
                config_sha256=fingerprint(config),
            )
            if provider_name == "openai":
                endpoint = config["endpoint"]
                key = os.environ.get("OPENAI_API_KEY", "")
                # Validate before any cases run; credentials remain only in memory.
                OpenAIModel(provider, endpoint=endpoint, api_key=key)
                executor = AgentExecutor(
                    lambda case: OpenAIModel(provider, endpoint=endpoint, api_key=key),
                    clock,
                    settings=settings,
                )
            else:
                if importlib.util.find_spec("boto3") is None:
                    raise ValueError("install the bedrock extra before selecting Bedrock")
                region = config["region"]
                executor = AgentExecutor(
                    lambda case: BedrockModel(provider, region=region), clock, settings=settings
                )
        provenance["protocol_sha256"] = (
            fingerprint("command-v1") if args.executor_config else fingerprint(PROTOCOL)
        )
        provenance["tool_schema_sha256"] = (
            "application-owned" if args.executor_config else fingerprint({})
        )
        lock = Path("uv.lock")
        provenance["dependency_lock_sha256"] = (
            hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else "unknown"
        )
        provenance["execution_settings_sha256"] = fingerprint(asdict(settings))
        baseline = read_object(args.baseline, limit=32 * 1_048_576) if args.baseline else None
        prepare_reports(args.report)
        report = evaluate(
            suite,
            executor,
            ExactGrader(),
            clock,
            provenance=provenance,
            gates=gates,
            split=args.split,
            repeats=args.repeats,
            baseline=baseline,
        )
        write_reports(args.report, report)
        print(
            json.dumps(
                {
                    "passed": report["passed"],
                    "summary": report["summary"],
                    "gate_failures": report["gate_failures"],
                },
                allow_nan=False,
            )
        )
        return 0 if report["passed"] else 1
    except (ValueError, TypeError, OSError, RecursionError, OverflowError) as error:
        # Never echo case content, endpoint credentials, provider text, or raw exceptions.
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": "invalid_evaluation_configuration",
                    "error_type": type(error).__name__,
                }
            )
        )
        return 2
