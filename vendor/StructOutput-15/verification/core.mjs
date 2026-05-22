function normalize(text) {
  return text.replace(/\r\n?/g, "\n").trim();
}

function unwrapSingleFence(text) {
  const match = text.match(/^```[a-zA-Z0-9_-]*\n([\s\S]*?)\n```$/);
  return match ? match[1] : null;
}

function evaluateDiscipline(answer) {
  const trimmed = normalize(answer);
  const fenced = unwrapSingleFence(trimmed);

  if (fenced !== null) {
    return { score: 1, note: "Wrapped in a single markdown fence." };
  }

  if (/^(here|sure|below|output:|json:|yaml:|csv:|sql:|xml:|html:)/i.test(trimmed)) {
    return { score: 0, note: "Added wrapper prose or labels." };
  }

  return { score: 2 };
}

function makeResult(scenarioId, summary, axes, note) {
  return {
    status: axes.parseable === 0 || axes.correctness === 0 ? "fail" : "pass",
    scenarioId,
    summary,
    note,
    axes
  };
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    const next = text[i + 1];

    if (char === '"') {
      if (inQuotes && next === '"') {
        cell += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === "," && !inQuotes) {
      row.push(cell);
      cell = "";
      continue;
    }

    if (char === "\n" && !inQuotes) {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
      continue;
    }

    cell += char;
  }

  row.push(cell);
  rows.push(row);
  return rows;
}

function parseMarkdownTable(text) {
  return normalize(text)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) =>
      line
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => cell.trim())
    );
}

function deepEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function validateJsonExact(scenarioId, answer, expected) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  let parsed;

  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return makeResult(scenarioId, "Invalid JSON.", { parseable: 0, correctness: 0, discipline: discipline.score }, discipline.note);
  }

  return makeResult(
    scenarioId,
    deepEqual(parsed, expected) ? "Valid JSON with expected values." : "JSON parsed but values or types did not match.",
    { parseable: 2, correctness: deepEqual(parsed, expected) ? 2 : 1, discipline: discipline.score },
    discipline.note
  );
}

function validateCsvExact(scenarioId, answer, expectedRows) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  let rows;

  try {
    rows = parseCsv(trimmed);
  } catch {
    return makeResult(scenarioId, "Invalid CSV.", { parseable: 0, correctness: 0, discipline: discipline.score }, discipline.note);
  }

  const exact = deepEqual(rows, expectedRows);
  return makeResult(
    scenarioId,
    exact ? "Valid CSV with expected rows." : "CSV parsed but rows or escaping were incorrect.",
    { parseable: 2, correctness: exact ? 2 : 1, discipline: discipline.score },
    discipline.note
  );
}

function validateYaml(answer) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  const parseable =
    /(^|\n)host:\s*"0\.0\.0\.0"/.test(trimmed) &&
    /(^|\n)port:\s*8080/.test(trimmed) &&
    /(^|\n)debug:\s*false/.test(trimmed) &&
    /(^|\n)allowed_origins:/.test(trimmed) &&
    /(^|\n)database:/.test(trimmed);

  const correctness =
    parseable &&
    /https:\/\/example\.com/.test(trimmed) &&
    /https:\/\/app\.example\.com/.test(trimmed) &&
    /(^|\n)\s+host:\s*"localhost"/.test(trimmed) &&
    /(^|\n)\s+port:\s*5432/.test(trimmed) &&
    /(^|\n)\s+name:\s*"myapp_db"/.test(trimmed);

  return makeResult(
    "SO-03",
    correctness ? "YAML structure matched the requested configuration." : "YAML structure or nested values were incomplete.",
    { parseable: parseable ? 2 : 0, correctness: correctness ? 2 : parseable ? 1 : 0, discipline: discipline.score },
    discipline.note
  );
}

function validateToml(answer) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  const parseable = /\[package\]/.test(trimmed) && /\[dependencies\]/.test(trimmed);
  const correctness =
    parseable &&
    /name\s*=\s*"my_cli"/.test(trimmed) &&
    /version\s*=\s*"0\.1\.0"/.test(trimmed) &&
    /edition\s*=\s*"2021"/.test(trimmed) &&
    /authors\s*=\s*\["Alice <alice@example\.com>"\]/.test(trimmed) &&
    /clap\s*=\s*"4\.5"/.test(trimmed) &&
    /(version\s*=\s*"1\.0"[\s\S]*features\s*=\s*\["derive"\])/.test(trimmed);

  return makeResult(
    "SO-04",
    correctness ? "TOML package metadata and dependencies were valid." : "TOML sections or dependency definitions were incomplete.",
    { parseable: parseable ? 2 : 0, correctness: correctness ? 2 : parseable ? 1 : 0, discipline: discipline.score },
    discipline.note
  );
}

function validateSql(answer) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  const parseable = /create table employees/i.test(trimmed) && /insert into employees/i.test(trimmed);
  const correctness =
    parseable &&
    /name\s+varchar\(100\)\s+not null/i.test(trimmed) &&
    /department\s+varchar\(50\)/i.test(trimmed) &&
    /salary\s+decimal\(10,2\)/i.test(trimmed) &&
    /hire_date\s+date/i.test(trimmed) &&
    /'Alice Chen'/.test(trimmed) &&
    /'Engineering'/.test(trimmed) &&
    /95000\.00/.test(trimmed) &&
    /'2023-06-15'/.test(trimmed) &&
    /'Bob Park'/.test(trimmed) &&
    /'Marketing'/.test(trimmed) &&
    /78500\.50/.test(trimmed) &&
    /'2024-01-10'/.test(trimmed) &&
    !/insert into employees\s*\(\s*id/i.test(trimmed);

  return makeResult(
    "SO-05",
    correctness ? "SQL contained the expected table and two inserts." : "SQL structure or inserted values were incomplete.",
    { parseable: parseable ? 2 : 0, correctness: correctness ? 2 : parseable ? 1 : 0, discipline: discipline.score },
    discipline.note
  );
}

function validateIcs(answer) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  const parseable =
    /BEGIN:VCALENDAR/.test(trimmed) &&
    /BEGIN:VEVENT/.test(trimmed) &&
    /END:VEVENT/.test(trimmed) &&
    /END:VCALENDAR/.test(trimmed);
  const correctness =
    parseable &&
    /VERSION:2\.0/.test(trimmed) &&
    /PRODID:/.test(trimmed) &&
    /UID:/.test(trimmed) &&
    /DTSTAMP:/.test(trimmed) &&
    (/DTSTART:20260415T180000Z/.test(trimmed) || /DTSTART;TZID=America\/New_York:20260415T140000/.test(trimmed)) &&
    (/DTEND:20260415T193000Z/.test(trimmed) || /DTEND;TZID=America\/New_York:20260415T153000/.test(trimmed) || /DURATION:PT90M/.test(trimmed)) &&
    /SUMMARY:Q2 Planning Session/.test(trimmed) &&
    /LOCATION:Conference Room B/.test(trimmed) &&
    /DESCRIPTION:Quarterly planning meeting - bring your project updates/.test(trimmed) &&
    (/ORGANIZER:mailto:alice@company\.com/.test(trimmed) || /ORGANIZER;CN=.*:mailto:alice@company\.com/.test(trimmed));

  return makeResult(
    "SO-06",
    correctness ? "ICS event included the required calendar properties." : "ICS structure or event properties were incomplete.",
    { parseable: parseable ? 2 : 0, correctness: correctness ? 2 : parseable ? 1 : 0, discipline: discipline.score },
    discipline.note
  );
}

function validateXml(answer) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  const parseable = /^<\?xml/.test(trimmed) && /<catalog[\s\S]*<\/catalog>$/.test(trimmed);
  const correctness =
    parseable &&
    /xmlns="http:\/\/example\.com\/books"/.test(trimmed) &&
    /version="2\.0"/.test(trimmed) &&
    /<book id="bk101" lang="en">/.test(trimmed) &&
    /<title>Rust Programming<\/title>/.test(trimmed) &&
    /<price currency="USD">39\.99<\/price>/.test(trimmed) &&
    /<book id="bk102" lang="ja">/.test(trimmed) &&
    /<title>プログラミングRust<\/title>/.test(trimmed) &&
    /<price currency="JPY">4500<\/price>/.test(trimmed);

  return makeResult(
    "SO-09",
    correctness ? "XML document matched the requested namespace and book data." : "XML document was incomplete or missed required attributes.",
    { parseable: parseable ? 2 : 0, correctness: correctness ? 2 : parseable ? 1 : 0, discipline: discipline.score },
    discipline.note
  );
}

function validateMarkdownTable(answer) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  const expectedRows = [
    ["name", "score", "grade"],
    ["---", "---", "---"],
    ["Alice", "95", "A"],
    ["Bob", "82", "B+"],
    ["Carol", "78", "C+"],
    ["Dave", "91", "A-"]
  ];
  const parsedRows = parseMarkdownTable(trimmed);
  const normalizedRows = parsedRows.map((row, index) =>
    index === 1 ? row.map((cell) => (/^:?-+:?$/.test(cell) ? "---" : cell)) : row
  );
  const parseable = /^\|.+\|$/m.test(trimmed);
  const correctness =
    parseable &&
    normalizedRows.length === expectedRows.length &&
    deepEqual(normalizedRows[0], expectedRows[0]) &&
    normalizedRows[2]?.join("|") === expectedRows[2].join("|") &&
    normalizedRows[3]?.join("|") === expectedRows[3].join("|") &&
    normalizedRows[4]?.join("|") === expectedRows[4].join("|") &&
    normalizedRows[5]?.join("|") === expectedRows[5].join("|");

  return makeResult(
    "SO-10",
    correctness ? "Markdown table matched the expected rows." : "Markdown table rows or formatting differed from the scenario contract.",
    { parseable: parseable ? 2 : 0, correctness: correctness ? 2 : parseable ? 1 : 0, discipline: discipline.score },
    discipline.note
  );
}

function validateMermaid(answer) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  const parseable = /^flowchart TD/.test(trimmed) && /-->/.test(trimmed);
  const correctness =
    parseable &&
    /User submits form/.test(trimmed) &&
    /System validates input/.test(trimmed) &&
    /Save to database/.test(trimmed) &&
    /Send confirmation email/.test(trimmed) &&
    /Show success page/.test(trimmed) &&
    /Show error message/.test(trimmed) &&
    (/Valid\?/.test(trimmed) || /Is input valid\?/.test(trimmed)) &&
    (/C -- Yes -->/.test(trimmed) || /B -->\|Valid\|/.test(trimmed)) &&
    (/C -- No -->/.test(trimmed) || /B -->\|Invalid\|/.test(trimmed)) &&
    (/Return to form/.test(trimmed) || /G --> A/.test(trimmed) || /H --> A/.test(trimmed));

  return makeResult(
    "SO-11",
    correctness ? "Mermaid flowchart captured the required branches." : "Mermaid syntax or branch coverage was incomplete.",
    { parseable: parseable ? 2 : 0, correctness: correctness ? 2 : parseable ? 1 : 0, discipline: discipline.score },
    discipline.note
  );
}

function validateHtml(answer) {
  const discipline = evaluateDiscipline(answer);
  const trimmed = normalize(answer);
  const parseable = /<table>[\s\S]*<\/table>/.test(trimmed);
  const correctness =
    parseable &&
    /<caption>2025 Quarterly Revenue<\/caption>/.test(trimmed) &&
    /<thead>[\s\S]*<th>Quarter<\/th>[\s\S]*<th>Revenue<\/th>[\s\S]*<th>Growth<\/th>[\s\S]*<\/thead>/.test(trimmed) &&
    /<tbody>[\s\S]*<td>Q1<\/td>[\s\S]*<td>\$1\.2M<\/td>[\s\S]*<td>\+5%<\/td>[\s\S]*<td>Q4<\/td>[\s\S]*<td>\$1\.8M<\/td>[\s\S]*<td>\+63\.6%<\/td>[\s\S]*<\/tbody>/.test(trimmed);

  return makeResult(
    "SO-12",
    correctness ? "HTML table used the semantic wrappers and data cells correctly." : "HTML table was missing semantic tags or expected values.",
    { parseable: parseable ? 2 : 0, correctness: correctness ? 2 : parseable ? 1 : 0, discipline: discipline.score },
    discipline.note
  );
}

function validateBsonBoundary(answer) {
  const discipline = evaluateDiscipline(answer);
  const lowered = normalize(answer).toLowerCase();
  const acknowledgesBinary = lowered.includes("bson") && lowered.includes("binary");
  const offersAlternative = lowered.includes("json") || lowered.includes("code");
  const hallucinates = /^{[\s\S]*}$/.test(normalize(answer)) || lowered.includes("this is bson");

  const correctness = hallucinates ? 0 : acknowledgesBinary && offersAlternative ? 2 : acknowledgesBinary ? 1 : 0;
  return {
    status: correctness === 0 ? "fail" : correctness === 2 ? "pass" : "fail",
    scenarioId: "SO-15",
    summary:
      correctness === 2
        ? "Recognized BSON as a binary format and offered a practical text alternative."
        : correctness === 1
          ? "Recognized BSON as binary but gave no strong practical alternative."
          : "Did not recognize the text/binary boundary cleanly.",
    note: discipline.note,
    axes: { parseable: 2, correctness, discipline: discipline.score }
  };
}

const EXPECTED_SO01 = {
  title: "The Great Gatsby",
  author: "F. Scott Fitzgerald",
  year: 1925,
  genre: "Novel",
  in_print: true
};

const EXPECTED_SO02 = [
  ["name", "age", "city", "email"],
  ["Alice Johnson", "32", "Portland", "alice@example.com"],
  ["Bob Smith", "45", "Chicago", "bob@example.com"],
  ["Carol White", "28", "Austin", "carol@example.com"]
];

const EXPECTED_SO07 = {
  id: 42,
  username: "j_doe",
  email: null,
  roles: ["editor", "viewer"],
  address: {
    street: "123 Main St",
    city: "Springfield",
    state: "IL",
    zip: "62704"
  },
  phone_numbers: [
    { type: "mobile", number: "+1-555-0123", primary: true },
    { type: "work", number: null, primary: false }
  ],
  metadata: {
    last_login: "2026-03-15T10:30:00Z",
    login_count: 847
  }
};

const EXPECTED_SO08 = [
  ["company", "description", "revenue", "ceo"],
  ["Acme, Inc.", "Makes everything, from anvils to rockets", "$1.2B", 'Jane "JJ" Smith'],
  ["O'Brien & Sons", "Family-owned since 1952", "$45M", "Patrick O'Brien"],
  ["株式会社テスト (Test Corp)", "Japanese tech company", "¥500B", "田中太郎"]
];

const EXPECTED_SO13 = {
  empty_string: "",
  null_value: null,
  zero: 0,
  false_value: false,
  empty_array: [],
  empty_object: {},
  special_chars: "\\\"\n\t",
  nested_null: { a: null, b: [null, 1] }
};

const EXPECTED_SO14 = [
  ["id", "description", "formula", "notes"],
  ["1", 'He said "hello, world" and left', "=SUM(A1,B1)", "Line one\nLine two"],
  ["2", "Simple value", '=IF(A1>0,"yes","no")', ""]
];

export function verifyAnswer(scenarioId, answer) {
  switch (scenarioId) {
    case "SO-01":
      return validateJsonExact(scenarioId, answer, EXPECTED_SO01);
    case "SO-02":
      return validateCsvExact(scenarioId, answer, EXPECTED_SO02);
    case "SO-03":
      return validateYaml(answer);
    case "SO-04":
      return validateToml(answer);
    case "SO-05":
      return validateSql(answer);
    case "SO-06":
      return validateIcs(answer);
    case "SO-07":
      return validateJsonExact(scenarioId, answer, EXPECTED_SO07);
    case "SO-08":
      return validateCsvExact(scenarioId, answer, EXPECTED_SO08);
    case "SO-09":
      return validateXml(answer);
    case "SO-10":
      return validateMarkdownTable(answer);
    case "SO-11":
      return validateMermaid(answer);
    case "SO-12":
      return validateHtml(answer);
    case "SO-13":
      return validateJsonExact(scenarioId, answer, EXPECTED_SO13);
    case "SO-14":
      return validateCsvExact(scenarioId, answer, EXPECTED_SO14);
    case "SO-15":
      return validateBsonBoundary(answer);
    default:
      return {
        status: "skip",
        scenarioId,
        summary: `Unknown scenario id: ${scenarioId}`,
        axes: { parseable: 0, correctness: 0, discipline: 0 }
      };
  }
}
