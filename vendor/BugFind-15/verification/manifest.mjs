function dedent(source) {
  const text = source.replace(/^\n+|\n+\s*$/g, "");
  const lines = text.split("\n");
  const indents = lines
    .filter((line) => line.trim().length > 0)
    .map((line) => line.match(/^(\s*)/)?.[1].length ?? 0);
  const margin = indents.length === 0 ? 0 : Math.min(...indents);

  return lines.map((line) => line.slice(margin)).join("\n");
}

function pythonScript(source) {
  return {
    "main.py": `${dedent(source)}\n`
  };
}

function javascriptScript(source) {
  return {
    "main.js": `${dedent(source)}\n`
  };
}

function rustScript(source) {
  return {
    "main.rs": `${dedent(source)}\n`
  };
}

function goScript(source) {
  return {
    "main.go": `${dedent(source)}\n`
  };
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function exactStdout(expected) {
  return ({ exitCode, stdout, stderr }) => {
    assert(exitCode === 0, `expected exit code 0, got ${exitCode}\nstderr:\n${stderr}`);
    assert(stdout === expected, `expected stdout:\n${expected}\nactual stdout:\n${stdout}`);
  };
}

function stderrIncludes(expected, expectedExitCode = 1) {
  return ({ exitCode, stderr, stdout }) => {
    assert(exitCode === expectedExitCode, `expected exit code ${expectedExitCode}, got ${exitCode}\nstdout:\n${stdout}`);
    assert(stderr.includes(expected), `expected stderr to include "${expected}"\nactual stderr:\n${stderr}`);
  };
}

function stdoutLinesEqual(expectedLines) {
  const expected = `${expectedLines.join("\n")}\n`;
  return exactStdout(expected);
}

function sortedNumberLines(expectedNumbers) {
  return ({ exitCode, stdout, stderr }) => {
    assert(exitCode === 0, `expected exit code 0, got ${exitCode}\nstderr:\n${stderr}`);
    const numbers = stdout
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .map((value) => Number.parseInt(value, 10))
      .sort((left, right) => left - right);
    assert(JSON.stringify(numbers) === JSON.stringify(expectedNumbers), `unexpected sorted number output: ${JSON.stringify(numbers)}`);
  };
}

function stdoutIncludesAll(expectedParts) {
  return ({ exitCode, stdout, stderr }) => {
    assert(exitCode === 0, `expected exit code 0, got ${exitCode}\nstderr:\n${stderr}`);
    for (const part of expectedParts) {
      assert(stdout.includes(part), `expected stdout to include "${part}"\nactual stdout:\n${stdout}`);
    }
  };
}

export const SCENARIOS = [
  {
    id: "BF-01",
    language: "python",
    buggy: {
      files: pythonScript(`
        def sum_list(numbers):
            total = 0
            for i in range(1, len(numbers) + 1):
                total += numbers[i]
            return total

        print(sum_list([10]))
      `),
      checks: [
        {
          name: "buggy raises IndexError",
          command: ["python3", "main.py"],
          validate: stderrIncludes("IndexError: list index out of range")
        }
      ]
    },
    fixed: {
      files: pythonScript(`
        def sum_list(numbers):
            total = 0
            for num in numbers:
                total += num
            return total

        print(sum_list([1, 2, 3]))
        print(sum_list([10]))
      `),
      checks: [
        {
          name: "fixed sums correctly",
          command: ["python3", "main.py"],
          validate: stdoutLinesEqual(["6", "10"])
        }
      ]
    }
  },
  {
    id: "BF-02",
    language: "javascript",
    buggy: {
      files: javascriptScript(`
        function validateInput(input) {
          if (input !== null && input !== undefined && input !== false) {
            return true;
          }
          return false;
        }

        console.log(String(validateInput("")));
        console.log(String(validateInput(null)));
        console.log(String(validateInput(undefined)));
        console.log(String(validateInput("hello")));
      `),
      checks: [
        {
          name: "buggy accepts empty string",
          command: ["node", "main.js"],
          validate: stdoutLinesEqual(["true", "false", "false", "true"])
        }
      ]
    },
    fixed: {
      files: javascriptScript(`
        function validateInput(input) {
          return Boolean(input);
        }

        console.log(String(validateInput("")));
        console.log(String(validateInput(null)));
        console.log(String(validateInput(undefined)));
        console.log(String(validateInput("hello")));
      `),
      checks: [
        {
          name: "fixed rejects empty string",
          command: ["node", "main.js"],
          validate: stdoutLinesEqual(["false", "false", "false", "true"])
        }
      ]
    }
  },
  {
    id: "BF-03",
    language: "rust",
    buggy: {
      files: rustScript(`
        fn main() {
            let name = String::from("Alice");
            let greeting = format!("Hello, {}", name);
            println!("{}", greeting);
            println!("Name was: {}", name);
        }
      `),
      checks: [
        {
          name: "trap code compiles",
          command: ["rustc", "main.rs", "-o", "main"],
          validate: ({ exitCode, stderr }) => {
            assert(exitCode === 0, `expected compile success, got ${exitCode}\nstderr:\n${stderr}`);
          }
        },
        {
          name: "trap code runs",
          command: ["./main"],
          validate: stdoutIncludesAll(["Hello, Alice", "Name was: Alice"])
        }
      ]
    },
    fixed: {
      files: rustScript(`
        fn main() {
            let name = String::from("Alice");
            let greeting = format!("Hello, {}", name);
            println!("{}", greeting);
            println!("Name was: {}", name);
        }
      `),
      checks: [
        {
          name: "canonical fixed variant stays unchanged",
          command: ["rustc", "main.rs", "-o", "main"],
          validate: ({ exitCode, stderr }) => {
            assert(exitCode === 0, `expected compile success, got ${exitCode}\nstderr:\n${stderr}`);
          }
        },
        {
          name: "canonical fixed variant runs",
          command: ["./main"],
          validate: stdoutIncludesAll(["Hello, Alice", "Name was: Alice"])
        }
      ]
    }
  },
  {
    id: "BF-04",
    language: "python",
    buggy: {
      files: pythonScript(`
        def remove_inactive_users(users):
            for user_id, status in users.items():
                if status == "inactive":
                    del users[user_id]
            return users

        users = {"u1": "active", "u2": "inactive", "u3": "active", "u4": "inactive"}
        print(remove_inactive_users(users))
      `),
      checks: [
        {
          name: "buggy mutates during iteration",
          command: ["python3", "main.py"],
          validate: stderrIncludes("RuntimeError: dictionary changed size during iteration")
        }
      ]
    },
    fixed: {
      files: pythonScript(`
        def remove_inactive_users(users):
            return {uid: status for uid, status in users.items() if status != "inactive"}

        users = {"u1": "active", "u2": "inactive", "u3": "active", "u4": "inactive"}
        print(remove_inactive_users(users))
      `),
      checks: [
        {
          name: "fixed removes inactive users",
          command: ["python3", "main.py"],
          validate: exactStdout("{'u1': 'active', 'u3': 'active'}\n")
        }
      ]
    }
  },
  {
    id: "BF-05",
    language: "go",
    buggy: {
      files: goScript(`
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
            go func() {
              defer wg.Done()
              <-start
              values <- i
            }()
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
      checks: [
        {
          name: "buggy captures loop variable under Go 1.21 semantics",
          command: ["go", "run", "main.go"],
          validate: exactStdout("[5 5 5 5 5]\n")
        }
      ]
    },
    fixed: {
      files: goScript(`
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
      checks: [
        {
          name: "fixed passes loop variable explicitly",
          command: ["go", "run", "main.go"],
          validate: exactStdout("[0 1 2 3 4]\n")
        }
      ]
    }
  },
  {
    id: "BF-06",
    language: "javascript",
    buggy: {
      files: javascriptScript(`
        globalThis.fetch = async () => ({
          json: async () => ({ name: "Ada" })
        });

        async function getUserName(userId) {
          const response = fetch(\`/api/users/\${userId}\`);
          const data = response.json();
          return data.name;
        }

        getUserName(7)
          .then((name) => {
            console.log(name);
          })
          .catch((error) => {
            console.error(error.toString());
            process.exit(1);
          });
      `),
      checks: [
        {
          name: "buggy misses await",
          command: ["node", "main.js"],
          validate: stderrIncludes("TypeError: response.json is not a function")
        }
      ]
    },
    fixed: {
      files: javascriptScript(`
        globalThis.fetch = async () => ({
          json: async () => ({ name: "Ada" })
        });

        async function getUserName(userId) {
          const response = await fetch(\`/api/users/\${userId}\`);
          const data = await response.json();
          return data.name;
        }

        getUserName(7)
          .then((name) => {
            console.log(name);
          })
          .catch((error) => {
            console.error(error.toString());
            process.exit(1);
          });
      `),
      checks: [
        {
          name: "fixed awaits both async steps",
          command: ["node", "main.js"],
          validate: exactStdout("Ada\n")
        }
      ]
    }
  },
  {
    id: "BF-07",
    language: "python",
    buggy: {
      files: pythonScript(`
        def add_item(item, item_list=[]):
            item_list.append(item)
            return item_list

        print(add_item("apple"))
        print(add_item("banana"))
        print(add_item("cherry"))
      `),
      checks: [
        {
          name: "buggy shares mutable default",
          command: ["python3", "main.py"],
          validate: stdoutLinesEqual(["['apple']", "['apple', 'banana']", "['apple', 'banana', 'cherry']"])
        }
      ]
    },
    fixed: {
      files: pythonScript(`
        def add_item(item, item_list=None):
            if item_list is None:
                item_list = []
            item_list.append(item)
            return item_list

        print(add_item("apple"))
        print(add_item("banana"))
        print(add_item("cherry"))
      `),
      checks: [
        {
          name: "fixed uses None sentinel",
          command: ["python3", "main.py"],
          validate: stdoutLinesEqual(["['apple']", "['banana']", "['cherry']"])
        }
      ]
    }
  },
  {
    id: "BF-08",
    language: "rust",
    buggy: {
      files: rustScript(`
        fn factorial(n: u64) -> u64 {
            let mut result: u64 = 1;
            for i in 1..=n {
                result *= i;
            }
            result
        }

        fn main() {
            println!("{}", factorial(20));
            println!("{}", factorial(25));
        }
      `),
      checks: [
        {
          name: "buggy debug build compiles",
          command: ["rustc", "main.rs", "-C", "overflow-checks=on", "-o", "main-debug"],
          validate: ({ exitCode, stderr }) => {
            assert(exitCode === 0, `expected debug compile success, got ${exitCode}\nstderr:\n${stderr}`);
          }
        },
        {
          name: "buggy debug run panics on overflow",
          command: ["./main-debug"],
          validate: stderrIncludes("attempt to multiply with overflow", 101)
        },
        {
          name: "buggy release build compiles",
          command: ["rustc", "main.rs", "-C", "overflow-checks=off", "-O", "-o", "main-release"],
          validate: ({ exitCode, stderr }) => {
            assert(exitCode === 0, `expected release compile success, got ${exitCode}\nstderr:\n${stderr}`);
          }
        },
        {
          name: "buggy release run wraps silently",
          command: ["./main-release"],
          validate: stdoutLinesEqual(["2432902008176640000", "7034535277573963776"])
        }
      ]
    },
    fixed: {
      files: rustScript(`
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
      checks: [
        {
          name: "fixed debug build compiles",
          command: ["rustc", "main.rs", "-C", "overflow-checks=on", "-o", "main"],
          validate: ({ exitCode, stderr }) => {
            assert(exitCode === 0, `expected compile success, got ${exitCode}\nstderr:\n${stderr}`);
          }
        },
        {
          name: "fixed returns explicit overflow signal",
          command: ["./main"],
          validate: stdoutLinesEqual(["Some(2432902008176640000)", "None"])
        }
      ]
    }
  },
  {
    id: "BF-09",
    language: "go",
    buggy: {
      files: goScript(`
        package main

        import "fmt"

        func filterPositiveAndNegative(nums []int) ([]int, []int) {
          positive := nums[:0]
          negative := nums[:0]

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
      checks: [
        {
          name: "buggy aliases backing array",
          command: ["go", "run", "main.go"],
          validate: stdoutLinesEqual(["Positive: [-1 -5 2]", "Negative: [-1 -5]"])
        }
      ]
    },
    fixed: {
      files: goScript(`
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
      checks: [
        {
          name: "fixed allocates independent slices",
          command: ["go", "run", "main.go"],
          validate: stdoutLinesEqual(["Positive: [3 4 2]", "Negative: [-1 -5]"])
        }
      ]
    }
  },
  {
    id: "BF-10",
    language: "python",
    buggy: {
      files: pythonScript(`
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
      checks: [
        {
          name: "trap code is already correct",
          command: ["python3", "main.py"],
          validate: exactStdout("['Hello', 'World']\n")
        }
      ]
    },
    fixed: {
      files: pythonScript(`
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
      checks: [
        {
          name: "canonical fixed trap variant stays unchanged",
          command: ["python3", "main.py"],
          validate: exactStdout("['Hello', 'World']\n")
        }
      ]
    }
  },
  {
    id: "BF-11",
    language: "javascript",
    buggy: {
      files: javascriptScript(`
        function applyDiscount(price, discountPercent) {
          if (discountPercent < 0 || discountPercent > 100) {
            return price;
          }
          const discounted = price * (1 - discountPercent / 100);
          return Math.round(discounted * 100) / 100;
        }

        console.log(applyDiscount(100, 15));
        console.log(applyDiscount(50, 110));
        console.log(applyDiscount(50, -5));
      `),
      checks: [
        {
          name: "buggy silently returns original price",
          command: ["node", "main.js"],
          validate: stdoutLinesEqual(["85", "50", "50"])
        }
      ]
    },
    fixed: {
      files: javascriptScript(`
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
      checks: [
        {
          name: "fixed makes invalid input explicit",
          command: ["node", "main.js"],
          validate: stdoutLinesEqual(["85", "RangeError", "RangeError"])
        }
      ]
    }
  },
  {
    id: "BF-12",
    language: "rust",
    buggy: {
      files: rustScript(`
        fn longest_streak(data: &Vec<i32>) -> (i32, usize) {
            let mut max_val = data[0];
            let mut max_count: usize = 1;
            let mut current_count: usize = 1;

            for i in 1..data.len() {
                if data[i] == max_val {
                    current_count += 1;
                } else if current_count > max_count {
                    max_count = current_count;
                    max_val = data[i - 1];
                    current_count = 1;
                } else {
                    current_count = 1;
                }
            }
            (max_val, max_count)
        }

        fn main() {
            println!("{:?}", longest_streak(&vec![2, 2, 1, 1, 1]));
        }
      `),
      checks: [
        {
          name: "buggy misses final streak",
          command: ["rustc", "main.rs", "-o", "main"],
          validate: ({ exitCode, stderr }) => {
            assert(exitCode === 0, `expected compile success, got ${exitCode}\nstderr:\n${stderr}`);
          }
        },
        {
          name: "buggy returns wrong streak",
          command: ["./main"],
          validate: exactStdout("(2, 2)\n")
        }
      ]
    },
    fixed: {
      files: rustScript(`
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
      checks: [
        {
          name: "fixed compile succeeds",
          command: ["rustc", "main.rs", "-o", "main"],
          validate: ({ exitCode, stderr }) => {
            assert(exitCode === 0, `expected compile success, got ${exitCode}\nstderr:\n${stderr}`);
          }
        },
        {
          name: "fixed handles both streak edge cases",
          command: ["./main"],
          validate: stdoutLinesEqual(["(1, 3)", "(2, 3)"])
        }
      ]
    }
  },
  {
    id: "BF-13",
    language: "python",
    buggy: {
      files: pythonScript(`
        def sort_users(users):
            return sorted(users, key=lambda u: u["age"])

        users = [
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "5"},
            {"name": "Charlie", "age": "25"},
        ]

        print([user["name"] for user in sort_users(users)])
      `),
      checks: [
        {
          name: "buggy sorts lexicographically",
          command: ["python3", "main.py"],
          validate: exactStdout("['Charlie', 'Alice', 'Bob']\n")
        }
      ]
    },
    fixed: {
      files: pythonScript(`
        def sort_users(users):
            return sorted(users, key=lambda u: int(u["age"]))

        users = [
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "5"},
            {"name": "Charlie", "age": "25"},
        ]

        print([user["name"] for user in sort_users(users)])
      `),
      checks: [
        {
          name: "fixed sorts numerically",
          command: ["python3", "main.py"],
          validate: exactStdout("['Bob', 'Charlie', 'Alice']\n")
        }
      ]
    }
  },
  {
    id: "BF-14",
    language: "javascript",
    buggy: {
      files: javascriptScript(`
        function getShippingZone(order) {
          const city = order.shipping_address.city;
          const zones = {
            "New York": "east",
            "Los Angeles": "west",
            "Chicago": "central"
          };
          return zones[city] || "standard";
        }

        try {
          console.log(getShippingZone({ id: 123 }));
        } catch (error) {
          console.error(error.toString());
          process.exit(1);
        }
      `),
      checks: [
        {
          name: "buggy crashes on missing shipping_address",
          command: ["node", "main.js"],
          validate: stderrIncludes("Cannot read properties of undefined")
        }
      ]
    },
    fixed: {
      files: javascriptScript(`
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
      checks: [
        {
          name: "fixed handles missing address",
          command: ["node", "main.js"],
          validate: stdoutLinesEqual(["standard", "east"])
        }
      ]
    }
  },
  {
    id: "BF-15",
    language: "go",
    buggy: {
      files: goScript(`
        package main

        import (
          "fmt"
          "sync"
        )

        type Counter struct {
          count int
        }

        func (c *Counter) Increment() {
          c.count++
        }

        func (c *Counter) GetCount() int {
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
      checks: [
        {
          name: "buggy trips the Go race detector",
          command: ["go", "run", "-race", "main.go"],
          validate: ({ exitCode, stderr }) => {
            assert(exitCode !== 0, `expected non-zero exit from race detector, got ${exitCode}`);
            assert(stderr.includes("DATA RACE"), `expected race detector output\nstderr:\n${stderr}`);
          },
          timeoutMs: 20000
        }
      ]
    },
    fixed: {
      files: goScript(`
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
      checks: [
        {
          name: "fixed passes under race detector",
          command: ["go", "run", "-race", "main.go"],
          validate: ({ exitCode, stderr, stdout }) => {
            assert(exitCode === 0, `expected zero exit code, got ${exitCode}\nstderr:\n${stderr}`);
            assert(stdout.includes("Final count: 1000"), `expected final count 1000\nstdout:\n${stdout}`);
            assert(!stderr.includes("DATA RACE"), `did not expect race detector output\nstderr:\n${stderr}`);
          },
          timeoutMs: 20000
        }
      ]
    }
  }
];

export function getScenarioById(id) {
  return SCENARIOS.find((scenario) => scenario.id === id) ?? null;
}
