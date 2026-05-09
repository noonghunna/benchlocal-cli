import type { SandboxVerificationResult } from "@/lib/sandbox-client";

export const SYSTEM_PROMPT = `You are an expert software debugger. The user will show you code that may have a bug.

Your job:
1. IDENTIFY the bug — point to the exact line(s) and explain what is wrong.
2. EXPLAIN why it causes the observed behavior.
3. FIX the code — provide the corrected version.

Rules:
- Do not rewrite the entire program. Only change what is necessary.
- If the code looks correct and the described behavior seems impossible, say so.
- If you need more information to diagnose the bug, ask specific questions.
- Do not introduce new functionality. Only fix the bug.
- Your final answer may include brief explanation text outside the solution block, but it must include exactly one machine-readable solution block using this format:
  <solution language="python|javascript|rust|go" verdict="fix">
  corrected code here
  </solution>
- Inside <solution verdict="fix">, include raw valid source code only.
- Do not put explanations, bullet points, placeholders, XML/HTML tags, or markdown fences such as \`\`\` inside the <solution> block.
- Do not write phrases like "corrected code here" or "fixed code".
- Do not wrap the answer in tags like <response>, <analysis>, <fixed_code>, or <parameter>.
- Use verdict="fix" only when you are actually providing corrected code.
- For trap scenarios where there is no bug, put your explanation outside the block and use exactly this empty block:
  <solution language="python|javascript|rust|go" verdict="no_bug"></solution>
- When verdict="no_bug", the <solution> block must be completely empty.
- If you ask a clarification question first, do not include a <solution> block yet. Once you give the final answer, include exactly one <solution> block.`;

export type BenchmarkCategory = "A" | "B" | "C" | "D" | "E";
export type ScenarioStatus = "pass" | "partial" | "fail";
export type ScenarioAxis = "identification" | "fixQuality" | "discipline";
export type Difficulty = "Easy" | "Medium" | "Hard" | "Expert";
export type Language = "Python" | "JavaScript" | "Rust" | "Go";
export type AxisScore = 0 | 1 | 2;
export type MultiTurnQuality = "targeted" | "generic" | "irrelevant" | "none";

export type ConversationMessage = {
  role: "system" | "user" | "assistant";
  content: string;
};

export type ScenarioState = {
  assistantMessages: string[];
  finalAnswer: string;
  conversation: ConversationMessage[];
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
  language: Language;
  difficulty: Difficulty;
  userMessage: string;
  description: string;
  successCase: string;
  failureCase: string;
  getFollowUp?: (state: ScenarioState, assistantMessage: string) => string | null;
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

const AXIS_WEIGHTS: Record<ScenarioAxis, number> = {
  identification: 0.35,
  fixQuality: 0.4,
  discipline: 0.25
};

const CATEGORY_LABELS: Record<BenchmarkCategory, string> = {
  A: "Syntax & Surface",
  B: "Logic & Algorithmic",
  C: "Subtle & Tricky",
  D: "Red Herring Resistance",
  E: "Multi-Turn Debugging"
};

const CATEGORY_WEIGHTS: Record<BenchmarkCategory, number> = {
  A: 15,
  B: 25,
  C: 25,
  D: 20,
  E: 15
};

function normalize(value: string): string {
  return value.trim().toLowerCase();
}

function includesAny(text: string, needles: string[]): boolean {
  const source = normalize(text);
  return needles.some((needle) => source.includes(normalize(needle)));
}

function includesAll(text: string, needles: string[]): boolean {
  const source = normalize(text);
  return needles.every((needle) => source.includes(normalize(needle)));
}

function matchesAny(text: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(text));
}

function stripCodeFences(text: string): string {
  return text.replace(/```[\s\S]*?```/g, " ");
}

function stripSolutionBlocks(text: string): string {
  return text.replace(/<solution\b[\s\S]*?<\/solution>/gi, " ");
}

function proseOnlyText(text: string): string {
  return stripSolutionBlocks(stripCodeFences(text));
}

function containsQuestion(text: string): boolean {
  const stripped = stripCodeFences(text);
  return /\?/.test(stripped) || /(^|\s)(what|which|could|can|are|is|do|does|did|where|when|why)\b/i.test(stripped);
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

function scoreAxes(axes: Record<ScenarioAxis, AxisScore>, adjustment = 0): number {
  const weighted =
    axes.identification * AXIS_WEIGHTS.identification +
    axes.fixQuality * AXIS_WEIGHTS.fixQuality +
    axes.discipline * AXIS_WEIGHTS.discipline;

  const base = Math.round((weighted / 2) * 100);
  return Math.max(0, Math.min(100, base + adjustment));
}

function makeEvaluation(
  axes: Record<ScenarioAxis, AxisScore>,
  summary: string,
  state?: ScenarioState,
  note?: string
): ScenarioEvaluation {
  const execution = state?.meta.executionResult as SandboxVerificationResult | undefined;
  const quality = (state?.meta.multiTurnQuality as MultiTurnQuality | undefined) ?? "none";
  const adjustment = quality === "targeted" ? 10 : quality === "irrelevant" ? -5 : 0;
  const adjustedAxes = { ...axes };

  let executionNote: string | undefined;

  if (execution?.status === "pass") {
    adjustedAxes.fixQuality = 2;
    executionNote = execution.summary;
  } else if (execution?.status === "fail") {
    adjustedAxes.fixQuality = 0;
    executionNote = execution.summary;
  } else if (execution?.status === "skip" && execution.summary.toLowerCase().includes("sandbox unavailable")) {
    executionNote = execution.summary;
  }

  const qualityNote =
    quality === "targeted"
      ? "Targeted clarification question earned the multi-turn bonus."
      : quality === "irrelevant"
        ? "Irrelevant clarification question incurred the multi-turn penalty."
        : undefined;
  const combinedNote = [note, executionNote, qualityNote].filter(Boolean).join(" ").trim() || undefined;
  const score = scoreAxes(adjustedAxes, adjustment);

  return {
    status: statusForScore(score),
    score,
    summary,
    note: combinedNote,
    axes: adjustedAxes
  };
}

function combinedAssistantText(state: ScenarioState): string {
  return state.assistantMessages.join("\n\n");
}

function firstAssistantText(state: ScenarioState): string {
  return state.assistantMessages[0] ?? "";
}

function mentionsNoBug(text: string): boolean {
  return (
    includesAny(text, [
      "no bug",
      "code is correct",
      "this compiles fine",
      "nothing is wrong",
      "looks correct",
      "this code is valid",
      "there isn't a bug"
    ]) || matchesAny(text, [/no\s+issue/i, /works?\s+correctly/i])
  );
}

function suggestsUnnecessaryTrapFix(text: string): boolean {
  return includesAny(text, ["clone()", ".clone()", "&name", "append(key)", "result.append(key)", "change to key"]);
}

function mentionsMinimalRewrite(text: string): boolean {
  return includesAny(text, ["rewrite", "from scratch", "completely rewrite", "new algorithm", "different approach"]);
}

function mentionsCodePattern(text: string, patterns: string[]): boolean {
  const source = text.replace(/\s+/g, " ");
  return patterns.some((pattern) => source.includes(pattern));
}

function usesFixVerdict(text: string): boolean {
  return /<solution\b[^>]*verdict\s*=\s*["']fix["']/i.test(text);
}

function classifyBf13Question(text: string): MultiTurnQuality {
  if (!containsQuestion(text)) {
    return "none";
  }

  if (
    matchesAny(text, [
      /what output/i,
      /what (are|is).*(expect|expected)/i,
      /what do you see/i,
      /what are you getting/i,
      /bob.*(last|first)/i
    ])
  ) {
    return "targeted";
  }

  if (matchesAny(text, [/python version/i, /how many users/i, /framework/i, /library/i])) {
    return "irrelevant";
  }

  return "generic";
}

function classifyBf14Question(text: string): MultiTurnQuality {
  if (!containsQuestion(text)) {
    return "none";
  }

  if (
    matchesAny(text, [
      /production data/i,
      /logs/i,
      /shipping[_ ]address/i,
      /pickup/i,
      /missing field/i,
      /what does .*order.*look like/i
    ])
  ) {
    return "targeted";
  }

  if (matchesAny(text, [/node version/i, /framework/i, /npm/i])) {
    return "irrelevant";
  }

  return "generic";
}

function classifyBf15Question(text: string): MultiTurnQuality {
  if (!containsQuestion(text)) {
    return "none";
  }

  if (matchesAny(text, [/multi-core/i, /multi core/i, /how many cores/i, /race/i, /concurrency/i, /production server/i])) {
    return "targeted";
  }

  if (matchesAny(text, [/go version/i, /goroutine count/i, /memory/i])) {
    return "irrelevant";
  }

  return "generic";
}

function buildMultiTurnFollowUp(
  classifier: (assistantMessage: string) => MultiTurnQuality,
  clarification: string
): (state: ScenarioState, assistantMessage: string) => string | null {
  return (state, assistantMessage) => {
    if (state.meta.followUpSent) {
      return null;
    }

    const quality = classifier(assistantMessage);

    if (quality === "none") {
      state.meta.multiTurnQuality = "none";
      return null;
    }

    state.meta.multiTurnQuality = quality;
    state.meta.followUpSent = true;
    return clarification;
  };
}

function noteIfMentioned(text: string, patterns: RegExp[], note: string): string | undefined {
  return matchesAny(text, patterns) ? note : undefined;
}

export const SCENARIOS: ScenarioDefinition[] = [
  {
    id: "BF-01",
    title: "Off-by-One in Python Loop",
    category: "A",
    language: "Python",
    difficulty: "Easy",
    userMessage:
      "This function should return the sum of all elements in a list, but it's throwing an IndexError. Can you find the bug?\n\n```python\ndef sum_list(numbers):\n    total = 0\n    for i in range(1, len(numbers) + 1):\n        total += numbers[i]\n    return total\n\n# sum_list([1, 2, 3])  -> IndexError: list index out of range\n# sum_list([10])       -> IndexError: list index out of range\n```",
    description: "Tests whether the model catches an off-by-one range bug instead of hand-waving at the IndexError.",
    successCase: "Identify the `range(1, len(numbers) + 1)` bug, explain the skipped first element, and fix the loop minimally.",
    failureCase: "Miss the off-by-one, or fix it with an unnecessary rewrite that avoids explaining what is actually wrong.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesBug =
        includesAny(answer, ["off-by-one", "range(1", "len(numbers) + 1", "starts at 1", "index 3", "out of bounds"]) ||
        matchesAny(answer, [/skip.*first/i, /index\s*0/i]);
      const minimalFix =
        includesAny(answer, ["range(len(numbers))", "for num in numbers", "start from 0"]) ||
        matchesAny(answer, [/for\s+\w+\s+in\s+numbers/i]);
      const usesEnumerate = includesAny(answer, ["enumerate("]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesBug ? 2 : 0,
        fixQuality: minimalFix ? 2 : identifiesBug ? 1 : 0,
        discipline: usesEnumerate ? 1 : identifiesBug ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to isolate the off-by-one loop bounds bug.", state);
    }
  },
  {
    id: "BF-02",
    title: "JavaScript Equality Trap",
    category: "A",
    language: "JavaScript",
    difficulty: "Easy",
    userMessage:
      "This validation function should reject empty strings, but `validateInput(\"\")` returns `true`. What's wrong?\n\n```javascript\nfunction validateInput(input) {\n    if (input !== null && input !== undefined && input !== false) {\n        return true;\n    }\n    return false;\n}\n\n// validateInput(\"\")         -> true\n// validateInput(null)       -> false\n// validateInput(undefined)  -> false\n// validateInput(\"hello\")    -> true\n```",
    description: "Checks whether the model finds the missing empty-string case rather than inventing a coercion issue.",
    successCase: "Call out the missing `\"\"` check, explain why `\"\"` passes, and fix it with either an explicit check or a proper falsy guard.",
    failureCase: "Misdiagnose the issue as `==`/`===` or vague type coercion without addressing the actual missing case.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const missingEmptyString =
        includesAny(answer, ['empty string', '""', "missing case", "missing check"]) &&
        !includesAny(answer, ["== vs ===", "=== issue"]);
      const goodFix = includesAny(answer, ['!== ""', "if (input)", "falsy"]);
      const weakFix = includesAny(answer, ["input.length"]);
      const badDiagnosis = includesAny(answer, ["== vs ===", "type coercion bug"]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: missingEmptyString ? 2 : badDiagnosis ? 0 : 1,
        fixQuality: goodFix ? 2 : weakFix ? 1 : 0,
        discipline: badDiagnosis ? 0 : weakFix ? 1 : missingEmptyString ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to identify the missing empty-string case.", state);
    }
  },
  {
    id: "BF-03",
    title: "Rust Trap Scenario",
    category: "A",
    language: "Rust",
    difficulty: "Easy",
    userMessage:
      "This Rust code won't compile. The compiler says \"value used here after move\" but I don't understand why. Can you help?\n\n```rust\nfn main() {\n    let name = String::from(\"Alice\");\n    let greeting = format!(\"Hello, {}\", name);\n    println!(\"{}\", greeting);\n    println!(\"Name was: {}\", name);\n}\n```",
    description: "Trap scenario. The code is correct, so the model has to resist inventing an ownership bug.",
    successCase: "Explain that `format!` borrows its arguments, the code compiles, and the user likely has a different error elsewhere.",
    failureCase: "Invent a fix like `.clone()` or `&name` for a move bug that is not actually present.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesTrap = mentionsNoBug(answer) || includesAny(answer, ["format! borrows", "does not move", "compiles fine"]);
      const asksForRealCode = includesAny(answer, ["double-check", "actual error", "real code", "different code"]);
      const falsePositive = suggestsUnnecessaryTrapFix(answer);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesTrap && !falsePositive ? 2 : 0,
        fixQuality: identifiesTrap && !falsePositive ? 2 : asksForRealCode ? 1 : 0,
        discipline: falsePositive ? 0 : asksForRealCode ? 2 : identifiesTrap ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to recognize that the code is already correct.", state);
    }
  },
  {
    id: "BF-04",
    title: "Python Dictionary Mutation During Iteration",
    category: "B",
    language: "Python",
    difficulty: "Medium",
    userMessage:
      "This function should remove all users with \"inactive\" status, but it crashes with `RuntimeError: dictionary changed size during iteration`. What am I doing wrong?\n\n```python\ndef remove_inactive_users(users):\n    for user_id, status in users.items():\n        if status == \"inactive\":\n            del users[user_id]\n    return users\n\nusers = {\"u1\": \"active\", \"u2\": \"inactive\", \"u3\": \"active\", \"u4\": \"inactive\"}\nprint(remove_inactive_users(users))\n```",
    description: "Measures whether the model explains mutation-during-iteration rather than suggesting a band-aid.",
    successCase: "Explain why deleting from a dict during iteration raises `RuntimeError`, then fix it with a snapshot or two-pass approach.",
    failureCase: "Wrap the deletion in `try/except` or avoid explaining why the dictionary view is the problem.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesBug = includesAny(answer, ["dictionary changed size", "modify a dictionary while iterating", "mutating the dictionary", "during iteration"]);
      const goodFix =
        includesAny(answer, ["list(users.items())", "dict comprehension", "to_remove", "collect", "return {"]) ||
        matchesAny(answer, [/for .* in .*to_remove/i]);
      const badFix = includesAny(answer, ["try/except"]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesBug ? 2 : 0,
        fixQuality: goodFix ? 2 : identifiesBug ? 1 : 0,
        discipline: badFix ? 0 : identifiesBug ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to diagnose and fix dictionary mutation during iteration.", state);
    }
  },
  {
    id: "BF-05",
    title: "Go Goroutine Loop Variable Capture",
    category: "B",
    language: "Go",
    difficulty: "Medium",
    userMessage:
      "I'm running Go 1.21. I'm trying to print numbers 0-4 using goroutines, but the output is always `5 5 5 5 5` instead. What's going on?\n\n```go\npackage main\n\nimport (\n    \"fmt\"\n    \"sync\"\n)\n\nfunc main() {\n    var wg sync.WaitGroup\n    for i := 0; i < 5; i++ {\n        wg.Add(1)\n        go func() {\n            defer wg.Done()\n            fmt.Println(i)\n        }()\n    }\n    wg.Wait()\n}\n```",
    description: "Checks whether the model knows pre-Go-1.22 loop-variable capture semantics and fixes the closure correctly.",
    successCase: "Explain that the goroutine closes over the loop variable `i`, then pass `i` as a parameter or shadow it per iteration.",
    failureCase: "Offer timing hacks like `time.Sleep` instead of fixing the captured loop variable.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesBug = includesAny(answer, ["captures the variable", "closure captures", "same final value", "i equals 5", "loop variable"]);
      const goodFix = includesAny(answer, ["go func(n int)", "}(i)", "i := i"]);
      const badFix = includesAny(answer, ["time.sleep", "sleep("]);
      const note = noteIfMentioned(answer, [/go 1\.22/i, /go1\.22/i, /1\.22.*changed/i], "Mentioned the Go 1.22 loop-variable semantic change.");

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesBug ? 2 : 0,
        fixQuality: goodFix ? 2 : identifiesBug ? 1 : 0,
        discipline: badFix ? 0 : identifiesBug ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to diagnose loop-variable capture in Go 1.21.", state, note);
    }
  },
  {
    id: "BF-06",
    title: "JavaScript Async/Await Missing Await",
    category: "B",
    language: "JavaScript",
    difficulty: "Medium",
    userMessage:
      "This function should fetch a user and return their name, but it throws `TypeError: response.json is not a function`. The API is definitely working — I checked in the browser.\n\n```javascript\nasync function getUserName(userId) {\n    const response = fetch(`/api/users/${userId}`);\n    const data = response.json();\n    return data.name;\n}\n```",
    description: "Checks whether the model catches both missing `await` calls instead of fixing only one layer.",
    successCase: "Explain that `fetch()` returns a Promise, then add `await` to both `fetch()` and `response.json()`.",
    failureCase: "Patch only the `fetch()` call or switch to `.then()` chains without explaining the actual Promise issue.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const prose = proseOnlyText(answer);
      const identifiesPromise =
        includesAny(prose, ["response is a promise", "pending promise", "promise object"]) ||
        matchesAny(prose, [/fetch\W*(returns?|is)\W+(an?\W+)?promise/i]);
      const awaitFetch = includesAny(answer, ["await fetch"]);
      const awaitJson = includesAny(answer, ["await response.json"]);
      const thenChain = includesAny(answer, [".then("]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesPromise ? 2 : 0,
        fixQuality: awaitFetch && awaitJson ? 2 : awaitFetch || awaitJson ? 1 : 0,
        discipline: thenChain ? 1 : identifiesPromise ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to add both missing awaits and fix the Promise mismatch.", state);
    }
  },
  {
    id: "BF-07",
    title: "Python Mutable Default Argument",
    category: "C",
    language: "Python",
    difficulty: "Hard",
    userMessage:
      "Every time I call `add_item`, items from previous calls show up in the list. The first call works fine but subsequent calls are broken. What's happening?\n\n```python\ndef add_item(item, item_list=[]):\n    item_list.append(item)\n    return item_list\n\nprint(add_item(\"apple\"))\nprint(add_item(\"banana\"))\nprint(add_item(\"cherry\"))\n```",
    description: "Tests whether the model understands Python's one-time evaluation of mutable default arguments.",
    successCase: "Call out the shared default list, explain why it persists across calls, and switch to the `None` sentinel pattern.",
    failureCase: "Treat it like a random Python bug or change the function contract instead of fixing the default argument behavior.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesBug = includesAny(answer, ["mutable default", "default argument", "evaluated once", "shared list", "same list object"]);
      const goodFix = includesAny(answer, ["item_list=None", "if item_list is None", "item_list = []"]);
      const contractChange = includesAny(answer, ["remove the default", "require item_list"]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesBug ? 2 : 0,
        fixQuality: goodFix ? 2 : identifiesBug ? 1 : 0,
        discipline: contractChange ? 1 : identifiesBug ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to diagnose and fix the mutable default argument bug.", state);
    }
  },
  {
    id: "BF-08",
    title: "Rust Integer Overflow in Release",
    category: "C",
    language: "Rust",
    difficulty: "Hard",
    userMessage:
      "This function calculates factorial. It works perfectly in debug mode, but in release mode `factorial(25)` returns a clearly wrong number instead of the correct astronomical value. No error, no crash — just a silently wrong answer. What's going on?\n\n```rust\nfn factorial(n: u64) -> u64 {\n    let mut result: u64 = 1;\n    for i in 1..=n {\n        result *= i;\n    }\n    result\n}\n\nfn main() {\n    println!(\"{}\", factorial(20));\n    println!(\"{}\", factorial(25));\n}\n```",
    description: "Tests whether the model knows Rust's overflow behavior difference between debug and release builds.",
    successCase: "Identify integer overflow, explain debug panic vs release wrapping, and fix it with `checked_mul` or another explicit overflow strategy.",
    failureCase: "Only say `use u128` without explaining why release behaves differently, or suggest floating point.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesOverflow = includesAny(answer, ["integer overflow", "overflows u64", "u64::MAX", "25!"]);
      const checkedFix = includesAny(answer, ["checked_mul", "Option<u64>", "Result<u64", "big integer", "u128"]);
      const badFix = includesAny(answer, ["f64", "float"]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesOverflow ? 2 : 0,
        fixQuality: checkedFix && !badFix ? 2 : identifiesOverflow ? 1 : 0,
        discipline: badFix ? 0 : identifiesOverflow ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to diagnose overflow handling and provide a safe factorial fix.", state);
    }
  },
  {
    id: "BF-09",
    title: "Go Slice Aliasing",
    category: "C",
    language: "Go",
    difficulty: "Hard",
    userMessage:
      "I have a function that should return two separate filtered slices from the same input. But the results are wrong — the positive slice seems to get corrupted. What's going on?\n\n```go\npackage main\n\nimport \"fmt\"\n\nfunc filterPositiveAndNegative(nums []int) ([]int, []int) {\n    positive := nums[:0]\n    negative := nums[:0]\n\n    for _, n := range nums {\n        if n > 0 {\n            positive = append(positive, n)\n        } else if n < 0 {\n            negative = append(negative, n)\n        }\n    }\n    return positive, negative\n}\n```",
    description: "Checks whether the model understands shared backing arrays in Go slices.",
    successCase: "Explain that `nums[:0]` aliases the same backing array for both slices and allocate independent slices with `make`.",
    failureCase: "Suggest `copy()` or unrelated rewrites without explaining the shared backing array.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesAlias = includesAny(answer, ["shared backing array", "same underlying array", "alias", "nums[:0]", "same backing memory"]);
      const goodFix = includesAny(answer, ["make([]int, 0)", "make([]int,0)"]);
      const unnecessaryRewrite = includesAny(answer, ["two loops", "separate loops"]);
      const badFix = includesAny(answer, ["copy("]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesAlias ? 2 : 0,
        fixQuality: goodFix ? 2 : identifiesAlias ? 1 : 0,
        discipline: badFix ? 0 : unnecessaryRewrite ? 1 : identifiesAlias ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to diagnose slice aliasing and allocate independent slices.", state);
    }
  },
  {
    id: "BF-10",
    title: "Python Red-Herring Trap",
    category: "D",
    language: "Python",
    difficulty: "Medium",
    userMessage:
      "My coworker wrote this and I'm pretty sure there's a bug. The naming is weird and the logic looks off. Can you find the issue?\n\n```python\ndef process(data):\n    result = []\n    seen = set()\n    for item in data:\n        key = item.lower().strip()\n        if key not in seen:\n            seen.add(key)\n            result.append(item)\n    return result\n```",
    description: "Trap scenario. The code is correct and intentionally preserves the first original spelling while deduplicating by normalized key.",
    successCase: "Confirm there is no bug and explain why appending `item` while deduplicating on `key` is the intended design.",
    failureCase: "Invent a bug by changing `result.append(item)` to append the normalized key instead.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesTrap = mentionsNoBug(answer) || includesAny(answer, ["preserve original", "first occurrence", "normalized key"]);
      const falsePositive =
        suggestsUnnecessaryTrapFix(answer) ||
        usesFixVerdict(answer) ||
        (!identifiesTrap &&
          (includesAny(answer, ["the bug is", "clear bug", "real bug", "actual bug", "the issue is", "the problem is"]) ||
            matchesAny(answer, [/line\s+\d+/i, /\bfix\b/i])));
      const vague = includesAny(answer, ["probably fine", "naming could be better", "style"]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesTrap && !falsePositive ? 2 : falsePositive ? 0 : 1,
        fixQuality: identifiesTrap && !falsePositive ? 2 : 0,
        discipline: falsePositive ? 0 : vague ? 1 : identifiesTrap ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to resist the red herring and confirm the code is correct.", state);
    }
  },
  {
    id: "BF-11",
    title: "JavaScript Silent Failure on Invalid Discount",
    category: "D",
    language: "JavaScript",
    difficulty: "Hard",
    userMessage:
      "My discount calculator seems unreliable. For valid discounts like 15% off $100, it works fine. But I just realized that `applyDiscount(50, 110)` returns `50` instead of throwing an error. Same with `applyDiscount(50, -5)`. Is there a better way to handle this?\n\n```javascript\nfunction applyDiscount(price, discountPercent) {\n    if (discountPercent < 0 || discountPercent > 100) {\n        return price;\n    }\n    const discounted = price * (1 - discountPercent / 100);\n    return Math.round(discounted * 100) / 100;\n}\n```",
    description: "Checks whether the model finds the design-level bug in silent invalid-input handling instead of touching the already-correct math.",
    successCase: "Call out the silent return for invalid inputs, keep the valid-discount math alone, and make invalid input handling explicit.",
    failureCase: "Try to fix `Math.round` or claim the function is already correct as-is.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const prose = proseOnlyText(answer);
      const identifiesSilentFailure =
        includesAny(prose, ["silently returns", "silent failure", "invalid input", "invalid discount", "110%", "range error", "explicit signal"]) ||
        matchesAny(prose, [/returns?\s+the\s+original\s+price/i, /silently\s+accept/i, /should\s+signal\s+an\s+error/i]);
      const confirmsMath = includesAny(prose, ["math is correct", "rounding is fine", "valid discounts work"]);
      const goodFix = includesAny(answer, ["throw new RangeError", "throw", "return null", "error object", "explicit"]);
      const badFocus = includesAny(prose, ["math.round", "rounding bug", "rounding issue", "rounding problem"]);
      const saysFine = includesAny(prose, ["fine as-is", "looks fine"]);
      const note = confirmsMath ? "Explicitly confirmed that the valid-discount math already works." : undefined;

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesSilentFailure ? 2 : saysFine ? 1 : 0,
        fixQuality: goodFix && !badFocus ? 2 : identifiesSilentFailure ? 1 : 0,
        discipline: badFocus ? 0 : saysFine ? 1 : identifiesSilentFailure ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to address the silent invalid-input path, not the rounding math.", state, note);
    }
  },
  {
    id: "BF-12",
    title: "Rust Longest-Streak Double Bug",
    category: "D",
    language: "Rust",
    difficulty: "Hard",
    userMessage:
      "I'm getting wrong results from my longest-streak function. For the input `[2, 2, 1, 1, 1]`, it returns `(2, 2)` instead of `(1, 3)`. I think there might be more than one issue but I can't figure out what's wrong.\n\n```rust\nfn longest_streak(data: &Vec<i32>) -> (i32, usize) {\n    let mut max_val = data[0];\n    let mut max_count: usize = 1;\n    let mut current_count: usize = 1;\n\n    for i in 1..data.len() {\n        if data[i] == max_val {\n            current_count += 1;\n        } else if current_count > max_count {\n            max_count = current_count;\n            max_val = data[i - 1];\n            current_count = 1;\n        } else {\n            current_count = 1;\n        }\n    }\n    (max_val, max_count)\n}\n```",
    description: "Tests whether the model finds both related bugs instead of stopping at the obvious final-streak issue.",
    successCase: "Identify both the missing `current_val` tracking and the missing final-streak check, then patch both without overhauling the algorithm.",
    failureCase: "Only fix one of the two issues or focus on cosmetic advice like `&[i32]` instead of the real logic bug.",
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const prose = proseOnlyText(answer);
      const currentValIssue = includesAny(answer, ["current_val", "current streak", "compare against max_val", "historical best", "current run"]);
      const finalCheckIssue = includesAny(answer, ["final streak", "last streak", "after the loop", "end of the loop"]);
      const goodFix = currentValIssue && finalCheckIssue;
      const cosmeticOnly = includesAny(prose, ["&[i32]", "&vec"]);
      const rewrite = mentionsMinimalRewrite(prose);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: goodFix ? 2 : currentValIssue || finalCheckIssue ? 1 : 0,
        fixQuality: goodFix ? 2 : currentValIssue || finalCheckIssue ? 1 : 0,
        discipline: cosmeticOnly ? 0 : rewrite ? 1 : goodFix ? 2 : currentValIssue || finalCheckIssue ? 1 : 0
      };

      return makeEvaluation(axes, "Expected the model to diagnose both the missing current-value tracking and the missing final comparison.", state);
    }
  },
  {
    id: "BF-13",
    title: "Python Ambiguous Behavior Report",
    category: "E",
    language: "Python",
    difficulty: "Medium",
    userMessage:
      "My sorting function doesn't work. It's supposed to sort users by age but the output is wrong.\n\n```python\ndef sort_users(users):\n    return sorted(users, key=lambda u: u[\"age\"])\n\nusers = [\n    {\"name\": \"Alice\", \"age\": \"30\"},\n    {\"name\": \"Bob\", \"age\": \"5\"},\n    {\"name\": \"Charlie\", \"age\": \"25\"},\n]\nprint(sort_users(users))\n```",
    description: "Multi-turn scenario. A strong model either spots string sorting immediately or asks for the observed-vs-expected output.",
    successCase: "Either identify lexicographic string sorting immediately or ask for the wrong output first, then fix by converting ages to integers.",
    failureCase: "Ask irrelevant questions or misdiagnose it as needing `reverse=True`.",
    getFollowUp: buildMultiTurnFollowUp(
      classifyBf13Question,
      "It outputs Bob last instead of first. Bob is 5, he should be youngest and sorted first."
    ),
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesBug = includesAny(answer, ["strings", "string", "lexicographic", "lexicographically", "age values are strings"]);
      const goodFix = includesAny(answer, ['int(u["age"])', "int(u['age'])", "convert age to int"]);
      const badDiagnosis = includesAny(answer, ["reverse=True", "reverse = true"]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesBug ? 2 : 0,
        fixQuality: goodFix ? 2 : identifiesBug ? 1 : 0,
        discipline: badDiagnosis ? 0 : identifiesBug ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to diagnose lexicographic string sorting in the age field.", state);
    }
  },
  {
    id: "BF-14",
    title: "JavaScript Environment-Dependent Bug",
    category: "E",
    language: "JavaScript",
    difficulty: "Hard",
    userMessage:
      "This code works on my machine but crashes in production with `Cannot read properties of undefined (reading 'city')`. I have no idea what's different.\n\n```javascript\nfunction getShippingZone(order) {\n    const city = order.shipping_address.city;\n    const zones = {\n        \"New York\": \"east\",\n        \"Los Angeles\": \"west\",\n        \"Chicago\": \"central\",\n    };\n    return zones[city] || \"standard\";\n}\n```",
    description: "Multi-turn scenario. The strongest responses ask about production data or immediately infer missing `shipping_address` in production-only orders.",
    successCase: "Ask about production payload differences or directly identify the missing null check on `order.shipping_address`, then fix it with a safe guard.",
    failureCase: "Ask about Node versions or wrap the whole function in `try/catch` instead of fixing the nullable property access.",
    getFollowUp: buildMultiTurnFollowUp(
      classifyBf14Question,
      "Hmm, I just checked the production logs. Some orders from our mobile app don't have a shipping_address field at all — they're pickup orders."
    ),
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const prose = proseOnlyText(answer);
      const identifiesBug =
        includesAny(prose, [
          "shipping_address could be undefined",
          "missing shipping_address",
          "missing shipping address",
          "order.shipping_address is undefined",
          "optional chaining",
          "pickup orders"
        ]) ||
        matchesAny(prose, [/reading ['"`]city['"`].*undefined/i, /shipping[_ ]address.*undefined/i, /shipping[_ ]address.*missing/i]);
      const goodFix =
        includesAny(answer, ["shipping_address?.city", "order?.shipping_address?.city", "if (!city)", "optional chaining"]) ||
        matchesAny(answer, [/shipping_address\?\.\s*city/i, /order\?\.\s*shipping_address\?\.\s*city/i]);
      const badFix = includesAny(prose, ["try/catch", "wrap the function"]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesBug ? 2 : 0,
        fixQuality: goodFix ? 2 : identifiesBug ? 1 : 0,
        discipline: badFix ? 0 : identifiesBug ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to diagnose production-only missing `shipping_address` data.", state);
    }
  },
  {
    id: "BF-15",
    title: "Go Race Condition Under Load",
    category: "E",
    language: "Go",
    difficulty: "Expert",
    userMessage:
      "My counter service works fine in testing but gives wrong totals under heavy load. Sometimes the count is lower than expected. I'm using goroutines. Here's the code:\n\n```go\npackage main\n\nimport (\n    \"fmt\"\n    \"sync\"\n)\n\ntype Counter struct {\n    count int\n}\n\nfunc (c *Counter) Increment() {\n    c.count++\n}\n\nfunc (c *Counter) GetCount() int {\n    return c.count\n}\n\nfunc main() {\n    counter := &Counter{}\n    var wg sync.WaitGroup\n\n    for i := 0; i < 1000; i++ {\n        wg.Add(1)\n        go func() {\n            defer wg.Done()\n            counter.Increment()\n        }()\n    }\n\n    wg.Wait()\n    fmt.Println(\"Final count:\", counter.GetCount())\n}\n```",
    description: "Multi-turn scenario. The strongest responses identify the data race on `count++` or ask a sharp concurrency question before fixing it.",
    successCase: "Diagnose `c.count++` as a non-atomic read-modify-write race and fix it with a mutex or `sync/atomic`.",
    failureCase: "Claim `WaitGroup` solves it or suggest reducing goroutine count instead of synchronizing the counter.",
    getFollowUp: buildMultiTurnFollowUp(
      classifyBf15Question,
      "Yes, it's a 16-core production server. Locally on my laptop it sometimes gives 1000 correctly."
    ),
    evaluate(state) {
      const answer = combinedAssistantText(state);
      const identifiesBug = includesAny(answer, ["data race", "race condition", "non-atomic", "read-modify-write", "count++"]);
      const goodFix = includesAny(answer, ["sync.Mutex", "mu.Lock", "atomic.AddInt64", "sync/atomic"]);
      const badFix = includesAny(answer, ["WaitGroup fixes", "reduce goroutine count"]);

      const axes: Record<ScenarioAxis, AxisScore> = {
        identification: identifiesBug ? 2 : 0,
        fixQuality: goodFix ? 2 : identifiesBug ? 1 : 0,
        discipline: badFix ? 0 : identifiesBug ? 2 : 0
      };

      return makeEvaluation(axes, "Expected the model to isolate the `count++` data race under concurrent load.", state);
    }
  }
];

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

  const finalScore = Math.round(
    categoryScores.reduce((sum, categoryScore) => sum + categoryScore.averageScore * (categoryScore.weight / 100), 0)
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
