export const SYSTEM_PROMPT = `You are a helpful assistant that solves practical reasoning problems.

Rules:
- Show concise visible work using equations, short bullet points, or numbered steps.
- Do not write a long essay.
- End with exactly one line that starts with "ANSWER: ".
- If the question asks for more than one value, format the final line as semicolon-separated key=value pairs.
- Use exact arithmetic when possible.
- Round only the final result when the problem context requires it.
- If the constraints are inconsistent, say so explicitly in the final answer.`;

export type BenchmarkCategory = "A" | "B" | "C" | "D" | "E";
export type ScenarioStatus = "pass" | "partial" | "fail";
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

type ScenarioSpec = {
  id: string;
  title: string;
  category: BenchmarkCategory;
  description: string;
  userMessage: string;
  successCase: string;
  failureCase: string;
  canonicalAnswer: string;
  acceptedAnswers?: string[];
  partialAnswers?: string[];
  checkpoints: string[];
};

const CATEGORY_LABELS: Record<BenchmarkCategory, string> = {
  A: "Everyday Arithmetic",
  B: "Logic Puzzles",
  C: "Multi-Step Word Problems",
  D: "Trick Questions and Traps",
  E: "Applied Reasoning"
};

const CATEGORY_WEIGHTS: Record<BenchmarkCategory, number> = {
  A: 15,
  B: 25,
  C: 20,
  D: 25,
  E: 15
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

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function normalizeCheckpointLabel(value: string): string {
  return normalizeText(value.replace(/_/g, " "));
}

function extractAnswerLine(answer: string): string {
  const lines = answer.replace(/\r\n?/g, "\n").split("\n");
  const matches = lines.filter((line) => line.startsWith("ANSWER: "));
  return matches.at(-1) ?? "";
}

function answerPayload(answerLine: string): string {
  return answerLine.replace(/^ANSWER:\s*/i, "").trim();
}

function trySingleValueMatch(canonicalAnswer: string, answerLine: string): boolean {
  const canonicalPayload = answerPayload(canonicalAnswer);
  const answerPayloadText = answerPayload(answerLine);

  if (canonicalPayload.includes(";") || !canonicalPayload.includes("=")) {
    return false;
  }

  const separatorIndex = canonicalPayload.indexOf("=");
  const expectedKey = normalizeCheckpointLabel(canonicalPayload.slice(0, separatorIndex));
  const expectedValue = normalizeText(canonicalPayload.slice(separatorIndex + 1));
  const actualNormalized = normalizeText(answerPayloadText);

  if (actualNormalized === expectedValue) {
    return true;
  }

  const actualWithoutLeadingLabel = actualNormalized.replace(/^[a-z_][a-z0-9_ ]*=\s*/i, "").trim();
  if (actualWithoutLeadingLabel === expectedValue) {
    return true;
  }

  return actualNormalized.includes(expectedKey) && actualNormalized.includes(expectedValue);
}

function evaluateAnswerAxis(spec: ScenarioSpec, rawAnswer: string): { points: 0 | 1 | 2; note?: string } {
  const answerLine = extractAnswerLine(rawAnswer);
  if (!answerLine) {
    return { points: 0, note: 'Missing final "ANSWER: " line.' };
  }

  const normalized = normalizeText(answerLine);
  const canonical = normalizeText(spec.canonicalAnswer);
  const accepted = (spec.acceptedAnswers ?? []).map(normalizeText);
  const partial = (spec.partialAnswers ?? []).map(normalizeText);

  if (normalized === canonical || accepted.includes(normalized)) {
    return { points: 2 };
  }

  if (trySingleValueMatch(spec.canonicalAnswer, answerLine)) {
    return { points: 2 };
  }

  if (partial.includes(normalized)) {
    return { points: 1, note: "Matched a scenario-defined partial answer." };
  }

  return { points: 0, note: `Unexpected final line: ${answerLine}` };
}

function evaluateTraceAxis(spec: ScenarioSpec, rawAnswer: string): { points: 0 | 1 | 2; note?: string } {
  const normalized = normalizeText(rawAnswer);
  const matched = spec.checkpoints.filter((checkpoint) => {
    const normalizedCheckpoint = normalizeText(checkpoint);

    if (normalized.includes(normalizedCheckpoint)) {
      return true;
    }

    const separatorIndex = checkpoint.indexOf("=");
    if (separatorIndex === -1) {
      return false;
    }

    const left = normalizeCheckpointLabel(checkpoint.slice(0, separatorIndex));
    const right = normalizeText(checkpoint.slice(separatorIndex + 1));
    return normalized.includes(left) && normalized.includes(right);
  });

  if (matched.length === spec.checkpoints.length) {
    return { points: 2 };
  }

  if (matched.length > 0) {
    return { points: 1, note: `Matched ${matched.length}/${spec.checkpoints.length} checkpoints.` };
  }

  return { points: 0, note: "No published checkpoints matched." };
}

function scoreScenario(answerPoints: 0 | 1 | 2, tracePoints: 0 | 1 | 2): number {
  return Math.round(100 * (0.7 * (answerPoints / 2) + 0.3 * (tracePoints / 2)));
}

const SCENARIO_SPECS: ScenarioSpec[] = [
  {
    id: "RM-01",
    title: "Bill Splitting with Tax and Tip",
    category: "A",
    description: "Multi-step percentage arithmetic with an explicit tip base and a per-person total.",
    userMessage: "Three friends had dinner. The food total was $84.00 before tax. Tax is 8.5%. They want to leave a 20% tip calculated on the pre-tax amount. How much does each person owe in total?",
    successCase: "Uses the right tax and tip bases, then ends with the published per-person answer line.",
    failureCase: "Calculates tip on the wrong base or omits the required final answer line.",
    canonicalAnswer: "ANSWER: per_person=$35.98",
    acceptedAnswers: ["ANSWER: $35.98", "ANSWER: 35.98", "ANSWER: per_person=35.98"],
    checkpoints: ["7.14", "16.80", "107.94", "35.98"]
  },
  {
    id: "RM-02",
    title: "Unit Conversion Chain",
    category: "A",
    description: "Two-step conversion from cups to grams to kilograms with a published partial-credit endpoint.",
    userMessage: "A recipe calls for 2.5 cups of flour. I only have a kitchen scale. If 1 cup of flour weighs approximately 125 grams, how many kilograms of flour do I need?",
    successCase: "Converts to kilograms and ends with the required answer format.",
    failureCase: "Stops at grams or omits the answer line entirely.",
    canonicalAnswer: "ANSWER: kg=0.3125",
    acceptedAnswers: ["ANSWER: kg=0.313"],
    partialAnswers: ["ANSWER: grams=312.5"],
    checkpoints: ["grams=312.5", "kg=0.3125"]
  },
  {
    id: "RM-03",
    title: "Percentage Change — Not What You Think",
    category: "A",
    description: "A percentage-increase trap where decrease and increase are not symmetric.",
    userMessage:
      "A shirt was originally $80. It went on sale for 25% off. After you bought it on sale, the store raised the original price by 25%. What is the new original price, and did you save money compared to the new price?\n\nReturn the final line exactly in this format:\nANSWER: new_original_price=<money>; saved_money=<yes|no>",
    successCase: "Separates the sale price from the later new original price and reports both requested outputs.",
    failureCase: "Claims the price simply returns to $80 or misses one of the requested values.",
    canonicalAnswer: "ANSWER: new_original_price=$100.00; saved_money=yes",
    acceptedAnswers: [
      "ANSWER: new_original_price=$100; saved_money=yes",
      "ANSWER: new_original_price=100; saved_money=yes"
    ],
    checkpoints: ["sale_price=60", "new_original_price=100", "saved_money=yes"]
  },
  {
    id: "RM-04",
    title: "Constraint Consistency Check",
    category: "B",
    description: "An intentionally unsatisfiable logic puzzle that should be rejected cleanly.",
    userMessage: "There are 5 houses in a row. Each house is painted a different color: red, blue, green, yellow, white. Given these clues:\n1. The red house is immediately to the left of the blue house.\n2. The green house is in the middle (position 3).\n3. The yellow house is not next to the green house.\n4. The white house is at one of the ends (position 1 or 5).\n\nWhat is the order of the houses from left to right?",
    successCase: 'Detects that the clues are inconsistent and ends with `ANSWER: status=unsat` or an accepted equivalent.',
    failureCase: "Forces an invalid arrangement instead of reporting the contradiction.",
    canonicalAnswer: "ANSWER: status=unsat",
    acceptedAnswers: [
      "ANSWER: status=no valid arrangement",
      "ANSWER: status=no solution",
      "ANSWER: the constraints are inconsistent.",
      "ANSWER: constraints are inconsistent",
      "ANSWER: inconsistent"
    ],
    checkpoints: ["green at position 3", "yellow must be at position 1 or 5", "white is at an end", "red and blue cannot be placed"]
  },
  {
    id: "RM-05",
    title: "Scheduling Conflict Resolution",
    category: "B",
    description: "Scheduling arithmetic with buffers, a fixed lunch block, and a max-fit conclusion.",
    userMessage:
      "I need to schedule 4 meetings today. Each meeting is 45 minutes long with 15 minutes of buffer between meetings. My available time starts at 9:00 AM and ends at 1:00 PM. I also have a fixed lunch break from 12:00 PM to 12:30 PM that cannot be moved.\n\nCan I fit all 4 meetings? If yes, what are the time slots? If no, how many can I fit?\n\nReturn the final line exactly in this format:\nANSWER: fit=<yes|no>; max_meetings=<integer>\n\nDo not include the slots in the final ANSWER line.",
    successCase: "Counts the usable minutes correctly and reports that only three meetings fit.",
    failureCase: "Ignores buffers or lunch and overstates capacity.",
    canonicalAnswer: "ANSWER: fit=no; max_meetings=3",
    acceptedAnswers: [
      "ANSWER: fit_all_4=no; max_meetings=3",
      "ANSWER: can_fit_4=no; max_meetings=3",
      "ANSWER: canfit4=no; maxmeetings=3",
      "ANSWER: fit_all=no; max_meetings=3",
      "ANSWER: all_fit=no; max_meetings=3",
      "ANSWER: can_fit_4=false; max_meetings=3",
      "ANSWER: fit_all=false; max_meetings=3",
      "ANSWER: all_fit=false; max_meetings=3",
      "ANSWER: canfit=no; maxmeetings=3",
      "ANSWER: fit_4=false; max_meetings=3",
      "ANSWER: fit_4=no; max_meetings=3",
      "ANSWER: canfit=false; maxmeetings=3"
    ],
    checkpoints: ["240", "30", "210", "225", "k = 3"]
  },
  {
    id: "RM-06",
    title: "Monty Hall Variant with Explicit Host Rule",
    category: "B",
    description: "Conditional probability with a nonstandard four-door host rule.",
    userMessage: "You're on a game show with 4 doors. Behind one door is a car. Behind the other 3 doors are goats. You pick Door 1.\n\nThe host knows where the car is. The host always opens exactly 2 goat doors among the 3 doors you did not choose, leaving exactly 1 unopened alternative door besides your original choice. If the host has more than one valid pair of goat doors to open, the host chooses uniformly at random among those valid pairs.\n\nIn this run, the host opens Door 3 and Door 4, both goats, and offers you the chance to switch to Door 2.\n\nWhat is the probability of winning the car if you switch to Door 2? What if you stay with Door 1?",
    successCase: "Applies the stated host rule and reports the 75%/25% split.",
    failureCase: "Treats the host choice as random noise or collapses to the wrong probability.",
    canonicalAnswer: "ANSWER: switch=75%; stay=25%",
    acceptedAnswers: ["ANSWER: switch=3/4; stay=1/4"],
    checkpoints: ["p(c_1) = 1/4", "p(h | c_2) = 1", "p(h) = 1/3", "p(c_2 | h) = 3/4"]
  },
  {
    id: "RM-07",
    title: "Speed, Distance, Time with a Twist",
    category: "C",
    description: "Round-trip average speed where arithmetic must be done over total distance and total time.",
    userMessage: "Alice drives from City A to City B at 60 km/h. The trip takes 3 hours. She then drives back from City B to City A, but hits traffic and averages only 40 km/h on the return.\n\nWhat is her average speed for the entire round trip?",
    successCase: "Computes both trip legs and reports the combined average speed.",
    failureCase: "Averages 60 and 40 directly instead of using total distance over total time.",
    canonicalAnswer: "ANSWER: avg_speed=48 km/h",
    checkpoints: ["distance_one_way=180", "return_time=4.5", "total_distance=360", "total_time=7.5", "avg_speed=48"]
  },
  {
    id: "RM-08",
    title: "Rate and Proportion Problem",
    category: "C",
    description: "Combines positive fill rates and a negative drain rate into one net-fill calculation.",
    userMessage:
      "A bathtub has two faucets. Faucet A alone fills the tub in 12 minutes. Faucet B alone fills it in 18 minutes. There's also a drain that empties the full tub in 36 minutes. If both faucets are open and the drain is open, how long does it take to fill the tub?\n\nReturn the final line exactly in this format:\nANSWER: fill_time=<number> minutes",
    successCase: "Adds the rates correctly and inverts the combined rate for the time.",
    failureCase: "Adds or subtracts times directly instead of rates.",
    canonicalAnswer: "ANSWER: fill_time=9 minutes",
    checkpoints: ["faucet a=1/12", "faucet b=1/18", "drain=-1/36", "net rate=1/9", "fill_time=9"]
  },
  {
    id: "RM-09",
    title: "Age Problem with Temporal Reasoning",
    category: "C",
    description: "Temporal algebra with two age relationships anchored at different times.",
    userMessage: "Five years ago, Maria was 3 times as old as her son. In 5 years from now, Maria will be twice as old as her son. How old is Maria now?",
    successCase: "Sets up the two time-shifted equations and solves them consistently.",
    failureCase: "Confuses the time offsets or solves only one equation.",
    canonicalAnswer: "ANSWER: maria=35",
    checkpoints: ["m-5=3(s-5)", "m+5=2(s+5)", "s=15", "m=35"]
  },
  {
    id: "RM-10",
    title: "The Classic Bat and Ball — Extended",
    category: "D",
    description: "The classic price trap with one more dependent step added for the glove.",
    userMessage: "A bat and a ball cost $1.10 together. The bat costs $1.00 more than the ball. A glove costs twice as much as the bat. How much does the glove cost?",
    successCase: "Solves the original bat-and-ball relation correctly before doubling the bat price.",
    failureCase: "Falls for the $0.10 ball trap and propagates the wrong price.",
    canonicalAnswer: "ANSWER: glove=$2.10",
    checkpoints: ["ball=0.05", "bat=1.05", "glove=2.10"]
  },
  {
    id: "RM-11",
    title: "The Lily Pad Problem — Backward Reasoning",
    category: "D",
    description: "A classic backward-reasoning check on doubling growth.",
    userMessage:
      "A lake has lily pads growing on it. The area covered by lily pads doubles every day. On day 30, the entire lake is covered. On what day was the lake half covered?\n\nReturn the final line exactly in this format:\nANSWER: day=<integer>",
    successCase: "Reasons backward one day from the fully covered state.",
    failureCase: "Treats the growth as linear instead of doubling.",
    canonicalAnswer: "ANSWER: day=29",
    checkpoints: ["doubles every day", "day 30=full", "day 29=half"]
  },
  {
    id: "RM-12",
    title: "Family-Relation Riddle",
    category: "D",
    description: "Relation-logic parsing with a self-reference trap.",
    userMessage:
      'A man points to a photograph and says, "Brothers and sisters I have none, but that man\'s father is my father\'s son." Who is in the photograph?\n\nUse the relationship from the speaker\'s point of view.\nReturn the final line exactly in this format:\nANSWER: person=<relationship>',
    successCase: "Resolves the self-reference and ends with his son as the answer.",
    failureCase: "Misreads `my father's son` as someone other than the speaker.",
    canonicalAnswer: "ANSWER: person=his son",
    acceptedAnswers: ["ANSWER: person=my son", "ANSWER: person=the speaker's son", "ANSWER: person=son"],
    checkpoints: ["my father's son=me", "that man's father=me", "person=son"]
  },
  {
    id: "RM-13",
    title: "Compound Interest Calculation",
    category: "E",
    description: "Practical finance math with monthly compounding and a required total-interest output.",
    userMessage:
      "I invest $5,000 in a savings account that earns 4.5% annual interest, compounded monthly. How much will I have after 3 years? What is the total interest earned?\n\nReturn the final line exactly in this format:\nANSWER: amount=<money>; interest=<money>",
    successCase: "Uses the compound-interest formula and reports both the balance and interest earned.",
    failureCase: "Uses simple interest or omits one of the requested outputs.",
    canonicalAnswer: "ANSWER: amount=$5721.24; interest=$721.24",
    acceptedAnswers: [
      "ANSWER: amount=5721.24; interest=721.24",
      "ANSWER: amount=$5,721.24; interest=$721.24",
      "ANSWER: amount=5,721.24; interest=721.24"
    ],
    checkpoints: ["a=p(1+r/n)^(nt)", "p=5000", "r=0.045", "n=12", "t=3", "amount=5721.24", "interest=721.24"]
  },
  {
    id: "RM-14",
    title: "Conversion with Multiple Systems",
    category: "E",
    description: "Temperature and time conversion across metric, imperial, and convection-oven adjustments.",
    userMessage:
      "A European recipe says to bake at 180°C for 45 minutes in a regular oven. I have a convection oven and I'm in the US. What temperature should I set in Fahrenheit, and for how long? Convection ovens should be set 25°F lower than the regular-oven equivalent, and baking time should be reduced by about 25%.\n\nRound the baking time to the nearest whole minute.\n\nReturn the final line exactly in this format:\nANSWER: temp_f=<integer>; time_min=<integer>",
    successCase: "Converts to Fahrenheit, applies the convection adjustment, and shortens the baking time correctly.",
    failureCase: "Misses one of the conversion steps or rounds the wrong value.",
    canonicalAnswer: "ANSWER: temp_f=331; time_min=34",
    acceptedAnswers: ["ANSWER: temp_f=330; time_min=34"],
    checkpoints: ["180c=356f", "356-25=331", "45*0.75=33.75", "time=34"]
  },
  {
    id: "RM-15",
    title: "Combinatorial Reasoning — PIN Possibilities",
    category: "E",
    description: "A counting problem that combines uniqueness, leading-digit, and strict-order constraints.",
    userMessage:
      "A website requires a 4-digit PIN, where each digit is 0–9. How many possible PINs are there if:\n1. All 4 digits must be different\n2. The PIN must start with a non-zero digit\n3. The digits must be in strictly increasing order\n\nReturn the final line exactly in this format:\nANSWER: count=<integer>",
    successCase: "Recognizes that strict ordering collapses arrangements to combinations and excludes zero correctly.",
    failureCase: "Counts permutations instead of the valid ordered digit sets.",
    canonicalAnswer: "ANSWER: count=126",
    checkpoints: ["one arrangement per set", "0 cannot be included", "choose 4 digits from 9", "126"]
  }
];

export const SCENARIOS: ScenarioDefinition[] = SCENARIO_SPECS.map((scenario) => ({
  id: scenario.id,
  title: scenario.title,
  category: scenario.category,
  description: scenario.description,
  userMessage: scenario.userMessage,
  successCase: scenario.successCase,
  failureCase: scenario.failureCase,
  evaluate: (state) => {
    const answerAxis = evaluateAnswerAxis(scenario, state.finalAnswer);
    const traceAxis = evaluateTraceAxis(scenario, state.finalAnswer);
    const score = scoreScenario(answerAxis.points, traceAxis.points);
    const note = [answerAxis.note, traceAxis.note].filter(Boolean).join(" ").trim() || undefined;

    return {
      status: statusForScore(score),
      score,
      summary: `Answer axis ${answerAxis.points}/2, trace axis ${traceAxis.points}/2 (${score}%).`,
      note
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
