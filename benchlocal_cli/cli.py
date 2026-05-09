"""CLI entry point — `benchlocal-cli ...`

TODO (Codex): implement argument parsing + command dispatch.
Target UX (subject to refinement):

    benchlocal-cli list
        → list all available packs with version + scoring type

    benchlocal-cli run --quick --endpoint URL --model NAME
        → run quick mode (ToolCall-15 + InstructFollow-15)

    benchlocal-cli run --medium --endpoint URL --model NAME [--repeat N]
        → run medium mode (quick + StructOutput-15 + DataExtract-15)

    benchlocal-cli run --full --endpoint URL --model NAME
        → run full mode (medium + ReasonMath-15 + warn-skip for stubbed packs)

    benchlocal-cli run --pack PACK_ID --endpoint URL --model NAME
        → run a single named pack (ignores mode flag)

    benchlocal-cli run ... --output {markdown,json}
        → output format (default: markdown to stdout)

    benchlocal-cli run ... --timeout-per-case SECONDS
        → per-scenario HTTP timeout (default: 60)

    benchlocal-cli run ... --previous-result PATH
        → compare against a previous result JSON, emit delta column

See docs/DESIGN.md for the rationale behind these choices.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns process exit code."""
    raise NotImplementedError(
        "benchlocal-cli is pre-alpha. Implementation in flight — see docs/DESIGN.md "
        "for the design and the GitHub project board for status."
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
