// Offline Node.js protocol fixture. This is not Pi or a model-quality test.
import { readFileSync } from "node:fs";

const request = JSON.parse(readFileSync(0, "utf8"));
if (request.schema_version !== 1) throw new Error("unsupported protocol");
process.stdout.write(JSON.stringify({
  schema_version: 1,
  result: { status: "completed", output: request.task, model_calls: 0 },
  usage: { requests: 0, input_tokens: 0, output_tokens: 0, estimated_cost_usd: 0 },
}));
