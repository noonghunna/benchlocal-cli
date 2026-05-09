import type { SandboxVerificationResult } from "@/lib/sandbox-client";

export const SYSTEM_PROMPT = `You are a helpful assistant that generates structured output.

Rules:
- Output ONLY the requested format.
- Do not add explanations, markdown fences, labels, or surrounding prose.
- Follow the exact schema or structure described in the request.
- Use proper escaping for special characters in the target format.
- Preserve required data types exactly.
- If a field has no value, use the format's standard null/empty representation.`;

export type BenchmarkCategory = "A" | "B" | "C" | "D" | "E" | "S";
export type ScenarioStatus = "pass" | "partial" | "fail";
export type ScenarioAxis = "parseable" | "correctness" | "discipline";
export type AxisScore = 0 | 1 | 2;
export type ScenarioState = {
  assistantMessages: string[];
  finalAnswer: string;
  meta: Record<string, unknown>;
};

export type ScenarioEvaluation = {
  status: ScenarioStatus;
  score: number;
  summary: string;
  note?: string;
  axes: Record<ScenarioAxis, AxisScore>;
};

export type ScenarioDefinition = {
  id: string;
  title: string;
  category: BenchmarkCategory;
  description: string;
  userMessage: string;
  successCase: string;
  failureCase: string;
  evaluate: (state: ScenarioState) => ScenarioEvaluation;
};

export type ScenarioCard = {
  id: string;
  title: string;
  category: BenchmarkCategory;
  description: string;
  userMessage: string;
  successCase: string;
  failureCase: string;
};

export type ModelScenarioResult = {
  scenarioId: string;
  status: ScenarioStatus;
  score: number;
  summary: string;
  note?: string;
  rawLog: string;
};

export type CategoryScore = {
  category: BenchmarkCategory;
  label: string;
  weight: number;
  averageScore: number;
  percent: number;
};

export type ModelScoreSummary = {
  scenarioResults: ModelScenarioResult[];
  categoryScores: CategoryScore[];
  finalScore: number;
  totalScore: number;
  maxScore: number;
  rating: string;
};

type ScenarioSpec = Omit<ScenarioDefinition, "evaluate">;

const AXIS_WEIGHTS: Record<ScenarioAxis, number> = {
  parseable: 0.4,
  correctness: 0.35,
  discipline: 0.25
};

const CATEGORY_LABELS: Record<BenchmarkCategory, string> = {
  A: "Basic Single-Format",
  B: "Less Common Formats",
  C: "Complex Structures",
  D: "Conversion & Multi-Format",
  E: "Adversarial Edge Cases",
  S: "Supplemental"
};

const CATEGORY_WEIGHTS: Record<BenchmarkCategory, number> = {
  A: 15,
  B: 20,
  C: 25,
  D: 20,
  E: 20,
  S: 0
};

function ratingForScore(score: number): string {
  if (score >= 90) {
    return "★★★★★ Excellent";
  }
  if (score >= 75) {
    return "★★★★ Good";
  }
  if (score >= 60) {
    return "★★★ Adequate";
  }
  if (score >= 40) {
    return "★★ Weak";
  }

  return "★ Poor";
}

function statusForScore(score: number): ScenarioStatus {
  if (score >= 85) {
    return "pass";
  }
  if (score >= 60) {
    return "partial";
  }

  return "fail";
}

function scoreAxes(axes: Record<ScenarioAxis, AxisScore>): number {
  const weighted =
    axes.parseable * AXIS_WEIGHTS.parseable +
    axes.correctness * AXIS_WEIGHTS.correctness +
    axes.discipline * AXIS_WEIGHTS.discipline;

  return Math.round((weighted / 2) * 100);
}

const SCENARIO_SPECS: ScenarioSpec[] = [
  {
    id: "SO-01",
    title: "Simple JSON Object",
    category: "A",
    description: "Baseline JSON output with exact scalar types.",
    userMessage: 'Generate a JSON object for a book with these details: title "The Great Gatsby", author "F. Scott Fitzgerald", year 1925, genre "Novel", in_print true.',
    successCase: "Emits a valid JSON object with all requested fields and types, without wrappers.",
    failureCase: "Uses the wrong types, invalid JSON syntax, or extra prose."
  },
  {
    id: "SO-02",
    title: "CSV with Headers",
    category: "A",
    description: "Basic CSV generation with exact headers and row values.",
    userMessage: "Generate a CSV file with headers: name, age, city, email. Include these 3 records:\n- Alice Johnson, 32, Portland, alice@example.com\n- Bob Smith, 45, Chicago, bob@example.com\n- Carol White, 28, Austin, carol@example.com",
    successCase: "Produces standard comma-separated CSV with the requested columns and rows only.",
    failureCase: "Changes delimiters, drops rows, or adds unrequested columns."
  },
  {
    id: "SO-03",
    title: "YAML Configuration",
    category: "A",
    description: "Nested YAML with lists, booleans, and integer fields.",
    userMessage: 'Generate a YAML configuration for a web server with these settings: host "0.0.0.0", port 8080, debug false, allowed_origins is a list containing "https://example.com" and "https://app.example.com", and database has nested fields: host "localhost", port 5432, name "myapp_db".',
    successCase: "Produces parseable YAML with the correct nesting and types.",
    failureCase: "Breaks indentation, types, or the requested structure."
  },
  {
    id: "SO-04",
    title: "TOML Configuration",
    category: "B",
    description: "TOML package metadata plus dependency declarations with features.",
    userMessage: 'Generate a TOML configuration file for a Rust project with: package name "my_cli", version "0.1.0", edition "2021", authors list with "Alice <alice@example.com>". Dependencies section: serde version "1.0" with features ["derive"], clap version "4.5".',
    successCase: "Uses TOML section syntax and correct dependency shapes.",
    failureCase: "Falls back to JSON/YAML syntax or breaks the TOML tables."
  },
  {
    id: "SO-05",
    title: "SQL CREATE + INSERT",
    category: "B",
    description: "SQLite DDL plus DML for a single table and two rows.",
    userMessage: 'Generate SQL to create a table called "employees" with columns: id (integer, primary key, auto increment), name (varchar 100, not null), department (varchar 50), salary (decimal 10,2), hire_date (date). Then insert 2 rows: "Alice Chen", "Engineering", 95000.00, "2023-06-15" and "Bob Park", "Marketing", 78500.50, "2024-01-10".',
    successCase: "Executes cleanly in SQLite and creates the requested table plus the two rows.",
    failureCase: "Uses non-SQLite syntax, fails to insert both rows, or injects explicit ids."
  },
  {
    id: "SO-06",
    title: "ICS Calendar Event",
    category: "B",
    description: "A minimally conformant calendar event with the right instant and duration.",
    userMessage: 'Generate an ICS calendar event for a team meeting on April 15, 2026, from 2:00 PM to 3:30 PM Eastern Time. Title: "Q2 Planning Session". Location: "Conference Room B". Description: "Quarterly planning meeting - bring your project updates". Organizer: alice@company.com.',
    successCase: "Builds a valid VCALENDAR/VEVENT wrapper with the required properties.",
    failureCase: "Omits required calendar blocks or gets the event time wrong."
  },
  {
    id: "SO-07",
    title: "Nested JSON with Arrays and Nulls",
    category: "C",
    description: "Deeply nested JSON with arrays of objects and required null values.",
    userMessage: 'Generate a JSON object representing an API response for a user profile. The user has: id 42, username "j_doe", email null (not verified yet), roles array with "editor" and "viewer", address object with street "123 Main St", city "Springfield", state "IL", zip "62704", and phone_numbers array containing two objects: one with type "mobile", number "+1-555-0123", primary true, and one with type "work", number null, primary false. Include a metadata object with last_login "2026-03-15T10:30:00Z" and login_count 847.',
    successCase: "Keeps the full nested structure and preserves types, especially nulls and strings.",
    failureCase: "Turns nulls into strings, changes scalar types, or drops nested keys."
  },
  {
    id: "SO-08",
    title: "CSV with Special Characters",
    category: "C",
    description: "RFC 4180 CSV escaping with commas, quotes, apostrophes, and Unicode.",
    userMessage: 'Generate a CSV with headers: company, description, revenue, ceo. Include these 3 records:\n- Acme, Inc., "Makes everything, from anvils to rockets", $1.2B, Jane "JJ" Smith\n- O\'Brien & Sons, "Family-owned since 1952", $45M, Patrick O\'Brien\n- 株式会社テスト (Test Corp), "Japanese tech company", ¥500B, 田中太郎',
    successCase: "Quotes and escapes fields correctly while preserving Unicode.",
    failureCase: "Breaks the CSV parser on commas or embedded quotes."
  },
  {
    id: "SO-09",
    title: "XML with Namespaces and Attributes",
    category: "C",
    description: "XML declaration, default namespace, element attributes, and nested elements.",
    userMessage: 'Generate an XML document representing a book catalog. The root element is "catalog" with namespace "http://example.com/books" and attribute version="2.0". Include 2 books, each with attributes id and lang. Book 1: id="bk101", lang="en", title "Rust Programming", author "Steve Klabnik", price 39.99 with currency attribute "USD". Book 2: id="bk102", lang="ja", title "プログラミングRust", author "Steve Klabnik", price 4500 with currency attribute "JPY". Include an XML declaration.',
    successCase: "Produces parseable XML with the right namespace, attributes, and book payloads.",
    failureCase: "Drops the declaration, namespace, or required attributes."
  },
  {
    id: "SO-10",
    title: "JSON to Markdown Table",
    category: "D",
    description: "Format conversion from structured JSON into a deterministic Markdown table.",
    userMessage: 'Convert this JSON array into a Markdown table:\n[{"name": "Alice", "score": 95, "grade": "A"}, {"name": "Bob", "score": 82, "grade": "B+"}, {"name": "Carol", "score": 78, "grade": "C+"}, {"name": "Dave", "score": 91, "grade": "A-"}]',
    successCase: "Outputs a well-formed Markdown table with the requested rows and columns.",
    failureCase: "Misses the table shape or mutates the provided values."
  },
  {
    id: "SO-11",
    title: "Natural Language to Mermaid Diagram",
    category: "D",
    description: "Flowchart conversion with branching control flow and diagram syntax.",
    userMessage: "Generate a Mermaid flowchart for this process: User submits a form. System validates the input. If valid, save to database and send confirmation email, then show success page. If invalid, show error message and return to form.",
    successCase: "Creates a Mermaid flowchart with the required valid and invalid branches.",
    failureCase: "Skips a branch or emits prose instead of diagram syntax."
  },
  {
    id: "SO-12",
    title: "HTML Table with Semantic Markup",
    category: "D",
    description: "Semantic HTML table output with caption, thead, tbody, th, and td usage.",
    userMessage: 'Generate an HTML table showing quarterly revenue. Headers: Quarter, Revenue, Growth. Data: Q1 $1.2M +5%, Q2 $1.4M +16.7%, Q3 $1.1M -21.4%, Q4 $1.8M +63.6%. Use thead, tbody, and th elements properly. Add a caption "2025 Quarterly Revenue".',
    successCase: "Produces semantic table markup with the caption and all four rows.",
    failureCase: "Uses plain text, misses the semantic wrappers, or drops cells."
  },
  {
    id: "SO-13",
    title: "JSON with Tricky Values",
    category: "E",
    description: "JSON escaping with empty values, booleans, nulls, nested nulls, and control characters.",
    userMessage: 'Generate a JSON object with these exact key-value pairs: empty_string should be "" (an empty string), null_value should be null, zero should be 0, false_value should be false, empty_array should be [], empty_object should be {}, special_chars should be the string containing a backslash, a double quote, a newline, and a tab: `\\\"\\n\\t`, nested_null should be an object with key "a" set to null and key "b" set to an array containing null and 1.',
    successCase: "Emits valid JSON with the exact control characters and nested null structure.",
    failureCase: "Mis-escapes the control characters or coerces the empty values into the wrong types."
  },
  {
    id: "SO-14",
    title: "CSV Adversarial — The Delimiter Minefield",
    category: "E",
    description: "Adversarial CSV with embedded commas, quotes, formulas, real newlines, and an empty trailing field.",
    userMessage: 'Generate a CSV with headers: id, description, formula, notes. Include these 2 records:\nRow 1: id 1, description is: He said "hello, world" and left, formula is: =SUM(A1,B1), notes is: Line one\\nLine two (actual newline in the value)\nRow 2: id 2, description is: Simple value, formula is: =IF(A1>0,"yes","no"), notes is empty',
    successCase: "Preserves the quoted newline and quote-doubling so the CSV still parses into two rows.",
    failureCase: "Breaks row alignment or uses the wrong escaping convention.",
  },
  {
    id: "SO-15",
    title: "Binary Format Boundary — BSON",
    category: "S",
    description: "Supplemental boundary-awareness check for a non-text binary format.",
    userMessage: 'Generate a BSON document with fields name "Alice" and age 30.',
    successCase: "Explains BSON is binary and offers a practical text-friendly alternative such as JSON or code.",
    failureCase: "Hallucinates a textual BSON format or mislabels plain JSON as BSON."
  }
];

export const SCENARIOS: ScenarioDefinition[] = SCENARIO_SPECS.map((scenario) => ({
  ...scenario,
  evaluate: (state) => {
    const validation = state.meta.validationResult as SandboxVerificationResult | undefined;

    if (!validation) {
      return {
        status: "fail",
        score: 0,
        summary: "Validator result missing.",
        axes: { parseable: 0, correctness: 0, discipline: 0 }
      };
    }

    if (validation.status === "skip") {
      return {
        status: "fail",
        score: 0,
        summary: validation.summary,
        note: validation.note,
        axes: validation.axes
      };
    }

    const score = scoreAxes(validation.axes);
    return {
      status: statusForScore(score),
      score,
      summary: validation.summary,
      note: validation.note,
      axes: validation.axes
    };
  }
}));

export function scoreModelResults(results: ModelScenarioResult[]): ModelScoreSummary {
  const categoryScores = (Object.keys(CATEGORY_LABELS) as BenchmarkCategory[]).map((category) => {
    const categoryResults = results.filter((result) => SCENARIOS.find((scenario) => scenario.id === result.scenarioId)?.category === category);
    const averageScore =
      categoryResults.length === 0
        ? 0
        : Math.round(categoryResults.reduce((sum, result) => sum + result.score, 0) / categoryResults.length);

    return {
      category,
      label: CATEGORY_LABELS[category],
      weight: CATEGORY_WEIGHTS[category],
      averageScore,
      percent: averageScore
    };
  });

  const coreCategories = categoryScores.filter((categoryScore) => categoryScore.category !== "S");
  const finalScore = Math.round(
    coreCategories.reduce((sum, categoryScore) => sum + categoryScore.averageScore * (categoryScore.weight / 100), 0)
  );
  const totalScore = results.reduce((sum, result) => sum + result.score, 0);

  return {
    scenarioResults: results,
    categoryScores,
    finalScore,
    totalScore,
    maxScore: SCENARIOS.length * 100,
    rating: ratingForScore(finalScore)
  };
}

export function getScenarioCards(): ScenarioCard[] {
  return SCENARIOS.map((scenario) => ({
    id: scenario.id,
    title: scenario.title,
    category: scenario.category,
    description: scenario.description,
    userMessage: scenario.userMessage,
    successCase: scenario.successCase,
    failureCase: scenario.failureCase
  }));
}
