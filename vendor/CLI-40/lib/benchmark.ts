import type { ScoredScenarioResult } from "@benchlocal/sdk";
import rawScenarios from "../verification/scenario-data.json";

export type Cli40Mode = "oneshot" | "multiround";

export type Cli40Scenario = {
  id: string;
  kind: Cli40Mode;
  categoryId: string;
  category: string;
  title: string;
  description: string;
  promptText: string;
  successCase: string;
  failureCase: string;
};

type CategoryInfo = {
  label: string;
  weight: number;
};

const CATEGORY_ORDER: readonly string[] = ["A", "B", "C", "D", "E", "F", "G", "H"];

const CATEGORY_INFO: Record<string, CategoryInfo> = {
  A: { label: "A — Text Processing", weight: 10 },
  B: { label: "B — Filesystem Operations", weight: 10 },
  C: { label: "C — Pipelines & Composition", weight: 15 },
  D: { label: "D — Archives, Compression, Encoding", weight: 10 },
  E: { label: "E — Diagnose and Fix", weight: 20 },
  F: { label: "F — Investigation", weight: 15 },
  G: { label: "G — Restraint and Safety", weight: 10 },
  H: { label: "H — Error Recovery", weight: 10 }
};

export const SCENARIOS: readonly Cli40Scenario[] = rawScenarios as Cli40Scenario[];

export function getScenarioCards(): Cli40Scenario[] {
  return [...SCENARIOS];
}

function average(values: number[]): number {
  if (values.length === 0) {
    return 0;
  }

  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function buildSummary(totalScore: number): string {
  if (totalScore >= 90) {
    return "CLI-40 performance is excellent and consistently operational.";
  }
  if (totalScore >= 75) {
    return "CLI-40 performance is good, with a few misses in subtle or recovery-heavy scenarios.";
  }
  if (totalScore >= 60) {
    return "CLI-40 performance is adequate, but reliability drops on harder CLI tasks.";
  }
  if (totalScore >= 40) {
    return "CLI-40 performance is weak and still needs close supervision.";
  }
  return "CLI-40 performance is poor and fails most benchmark conditions.";
}

export function scoreModelResults(results: ScoredScenarioResult[]) {
  const scenarioMap = new Map(SCENARIOS.map((scenario) => [scenario.id, scenario]));
  const categoryScores = new Map<string, number[]>();

  for (const result of results) {
    const scenario = scenarioMap.get(result.scenarioId);
    if (!scenario) {
      continue;
    }

    const scores = categoryScores.get(scenario.categoryId) ?? [];
    scores.push(result.score);
    categoryScores.set(scenario.categoryId, scores);
  }

  const categories = CATEGORY_ORDER
    .filter((categoryId) => categoryScores.has(categoryId))
    .map((categoryId) => {
      const info = CATEGORY_INFO[categoryId];
      const score = Math.round(average(categoryScores.get(categoryId) ?? []));
      return {
        id: categoryId.toLowerCase(),
        label: info.label,
        score,
        weight: info.weight
      };
    });

  const weightedTotal = categories.length === 0
    ? 0
    : Math.round(
      categories.reduce((sum, category) => sum + category.score * (category.weight ?? 0), 0) /
      categories.reduce((sum, category) => sum + (category.weight ?? 0), 0)
    );

  return {
    totalScore: weightedTotal,
    categories,
    summary: buildSummary(weightedTotal)
  };
}
