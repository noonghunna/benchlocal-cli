import type { ProgressEmitter, ScenarioResult } from "@benchlocal/sdk";
import type { Cli40Scenario } from "./benchmark";

export type ModelConfig = {
  id: string;
  label: string;
  provider: string;
  providerModel: string;
  inferenceBaseUrl: string;
  authMode: "none" | "bearer";
  apiKey?: string;
  exposedModel: string;
};

type RunOptions = {
  runId: string;
  verifierUrl: string;
  temperature?: number;
  top_p?: number;
  top_k?: number;
  min_p?: number;
  repetition_penalty?: number;
  request_timeout_seconds?: number;
  signal?: AbortSignal;
};

type VerifierRequest = {
  scenarioId: string;
  runId: string;
  model: ModelConfig;
  generation: {
    temperature?: number;
    top_p?: number;
    top_k?: number;
    min_p?: number;
    repetition_penalty?: number;
    request_timeout_seconds?: number;
  };
};

async function postScenarioToVerifier(verifierUrl: string, payload: VerifierRequest, signal?: AbortSignal): Promise<ScenarioResult> {
  const response = await fetch(new URL("/run-scenario", verifierUrl), {
    method: "POST",
    headers: {
      "content-type": "application/json"
    },
    body: JSON.stringify(payload),
    signal
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`Verifier request failed with ${response.status} ${response.statusText}: ${detail}`.trim());
  }

  return await response.json() as ScenarioResult;
}

export async function runScenarioForModel(
  model: ModelConfig,
  scenario: Cli40Scenario,
  emit: ProgressEmitter,
  options: RunOptions
): Promise<ScenarioResult> {
  await emit({
    type: "model_progress",
    modelId: model.id,
    scenarioId: scenario.id,
    message: `Submitting ${scenario.id} ${scenario.title} to the CLI-40 verifier.`
  });

  return postScenarioToVerifier(
    options.verifierUrl,
    {
      scenarioId: scenario.id,
      runId: options.runId,
      model,
      generation: {
        temperature: options.temperature,
        top_p: options.top_p,
        top_k: options.top_k,
        min_p: options.min_p,
        repetition_penalty: options.repetition_penalty,
        request_timeout_seconds: options.request_timeout_seconds
      }
    },
    options.signal
  );
}
