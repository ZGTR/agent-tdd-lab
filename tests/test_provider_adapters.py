"""Local HTTP contracts and AWS SDK stubs establish adapter behavior, not live inference."""

import json
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from agent_tdd.adapters.providers import (
    PROTOCOL,
    BedrockModel,
    OpenAIModel,
    ProviderSettings,
    UsageLedger,
)
from agent_tdd.types import ModelError, Observation


@pytest.fixture
def endpoint():
    state = SimpleNamespace(requests=[], response={}, status=200, headers={})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state.requests.append((json.loads(body), self.headers.get("Authorization")))
            self.send_response(state.status)
            for key, value in state.headers.items():
                self.send_header(key, value)
            self.end_headers()
            raw = (
                state.response
                if isinstance(state.response, bytes)
                else json.dumps(state.response).encode()
            )
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def settings(**overrides):
    return ProviderSettings(
        **(
            dict(
                model="test-model",
                prompt="Do the task.",
                input_usd_per_million=2,
                output_usd_per_million=4,
            )
            | overrides
        )
    )


def response(text='{"action":"finish","output":{"answer":42}}', **overrides):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    } | overrides


def test_http_real_transport_request_shape_auth_history_and_measured_usage(endpoint):
    endpoint.response = response()
    model = OpenAIModel(settings(), endpoint=endpoint.url, api_key="test-secret")
    result = model.decide(
        {"task": "data"}, (Observation("lookup", True, {"value": 1}),), timeout_seconds=2
    )
    assert result.output == {"answer": 42}
    body, auth = endpoint.requests[0]
    assert auth == "Bearer test-secret"
    assert body["model"] == "test-model" and body["max_tokens"] == 512
    assert body["messages"][0]["content"] == PROTOCOL + "Do the task."
    content = json.loads(body["messages"][1]["content"])
    assert content["observations"][0]["tool"] == "lookup"
    assert not {"expected", "script", "approval_authority"} & content.keys()
    assert model.usage().requests == 1
    assert model.usage().input_tokens == 10
    assert model.usage().output_tokens == 5
    assert model.usage().estimated_cost_usd == 0.00004


def test_http_tool_decision_and_no_key_for_loopback(endpoint):
    endpoint.response = response('{"action":"tool","tool":"add","arguments":{"left":2}}')
    model = OpenAIModel(settings(), endpoint=endpoint.url)
    result = model.decide({}, (), timeout_seconds=1)
    assert result.tool == "add" and result.arguments == {"left": 2}
    assert endpoint.requests[0][1] is None


@pytest.mark.parametrize(
    "value",
    [
        b"invalid-json",
        b"x" * 1_048_577,
        [],
        response(None),
        response("[]"),
        response("null"),
        response("42"),
        response("{bad"),
        response('{"action":"finish","output":NaN}'),
        response('{"action":"finish","output":42,"extra":true}'),
        response('{"action":"tool","tool":"x","arguments":[]}'),
        response('{"action":"unknown"}'),
        response(choices=[]),
        response(choices=[{"finish_reason": "length", "message": {"content": "{}"}}]),
    ],
)
def test_http_malformed_responses_fail_once_without_leaking(endpoint, value):
    endpoint.response = value
    model = OpenAIModel(settings(), endpoint=endpoint.url, api_key="secret")
    with pytest.raises(ModelError, match="^provider_request_failed$") as caught:
        model.decide({}, (), timeout_seconds=1)
    assert "secret" not in str(caught.value)
    assert model.usage().requests == 1 and len(endpoint.requests) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 429, 500])
def test_http_errors_and_redirects_never_retry_or_forward_credentials(endpoint, status):
    endpoint.status = status
    endpoint.headers = {"Location": endpoint.url + "/another-host"}
    endpoint.response = {"secret": "vendor-exception"}
    model = OpenAIModel(settings(), endpoint=endpoint.url, api_key="do-not-forward")
    with pytest.raises(ModelError):
        model.decide({}, (), timeout_seconds=1)
    assert len(endpoint.requests) == 1
    assert model.usage().requests == 1 and model.usage().input_tokens is None
    assert model.usage().estimated_cost_usd is None


def test_http_missing_usage_is_unknown_and_poisoned_across_requests(endpoint):
    endpoint.response = response(usage={})
    model = OpenAIModel(settings(), endpoint=endpoint.url)
    model.decide({}, (), timeout_seconds=1)
    endpoint.response = response()
    model.decide({}, (), timeout_seconds=1)
    assert model.usage().requests == 2
    assert model.usage().input_tokens is None and model.usage().estimated_cost_usd is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/chat",
        "https://user:password@example.com/chat",
        "https://example.com/chat?api_key=secret",
        "https://example.com/chat#secret",
        "file:///tmp/x",
    ],
)
def test_endpoint_restrictions(endpoint):
    with pytest.raises(ValueError):
        OpenAIModel(settings(), endpoint=endpoint)


def test_local_credential_control_characters_rejected():
    with pytest.raises(ValueError):
        OpenAIModel(settings(), endpoint="http://localhost/v1/chat/completions", api_key="a\r\nb")


def test_input_and_timeout_limits_do_not_dispatch(endpoint):
    model = OpenAIModel(settings(max_input_bytes=1), endpoint=endpoint.url)
    with pytest.raises(ModelError):
        model.decide({}, (), timeout_seconds=1)
    assert model.usage().requests == 0
    with pytest.raises(ValueError):
        model.decide({}, (), timeout_seconds=0)
    assert not endpoint.requests


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": ""},
        {"prompt": ""},
        {"prompt": "x" * 32769},
        {"max_output_tokens": 0},
        {"max_input_bytes": True},
        {"input_usd_per_million": None},
        {"input_usd_per_million": -1},
        {"input_usd_per_million": float("inf")},
    ],
)
def test_invalid_settings(overrides):
    with pytest.raises(ValueError):
        settings(**overrides)


def bedrock_response(text='{"action":"finish","output":42}', **overrides):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        "metrics": {"latencyMs": 1},
    } | overrides


def test_bedrock_injected_client_normalizes_and_records_attempts():
    calls = []

    class Client:
        def converse(self, **kwargs):
            calls.append(kwargs)
            return bedrock_response()

    model = BedrockModel(settings(), region="us-east-1", client_factory=lambda timeout: Client())
    assert model.decide({}, (), timeout_seconds=2).output == 42
    assert calls[0]["modelId"] == "test-model"
    assert calls[0]["inferenceConfig"] == {"maxTokens": 512, "temperature": 0}
    assert model.usage().requests == 1 and model.usage().input_tokens == 10


@pytest.mark.parametrize(
    "value",
    [
        bedrock_response(stopReason="max_tokens"),
        bedrock_response("not-json"),
        bedrock_response(output={"message": {"content": []}}),
        bedrock_response(usage={"inputTokens": True, "outputTokens": 1}),
    ],
)
def test_bedrock_bad_response_and_usage_are_fail_closed(value):
    class Client:
        def converse(self, **kwargs):
            return value

    model = BedrockModel(settings(), region="us-east-1", client_factory=lambda timeout: Client())
    if value.get("usage", {}).get("inputTokens") is True:
        model.decide({}, (), timeout_seconds=1)
        assert model.usage().input_tokens is None
    else:
        with pytest.raises(ModelError):
            model.decide({}, (), timeout_seconds=1)
    assert model.usage().requests == 1


def test_bedrock_factory_error_no_retry_or_exception_payload():
    calls = []

    def factory(timeout):
        calls.append(timeout)
        raise RuntimeError("secret AWS error")

    model = BedrockModel(settings(), region="us-east-1", client_factory=factory)
    with pytest.raises(ModelError, match="^provider_request_failed$"):
        model.decide({}, (), timeout_seconds=0.5)
    assert calls == [0.5] and model.usage().requests == 1
    assert model.usage().input_tokens is None
    with pytest.raises(ValueError):
        BedrockModel(settings(), region="")


def test_real_boto_client_configuration_and_stubber_contract(monkeypatch):
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber

    original = boto3.client
    clients = []

    def client(*args, **kwargs):
        instance = original(
            *args, **kwargs, aws_access_key_id="testing", aws_secret_access_key="testing"
        )
        clients.append(instance)
        stub = Stubber(instance)
        stub.add_response(
            "converse",
            bedrock_response(),
            {
                "modelId": "test-model",
                "system": [{"text": PROTOCOL + "Do the task."}],
                "messages": [
                    {"role": "user", "content": [{"text": '{"task": {}, "observations": []}'}]}
                ],
                "inferenceConfig": {"maxTokens": 512, "temperature": 0},
            },
        )
        stub.activate()
        return instance

    monkeypatch.setattr(boto3, "client", client)
    model = BedrockModel(settings(), region="us-east-1")
    assert model.decide({}, (), timeout_seconds=0.5).output == 42
    assert clients[0].meta.config.retries == {"total_max_attempts": 1, "mode": "standard"}
    assert clients[0].meta.config.read_timeout == 0.5
    assert clients[0].meta.config.connect_timeout == 0.5
    assert model.usage().estimated_cost_usd == 0.00004


def test_unpriced_or_unknown_usage_never_becomes_free():
    ledger = UsageLedger(ProviderSettings("model", "prompt"))
    assert ledger.usage().estimated_cost_usd == 0
    ledger.start()
    ledger.record(10, 5)
    assert ledger.usage().estimated_cost_usd is None
    ledger.start()
    ledger.record(-1, 5)
    assert ledger.usage().input_tokens is None


def test_duplicate_decision_keys_cannot_reinterpret_an_action(endpoint):
    endpoint.response = response('{"action":"tool","action":"finish","output":42}')
    model = OpenAIModel(settings(), endpoint=endpoint.url)
    with pytest.raises(ModelError):
        model.decide({}, (), timeout_seconds=1)
    endpoint.response = response('"a string is not a decision object"')
    with pytest.raises(ModelError):
        model.decide({}, (), timeout_seconds=1)


def test_unreasonable_token_values_and_cost_overflow_become_unknown():
    ledger = UsageLedger(settings())
    ledger.start()
    ledger.record(2**64, 1)
    assert ledger.usage().input_tokens is None
    ledger = UsageLedger(settings(input_usd_per_million=1e308, output_usd_per_million=1e308))
    ledger.start()
    ledger.record(100, 100)
    assert ledger.usage().estimated_cost_usd is None
