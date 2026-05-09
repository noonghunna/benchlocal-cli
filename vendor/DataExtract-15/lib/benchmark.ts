export const SYSTEM_PROMPT = `You are a data extraction assistant. The user will provide you with unstructured text and a list of fields to extract.

Rules:
- Extract ONLY information that is explicitly stated in the source text.
- For string fields, copy the exact value from the source text. You may return an exact subspan when the requested field is only one component of a longer phrase.
- Do NOT paraphrase, summarize, translate, expand abbreviations, correct typos, normalize formatting, or rewrite values.
- If a field spans multiple consecutive source lines, preserve those line breaks in the JSON string.
- If a field's value cannot be determined from explicit source text, use null.
- Do NOT infer, guess, or use background knowledge.
- Output valid JSON with the exact field names specified.
- Output ONLY the JSON object or JSON array. No explanations, no markdown fences.
- For numeric fields, output a JSON number only when the number is explicitly stated in the source text and can be copied by deterministic parsing.
- For boolean fields, output true or false only when the source text explicitly states the condition.
- If the prompt explicitly instructs you to resolve conflicts, use the final / most recent stated value. Otherwise do not resolve by inference.
- Preserve original capitalization, punctuation, wording, and language for string values.`;

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
  expected: unknown;
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

const CATEGORY_LABELS: Record<BenchmarkCategory, string> = {
  A: "Clean Extraction",
  B: "Noisy and Informal",
  C: "Multi-Entity",
  D: "Implicit and Missing",
  E: "Complex Documents"
};

const CATEGORY_WEIGHTS: Record<BenchmarkCategory, number> = {
  A: 15,
  B: 20,
  C: 25,
  D: 25,
  E: 15
};

const ARRAY_OBJECT_ANCHORS: Record<string, string> = {
  "DE-02.items": "name",
  "DE-07.$root": "name",
  "DE-13.line_items": "description",
  "DE-13.discounts": "description"
};

type ScalarComparison = { correct: boolean; reason?: string };
type AggregateComparison = { correct: number; total: number; notes: string[] };
type ComplianceCheck = {
  validJson: boolean;
  exactTopLevelShape: boolean;
  requestedFieldsOnly: boolean;
  noMissingExpectedFields: boolean;
  notes: string[];
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

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function topLevelShape(value: unknown): "array" | "object" | "other" {
  if (Array.isArray(value)) {
    return "array";
  }
  if (isPlainObject(value)) {
    return "object";
  }

  return "other";
}

function normalizeString(value: string): string {
  return value.trim();
}

function compareScalar(expected: unknown, actual: unknown): ScalarComparison {
  if (expected === null) {
    return { correct: actual === null, reason: actual === null ? undefined : "expected null" };
  }

  if (typeof expected === "string") {
    return {
      correct: typeof actual === "string" && normalizeString(actual) === normalizeString(expected),
      reason: typeof actual === "string" ? undefined : "expected string"
    };
  }

  if (typeof expected === "number") {
    return {
      correct: typeof actual === "number" && Number.isFinite(actual) && Math.abs(actual - expected) <= 0.01,
      reason: typeof actual === "number" ? undefined : "expected number"
    };
  }

  if (typeof expected === "boolean") {
    return {
      correct: actual === expected,
      reason: typeof actual === "boolean" ? undefined : "expected boolean"
    };
  }

  return { correct: false, reason: "unsupported scalar type" };
}

function compareScalarArray(expected: unknown[], actual: unknown): AggregateComparison {
  if (!Array.isArray(actual)) {
    return { correct: 0, total: 1, notes: ["expected array"] };
  }

  if (expected.length !== actual.length) {
    return {
      correct: 0,
      total: 1,
      notes: [`expected ${expected.length} items but received ${actual.length}`]
    };
  }

  const remaining = [...actual];

  for (const expectedItem of expected) {
    const matchIndex = remaining.findIndex((candidate) => compareScalar(expectedItem, candidate).correct);
    if (matchIndex === -1) {
      return {
        correct: 0,
        total: 1,
        notes: ["array values did not match expected set"]
      };
    }
    remaining.splice(matchIndex, 1);
  }

  return { correct: 1, total: 1, notes: [] };
}

function compareObjectArray(
  expected: Array<Record<string, unknown>>,
  actual: unknown,
  scenarioId: string,
  path: string
): AggregateComparison {
  if (!Array.isArray(actual)) {
    return { correct: 0, total: expected.length * (expected[0] ? Object.keys(expected[0]).length : 1), notes: ["expected array"] };
  }

  const anchor = ARRAY_OBJECT_ANCHORS[`${scenarioId}.${path || "$root"}`];
  if (!anchor) {
    return { correct: 0, total: expected.length || 1, notes: [`missing anchor key for ${scenarioId}.${path || "$root"}`] };
  }

  const actualByAnchor = new Map<string, Record<string, unknown>>();
  for (const item of actual) {
    if (!isPlainObject(item) || !(anchor in item) || typeof item[anchor] !== "string") {
      continue;
    }
    actualByAnchor.set(String(item[anchor]), item);
  }

  let correct = 0;
  let total = 0;
  const notes: string[] = [];

  for (const expectedItem of expected) {
    const actualItem = actualByAnchor.get(String(expectedItem[anchor]));
    for (const [key, expectedValue] of Object.entries(expectedItem)) {
      total += 1;
      if (!actualItem) {
        notes.push(`missing object with ${anchor}=${String(expectedItem[anchor])}`);
        continue;
      }

      const result = compareValue(expectedValue, actualItem[key], scenarioId, path ? `${path}.${key}` : key);
      correct += result.correct;
      if (result.notes.length > 0) {
        notes.push(...result.notes);
      }
    }
  }

  return { correct, total, notes };
}

function compareObject(expected: Record<string, unknown>, actual: unknown, scenarioId: string, path = ""): AggregateComparison {
  if (!isPlainObject(actual)) {
    return { correct: 0, total: Object.keys(expected).length, notes: ["expected object"] };
  }

  let correct = 0;
  let total = 0;
  const notes: string[] = [];

  for (const [key, expectedValue] of Object.entries(expected)) {
    const nestedPath = path ? `${path}.${key}` : key;
    const result = compareValue(expectedValue, actual[key], scenarioId, nestedPath);
    correct += result.correct;
    total += result.total;
    if (result.notes.length > 0) {
      notes.push(...result.notes);
    }
  }

  return { correct, total, notes };
}

function compareValue(expected: unknown, actual: unknown, scenarioId: string, path: string): AggregateComparison {
  if (Array.isArray(expected)) {
    if (expected.every(isPlainObject)) {
      return compareObjectArray(expected as Array<Record<string, unknown>>, actual, scenarioId, path);
    }

    return compareScalarArray(expected, actual);
  }

  if (isPlainObject(expected)) {
    return compareObject(expected, actual, scenarioId, path);
  }

  const scalar = compareScalar(expected, actual);
  return {
    correct: scalar.correct ? 1 : 0,
    total: 1,
    notes: scalar.correct ? [] : [`${path}: ${scalar.reason ?? "mismatch"}`]
  };
}

function evaluateCompliance(expected: unknown, actual: unknown): ComplianceCheck {
  const notes: string[] = [];
  const expectedShape = topLevelShape(expected);
  const actualShape = topLevelShape(actual);
  const exactTopLevelShape = expectedShape === actualShape;
  if (!exactTopLevelShape) {
    notes.push(`top-level shape mismatch: expected ${expectedShape}, received ${actualShape}`);
  }

  let requestedFieldsOnly = true;
  let noMissingExpectedFields = true;

  if (isPlainObject(expected) && isPlainObject(actual)) {
    const expectedKeys = new Set(Object.keys(expected));
    const actualKeys = new Set(Object.keys(actual));
    const extra = [...actualKeys].filter((key) => !expectedKeys.has(key));
    const missing = [...expectedKeys].filter((key) => !actualKeys.has(key));

    if (extra.length > 0) {
      requestedFieldsOnly = false;
      notes.push(`extra top-level fields: ${extra.join(", ")}`);
    }

    if (missing.length > 0) {
      noMissingExpectedFields = false;
      notes.push(`missing top-level fields: ${missing.join(", ")}`);
    }
  }

  return {
    validJson: true,
    exactTopLevelShape,
    requestedFieldsOnly,
    noMissingExpectedFields,
    notes
  };
}

function evaluateScenarioOutput(scenarioId: string, expected: unknown, finalAnswer: string): ScenarioEvaluation {
  let parsed: unknown;

  try {
    parsed = JSON.parse(finalAnswer);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Invalid JSON.";
    return {
      status: "fail",
      score: 0,
      summary: `Invalid JSON: ${message}`,
      note: "Official score is 0 when the response is not valid JSON."
    };
  }

  const compliance = evaluateCompliance(expected, parsed);
  const comparison = compareValue(expected, parsed, scenarioId, "");
  const score = comparison.total === 0 ? 0 : Math.round((comparison.correct / comparison.total) * 100);
  const complianceFlags = [
    compliance.exactTopLevelShape ? "shape ok" : "shape fail",
    compliance.requestedFieldsOnly ? "fields only" : "extra fields",
    compliance.noMissingExpectedFields ? "no missing fields" : "missing fields"
  ].join(", ");
  const noteParts = [...compliance.notes, ...comparison.notes];

  return {
    status: statusForScore(score),
    score,
    summary: `${comparison.correct}/${comparison.total} atomic fields correct (${score}%). ${complianceFlags}.`,
    note: noteParts.length > 0 ? noteParts.join(" | ") : undefined
  };
}

type ScenarioSpec = Omit<ScenarioDefinition, "evaluate">;

const SCENARIO_SPECS: ScenarioSpec[] = [
  {
    id: "DE-01",
    title: "Business Card / Contact Info",
    category: "A",
    description: "Baseline extraction from well-structured input. Every field is clearly labeled and unambiguous.",
    userMessage:
      "Extract the contact information from this text:\n\nDr. Sarah Chen, Ph.D.\nSenior Research Scientist\nBioGen Therapeutics, Inc.\n1420 Harbor Blvd, Suite 300\nSan Diego, CA 92101\nTel: (858) 555-0147\nEmail: s.chen@biogentherapeutics.com\nLinkedIn: linkedin.com/in/sarahchen-phd\n\nFields: name, title, company, address, city, state, zip, phone, email, linkedin_url",
    expected: JSON.parse(String.raw`{
  "name": "Dr. Sarah Chen, Ph.D.",
  "title": "Senior Research Scientist",
  "company": "BioGen Therapeutics, Inc.",
  "address": "1420 Harbor Blvd, Suite 300",
  "city": "San Diego",
  "state": "CA",
  "zip": "92101",
  "phone": "(858) 555-0147",
  "email": "s.chen@biogentherapeutics.com",
  "linkedin_url": "linkedin.com/in/sarahchen-phd"
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-02",
    title: "Receipt / Invoice",
    category: "A",
    description:
      'Structured extraction from a receipt format. Prices must be numbers (not strings with "$"). The model must handle the items array and distinguish between subtotal, tax, and total.',
    userMessage:
      "Extract the transaction details from this receipt:\n\nURBAN BEAN COFFEE\n847 Market Street\nSan Francisco, CA 94103\n\nDate: 03/15/2026  Time: 08:42 AM\nCashier: Mike  Register: #3\n\nAmericano (L)          $4.75\nOat Milk Latte (M)     $5.50\nBlueberry Muffin       $3.25\n\nSubtotal:             $13.50\nTax (8.625%):          $1.16\nTotal:                $14.66\n\nVisa ending 4821\nAuth: 772941\n\nFields: store_name, store_address, date, time, items (array of {name, price}), subtotal, tax_rate, tax_amount, total, payment_method, card_last_four",
    expected: JSON.parse(String.raw`{
  "store_name": "URBAN BEAN COFFEE",
  "store_address": "847 Market Street\nSan Francisco, CA 94103",
  "date": "03/15/2026",
  "time": "08:42 AM",
  "items": [
    {"name": "Americano (L)", "price": 4.75},
    {"name": "Oat Milk Latte (M)", "price": 5.50},
    {"name": "Blueberry Muffin", "price": 3.25}
  ],
  "subtotal": 13.50,
  "tax_rate": "8.625%",
  "tax_amount": 1.16,
  "total": 14.66,
  "payment_method": "Visa",
  "card_last_four": "4821"
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-03",
    title: "Job Posting",
    category: "A",
    description:
      'Extracting from semi-structured text with multiple field types: strings, numbers, arrays. The model must parse salary range, distinguish required vs. preferred skills, and extract years of experience from prose ("3+ years").',
    userMessage:
      "Extract the job details from this posting:\n\n🚀 We're Hiring! Senior Frontend Engineer\n\nLocation: Austin, TX (Hybrid - 3 days in office)\nTeam: Product Engineering\nReports to: VP of Engineering\n\nCompensation: $145,000 - $185,000 base + equity + benefits\n\nAbout the role:\nWe're looking for a senior frontend engineer to lead our design system rebuild. You'll work with React, TypeScript, and our custom component library. 3+ years of experience required.\n\nMust have: React, TypeScript, CSS-in-JS, accessibility (WCAG 2.1)\nNice to have: GraphQL, Storybook, animation (Framer Motion)\n\nApply at careers.example.com/senior-fe or email jobs@example.com\n\nFields: job_title, location, work_model, team, reports_to, salary_min, salary_max, required_skills (array), preferred_skills (array), experience_years_min, apply_url, apply_email",
    expected: JSON.parse(String.raw`{
  "job_title": "Senior Frontend Engineer",
  "location": "Austin, TX",
  "work_model": "Hybrid",
  "team": "Product Engineering",
  "reports_to": "VP of Engineering",
  "salary_min": 145000,
  "salary_max": 185000,
  "required_skills": ["React", "TypeScript", "CSS-in-JS", "accessibility (WCAG 2.1)"],
  "preferred_skills": ["GraphQL", "Storybook", "animation (Framer Motion)"],
  "experience_years_min": 3,
  "apply_url": "careers.example.com/senior-fe",
  "apply_email": "jobs@example.com"
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-04",
    title: "Informal Email Thread",
    category: "B",
    description:
      "Extraction from conversational, informal text. The model must: read the thread chronologically (the latest email has the final decision), distinguish the sprint planning meeting from the retro meeting, and extract from casual language.",
    userMessage:
      "Extract the meeting details from this email thread:\n\n---\nFrom: Lisa Park <lisa.p@company.com>\nTo: Team-Alpha <team-alpha@company.com>\nDate: March 10, 2026, 3:47 PM\nSubject: Re: Re: sprint planning??\n\nok so after talking to Jake, let's do Thursday instead of Wednesday. Same time tho - 10am. We can use the Maple room since Oak is booked. bring your laptops, we're gonna do live estimation this time\n\nbtw the retro from last sprint is still on for Friday 2pm in Birch. don't confuse the two lol\n\n- Lisa\n---\nFrom: Jake Torres <j.torres@company.com>\nTo: Team-Alpha <team-alpha@company.com>\nDate: March 10, 2026, 2:15 PM\nSubject: Re: sprint planning??\n\nWednesday doesn't work for me, can we move it? Thursday or Friday works. Also the Oak room has a broken projector so maybe book a different one.\n---\n\nFields: meeting_name, day, time, room, organizer_name, organizer_email, note",
    expected: JSON.parse(String.raw`{
  "meeting_name": "sprint planning",
  "day": "Thursday",
  "time": "10am",
  "room": "Maple",
  "organizer_name": "Lisa Park",
  "organizer_email": "lisa.p@company.com",
  "note": "bring your laptops, we're gonna do live estimation this time"
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-05",
    title: "Product Review with Embedded Specs",
    category: "B",
    description:
      "Extracting from an opinionated, informal product review. The model must distinguish the reviewed product's specs from competitor specs, handle two different prices (sale vs. original), identify competitors mentioned in comparison, and copy complaint / recommendation text exactly rather than paraphrasing it.",
    userMessage:
      "Extract the product details and reviewer's assessment from this review:\n\n★★★★☆\nReviewed by: TechDad_42 on Feb 28, 2026\nVerified Purchase\n\nBought the XR-7500 Pro noise-cancelling headphones for my daily commute. Was deciding between these and the Sony WH-1000XM5 ($348) but went with these since they were on sale for $279 (normally $329). Battery life is honestly amazing — I get about 38 hours on a single charge vs the 30 hours Sony claims. The ANC is good but not quite as good as my old Bose QC45s tbh. Sound quality is excellent for the price point. They fold flat which is great for my bag.\n\nOne complaint: the app (v3.2.1) is buggy on Android 14. Crashes when trying to customize EQ. Not a dealbreaker since the default sound profile is great.\n\nWeight: 254g. Bluetooth 5.3. USB-C charging.\n\nWould I recommend? Yeah, especially at the sale price. Best value under $300 IMO.\n\nFields: product_name, product_price_paid, product_price_original, rating_stars, reviewer_name, battery_life_hours, weight_grams, bluetooth_version, charging_type, competitor_1_name, competitor_1_price, competitor_2_name, complaint, recommendation",
    expected: JSON.parse(String.raw`{
  "product_name": "XR-7500 Pro",
  "product_price_paid": 279,
  "product_price_original": 329,
  "rating_stars": 4,
  "reviewer_name": "TechDad_42",
  "battery_life_hours": 38,
  "weight_grams": 254,
  "bluetooth_version": "5.3",
  "charging_type": "USB-C",
  "competitor_1_name": "Sony WH-1000XM5",
  "competitor_1_price": 348,
  "competitor_2_name": "Bose QC45s",
  "complaint": "the app (v3.2.1) is buggy on Android 14. Crashes when trying to customize EQ.",
  "recommendation": "Yeah, especially at the sale price."
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-06",
    title: "Handwritten-Style Notes (OCR Simulation)",
    category: "B",
    description:
      'Medical abbreviation parsing (CC, BP, HR, h/o, r/t, Rx, Pt) and extracting structured data from abbreviated notes without expanding abbreviations in string fields. The `referral` field is explicitly "none at this time" — the correct extraction is `null` (no referral was made).',
    userMessage:
      "Extract the patient visit details from these medical office notes (transcribed from handwritten):\n\nPt: Margaret Liu    DOB: 5/12/1958\nVisit: 3-20-2026    Provider: Dr. Patel\n\nCC: persistent cough x 3 weeks, worse at night\nno fever, no weight loss\nh/o asthma - childhood, resolved\n\nBP 128/82  HR 76  Temp 98.4  SpO2 97%\n\nAssessment: likely post-nasal drip r/t seasonal allergies\nPlan: fluticasone nasal spray, follow up 2 wks if no improvement\nReferral: none at this time\n\nRx: fluticasone propionate 50mcg, 2 sprays each nostril daily x 30 days\n\nFields: patient_name, date_of_birth, visit_date, provider, chief_complaint, blood_pressure_systolic, blood_pressure_diastolic, heart_rate, temperature, oxygen_saturation, assessment, medication_name, medication_dose, medication_duration, referral",
    expected: JSON.parse(String.raw`{
  "patient_name": "Margaret Liu",
  "date_of_birth": "5/12/1958",
  "visit_date": "3-20-2026",
  "provider": "Dr. Patel",
  "chief_complaint": "persistent cough x 3 weeks, worse at night",
  "blood_pressure_systolic": 128,
  "blood_pressure_diastolic": 82,
  "heart_rate": 76,
  "temperature": 98.4,
  "oxygen_saturation": 97,
  "assessment": "likely post-nasal drip r/t seasonal allergies",
  "medication_name": "fluticasone propionate",
  "medication_dose": "50mcg, 2 sprays each nostril daily",
  "medication_duration": "30 days",
  "referral": null
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-07",
    title: "Multiple People in One Document",
    category: "C",
    description:
      "Multi-entity extraction where the model must correctly associate attributes with the right person. Marcus's phone is not Sarah's phone. Sarah's location is LA (her new location), not Chicago (where she's moving from). Priya's rate belongs only to Priya.",
    userMessage:
      "Extract information about each person mentioned in this email:\n\nHi team,\n\nQuick updates after today's meeting:\n\n— Marcus Washington (Senior Designer, NYC office) is taking over the Acme rebrand from Sarah. He'll be on-site in Chicago next week. His direct line is 212-555-0189. Contact him at m.washington@studio.com\n\n— Sarah Kim is transitioning to the Globex account effective April 1. She's relocating from Chicago to the LA office. No new phone yet — use her email sarah.k@studio.com for now.\n\n— The freelance illustrator we hired for Acme is Priya Desai. She charges $95/hour. Her portfolio is at priyadesai.com. She's based in Toronto but works US East Coast hours. Email: priya@priyadesai.com\n\nPlease update your contacts accordingly.\n— Jordan\n\nFields per person: name, role, location, email, phone, hourly_rate, note\nExtract as an array of person objects.",
    expected: JSON.parse(String.raw`[
  {
    "name": "Marcus Washington",
    "role": "Senior Designer",
    "location": "NYC",
    "email": "m.washington@studio.com",
    "phone": "212-555-0189",
    "hourly_rate": null,
    "note": "taking over the Acme rebrand from Sarah. He'll be on-site in Chicago next week"
  },
  {
    "name": "Sarah Kim",
    "role": null,
    "location": "LA",
    "email": "sarah.k@studio.com",
    "phone": null,
    "hourly_rate": null,
    "note": "transitioning to the Globex account effective April 1. She's relocating from Chicago to the LA office"
  },
  {
    "name": "Priya Desai",
    "role": "freelance illustrator",
    "location": "Toronto",
    "email": "priya@priyadesai.com",
    "phone": null,
    "hourly_rate": 95,
    "note": "works US East Coast hours"
  }
]`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-08",
    title: "Conflicting Information",
    category: "C",
    description:
      "Temporal conflict resolution. The model must track which update supersedes which. The start time changed in the March 18 correction. The location changed in the March 12 update. The catering price changed in the March 12 update. The date did not change (explicitly 'Same date').",
    userMessage:
      "Extract the event details from this text. If information conflicts, extract the MOST RECENT version.\n\n=== ORIGINAL INVITE (March 1) ===\nAnnual Company Picnic\nDate: Saturday, June 14\nTime: 11:00 AM – 4:00 PM\nLocation: Riverside Park, Shelter B\nRSVP by May 15 to events@company.com\nCatering by Fresh Bites ($22/person)\n\n=== UPDATE (March 12) ===\nHey all — slight change of plans. We're moving the picnic to Lincoln Park (Shelter A) because Riverside is under construction. Same date and time. Also the caterer changed their pricing to $25/person.\n\n=== CORRECTION (March 18) ===\nSorry, one more update. The start time is now 11:30 AM (the park opens later than we thought). End time stays 4:00 PM. Everything else from the March 12 update still stands.\n\nFields: event_name, date, start_time, end_time, location_park, location_shelter, rsvp_deadline, rsvp_email, catering_company, catering_price_per_person",
    expected: JSON.parse(String.raw`{
  "event_name": "Annual Company Picnic",
  "date": "Saturday, June 14",
  "start_time": "11:30 AM",
  "end_time": "4:00 PM",
  "location_park": "Lincoln Park",
  "location_shelter": "Shelter A",
  "rsvp_deadline": "May 15",
  "rsvp_email": "events@company.com",
  "catering_company": "Fresh Bites",
  "catering_price_per_person": 25
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-09",
    title: "Nested Quotes — Email Within Email",
    category: "C",
    description:
      "Context layering. The email thread contains three different room requests from three different people at three different times. The model must extract only Alice's current request (the outermost message), not Bob's cancelled request or Carol's suggestion.",
    userMessage:
      "Extract ONLY the current (outermost) request details. Ignore quoted/forwarded content.\n\nFrom: Alice Wong <alice@company.com>\nTo: Facilities <facilities@company.com>\nDate: March 20, 2026\nSubject: FW: Re: Conference Room Request\n\nHi Facilities,\n\nForwarding this thread for context, but here's what I actually need:\n\nPlease book the Atlas room for 8 people on March 28, 2:00 PM–3:30 PM. We need a projector and whiteboard. No catering needed.\n\nThanks,\nAlice\n\n-------- Forwarded Message --------\nFrom: Bob Chen <bob@company.com>\nDate: March 15, 2026\n\nAlice, I originally asked for the Summit room on March 22 for 12 people with lunch catering for the Q1 review. But that's been cancelled now. Can you see if Atlas is available for a smaller meeting instead?\n\n-------- Original Message --------\nFrom: Carol Davis <carol@company.com>\nDate: March 10, 2026\n\nBob — the Phoenix room is booked for March 22. Try Summit or Atlas. We can do catering for up to 20 people.\n\nFields: requester_name, requester_email, room, date, start_time, end_time, attendee_count, needs_projector, needs_whiteboard, needs_catering",
    expected: JSON.parse(String.raw`{
  "requester_name": "Alice Wong",
  "requester_email": "alice@company.com",
  "room": "Atlas",
  "date": "March 28",
  "start_time": "2:00 PM",
  "end_time": "3:30 PM",
  "attendee_count": 8,
  "needs_projector": true,
  "needs_whiteboard": true,
  "needs_catering": false
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-10",
    title: "The Over-Extraction Trap",
    category: "D",
    description:
      `The model's ability to return null for fields that are genuinely unknowable. The reviewer explicitly says they are "not 100% sure" about the neighborhood, "didn't catch" the chef's name, has "no idea" what was paid, and can't pinpoint the location. There is no rating score, only positive sentiment.`,
    userMessage:
      `Extract the restaurant details from this review:\n\n"Had dinner at Sakura Sushi last night with my partner. The place is tucked away on a quiet street in what I think is the Nob Hill neighborhood — honestly not 100% sure. The omakase was incredible, maybe the best I've had in the city. We spent about 2 hours there. The chef (didn't catch his name) was super friendly. No idea what we paid — my partner handled the bill. We took an Uber home so I know it's somewhere near California Street but I really couldn't point to it on a map. Definitely going back."\n\nFields: restaurant_name, cuisine_type, neighborhood, street_address, chef_name, price_paid, visit_duration, rating_score, reservation_required, parking_available`,
    expected: JSON.parse(String.raw`{
  "restaurant_name": "Sakura Sushi",
  "cuisine_type": "Sushi",
  "neighborhood": null,
  "street_address": null,
  "chef_name": null,
  "price_paid": null,
  "visit_duration": "about 2 hours",
  "rating_score": null,
  "reservation_required": null,
  "parking_available": null
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-11",
    title: "Negation and Correction in Text",
    category: "D",
    description: "Tracking corrections and self-corrections in stream-of-consciousness text. Every field has been revised at least once. The model must extract the final stated value, not the first.",
    userMessage:
      `Extract the final confirmed details from this planning message:\n\n"OK so for the team offsite: we were thinking Aspen but actually that's too expensive so let's do Lake Tahoe instead. Budget per person is $500, wait no, I just checked and it's $450 after the flights went up. Dates are April 10-12. Actually hold on — April 10 is a Monday and people need to fly in Sunday night, so let's do April 11-13 (Tue-Thu). We'll need 8 rooms. No wait, Jessica and Tom are sharing, and Mike can't make it anymore, so 6 rooms. The hotel is Mountain View Lodge. Or was it Mountain View Inn? Let me check... it's Mountain View Lodge. Confirmed."\n\nFields: destination, budget_per_person, start_date, end_date, num_rooms, hotel_name, total_attendees`,
    expected: JSON.parse(String.raw`{
  "destination": "Lake Tahoe",
  "budget_per_person": 450,
  "start_date": "April 11",
  "end_date": "April 13",
  "num_rooms": 6,
  "hotel_name": "Mountain View Lodge",
  "total_attendees": null
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-12",
    title: "Decoy Entity — Don't Extract the Wrong Thing",
    category: "D",
    description:
      "Entity disambiguation. Three laptops are mentioned with their specs. The model must extract only the ZenBook's details. MacBook's 18-hour battery and $1,099 price, Dell's 1.2kg weight and $1,199 price are decoys.",
    userMessage:
      `Extract details about the PRODUCT BEING REVIEWED, not any competitors mentioned.\n\n"Bought a ZenBook Pro 15 laptop for my daughter's college. $1,299 at Best Buy. She was also considering the MacBook Air M3 ($1,099) and the Dell XPS 13 ($1,199) but we went with ASUS because of the OLED display and 16GB RAM. Battery life is about 10 hours which is less than the MacBook's 18 hours but she'll mostly use it plugged in. It's heavier than the Dell at 1.8kg vs 1.2kg but the 15-inch screen was important to her.\n\nThe i7-13700H processor handles everything she throws at it. 512GB SSD is fine for now — she keeps everything in Google Drive anyway. One thing that bugs me: it came with McAfee preinstalled and a bunch of other bloatware. The ASUS customer support was also pretty bad when I called about a driver issue.\n\nOverall: 4/5 stars. Great laptop for the price, just wish ASUS cleaned up the software experience."\n\nFields: product_name, brand, price, store, display_type, display_size, ram_gb, processor, storage, battery_life_hours, weight_kg, rating, operating_system, complaint`,
    expected: JSON.parse(String.raw`{
  "product_name": "ZenBook Pro 15",
  "brand": "ASUS",
  "price": 1299,
  "store": "Best Buy",
  "display_type": "OLED",
  "display_size": "15-inch",
  "ram_gb": 16,
  "processor": "i7-13700H",
  "storage": "512GB SSD",
  "battery_life_hours": 10,
  "weight_kg": 1.8,
  "rating": 4,
  "operating_system": null,
  "complaint": "it came with McAfee preinstalled and a bunch of other bloatware. The ASUS customer support was also pretty bad when I called about a driver issue."
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-13",
    title: "Multi-Section Invoice with Discounts",
    category: "E",
    description: "Complex structured extraction with nested arrays, multiple sections, and financial data requiring exact precision.",
    userMessage:
      "Extract the invoice details:\n\nINVOICE #INV-2026-0847\n\nFrom: CloudTech Solutions, LLC\n123 Innovation Way, Seattle, WA 98101\nTax ID: 91-7654321\n\nBill To: Meridian Corp\nAttn: Accounts Payable\n500 Commerce Dr, Portland, OR 97201\nPO#: MC-2026-445\n\nInvoice Date: March 15, 2026\nDue Date: April 14, 2026\nTerms: Net 30\n\n┌─────────────────────────────────────────────────────────────────────┐\n│ Description                    │ Qty │ Unit Price │ Amount         │\n├─────────────────────────────────────────────────────────────────────┤\n│ Cloud Hosting - Pro Plan       │ 12  │ $149.00    │ $1,788.00     │\n│ API Gateway - Standard         │ 1   │ $299.00    │ $299.00       │\n│ SSL Certificate - Wildcard     │ 2   │ $89.00     │ $178.00       │\n│ Professional Services (hrs)    │ 8   │ $175.00    │ $1,400.00     │\n│ Data Migration (one-time)      │ 1   │ $500.00    │ $500.00       │\n├─────────────────────────────────────────────────────────────────────┤\n│ Subtotal                                           │ $4,165.00     │\n│ Volume Discount (10%)                              │ -$416.50      │\n│ Early Payment Credit                               │ -$50.00       │\n│ ─────────────────────────────────────────────────────────────────── │\n│ Total Due                                          │ $3,698.50     │\n└─────────────────────────────────────────────────────────────────────┘\n\nPayment: Wire transfer to Account #****4521 (Chase Bank)\nFor questions: billing@cloudtech.io\n\nFields: invoice_number, vendor_name, vendor_address, vendor_tax_id, client_name, client_po, invoice_date, due_date, payment_terms, line_items (array of {description, qty, unit_price, amount}), subtotal, discounts (array of {description, amount}), total_due, payment_method, billing_contact_email",
    expected: JSON.parse(String.raw`{
  "invoice_number": "INV-2026-0847",
  "vendor_name": "CloudTech Solutions, LLC",
  "vendor_address": "123 Innovation Way, Seattle, WA 98101",
  "vendor_tax_id": "91-7654321",
  "client_name": "Meridian Corp",
  "client_po": "MC-2026-445",
  "invoice_date": "March 15, 2026",
  "due_date": "April 14, 2026",
  "payment_terms": "Net 30",
  "line_items": [
    {"description": "Cloud Hosting - Pro Plan", "qty": 12, "unit_price": 149.00, "amount": 1788.00},
    {"description": "API Gateway - Standard", "qty": 1, "unit_price": 299.00, "amount": 299.00},
    {"description": "SSL Certificate - Wildcard", "qty": 2, "unit_price": 89.00, "amount": 178.00},
    {"description": "Professional Services (hrs)", "qty": 8, "unit_price": 175.00, "amount": 1400.00},
    {"description": "Data Migration (one-time)", "qty": 1, "unit_price": 500.00, "amount": 500.00}
  ],
  "subtotal": 4165.00,
  "discounts": [
    {"description": "Volume Discount (10%)", "amount": -416.50},
    {"description": "Early Payment Credit", "amount": -50.00}
  ],
  "total_due": 3698.50,
  "payment_method": "Wire transfer",
  "billing_contact_email": "billing@cloudtech.io"
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-14",
    title: "Multi-Language Document",
    category: "E",
    description:
      "Extraction from bilingual content (Japanese/English). The model must handle Japanese yen formatting, bilingual labels, CJK characters, and preserve source-language strings exactly when a field is returned as text.",
    userMessage:
      "Extract the product details from this bilingual product listing:\n\n【新発売】ワイヤレスイヤホン AirPulse X3\nNew Release: AirPulse X3 Wireless Earbuds\n\n価格 / Price: ¥12,980 (税込 / tax included)\n色 / Colors: ミッドナイトブラック (Midnight Black), パールホワイト (Pearl White), サクラピンク (Sakura Pink)\n\n仕様 / Specifications:\nドライバー / Driver: 10mm ダイナミック / 10mm Dynamic\nBluetooth: 5.3\nバッテリー / Battery: イヤホン本体 8時間 / Earbuds 8 hours, ケース込み 32時間 / With case 32 hours\n防水 / Water resistance: IPX5\n重量 / Weight: 片耳 5.2g / Per earbud 5.2g, ケース 42g / Case 42g\nノイキャン / ANC: ✓ アダプティブ / Adaptive\n\n付属品 / Included: USB-C充電ケーブル, イヤーチップ(S/M/L), 取扱説明書\nIncluded: USB-C charging cable, ear tips (S/M/L), user manual\n\n販売元 / Sold by: SoundWave Electronics Co., Ltd. (東京都渋谷区)\n\nFields: product_name, product_type, price_jpy, tax_included, colors (array), driver_size, bluetooth_version, battery_life_earbuds_hours, battery_life_with_case_hours, water_resistance_rating, earbud_weight_grams, case_weight_grams, has_anc, anc_type, included_items (array), seller_name, seller_location",
    expected: JSON.parse(String.raw`{
  "product_name": "AirPulse X3",
  "product_type": "Wireless Earbuds",
  "price_jpy": 12980,
  "tax_included": true,
  "colors": ["Midnight Black", "Pearl White", "Sakura Pink"],
  "driver_size": "10mm",
  "bluetooth_version": "5.3",
  "battery_life_earbuds_hours": 8,
  "battery_life_with_case_hours": 32,
  "water_resistance_rating": "IPX5",
  "earbud_weight_grams": 5.2,
  "case_weight_grams": 42,
  "has_anc": true,
  "anc_type": "Adaptive",
  "included_items": ["USB-C charging cable", "ear tips (S/M/L)", "user manual"],
  "seller_name": "SoundWave Electronics Co., Ltd.",
  "seller_location": "東京都渋谷区"
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  },
  {
    id: "DE-15",
    title: "The Empty Document Trap",
    category: "E",
    description:
      `The model's ability to say "I don't know" when the text contains zero extractable information. This generic "About Us" page has no specific details whatsoever, so every field should be null.`,
    userMessage:
      `Extract the company details from this "About Us" page:\n\nWelcome to our website! We're passionate about what we do and we've been doing it for a long time. Our team of dedicated professionals works hard every day to deliver the best results for our clients. We believe in innovation, integrity, and excellence.\n\n"Great service!" — A happy customer\n"Would recommend!" — Another satisfied client\n\nContact us today to learn how we can help your business grow!\n© 2026 All rights reserved.\n\nFields: company_name, founding_year, ceo_name, employee_count, headquarters_city, industry, annual_revenue, phone_number, email, website_url`,
    expected: JSON.parse(String.raw`{
  "company_name": null,
  "founding_year": null,
  "ceo_name": null,
  "employee_count": null,
  "headquarters_city": null,
  "industry": null,
  "annual_revenue": null,
  "phone_number": null,
  "email": null,
  "website_url": null
}`),
    successCase: "Returns valid JSON and preserves exact source text for the requested fields.",
    failureCase: "Hallucinates, normalizes, or extracts fields that are not explicitly supported by the source text."
  }
];

export const SCENARIOS: ScenarioDefinition[] = SCENARIO_SPECS.map((scenario) => ({
  ...scenario,
  evaluate: (state) => evaluateScenarioOutput(scenario.id, scenario.expected, state.finalAnswer)
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
