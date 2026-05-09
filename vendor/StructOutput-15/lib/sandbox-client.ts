import type { AxisScore, ScenarioAxis } from "@/lib/benchmark";

export type SandboxVerificationResult =
  | {
      status: "pass" | "fail";
      scenarioId: string;
      summary: string;
      note?: string;
      axes: Record<ScenarioAxis, AxisScore>;
    }
  | {
      status: "skip";
      scenarioId: string;
      summary: string;
      note?: string;
      axes: Record<ScenarioAxis, AxisScore>;
    };

const DEFAULT_VALIDATOR_URL = "http://127.0.0.1:4011";
const DEFAULT_VALIDATOR_TIMEOUT_MS = 20_000;

export type SandboxValidationOptions = {
  validatorUrl?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
};

function resolveValidatorUrl(options?: SandboxValidationOptions): string {
  const raw = options?.validatorUrl?.trim() || process.env.STRUCTOUTPUT_VALIDATOR_URL?.trim() || DEFAULT_VALIDATOR_URL;
  return raw.replace(/\/+$/, "");
}

function resolveTimeoutMs(options?: SandboxValidationOptions): number {
  if (options?.timeoutMs !== undefined) {
    const requested = Math.trunc(options.timeoutMs);
    if (Number.isFinite(requested) && requested > 0) {
      return requested;
    }
  }

  const raw = process.env.STRUCTOUTPUT_VALIDATOR_TIMEOUT_MS?.trim();

  if (!raw) {
    return DEFAULT_VALIDATOR_TIMEOUT_MS;
  }

  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_VALIDATOR_TIMEOUT_MS;
}

export async function verifyAnswerInSandbox(
  scenarioId: string,
  answer: string,
  options?: SandboxValidationOptions
): Promise<SandboxVerificationResult> {
  const validatorUrl = resolveValidatorUrl(options);
  const timeoutMs = resolveTimeoutMs(options);
  const timeoutController = new AbortController();
  const timeoutHandle = setTimeout(() => timeoutController.abort(), timeoutMs);
  const parentSignal = options?.signal;
  const abortController = new AbortController();

  const abortFromParent = () => abortController.abort(parentSignal?.reason);
  const abortFromTimeout = () => abortController.abort(new DOMException("Validator timed out.", "TimeoutError"));

  parentSignal?.addEventListener("abort", abortFromParent, { once: true });
  timeoutController.signal.addEventListener("abort", abortFromTimeout, { once: true });

  let response: Response;

  try {
    response = await fetch(`${validatorUrl}/verify-answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenarioId, answer }),
      signal: abortController.signal
    });
  } catch (error) {
    if (parentSignal?.aborted) {
      throw new Error("Validator request aborted.");
    }

    return {
      status: "skip",
      scenarioId,
      summary: error instanceof Error ? `Validator unavailable: ${error.message}` : "Validator unavailable.",
      axes: { parseable: 0, correctness: 0, discipline: 0 }
    };
  } finally {
    clearTimeout(timeoutHandle);
    parentSignal?.removeEventListener("abort", abortFromParent);
  }

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { error?: string } | null;
    return {
      status: "skip",
      scenarioId,
      summary: payload?.error ? `Validator error: ${payload.error}` : `Validator error: HTTP ${response.status}.`,
      axes: { parseable: 0, correctness: 0, discipline: 0 }
    };
  }

  return (await response.json()) as SandboxVerificationResult;
}
