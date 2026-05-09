import { SCENARIOS, scoreModelResults, type ModelScenarioResult, type ScenarioDefinition, type ScenarioState } from "@/lib/benchmark";
import { callModel, createInitialMessages, type GenerationParams } from "@/lib/llm-client";
import type { ModelConfig } from "@/lib/models";
import { verifyAnswerInSandbox, type SandboxValidationOptions } from "@/lib/sandbox-client";

export type RunEvent =
  | {
      type: "run_started";
      models: Array<{ id: string; label: string }>;
      totalScenarios: number;
    }
  | {
      type: "scenario_started";
      scenarioId: string;
      title: string;
      index: number;
      total: number;
    }
  | {
      type: "model_progress";
      modelId: string;
      scenarioId: string;
      message: string;
    }
  | {
      type: "scenario_result";
      modelId: string;
      scenarioId: string;
      result: ModelScenarioResult;
    }
  | {
      type: "scenario_finished";
      scenarioId: string;
    }
  | {
      type: "run_finished";
      scores: Record<string, ReturnType<typeof scoreModelResults>>;
    }
  | {
      type: "run_error";
      message: string;
    };

export type Emit = (event: RunEvent) => Promise<void> | void;

const PROVIDER_ERROR_RETRY_PATTERN = /provider returned error/i;
const TIMEOUT_RETRY_PATTERN = /request timed out|aborted due to timeout|timeouterror|aborterror/i;
const MAX_MODEL_ATTEMPTS = 3;

function resolveScenarios(requestedScenarioIds?: string[]): ScenarioDefinition[] {
  if (!requestedScenarioIds || requestedScenarioIds.length === 0) {
    return SCENARIOS;
  }

  const requested = new Set(requestedScenarioIds);
  const selected = SCENARIOS.filter((scenario) => requested.has(scenario.id));

  if (selected.length !== requested.size) {
    const found = new Set(selected.map((scenario) => scenario.id));
    const missing = requestedScenarioIds.filter((scenarioId) => !found.has(scenarioId));
    throw new Error(`Unknown scenario id${missing.length > 1 ? "s" : ""}: ${missing.join(", ")}`);
  }

  return selected;
}

function sleep(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);

    const abort = () => {
      clearTimeout(timeout);
      reject(new Error("Request aborted."));
    };

    if (signal) {
      signal.addEventListener("abort", abort, { once: true });
    }
  });
}

function isRetryableModelError(error: Error): boolean {
  return PROVIDER_ERROR_RETRY_PATTERN.test(error.message) || TIMEOUT_RETRY_PATTERN.test(error.message);
}

function formatScenarioTrace(
  model: ModelConfig,
  scenario: ScenarioDefinition,
  evaluation: { status: string; summary: string; score?: number; note?: string },
  traceLines: string[]
): string {
  return [
    `model=${model.model}`,
    `scenario=${scenario.id} ${scenario.title}`,
    "",
    ...traceLines,
    "",
    `verdict=${evaluation.status}`,
    evaluation.score !== undefined ? `score=${evaluation.score}` : "",
    `summary=${evaluation.summary}`,
    evaluation.note ? `note=${evaluation.note}` : ""
  ]
    .filter(Boolean)
    .join("\n");
}

async function callModelWithRetry(
  model: ModelConfig,
  userMessage: string,
  params: GenerationParams | undefined,
  traceLines: string[],
  emit: Emit,
  scenarioId: string
) {
  let response: Awaited<ReturnType<typeof callModel>> | null = null;
  let lastError: Error | null = null;

  for (let attempt = 1; attempt <= MAX_MODEL_ATTEMPTS; attempt += 1) {
    try {
      response = await callModel(model, createInitialMessages(userMessage), params);
      lastError = null;
      break;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error("Unknown model execution error.");

      if (!isRetryableModelError(lastError) || attempt === MAX_MODEL_ATTEMPTS) {
        throw lastError;
      }

      traceLines.push(`retry_attempt_${attempt}=${lastError.message}`);
      const isTimeout = TIMEOUT_RETRY_PATTERN.test(lastError.message);
      await emit({
        type: "model_progress",
        modelId: model.id,
        scenarioId,
        message: isTimeout
          ? `Model request timed out, retrying (${attempt + 1}/${MAX_MODEL_ATTEMPTS})`
          : `Provider returned error, retrying (${attempt + 1}/${MAX_MODEL_ATTEMPTS})`
      });
      await sleep(750 * attempt, params?.signal);
    }
  }

  if (!response) {
    throw lastError ?? new Error("Unknown model execution error.");
  }

  return response;
}

function isCancellationError(error: unknown): boolean {
  return error instanceof Error && /request aborted|validator request aborted/i.test(error.message);
}

export async function runScenarioForModel(
  model: ModelConfig,
  scenario: ScenarioDefinition,
  emit: Emit,
  params?: GenerationParams,
  validationOptions?: SandboxValidationOptions
): Promise<ModelScenarioResult> {
  const state: ScenarioState = {
    assistantMessages: [],
    finalAnswer: "",
    meta: {}
  };
  const traceLines = [`user_turn_1=${scenario.userMessage}`];

  await emit({
    type: "model_progress",
    modelId: model.id,
    scenarioId: scenario.id,
    message: "Calling model"
  });

  try {
    const response = await callModelWithRetry(model, scenario.userMessage, params, traceLines, emit, scenario.id);
    const content = response.content || "[empty response]";
    state.assistantMessages.push(content);
    state.finalAnswer = content;
    traceLines.push(`assistant_turn_1=${content}`);
  } catch (error) {
    if (isCancellationError(error)) {
      throw error;
    }

    const summary = error instanceof Error ? error.message : "Unknown model execution error.";
    traceLines.push(`error=${summary}`);

    return {
      scenarioId: scenario.id,
      status: "fail",
      score: 0,
      summary,
      rawLog: formatScenarioTrace(model, scenario, { status: "fail", score: 0, summary }, traceLines)
    };
  }

  traceLines.push(`final_answer=${state.finalAnswer}`);

  await emit({
    type: "model_progress",
    modelId: model.id,
    scenarioId: scenario.id,
    message: "Validating artifact in container"
  });

  const validation = await verifyAnswerInSandbox(scenario.id, state.finalAnswer, {
    ...validationOptions,
    signal: params?.signal
  });
  state.meta.validationResult = validation;
  traceLines.push(`validator_status=${validation.status}`);
  traceLines.push(`validator_summary=${validation.summary}`);

  const evaluation = scenario.evaluate(state);

  return {
    scenarioId: scenario.id,
    status: evaluation.status,
    score: evaluation.score,
    summary: evaluation.summary,
    note: evaluation.note,
    rawLog: formatScenarioTrace(model, scenario, evaluation, traceLines)
  };
}

export async function runBenchmark(
  models: ModelConfig[],
  emit: Emit,
  requestedScenarioIds?: string[],
  params?: GenerationParams,
  validationOptions?: SandboxValidationOptions
): Promise<void> {
  const scenarios = resolveScenarios(requestedScenarioIds);
  const resultsByModel: Record<string, ModelScenarioResult[]> = Object.fromEntries(models.map((model) => [model.id, []]));

  await emit({
    type: "run_started",
    models: models.map((model) => ({ id: model.id, label: model.label })),
    totalScenarios: scenarios.length
  });

  try {
    const runScenario = async (model: ModelConfig, scenario: ScenarioDefinition) => {
      const result = await runScenarioForModel(model, scenario, emit, params, validationOptions);
      return { modelId: model.id, scenarioId: scenario.id, result };
    };

    const emitResult = async (modelId: string, scenarioId: string, result: ModelScenarioResult) => {
      resultsByModel[modelId].push(result);
      await emit({ type: "scenario_result", modelId, scenarioId, result });
    };

    for (const [index, scenario] of scenarios.entries()) {
      await emit({
        type: "scenario_started",
        scenarioId: scenario.id,
        title: scenario.title,
        index: index + 1,
        total: scenarios.length
      });

      const results = await Promise.all(models.map((model) => runScenario(model, scenario)));
      for (const { modelId, scenarioId, result } of results) {
        await emitResult(modelId, scenarioId, result);
      }

      await emit({
        type: "scenario_finished",
        scenarioId: scenario.id
      });
    }

    const scores = Object.fromEntries(
      Object.entries(resultsByModel).map(([modelId, results]) => [modelId, scoreModelResults(results)])
    );

    await emit({
      type: "run_finished",
      scores
    });
  } catch (error) {
    await emit({
      type: "run_error",
      message: error instanceof Error ? error.message : "Unknown benchmark error."
    });
  }
}
