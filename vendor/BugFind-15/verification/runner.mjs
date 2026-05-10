import { runCanonicalVariants } from "./core.mjs";
import { SCENARIOS } from "./manifest.mjs";

function parseArgs(argv) {
  const parsed = {
    scenarioIds: [],
    variant: "all",
    json: false,
    list: false
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];

    if (arg === "--scenario" || arg === "-s") {
      parsed.scenarioIds.push(argv[index + 1]);
      index += 1;
      continue;
    }

    if (arg === "--variant" || arg === "-v") {
      parsed.variant = argv[index + 1] ?? "all";
      index += 1;
      continue;
    }

    if (arg === "--json") {
      parsed.json = true;
      continue;
    }

    if (arg === "--list") {
      parsed.list = true;
      continue;
    }
  }

  return parsed;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.list) {
    for (const scenario of SCENARIOS) {
      console.log(`${scenario.id} (${scenario.language})`);
    }
    return;
  }

  const summary = await runCanonicalVariants({
    scenarioIds: args.scenarioIds,
    variant: args.variant
  });

  if (args.json) {
    console.log(JSON.stringify(summary, null, 2));
  } else {
    for (const result of summary.results) {
      console.log(`${result.status === "passed" ? "PASS" : "FAIL"} ${result.id} (${result.language})`);
      for (const variant of result.variants) {
        console.log(`  ${variant.status === "passed" ? "PASS" : "FAIL"} ${variant.variant}`);
        for (const check of variant.checks) {
          console.log(`    ${check.status === "passed" ? "PASS" : "FAIL"} ${check.name}`);
          if (check.status === "failed") {
            console.log(`      command: ${check.command}`);
            console.log(`      error: ${check.error}`);
          }
        }
      }
    }
  }

  if (summary.status === "failed") {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
