import { spawn } from "node:child_process";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";

import { HERMES_PINNED_COMMIT } from "./manifest.mjs";

const HERMES_SOURCE_DIR = "/opt/hermes-agent";
const HERMES_VENV_DIR = "/opt/hermes-venv";
const AGENT_RUNNER_PATH = "/opt/verification/agent-runner.py";

function getVenvBinaryPath(venvDir, name) {
  return path.join(venvDir, "bin", name);
}

function commandLabel(file, args) {
  return [file, ...args].join(" ");
}

function truncateTail(value, maxChars = 20000) {
  if (value.length <= maxChars) {
    return value;
  }

  return `${value.slice(0, maxChars)}\n...[truncated]`;
}

export async function pathExists(targetPath) {
  try {
    await stat(targetPath);
    return true;
  } catch {
    return false;
  }
}

export function runCommand(file, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(file, [...args], {
      cwd: options.cwd,
      env: options.env,
      stdio: ["ignore", "pipe", "pipe"]
    });

    let stdout = "";
    let stderr = "";
    let settled = false;
    let timeout;

    const finish = (callback) => {
      if (settled) {
        return;
      }

      settled = true;
      if (timeout) {
        clearTimeout(timeout);
      }
      if (options.signal) {
        options.signal.removeEventListener("abort", abortListener);
      }
      callback();
    };

    const abortListener = () => {
      child.kill("SIGTERM");
      finish(() => reject(new Error(`Command aborted: ${commandLabel(file, args)}`)));
    };

    if (options.signal) {
      if (options.signal.aborted) {
        abortListener();
        return;
      }
      options.signal.addEventListener("abort", abortListener, { once: true });
    }

    if (options.timeoutMs && options.timeoutMs > 0) {
      timeout = setTimeout(() => {
        child.kill("SIGTERM");
        finish(() => reject(new Error(`Command timed out after ${options.timeoutMs}ms: ${commandLabel(file, args)}`)));
      }, options.timeoutMs);
    }

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout = truncateTail(`${stdout}${chunk}`);
    });
    child.stderr.on("data", (chunk) => {
      stderr = truncateTail(`${stderr}${chunk}`);
    });
    child.on("error", (error) => {
      finish(() => reject(error));
    });
    child.on("close", (code) => {
      finish(() => resolve({
        exitCode: code ?? 1,
        stdout,
        stderr
      }));
    });
  });
}

async function getGitRevision(repoDir) {
  const result = await runCommand("git", ["rev-parse", "HEAD"], {
    cwd: repoDir,
    timeoutMs: 10000
  });

  if (result.exitCode !== 0) {
    throw new Error(
      `Failed to read git revision for ${repoDir}.\ncommand=${commandLabel("git", ["rev-parse", "HEAD"])}\nstdout=${result.stdout}\nstderr=${result.stderr}`
    );
  }

  return result.stdout.trim();
}

function yamlScalar(value) {
  return JSON.stringify(value);
}

function buildHermesProviderEnv(model) {
  const env = {};

  if (model.authMode !== "bearer" || !model.apiKey) {
    return env;
  }

  const normalizedBaseUrl = String(model.inferenceBaseUrl || "").trim().toLowerCase();
  const isOpenRouter = normalizedBaseUrl.includes("openrouter.ai");

  if (isOpenRouter) {
    env.OPENROUTER_API_KEY = model.apiKey;
    env.OPENAI_API_KEY = "";
    return env;
  }

  env.OPENAI_API_KEY = model.apiKey;
  env.OPENROUTER_API_KEY = "";
  return env;
}

async function writeHermesConfig(hermesHomeDir, workspaceDir, model, maxTurns, options = {}) {
  await mkdir(hermesHomeDir, { recursive: true });
  const configPath = path.join(hermesHomeDir, "config.yaml");
  const lines = [
    "model:",
    `  default: ${yamlScalar(model.exposedModel)}`,
    '  provider: "custom"',
    `  base_url: ${yamlScalar(model.inferenceBaseUrl)}`
  ];

  if (model.authMode === "bearer" && model.apiKey) {
    lines.push(`  api_key: ${yamlScalar(model.apiKey)}`);
  }

  lines.push(
    "terminal:",
    '  backend: "local"',
    `  cwd: ${yamlScalar(workspaceDir)}`,
    "  timeout: 60",
    "  lifetime_seconds: 300",
    "display:",
    '  tool_progress: "off"',
    "  streaming: false",
    "  inline_diffs: false",
    "  show_reasoning: false",
    "compression:",
    "  enabled: false",
    "approvals:",
    '  mode: "manual"',
    "  timeout: 30",
    "agent:",
    `  max_turns: ${maxTurns}`
  );

  if (options.browserAllowPrivateUrls || options.browserCommandTimeout) {
    lines.push("browser:");
    if (options.browserAllowPrivateUrls) {
      lines.push("  allow_private_urls: true");
    }
    if (options.browserCommandTimeout) {
      lines.push(`  command_timeout: ${Number(options.browserCommandTimeout)}`);
    }
  }

  const auxiliaryTasks = Array.isArray(options.auxiliaryTasks)
    ? options.auxiliaryTasks
    : ["session_search", "web_extract", "approval"];

  if (auxiliaryTasks.length > 0) {
    lines.push("auxiliary:");

    for (const taskName of auxiliaryTasks) {
      lines.push(
        `  ${taskName}:`,
        '    provider: "custom"',
        `    model: ${yamlScalar(model.exposedModel)}`,
        `    base_url: ${yamlScalar(model.inferenceBaseUrl)}`
      );

      if (model.authMode === "bearer" && model.apiKey) {
        lines.push(`    api_key: ${yamlScalar(model.apiKey)}`);
      }
    }
  }

  await writeFile(configPath, `${lines.join("\n")}\n`, "utf8");
  return configPath;
}

function parseSessionId(stdout) {
  const match = stdout.match(/(?:^|\n)session_id:\s*([^\s]+)\s*$/m);
  return match?.[1];
}

function parseFinalAnswer(stdout) {
  const sessionMarker = stdout.match(/^(.*?)(?:\n\nsession_id:\s*[^\s]+\s*)$/s);
  const candidate = (sessionMarker?.[1] ?? stdout).trim();
  return candidate || undefined;
}

export async function resolveHermesRuntime() {
  const hermesBin = getVenvBinaryPath(HERMES_VENV_DIR, "hermes");
  const pythonBin = getVenvBinaryPath(HERMES_VENV_DIR, "python");

  if (!(await pathExists(hermesBin))) {
    throw new Error(`Hermes CLI is missing from the verifier image at ${hermesBin}.`);
  }

  const revision = await getGitRevision(HERMES_SOURCE_DIR);
  if (revision !== HERMES_PINNED_COMMIT) {
    throw new Error(`Pinned Hermes checkout mismatch: expected ${HERMES_PINNED_COMMIT}, received ${revision}.`);
  }

  return {
    sourceDir: HERMES_SOURCE_DIR,
    venvDir: HERMES_VENV_DIR,
    pythonBin,
    hermesBin,
    revision
  };
}

export async function executeHermesQuietQuery(request) {
  const hermesHomeDir = path.join(request.runDir, "hermes-home");
  const promptPath = path.join(request.runDir, "prompt.txt");
  const stdoutPath = path.join(request.runDir, "stdout.txt");
  const stderrPath = path.join(request.runDir, "stderr.txt");
  const providerEnv = buildHermesProviderEnv(request.model);

  await mkdir(request.runDir, { recursive: true });
  await mkdir(request.workspaceDir, { recursive: true });

  await writeFile(promptPath, `${request.prompt}\n`, "utf8");
  await writeHermesConfig(hermesHomeDir, request.workspaceDir, request.model, request.maxTurns);

  const args = [
    "chat",
    "-q",
    request.prompt,
    "-Q",
    "-t",
    request.toolsets.join(","),
    "--max-turns",
    String(request.maxTurns),
    "-m",
    request.model.exposedModel
  ];

  const result = await runCommand(request.runtime.hermesBin, args, {
    cwd: request.workspaceDir,
    env: {
      ...process.env,
      ...providerEnv,
      ...(request.env ?? {}),
      HERMES_HOME: hermesHomeDir,
      HERMES_WRITE_SAFE_ROOT: request.workspaceDir,
      HERMES_SESSION_SOURCE: "benchlocal-hermesagent-20"
    },
    signal: request.signal,
    timeoutMs: 10 * 60 * 1000
  });

  await writeFile(stdoutPath, result.stdout, "utf8");
  await writeFile(stderrPath, result.stderr, "utf8");

  const sessionId = parseSessionId(result.stdout);
  const sessionLogPath = sessionId
    ? path.join(hermesHomeDir, "sessions", `session_${sessionId}.json`)
    : undefined;

  let sessionLog;
  if (sessionLogPath && await pathExists(sessionLogPath)) {
    try {
      sessionLog = JSON.parse(await readFile(sessionLogPath, "utf8"));
    } catch {}
  }

  const lastAssistantText = [...(sessionLog?.messages ?? [])]
    .reverse()
    .find((message) => message.role === "assistant" && typeof message.content === "string")
    ?.content;

  return {
    exitCode: result.exitCode,
    stdout: result.stdout,
    stderr: result.stderr,
    sessionId,
    finalAnswer: lastAssistantText ?? parseFinalAnswer(result.stdout),
    sessionLogPath,
    sessionLog,
    promptPath,
    stdoutPath,
    stderrPath
  };
}

export async function executeHermesAgentRun(request) {
  const hermesHomeDir = path.join(request.runDir, "hermes-home");
  const requestPath = path.join(request.runDir, "agent-request.json");
  const resultPath = path.join(request.runDir, "agent-result.json");
  const stdoutPath = path.join(request.runDir, "agent-runner-stdout.txt");
  const stderrPath = path.join(request.runDir, "agent-runner-stderr.txt");
  const providerEnv = buildHermesProviderEnv(request.model);

  await mkdir(request.runDir, { recursive: true });
  await mkdir(request.workspaceDir, { recursive: true });
  await writeHermesConfig(
    hermesHomeDir,
    request.workspaceDir,
    request.model,
    request.maxTurns,
    {
      auxiliaryTasks: request.auxiliaryTasks,
      browserAllowPrivateUrls: request.browserAllowPrivateUrls,
      browserCommandTimeout: request.browserCommandTimeout
    }
  );

  await writeFile(requestPath, JSON.stringify({
    workspaceDir: request.workspaceDir,
    hermesHomeDir,
    prompt: request.prompt,
    model: request.model,
    toolsets: request.toolsets,
    maxTurns: request.maxTurns,
    resultPath,
    sessionId: request.sessionId,
    sessionSeed: request.sessionSeed ?? [],
    generation: request.generation ?? {},
    followUps: request.followUps ?? {}
  }, null, 2), "utf8");

  const result = await runCommand(request.runtime.pythonBin, [AGENT_RUNNER_PATH, requestPath], {
    cwd: request.workspaceDir,
    env: {
      ...process.env,
      ...providerEnv,
      ...(request.env ?? {}),
      HERMES_HOME: hermesHomeDir,
      HERMES_WRITE_SAFE_ROOT: request.workspaceDir,
      HERMES_SESSION_SOURCE: "benchlocal-hermesagent-20"
    },
    signal: request.signal,
    timeoutMs: 10 * 60 * 1000
  });

  await writeFile(stdoutPath, result.stdout, "utf8");
  await writeFile(stderrPath, result.stderr, "utf8");

  let parsed;
  try {
    parsed = JSON.parse(await readFile(resultPath, "utf8"));
  } catch (error) {
    throw new Error(
      `Hermes agent runner did not produce valid JSON.\nstdout=${result.stdout}\nstderr=${result.stderr}\nerror=${error instanceof Error ? error.message : String(error)}`
    );
  }

  return {
    exitCode: result.exitCode,
    stdout: result.stdout,
    stderr: result.stderr,
    stdoutPath,
    stderrPath,
    ...parsed
  };
}
