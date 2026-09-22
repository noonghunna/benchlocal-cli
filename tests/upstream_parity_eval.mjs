// Oracle for tests/test_upstream_verifier_parity.py (#143).
//
// Runs the VENDORED upstream graders — not a transcription of them — on a batch
// of answers read from stdin as JSON [{id, answer}], and prints [{id, score,
// status}]. InstructFollow-15 scores through lib/benchmark.ts; StructOutput-15
// through verification/core.mjs (validators) and lib/benchmark.ts (axis
// scoring), the same chain upstream's orchestrator uses.
//
// Needs node >= 22.6 for --experimental-strip-types (benchmark.ts is TypeScript).
import { SCENARIOS as IF_SCENARIOS } from "../vendor/InstructFollow-15/lib/benchmark.ts";
import { SCENARIOS as SO_SCENARIOS } from "../vendor/StructOutput-15/lib/benchmark.ts";
import { verifyAnswer } from "../vendor/StructOutput-15/verification/core.mjs";

const scenarios = new Map([...IF_SCENARIOS, ...SO_SCENARIOS].map((scenario) => [scenario.id, scenario]));

function evaluate(id, answer) {
  const scenario = scenarios.get(id);
  if (!scenario) throw new Error(`unknown scenario ${id}`);
  const meta = id.startsWith("SO-") ? { validationResult: verifyAnswer(id, answer) } : {};
  const result = scenario.evaluate({ assistantMessages: [answer], finalAnswer: answer, meta });
  return { id, score: result.score, status: result.status };
}

let input = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) input += chunk;
const cases = JSON.parse(input);
process.stdout.write(JSON.stringify(cases.map(({ id, answer }) => evaluate(id, answer))));
