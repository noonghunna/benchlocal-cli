import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

import { SCENARIOS, getScenarioById } from "./manifest.mjs";
import { parseSolutionSubmission } from "./service-helpers.mjs";

function ensureDir(dirPath) {
  return fs.mkdir(dirPath, { recursive: true });
}

async function writeFiles(baseDir, files) {
  for (const [relativePath, content] of Object.entries(files)) {
    const filePath = path.join(baseDir, relativePath);
    await ensureDir(path.dirname(filePath));
    await fs.writeFile(filePath, content, "utf8");
  }
}

export function runCommand(command, cwd, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    const child = spawn(command[0], command.slice(1), {
      cwd,
      env: {
        ...process.env,
        HOME: "/tmp/home",
        TMPDIR: "/tmp",
        GOCACHE: "/tmp/go-cache",
        GOMODCACHE: "/tmp/go-mod-cache",
        CGO_ENABLED: "1"
      }
    });

    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });

    child.on("close", (code, signal) => {
      clearTimeout(timer);

      if (timedOut) {
        resolve({
          exitCode: -1,
          signal: signal ?? "SIGKILL",
          stdout,
          stderr: `${stderr}\nTimed out after ${timeoutMs}ms.`
        });
        return;
      }

      resolve({
        exitCode: code ?? 0,
        signal: signal ?? null,
        stdout,
        stderr
      });
    });
  });
}

async function executeCandidate(tempRoot, scenario, variantName, candidate) {
  const workDir = path.join(tempRoot, `${scenario.id}-${variantName}-${candidate.label.replace(/[^a-z0-9_-]+/gi, "_")}`);
  await ensureDir(workDir);
  await writeFiles(workDir, candidate.files);

  const checks = candidate.checks ?? scenario[variantName].checks;
  const checkResults = [];

  for (const check of checks) {
    const result = await runCommand(check.command, workDir, check.timeoutMs);

    try {
      await check.validate(result);
      checkResults.push({
        name: check.name,
        status: "passed",
        command: check.command.join(" "),
        exitCode: result.exitCode
      });
    } catch (error) {
      checkResults.push({
        name: check.name,
        status: "failed",
        command: check.command.join(" "),
        exitCode: result.exitCode,
        stdout: result.stdout,
        stderr: result.stderr,
        error: error instanceof Error ? error.message : String(error)
      });

      return {
        label: candidate.label,
        status: "failed",
        checks: checkResults,
        source: candidate.source ?? "generated"
      };
    }
  }

  return {
    label: candidate.label,
    status: "passed",
    checks: checkResults,
    source: candidate.source ?? "generated"
  };
}

export async function runCanonicalVariants({ scenarioIds, variant = "all" } = {}) {
  await Promise.all([ensureDir("/tmp/home"), ensureDir("/tmp/go-cache"), ensureDir("/tmp/go-mod-cache")]);

  const selectedScenarios =
    !scenarioIds || scenarioIds.length === 0
      ? SCENARIOS
      : scenarioIds.map((scenarioId) => {
          const scenario = getScenarioById(scenarioId);
          if (!scenario) {
            throw new Error(`Unknown scenario id: ${scenarioId}`);
          }
          return scenario;
        });

  if (!["all", "buggy", "fixed"].includes(variant)) {
    throw new Error(`Unsupported variant "${variant}". Use all, buggy, or fixed.`);
  }

  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "bugfind-verify-"));
  const variantNames = variant === "all" ? ["buggy", "fixed"] : [variant];
  const results = [];

  try {
    for (const scenario of selectedScenarios) {
      const scenarioResult = {
        id: scenario.id,
        language: scenario.language,
        status: "passed",
        variants: []
      };

      for (const variantName of variantNames) {
        const candidate = {
          label: variantName,
          files: scenario[variantName].files,
          checks: scenario[variantName].checks,
          source: "canonical"
        };
        const variantResult = await executeCandidate(tempRoot, scenario, variantName, candidate);
        scenarioResult.variants.push({
          variant: variantName,
          status: variantResult.status,
          checks: variantResult.checks,
          label: variantResult.label,
          source: variantResult.source
        });

        if (variantResult.status === "failed") {
          scenarioResult.status = "failed";
        }
      }

      results.push(scenarioResult);
    }
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }

  return {
    status: results.some((result) => result.status === "failed") ? "failed" : "passed",
    scenarioCount: results.length,
    results
  };
}

export async function verifyAnswer(scenarioId, answer) {
  await Promise.all([ensureDir("/tmp/home"), ensureDir("/tmp/go-cache"), ensureDir("/tmp/go-mod-cache")]);

  const scenario = getScenarioById(scenarioId);

  if (!scenario) {
    throw new Error(`Unknown scenario id: ${scenarioId}`);
  }

  const submission = parseSolutionSubmission(scenario, answer);

  if (submission.status !== "ok") {
    return {
      scenarioId,
      status: "fail",
      summary: submission.summary,
      candidatesTried: 0,
      results: []
    };
  }

  const { candidates } = submission;

  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "bugfind-answer-"));
  const results = [];

  try {
    for (const candidate of candidates) {
      const candidateResult = await executeCandidate(tempRoot, scenario, "fixed", candidate);
      results.push(candidateResult);

      if (candidateResult.status === "passed") {
        return {
          scenarioId,
          status: "pass",
          summary: `Sandbox verified a runnable fix via candidate "${candidateResult.label}".`,
          candidate: {
            label: candidateResult.label,
            source: candidateResult.source
          },
          candidatesTried: results.length,
          results
        };
      }
    }
  } finally {
    await fs.rm(tempRoot, { recursive: true, force: true });
  }

  return {
    scenarioId,
    status: "fail",
    summary: "Sandbox executed derived candidate fixes, but none passed the canonical checks.",
    candidatesTried: results.length,
    results
  };
}
