import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdir, readFile, rm } from "node:fs/promises";
import path from "node:path";

function truncateText(value, maxBytes = 32 * 1024) {
  const buffer = Buffer.from(value ?? "", "utf8");
  if (buffer.length <= maxBytes) {
    return buffer.toString("utf8");
  }
  return Buffer.concat([
    buffer.subarray(0, maxBytes),
    Buffer.from(`\n...[truncated ${buffer.length - maxBytes} bytes]`)
  ]).toString("utf8");
}

export class BashSession {
  constructor({ cwd, tempRoot }) {
    this.cwd = cwd;
    this.tempRoot = tempRoot;
    this.child = null;
    this.stdoutBuffer = "";
    this.stderrBuffer = "";
    this.closed = false;
  }

  async start() {
    await mkdir(this.tempRoot, { recursive: true });

    this.child = spawn("bash", ["--noprofile", "--norc"], {
      cwd: this.cwd,
      env: {
        ...process.env,
        PS1: ""
      },
      stdio: ["pipe", "pipe", "pipe"]
    });

    this.child.stdout.setEncoding("utf8");
    this.child.stderr.setEncoding("utf8");
    this.child.stdout.on("data", (chunk) => {
      this.stdoutBuffer += chunk;
    });
    this.child.stderr.on("data", (chunk) => {
      this.stderrBuffer += chunk;
    });
  }

  async run(command, timeoutSeconds = 30) {
    if (!this.child || this.closed) {
      throw new Error("Bash session is not running.");
    }

    const id = randomUUID();
    const commandFile = path.join(this.tempRoot, `command-${id}.sh`);
    const stdoutFile = path.join(this.tempRoot, `stdout-${id}.txt`);
    const stderrFile = path.join(this.tempRoot, `stderr-${id}.txt`);
    const marker = `__CLI40_DONE_${id}__`;

    const wrapped = [
      `cat > ${shellQuote(commandFile)} <<'__CLI40_CMD_${id}__'`,
      command,
      `__CLI40_CMD_${id}__`,
      `: > ${shellQuote(stdoutFile)}`,
      `: > ${shellQuote(stderrFile)}`,
      `{ source ${shellQuote(commandFile)}; } >${shellQuote(stdoutFile)} 2>${shellQuote(stderrFile)}`,
      `__cli40_ec=$?`,
      `printf '${marker}:%s\\n' "$__cli40_ec"`
    ].join("\n");

    const startIndex = this.stdoutBuffer.length;
    this.child.stdin.write(`${wrapped}\n`);

    const exitCodeText = await this.waitForMarker(marker, startIndex, timeoutSeconds * 1000);
    const exitCode = Number.parseInt(exitCodeText, 10);
    const stdout = truncateText(await readFile(stdoutFile, "utf8"));
    const stderr = truncateText(await readFile(stderrFile, "utf8"));

    await rm(commandFile, { force: true });
    await rm(stdoutFile, { force: true });
    await rm(stderrFile, { force: true });

    return {
      stdout,
      stderr,
      exit_code: Number.isFinite(exitCode) ? exitCode : 1,
      timed_out: false
    };
  }

  async waitForMarker(marker, startIndex, timeoutMs) {
    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
      const slice = this.stdoutBuffer.slice(startIndex);
      const match = slice.match(new RegExp(`${marker}:(\\d+)`));
      if (match) {
        return match[1];
      }

      if (this.child.exitCode !== null) {
        throw new Error(`Bash session exited unexpectedly with code ${this.child.exitCode}. stderr=${this.stderrBuffer}`);
      }

      await new Promise((resolve) => setTimeout(resolve, 25));
    }

    await this.close();
    throw new Error(`Bash command timed out after ${Math.round(timeoutMs / 1000)}s.`);
  }

  async close() {
    if (!this.child || this.closed) {
      return;
    }

    this.closed = true;
    this.child.kill("SIGTERM");
    await new Promise((resolve) => {
      this.child.once("exit", () => resolve());
      setTimeout(resolve, 1000);
    });
  }
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}
