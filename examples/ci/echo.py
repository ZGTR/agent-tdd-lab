"""Offline command-protocol wiring fixture. No model or framework is exercised."""

import json
import sys

request = json.loads(sys.stdin.buffer.read())
assert request["schema_version"] == 1
print(
    json.dumps(
        {
            "schema_version": 1,
            "result": {"status": "completed", "output": request["task"], "model_calls": 0},
            "usage": {
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_usd": 0,
            },
        }
    )
)
