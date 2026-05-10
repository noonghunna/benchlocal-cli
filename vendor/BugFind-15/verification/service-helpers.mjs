function dedent(source) {
  const text = source.replace(/^\n+|\n+\s*$/g, "");
  const lines = text.split("\n");
  const indents = lines
    .filter((line) => line.trim().length > 0)
    .map((line) => line.match(/^(\s*)/)?.[1].length ?? 0);
  const margin = indents.length === 0 ? 0 : Math.min(...indents);

  return lines.map((line) => line.slice(margin)).join("\n");
}

function normalize(text) {
  return text.trim().toLowerCase();
}

function includesAny(text, patterns) {
  const source = normalize(text);
  return patterns.some((pattern) => source.includes(normalize(pattern)));
}

function extractCodeBlocks(answer, aliases) {
  const blocks = [];
  const matcher = /```([a-zA-Z0-9_+-]*)\n([\s\S]*?)```/g;
  let match = matcher.exec(answer);

  while (match) {
    const language = match[1]?.trim().toLowerCase() ?? "";
    const code = match[2];

    if (aliases.length === 0 || aliases.includes(language) || language === "") {
      blocks.push(code);
    }

    match = matcher.exec(answer);
  }

  return blocks;
}

function candidate(label, files, source = "extracted_code", checks) {
  return checks ? { label, files, source, checks } : { label, files, source };
}

function pythonFile(code) {
  return { "main.py": `${dedent(code)}\n` };
}

function javascriptFile(code) {
  return { "main.js": `${dedent(code)}\n` };
}

function rustFile(code) {
  return { "main.rs": `${dedent(code)}\n` };
}

function goFile(code) {
  return { "main.go": `${dedent(code)}\n` };
}

function stripUnusedGoFmtImport(code) {
  const text = dedent(code);

  if (text.includes("fmt.")) {
    return text;
  }

  return text
    .replace(/^\s*import\s+"fmt"\s*\n?/m, "")
    .replace(/^\s*"fmt"\s*\n/m, "");
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function markerCheck(marker, validatePayload) {
  return ({ exitCode, stdout, stderr }) => {
    assert(exitCode === 0, `expected exit code 0, got ${exitCode}\nstderr:\n${stderr}`);
    const line = stdout
      .split("\n")
      .map((entry) => entry.trim())
      .find((entry) => entry.startsWith(marker));
    assert(line, `expected stdout marker "${marker}"\nactual stdout:\n${stdout}`);
    validatePayload(line.slice(marker.length));
  };
}

function markerJsonCheck(marker, validatePayload) {
  return markerCheck(marker, (payload) => {
    let parsed;

    try {
      parsed = JSON.parse(payload);
    } catch (error) {
      throw new Error(`failed to parse JSON payload for ${marker}: ${error instanceof Error ? error.message : String(error)}`);
    }

    validatePayload(parsed);
  });
}

function parseIntegerSet(stdout) {
  return [...stdout.matchAll(/-?\d+/g)].map((match) => Number.parseInt(match[0], 10));
}

function exactNumberSetCheck(expectedNumbers) {
  return ({ exitCode, stdout, stderr }) => {
    assert(exitCode === 0, `expected exit code 0, got ${exitCode}\nstderr:\n${stderr}`);
    const numbers = parseIntegerSet(stdout).sort((left, right) => left - right);
    const expected = [...expectedNumbers].sort((left, right) => left - right);
    assert(JSON.stringify(numbers) === JSON.stringify(expected), `expected numbers ${JSON.stringify(expected)}\nactual stdout:\n${stdout}`);
  };
}

function containsAny(text, parts) {
  return parts.some((part) => text.includes(part));
}

function buildCheck(name, command, validate, timeoutMs) {
  return timeoutMs ? { name, command, validate, timeoutMs } : { name, command, validate };
}

function normalizeMeaningfulLines(stdout) {
  return stdout
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function addPythonHarness(block, harness) {
  if (block.includes("print(")) {
    return pythonFile(block);
  }

  return pythonFile(`${block}\n\n${harness}`);
}

function addJavascriptHarness(block, harness) {
  if (block.includes("console.log(") || block.includes("process.exit(")) {
    return javascriptFile(block);
  }

  return javascriptFile(`${block}\n\n${harness}`);
}

function addRustHarness(block, harness) {
  if (block.includes("fn main()")) {
    return rustFile(block);
  }

  return rustFile(`${block}\n\n${harness}`);
}

function addGoHarness(block, harness) {
  if (block.includes("package main")) {
    return goFile(block);
  }

  return goFile(`${harness}\n`);
}

function appendPythonHarness(block, harness) {
  return pythonFile(`${dedent(block)}\n\n${dedent(harness)}\n`);
}

function appendJavascriptHarness(block, harness) {
  return javascriptFile(`${dedent(block)}\n\n${dedent(harness)}\n`);
}

function noBugAnswer(answer) {
  return includesAny(answer, [
    "no bug",
    "code is correct",
    "compiles fine",
    "works correctly",
    "nothing is wrong",
    "no issue"
  ]);
}

function buildPythonCandidates(scenarioId, answer) {
  const blocks = extractCodeBlocks(answer, ["python", "py"]);
  const results = [];

  switch (scenarioId) {
    case "BF-01":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("def sum_list")) {
          results.push(candidate(`code-block-${index + 1}`, addPythonHarness(block, 'print(sum_list([1, 2, 3]))\nprint(sum_list([10]))')));
        }
      }
      if (includesAny(answer, ["for num in numbers", "range(len(numbers))", "start from 0"])) {
        results.push(
          candidate(
            "synthesized-loop-fix",
            pythonFile(`
              def sum_list(numbers):
                  total = 0
                  for num in numbers:
                      total += num
                  return total

              print(sum_list([1, 2, 3]))
              print(sum_list([10]))
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-04":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("def remove_inactive_users")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addPythonHarness(
                block,
                'users = {"u1": "active", "u2": "inactive", "u3": "active", "u4": "inactive"}\nprint(remove_inactive_users(users))'
              )
            )
          );
        }
      }
      if (includesAny(answer, ["dict comprehension", "return {", "to_remove", "list(users.items())"])) {
        results.push(
          candidate(
            "synthesized-dict-fix",
            pythonFile(`
              def remove_inactive_users(users):
                  return {uid: status for uid, status in users.items() if status != "inactive"}

              users = {"u1": "active", "u2": "inactive", "u3": "active", "u4": "inactive"}
              print(remove_inactive_users(users))
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-07":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("def add_item")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addPythonHarness(block, 'print(add_item("apple"))\nprint(add_item("banana"))\nprint(add_item("cherry"))')
            )
          );
        }
      }
      if (includesAny(answer, ["item_list=None", "if item_list is None"])) {
        results.push(
          candidate(
            "synthesized-none-sentinel",
            pythonFile(`
              def add_item(item, item_list=None):
                  if item_list is None:
                      item_list = []
                  item_list.append(item)
                  return item_list

              print(add_item("apple"))
              print(add_item("banana"))
              print(add_item("cherry"))
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-10":
      if (noBugAnswer(answer)) {
        results.push(
          candidate(
            "canonical-no-change",
            pythonFile(`
              def process(data):
                  result = []
                  seen = set()
                  for item in data:
                      key = item.lower().strip()
                      if key not in seen:
                          seen.add(key)
                          result.append(item)
                  return result

              print(process(["Hello", "  hello ", "HELLO", "World", "world"]))
            `),
            "canonical_no_change"
          )
        );
      }
      break;
    case "BF-13":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("def sort_users")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addPythonHarness(
                block,
                'users = [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "5"}, {"name": "Charlie", "age": "25"}]\nprint([user["name"] for user in sort_users(users)])'
              )
            )
          );
        }
      }
      if (includesAny(answer, ['int(u["age"])', "int(u['age'])", "convert age to int"])) {
        results.push(
          candidate(
            "synthesized-int-cast",
            pythonFile(`
              def sort_users(users):
                  return sorted(users, key=lambda u: int(u["age"]))

              users = [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "5"}, {"name": "Charlie", "age": "25"}]
              print([user["name"] for user in sort_users(users)])
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    default:
      break;
  }

  return results;
}

function buildJavascriptCandidates(scenarioId, answer) {
  const blocks = extractCodeBlocks(answer, ["javascript", "js", "node"]);
  const results = [];

  switch (scenarioId) {
    case "BF-02":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("validateInput")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addJavascriptHarness(
                block,
                'console.log(String(validateInput("")));\nconsole.log(String(validateInput(null)));\nconsole.log(String(validateInput(undefined)));\nconsole.log(String(validateInput("hello")));'
              )
            )
          );
        }
      }
      if (includesAny(answer, ['!== ""', "if (input)", "falsy"])) {
        results.push(
          candidate(
            "synthesized-empty-string-fix",
            javascriptFile(`
              function validateInput(input) {
                return Boolean(input);
              }

              console.log(String(validateInput("")));
              console.log(String(validateInput(null)));
              console.log(String(validateInput(undefined)));
              console.log(String(validateInput("hello")));
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-06":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("getUserName")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addJavascriptHarness(
                block,
                `
                globalThis.fetch = async () => ({ json: async () => ({ name: "Ada" }) });
                getUserName(7).then((name) => console.log(name)).catch((error) => { console.error(error.toString()); process.exit(1); });
                `
              )
            )
          );
        }
      }
      if (includesAny(answer, ["await fetch", "await response.json"])) {
        results.push(
          candidate(
            "synthesized-await-fix",
            javascriptFile(`
              globalThis.fetch = async () => ({ json: async () => ({ name: "Ada" }) });

              async function getUserName(userId) {
                const response = await fetch(\`/api/users/\${userId}\`);
                const data = await response.json();
                return data.name;
              }

              getUserName(7).then((name) => console.log(name)).catch((error) => { console.error(error.toString()); process.exit(1); });
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-11":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("applyDiscount")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addJavascriptHarness(
                block,
                `
                try {
                  console.log(applyDiscount(100, 15));
                  console.log(applyDiscount(50, 110));
                  console.log(applyDiscount(50, -5));
                } catch (error) {
                  console.log(85);
                  console.log(error.name);
                  console.log(error.name);
                }
                `
              )
            )
          );
        }
      }
      if (includesAny(answer, ["throw new RangeError", "throw", "invalid discount"])) {
        results.push(
          candidate(
            "synthesized-range-error",
            javascriptFile(`
              function applyDiscount(price, discountPercent) {
                if (discountPercent < 0 || discountPercent > 100) {
                  throw new RangeError(\`Invalid discount: \${discountPercent}%\`);
                }
                const discounted = price * (1 - discountPercent / 100);
                return Math.round(discounted * 100) / 100;
              }

              console.log(applyDiscount(100, 15));
              for (const discount of [110, -5]) {
                try {
                  console.log(applyDiscount(50, discount));
                } catch (error) {
                  console.log(error.name);
                }
              }
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-14":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("getShippingZone")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addJavascriptHarness(block, 'console.log(getShippingZone({ id: 123 }));\nconsole.log(getShippingZone({ shipping_address: { city: "New York" } }));')
            )
          );
        }
      }
      if (includesAny(answer, ["shipping_address?.city", "optional chaining", "if (!city)"])) {
        results.push(
          candidate(
            "synthesized-optional-chaining",
            javascriptFile(`
              function getShippingZone(order) {
                const city = order.shipping_address?.city;
                if (!city) return "standard";
                const zones = {
                  "New York": "east",
                  "Los Angeles": "west",
                  "Chicago": "central"
                };
                return zones[city] || "standard";
              }

              console.log(getShippingZone({ id: 123 }));
              console.log(getShippingZone({ shipping_address: { city: "New York" } }));
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    default:
      break;
  }

  return results;
}

function buildRustCandidates(scenarioId, answer) {
  const blocks = extractCodeBlocks(answer, ["rust", "rs"]);
  const results = [];

  switch (scenarioId) {
    case "BF-03":
      if (noBugAnswer(answer) || includesAny(answer, ["format! borrows", "does not move"])) {
        results.push(
          candidate(
            "canonical-no-change",
            rustFile(`
              fn main() {
                  let name = String::from("Alice");
                  let greeting = format!("Hello, {}", name);
                  println!("{}", greeting);
                  println!("Name was: {}", name);
              }
            `),
            "canonical_no_change"
          )
        );
      }
      break;
    case "BF-08":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("factorial")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addRustHarness(block, 'fn main() { println!("{:?}", factorial(20)); println!("{:?}", factorial(25)); }')
            )
          );
        }
      }
      if (includesAny(answer, ["checked_mul", "Option<u64>"])) {
        results.push(
          candidate(
            "synthesized-checked-mul",
            rustFile(`
              fn factorial(n: u64) -> Option<u64> {
                  let mut result: u64 = 1;
                  for i in 1..=n {
                      result = result.checked_mul(i)?;
                  }
                  Some(result)
              }

              fn main() {
                  println!("{:?}", factorial(20));
                  println!("{:?}", factorial(25));
              }
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-12":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("longest_streak")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addRustHarness(block, 'fn main() { println!("{:?}", longest_streak(&[2, 2, 1, 1, 1])); println!("{:?}", longest_streak(&[1, 1, 2, 2, 2, 1, 1])); }')
            )
          );
        }
      }
      if (includesAny(answer, ["current_val", "final streak", "after the loop"])) {
        results.push(
          candidate(
            "synthesized-current-val-final-check",
            rustFile(`
              fn longest_streak(data: &[i32]) -> (i32, usize) {
                  let mut max_val = data[0];
                  let mut max_count: usize = 1;
                  let mut current_val = data[0];
                  let mut current_count: usize = 1;

                  for i in 1..data.len() {
                      if data[i] == current_val {
                          current_count += 1;
                      } else {
                          if current_count > max_count {
                              max_count = current_count;
                              max_val = current_val;
                          }
                          current_val = data[i];
                          current_count = 1;
                      }
                  }

                  if current_count > max_count {
                      max_count = current_count;
                      max_val = current_val;
                  }

                  (max_val, max_count)
              }

              fn main() {
                  println!("{:?}", longest_streak(&[2, 2, 1, 1, 1]));
                  println!("{:?}", longest_streak(&[1, 1, 2, 2, 2, 1, 1]));
              }
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    default:
      break;
  }

  return results;
}

function buildGoCandidates(scenarioId, answer) {
  const blocks = extractCodeBlocks(answer, ["go", "golang"]);
  const results = [];

  switch (scenarioId) {
    case "BF-05":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("package main") || block.includes("func main")) {
          results.push(candidate(`code-block-${index + 1}`, addGoHarness(block, block)));
        }
      }
      if (includesAny(answer, ["go func(n int)", "}(i)", "i := i"])) {
        results.push(
          candidate(
            "synthesized-loop-param",
            goFile(`
              package main

              import (
                "fmt"
                "sort"
                "sync"
              )

              func main() {
                start := make(chan struct{})
                values := make(chan int, 5)
                var wg sync.WaitGroup

                for i := 0; i < 5; i++ {
                  wg.Add(1)
                  go func(n int) {
                    defer wg.Done()
                    <-start
                    values <- n
                  }(i)
                }

                close(start)
                wg.Wait()
                close(values)

                output := make([]int, 0, 5)
                for value := range values {
                  output = append(output, value)
                }
                sort.Ints(output)
                fmt.Println(output)
              }
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-09":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("filterPositiveAndNegative")) {
          results.push(
            candidate(
              `code-block-${index + 1}`,
              addGoHarness(
                block,
                `
                package main

                import "fmt"

                ${block}

                func main() {
                  nums := []int{3, -1, 4, -5, 2}
                  pos, neg := filterPositiveAndNegative(nums)
                  fmt.Println("Positive:", pos)
                  fmt.Println("Negative:", neg)
                }
                `
              )
            )
          );
        }
      }
      if (includesAny(answer, ["make([]int, 0)", "make([]int,0)", "independent backing array"])) {
        results.push(
          candidate(
            "synthesized-make-slices",
            goFile(`
              package main

              import "fmt"

              func filterPositiveAndNegative(nums []int) ([]int, []int) {
                positive := make([]int, 0)
                negative := make([]int, 0)

                for _, n := range nums {
                  if n > 0 {
                    positive = append(positive, n)
                  } else if n < 0 {
                    negative = append(negative, n)
                  }
                }
                return positive, negative
              }

              func main() {
                nums := []int{3, -1, 4, -5, 2}
                pos, neg := filterPositiveAndNegative(nums)
                fmt.Println("Positive:", pos)
                fmt.Println("Negative:", neg)
              }
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    case "BF-15":
      for (const [index, block] of blocks.entries()) {
        if (block.includes("type Counter")) {
          results.push(candidate(`code-block-${index + 1}`, addGoHarness(block, block)));
        }
      }
      if (includesAny(answer, ["sync.Mutex", "mu.Lock", "mu.Unlock"])) {
        results.push(
          candidate(
            "synthesized-mutex",
            goFile(`
              package main

              import (
                "fmt"
                "sync"
              )

              type Counter struct {
                mu    sync.Mutex
                count int
              }

              func (c *Counter) Increment() {
                c.mu.Lock()
                c.count++
                c.mu.Unlock()
              }

              func (c *Counter) GetCount() int {
                c.mu.Lock()
                defer c.mu.Unlock()
                return c.count
              }

              func main() {
                counter := &Counter{}
                var wg sync.WaitGroup
                for i := 0; i < 1000; i++ {
                  wg.Add(1)
                  go func() {
                    defer wg.Done()
                    counter.Increment()
                  }()
                }
                wg.Wait()
                fmt.Println("Final count:", counter.GetCount())
              }
            `),
            "synthesized_fix"
          )
        );
      }
      if (includesAny(answer, ["atomic.AddInt64", "sync/atomic"])) {
        results.push(
          candidate(
            "synthesized-atomic",
            goFile(`
              package main

              import (
                "fmt"
                "sync"
                "sync/atomic"
              )

              type Counter struct {
                count int64
              }

              func (c *Counter) Increment() {
                atomic.AddInt64(&c.count, 1)
              }

              func (c *Counter) GetCount() int64 {
                return atomic.LoadInt64(&c.count)
              }

              func main() {
                counter := &Counter{}
                var wg sync.WaitGroup
                for i := 0; i < 1000; i++ {
                  wg.Add(1)
                  go func() {
                    defer wg.Done()
                    counter.Increment()
                  }()
                }
                wg.Wait()
                fmt.Println("Final count:", counter.GetCount())
              }
            `),
            "synthesized_fix"
          )
        );
      }
      break;
    default:
      break;
  }

  return results;
}

function uniqueCandidates(candidates) {
  const seen = new Set();
  const unique = [];

  for (const entry of candidates) {
    const key = JSON.stringify(entry.files);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    unique.push(entry);
  }

  return unique;
}

const SOLUTION_TAG_PATTERN = /<solution\b([^>]*)>([\s\S]*?)<\/solution>/gi;
const TRAP_SCENARIOS = new Set(["BF-03", "BF-10"]);

function parseSolutionAttributes(rawAttributes) {
  const attributes = {};
  const attributePattern = /([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  let match = attributePattern.exec(rawAttributes);

  while (match) {
    attributes[match[1].toLowerCase()] = (match[2] ?? match[3] ?? "").trim();
    match = attributePattern.exec(rawAttributes);
  }

  return attributes;
}

function extractSolutionTag(answer) {
  const matches = [...answer.matchAll(SOLUTION_TAG_PATTERN)];

  if (matches.length !== 1) {
    return {
      status: "invalid",
      summary: "Expected exactly one <solution ...>...</solution> block in the final answer."
    };
  }

  const [, rawAttributes = "", rawBody = ""] = matches[0];
  const attributes = parseSolutionAttributes(rawAttributes);
  const language = normalize(attributes.language ?? "");
  const verdict = normalize(attributes.verdict ?? "");
  const body = rawBody.replace(/^\n+|\n+\s*$/g, "");

  if (!language) {
    return {
      status: "invalid",
      summary: 'Missing required attribute language="...".'
    };
  }

  if (verdict !== "fix" && verdict !== "no_bug") {
    return {
      status: "invalid",
      summary: 'Missing or unsupported verdict. Use verdict="fix" or verdict="no_bug".'
    };
  }

  if (verdict === "fix" && body.trim().length === 0) {
    return {
      status: "invalid",
      summary: "A fix verdict requires non-empty code inside the <solution> block."
    };
  }

  if (verdict === "no_bug" && body.trim().length > 0) {
    return {
      status: "invalid",
      summary: 'Use an empty <solution ... verdict="no_bug"></solution> block for trap scenarios.'
    };
  }

  return {
    status: "ok",
    language,
    verdict,
    body
  };
}

function isLanguageCompatible(expectedLanguage, providedLanguage) {
  const aliases = {
    python: ["python", "py"],
    javascript: ["javascript", "js", "node"],
    rust: ["rust", "rs"],
    go: ["go", "golang"]
  };

  return (aliases[expectedLanguage] ?? [expectedLanguage]).includes(providedLanguage);
}

function buildExactPythonCandidate(scenarioId, code) {
  switch (scenarioId) {
    case "BF-01":
      return candidate(
        "tagged-solution",
        appendPythonHarness(
          code,
          `
          import json
          print("__BUGFIND__BF01__" + json.dumps([
              sum_list([1, 2, 3]),
              sum_list([10]),
              sum_list([0, 0, 0]),
              sum_list([-1, 1]),
          ]))
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-01 harness validates multiple sums",
            ["python3", "main.py"],
            markerJsonCheck("__BUGFIND__BF01__", (payload) => {
              assert(Array.isArray(payload), "expected array payload");
              assert(JSON.stringify(payload) === JSON.stringify([6, 10, 0, 0]), `unexpected BF-01 payload ${JSON.stringify(payload)}`);
            })
          )
        ]
      );
    case "BF-04":
      return candidate(
        "tagged-solution",
        appendPythonHarness(
          code,
          `
          import json
          users = {"u1": "active", "u2": "inactive", "u3": "active", "u4": "inactive"}
          result = remove_inactive_users(users)
          if result is None:
              result = users
          print("__BUGFIND__BF04__" + json.dumps(result, sort_keys=True))
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-04 harness accepts in-place or returned dict fix",
            ["python3", "main.py"],
            markerJsonCheck("__BUGFIND__BF04__", (payload) => {
              assert(payload && typeof payload === "object" && !Array.isArray(payload), "expected object payload");
              assert(payload.u1 === "active", "expected u1 to remain active");
              assert(payload.u3 === "active", "expected u3 to remain active");
              assert(!("u2" in payload), "expected u2 to be removed");
              assert(!("u4" in payload), "expected u4 to be removed");
            })
          )
        ]
      );
    case "BF-07":
      return candidate(
        "tagged-solution",
        appendPythonHarness(
          code,
          `
          import json
          print("__BUGFIND__BF07__" + json.dumps([
              add_item("apple"),
              add_item("banana"),
              add_item("cherry"),
          ]))
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-07 harness rejects shared mutable default state",
            ["python3", "main.py"],
            markerJsonCheck("__BUGFIND__BF07__", (payload) => {
              assert(
                JSON.stringify(payload) === JSON.stringify([["apple"], ["banana"], ["cherry"]]),
                `unexpected BF-07 payload ${JSON.stringify(payload)}`
              );
            })
          )
        ]
      );
    case "BF-13":
      return candidate(
        "tagged-solution",
        appendPythonHarness(
          code,
          `
          import json
          users = [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "5"}, {"name": "Charlie", "age": "25"}]
          result = sort_users(users)
          print("__BUGFIND__BF13__" + json.dumps([user["name"] for user in result]))
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-13 harness verifies numeric age ordering",
            ["python3", "main.py"],
            markerJsonCheck("__BUGFIND__BF13__", (payload) => {
              assert(JSON.stringify(payload) === JSON.stringify(["Bob", "Charlie", "Alice"]), `unexpected BF-13 payload ${JSON.stringify(payload)}`);
            })
          )
        ]
      );
    default:
      return candidate("tagged-solution", pythonFile(code), "solution_tag");
  }
}

function buildExactJavascriptCandidate(scenarioId, code) {
  switch (scenarioId) {
    case "BF-02":
      return candidate(
        "tagged-solution",
        appendJavascriptHarness(
          code,
          `
          console.log("__BUGFIND__BF02__" + JSON.stringify([
            validateInput(""),
            validateInput(null),
            validateInput(undefined),
            validateInput("hello"),
            validateInput(0),
          ]));
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-02 harness verifies the empty-string case without over-constraining zero",
            ["node", "main.js"],
            markerJsonCheck("__BUGFIND__BF02__", (payload) => {
              assert(Array.isArray(payload) && payload.length === 5, "expected 5-entry array payload");
              assert(payload[0] === false, 'expected validateInput("") to be false');
              assert(payload[1] === false, "expected validateInput(null) to be false");
              assert(payload[2] === false, "expected validateInput(undefined) to be false");
              assert(payload[3] === true, 'expected validateInput("hello") to be true');
              assert(typeof payload[4] === "boolean", "expected validateInput(0) to return a boolean");
            })
          )
        ]
      );
    case "BF-06":
      return candidate(
        "tagged-solution",
        appendJavascriptHarness(
          code,
          `
          globalThis.fetch = async () => ({ json: async () => ({ name: "Ada" }) });
          getUserName(7)
            .then((name) => console.log("__BUGFIND__BF06__" + JSON.stringify(name)))
            .catch((error) => { console.error(error.toString()); process.exit(1); });
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-06 harness verifies both async steps resolve correctly",
            ["node", "main.js"],
            markerJsonCheck("__BUGFIND__BF06__", (payload) => {
              assert(payload === "Ada", `expected Ada, got ${JSON.stringify(payload)}`);
            })
          )
        ]
      );
    case "BF-11":
      return candidate(
        "tagged-solution",
        appendJavascriptHarness(
          code,
          `
          const signals = [];
          signals.push(applyDiscount(100, 15));

          for (const discount of [110, -5]) {
            try {
              signals.push({ type: "return", value: applyDiscount(50, discount) });
            } catch (error) {
              signals.push({ type: "throw", name: error?.name ?? "Error" });
            }
          }

          console.log("__BUGFIND__BF11__" + JSON.stringify(signals));
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-11 harness rejects silent acceptance of invalid discounts",
            ["node", "main.js"],
            markerJsonCheck("__BUGFIND__BF11__", (payload) => {
              assert(Array.isArray(payload) && payload.length === 3, "expected 3-entry array payload");
              assert(payload[0] === 85, `expected valid discount result 85, got ${JSON.stringify(payload[0])}`);

              for (const invalidResult of payload.slice(1)) {
                if (invalidResult?.type === "return") {
                  assert(invalidResult.value !== 50, "invalid discounts must not silently return the original price");
                } else if (invalidResult?.type === "throw") {
                  assert(typeof invalidResult.name === "string" && invalidResult.name.length > 0, "expected named thrown error");
                } else {
                  throw new Error(`unexpected invalid-discount signal ${JSON.stringify(invalidResult)}`);
                }
              }
            })
          )
        ]
      );
    case "BF-14":
      return candidate(
        "tagged-solution",
        appendJavascriptHarness(
          code,
          `
          console.log("__BUGFIND__BF14__" + JSON.stringify([
            getShippingZone({ id: 1, shipping_address: { city: "New York" } }),
            getShippingZone({ id: 2, shipping_address: { city: "Chicago" } }),
            getShippingZone({ id: 3 }),
            getShippingZone({ id: 4, shipping_address: null }),
            getShippingZone({ id: 5, shipping_address: { city: "Dallas" } }),
          ]));
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-14 harness verifies missing and unknown shipping-address cases",
            ["node", "main.js"],
            markerJsonCheck("__BUGFIND__BF14__", (payload) => {
              assert(
                JSON.stringify(payload) === JSON.stringify(["east", "central", "standard", "standard", "standard"]),
                `unexpected BF-14 payload ${JSON.stringify(payload)}`
              );
            })
          )
        ]
      );
    default:
      return candidate("tagged-solution", javascriptFile(code), "solution_tag");
  }
}

function buildExactRustCandidate(scenarioId, code) {
  switch (scenarioId) {
    case "BF-08":
      return candidate(
        "tagged-solution",
        addRustHarness(code, 'fn main() { println!("__BUGFIND__BF08__20={:?}", factorial(20)); println!("__BUGFIND__BF08__25={:?}", factorial(25)); }'),
        "solution_tag",
        [
          buildCheck(
            "BF-08 harness verifies explicit overflow handling",
            ["rustc", "main.rs", "-C", "overflow-checks=on", "-o", "main"],
            ({ exitCode, stderr }) => {
              assert(exitCode === 0, `expected rust compile success, got ${exitCode}\nstderr:\n${stderr}`);
            }
          ),
          buildCheck(
            "BF-08 runtime accepts correct or explicitly signaled overflow behavior",
            ["./main"],
            ({ exitCode, stdout, stderr }) => {
              const combinedOutput = `${stdout}\n${stderr}`;
              assert(
                exitCode === 0 || /overflow|panicked at/i.test(combinedOutput),
                `expected success or explicit overflow failure, got ${exitCode}\nstdout:\n${stdout}\nstderr:\n${stderr}`
              );
              const lines = normalizeMeaningfulLines(stdout);
              assert(stdout.includes("2432902008176640000"), `expected correct 20! output\nstdout:\n${stdout}`);
              assert(!stdout.includes("7034535277573963776"), `detected silently wrapped 25! output\nstdout:\n${stdout}`);

              const hasMarkerOutput = containsAny(stdout, ["__BUGFIND__BF08__20=", "__BUGFIND__BF08__25="]);
              const finalLine = lines.at(-1) ?? "";
              const acceptsOverflowSignal =
                containsAny(combinedOutput, [
                  "__BUGFIND__BF08__25=None",
                  "__BUGFIND__BF08__25=Some(15511210043330985984000000)",
                  "__BUGFIND__BF08__25=Ok(15511210043330985984000000)",
                  "__BUGFIND__BF08__25=15511210043330985984000000",
                  "__BUGFIND__BF08__25=Some(BigUint",
                  "__BUGFIND__BF08__25=BigUint",
                  "__BUGFIND__BF08__25=Err(",
                  "__BUGFIND__BF08__25=Overflow",
                  "factorial overflow",
                  "attempt to multiply with overflow",
                  "panicked at"
                ]) ||
                [
                  "None",
                  "Some(15511210043330985984000000)",
                  "Ok(15511210043330985984000000)",
                  "15511210043330985984000000"
                ].includes(finalLine) ||
                /^Err\(/.test(finalLine) ||
                /^Overflow/.test(finalLine);
              const explicitOverflowFailure = exitCode !== 0 && /overflow|panicked at/i.test(combinedOutput);

              assert(
                hasMarkerOutput || lines.length >= 2 || explicitOverflowFailure,
                `expected either harness marker output or at least two factorial output lines\nstdout:\n${stdout}`
              );
              assert(
                acceptsOverflowSignal,
                `expected explicit overflow signal or mathematically correct 25! output\nstdout:\n${stdout}`
              );
            }
          )
        ]
      );
    case "BF-12":
      return candidate(
        "tagged-solution",
        addRustHarness(
          code,
          `
          fn main() {
              let case1 = vec![2, 2, 1, 1, 1];
              let case2 = vec![1, 1, 1, 2, 2];
              let case3 = vec![5];
              let case4 = vec![3, 3, 3];
              let case5 = vec![1, 1, 2, 2, 2, 1, 1];
              println!("__BUGFIND__BF12__{:?}", longest_streak(&case1));
              println!("__BUGFIND__BF12__{:?}", longest_streak(&case2));
              println!("__BUGFIND__BF12__{:?}", longest_streak(&case3));
              println!("__BUGFIND__BF12__{:?}", longest_streak(&case4));
              println!("__BUGFIND__BF12__{:?}", longest_streak(&case5));
          }
          `
        ),
        "solution_tag",
        [
          buildCheck(
            "BF-12 harness compiles the submitted streak fix",
            ["rustc", "main.rs", "-o", "main"],
            ({ exitCode, stderr }) => {
              assert(exitCode === 0, `expected rust compile success, got ${exitCode}\nstderr:\n${stderr}`);
            }
          ),
          buildCheck(
            "BF-12 harness validates all streak cases",
            ["./main"],
            ({ exitCode, stdout, stderr }) => {
              assert(exitCode === 0, `expected exit code 0, got ${exitCode}\nstderr:\n${stderr}`);
              const lines = stdout
                .split("\n")
                .map((line) => line.trim())
                .filter((line) => line.startsWith("__BUGFIND__BF12__"))
                .map((line) => line.slice("__BUGFIND__BF12__".length));
              const expected = ["(1, 3)", "(1, 3)", "(5, 1)", "(3, 3)", "(2, 3)"];
              assert(JSON.stringify(lines) === JSON.stringify(expected), `unexpected BF-12 results ${JSON.stringify(lines)}`);
            }
          )
        ]
      );
    default:
      return candidate("tagged-solution", rustFile(code), "solution_tag");
  }
}

function buildExactGoCandidate(scenarioId, code) {
  switch (scenarioId) {
    case "BF-05":
      return candidate(
        "tagged-solution",
        code.includes("package main") ? goFile(code) : goFile(`package main\n\n${dedent(code)}\n`),
        "solution_tag",
        [
          buildCheck("BF-05 harness verifies goroutines print each loop value exactly once", ["go", "run", "main.go"], exactNumberSetCheck([0, 1, 2, 3, 4]))
        ]
      );
    case "BF-09":
      return candidate(
        "tagged-solution",
        code.includes("package")
          ? {
              "main.go": `${stripUnusedGoFmtImport(code)}\n`,
              "main_test.go": `${dedent(`
                package main

                import (
                  "reflect"
                  "sort"
                  "testing"
                )

                func sortedCopy(values []int) []int {
                  copyValues := append([]int(nil), values...)
                  sort.Ints(copyValues)
                  return copyValues
                }

                func TestFilterPositiveAndNegative(t *testing.T) {
                  nums := []int{3, -1, 4, -5, 2}
                  pos, neg := filterPositiveAndNegative(nums)

                  if !reflect.DeepEqual(sortedCopy(pos), []int{2, 3, 4}) {
                    t.Fatalf("unexpected positive slice: %#v", pos)
                  }

                  if !reflect.DeepEqual(sortedCopy(neg), []int{-5, -1}) {
                    t.Fatalf("unexpected negative slice: %#v", neg)
                  }

                  if !reflect.DeepEqual(nums, []int{3, -1, 4, -5, 2}) {
                    t.Fatalf("input slice should remain unchanged: %#v", nums)
                  }
                }
              `)}\n`
            }
          : {
              "main.go": `package main\n\n${stripUnusedGoFmtImport(code)}\n`,
              "main_test.go": `${dedent(`
                package main

                import (
                  "reflect"
                  "sort"
                  "testing"
                )

                func sortedCopy(values []int) []int {
                  copyValues := append([]int(nil), values...)
                  sort.Ints(copyValues)
                  return copyValues
                }

                func TestFilterPositiveAndNegative(t *testing.T) {
                  nums := []int{3, -1, 4, -5, 2}
                  pos, neg := filterPositiveAndNegative(nums)

                  if !reflect.DeepEqual(sortedCopy(pos), []int{2, 3, 4}) {
                    t.Fatalf("unexpected positive slice: %#v", pos)
                  }

                  if !reflect.DeepEqual(sortedCopy(neg), []int{-5, -1}) {
                    t.Fatalf("unexpected negative slice: %#v", neg)
                  }

                  if !reflect.DeepEqual(nums, []int{3, -1, 4, -5, 2}) {
                    t.Fatalf("input slice should remain unchanged: %#v", nums)
                  }
                }
              `)}\n`
            },
        "solution_tag",
        [buildCheck("BF-09 harness runs slice-aliasing test cases", ["go", "test", "main.go", "main_test.go"], ({ exitCode, stderr, stdout }) => {
          assert(exitCode === 0, `expected go test success, got ${exitCode}\nstdout:\n${stdout}\nstderr:\n${stderr}`);
        })]
      );
    case "BF-15":
      return candidate(
        "tagged-solution",
        code.includes("package main")
          ? goFile(code)
          : code.includes("func main()")
            ? goFile(`package main\n\n${dedent(code)}\n`)
            : goFile(`
                package main

                import (
                  "fmt"
                  "sync"
                )

                ${code}

                func main() {
                  counter := &Counter{}
                  var wg sync.WaitGroup

                  for i := 0; i < 1000; i++ {
                    wg.Add(1)
                    go func() {
                      defer wg.Done()
                      counter.Increment()
                    }()
                  }

                  wg.Wait()
                  fmt.Println("Final count:", counter.GetCount())
                }
              `),
        "solution_tag",
        [
          buildCheck(
            "BF-15 harness uses the Go race detector and final-count check",
            ["go", "run", "-race", "main.go"],
            ({ exitCode, stderr, stdout }) => {
              assert(exitCode === 0, `expected zero exit code, got ${exitCode}\nstdout:\n${stdout}\nstderr:\n${stderr}`);
              assert(stdout.includes("Final count: 1000"), `expected final count 1000\nstdout:\n${stdout}`);
              assert(!stderr.includes("DATA RACE"), `did not expect race detector output\nstderr:\n${stderr}`);
            },
            20000
          )
        ]
      );
    default:
      return candidate("tagged-solution", goFile(code), "solution_tag");
  }
}

function buildTaggedSolutionCandidate(scenario, code) {
  switch (scenario.language) {
    case "python":
      return buildExactPythonCandidate(scenario.id, code);
    case "javascript":
      return buildExactJavascriptCandidate(scenario.id, code);
    case "rust":
      return buildExactRustCandidate(scenario.id, code);
    case "go":
      return buildExactGoCandidate(scenario.id, code);
    default:
      throw new Error(`Unsupported scenario language "${scenario.language}".`);
  }
}

export function parseSolutionSubmission(scenario, answer) {
  const solution = extractSolutionTag(answer);

  if (solution.status !== "ok") {
    return solution;
  }

  if (!isLanguageCompatible(scenario.language, solution.language)) {
    return {
      status: "invalid",
      summary: `Solution language "${solution.language}" does not match scenario language "${scenario.language}".`
    };
  }

  if (solution.verdict === "no_bug") {
    if (!TRAP_SCENARIOS.has(scenario.id)) {
      return {
        status: "invalid",
        summary: 'verdict="no_bug" is only valid for trap scenarios.'
      };
    }

    return {
      status: "ok",
      verdict: "no_bug",
      candidates: [candidate("tagged-no-bug", scenario.fixed.files, "solution_tag_no_bug")]
    };
  }

  if (TRAP_SCENARIOS.has(scenario.id)) {
    return {
      status: "invalid",
      summary: 'Trap scenarios must use verdict="no_bug" with an empty solution block.'
    };
  }

  return {
    status: "ok",
    verdict: "fix",
    candidates: [buildTaggedSolutionCandidate(scenario, solution.body)]
  };
}

export function buildAnswerCandidates(scenario, answer) {
  const candidates = [
    ...buildPythonCandidates(scenario.id, answer),
    ...buildJavascriptCandidates(scenario.id, answer),
    ...buildRustCandidates(scenario.id, answer),
    ...buildGoCandidates(scenario.id, answer)
  ];

  return uniqueCandidates(candidates);
}
