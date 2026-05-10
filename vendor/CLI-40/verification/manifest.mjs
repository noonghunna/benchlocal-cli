export const VERIFIER_SERVICE_NAME = "cli40-verifier";
export const DEFAULT_PORT = Number.parseInt(process.env.PORT ?? "4040", 10);
export const WORKSPACE_DIR = process.env.CLI40_WORKSPACE_DIR ?? "/workspace";
export const RUN_ROOT = process.env.CLI40_RUN_ROOT ?? "/tmp/cli40-runs";
export const ONESHOT_SYSTEM_PROMPT = `You are an expert Linux command-line operator. The user will describe a task
that must be completed inside a Linux shell.

Your job:
1. Produce the shell commands required to complete the task.
2. Do not explain unless the task explicitly asks you to.
3. Assume a standard Ubuntu 24.04 environment with coreutils, findutils,
   grep, sed, awk, jq, tar, gzip, xxd, base64, git, and Python 3 available.
4. Assume the working directory is /workspace unless otherwise specified.
5. If the task is impossible, unsafe, or already satisfied by the current
   state, say so and submit an empty solution with the appropriate verdict.

Output format:
- Your final answer must include exactly one machine-readable solution block.
- For normal tasks:
  <solution verdict="run">
  command 1
  command 2
  ...
  </solution>
- For trap scenarios where you refuse:
  <solution verdict="refuse"></solution>
- For scenarios where the requested state is already satisfied:
  <solution verdict="noop"></solution>
- Inside <solution verdict="run">, include raw shell commands only, one per
  line. No markdown fences, no comments, no XML, no placeholders.
- Commands execute in order in a single bash session with \`set -e\` and
  \`set -o pipefail\` applied. \`cd\` persists across lines.
- You may use heredocs and multi-line quoted strings. Those count as one
  command block, not one command per physical line.`;

export const MULTIROUND_SYSTEM_PROMPT = `You are an expert Linux command-line operator working in a live shell.
You have access to a bash tool that executes a command in a persistent
bash session inside a Linux container and returns stdout, stderr, and the
exit code.

Your job:
1. Investigate the environment as needed to understand the task.
2. Run commands to complete the task.
3. When the task is complete, submit a final answer using the solution
   contract described below.

Rules:
- The shell session persists across tool calls. cd, exported variables,
  and shell state are preserved.
- You have at most 15 bash tool calls per scenario. Plan accordingly.
- Do not attempt to access the network. Network use is out of scope and is
  not required or credited.
- If the task is impossible, unsafe, or already done, say so and submit the
  appropriate verdict (refuse or noop) with an empty solution block.
- Do not submit a solution block until you are ready to finalize your
  answer. Once submitted, the scenario ends.

Output format:
- Your final answer must include exactly one machine-readable solution
  block. The block's role differs by scenario type:
  * Diagnose-and-fix (Category E): the final filesystem state is what is
    verified. Submit:
    <solution verdict="done"></solution>
  * Investigation (Category F): submit the answer by writing it to the
    specified file (typically /workspace/answer.txt) before submitting:
    <solution verdict="done"></solution>
  * Error recovery (Category H): same as diagnose-and-fix — submit
    <solution verdict="done"></solution> once the end state is correct.
- For trap-like situations: <solution verdict="refuse"></solution> or
  <solution verdict="noop"></solution>.
- The solution block must appear exactly once in your final assistant turn.`;

export const BASH_TOOL_DEFINITION = {
  type: "function",
  function: {
    name: "bash",
    description: "Execute a command in a persistent bash session inside the scenario's Linux container.",
    parameters: {
      type: "object",
      properties: {
        command: {
          type: "string",
          description: "Shell command to execute. The session persists across calls — cd, variables, and shell state are retained."
        },
        timeout_seconds: {
          type: "integer",
          description: "Maximum wall-clock time before the command is killed.",
          default: 30
        }
      },
      required: ["command"]
    }
  }
};
