# Changelog

## 0.7.1

- Add runner-side multi-turn sandbox orchestration for CLI-40 multi-round scenarios and HermesAgent-20.
- Generalize sandbox client multi-turn methods while keeping Hermes aliases for compatibility.
- Add CLI-40 `/verify-start`, `/verify-turn`, and `/verify-end` endpoints with iterative bash feedback and upstream replay grading.
- Persist multi-turn diagnostics in scenario results: turn count, assistant messages, and tool calls.
- Mark sandbox health endpoints as `stage="v0.7.1"`.

## 0.7.0

- Vendor upstream `verification/` runtimes for BugFind-15, CLI-40, and HermesAgent-20.
- Delegate BugFind verification to upstream `verifyAnswer`, with runtime support for Python, Node, Go, and Rust checks.
- Delegate CLI one-shot and replay verification to upstream verifier functions, and relax scripting-language bans to match the upstream execution model.
- Copy vendored verification runtimes into Docker build contexts during `tools/build-sandboxes.sh`.
- Mark sandbox health endpoints as `stage="v0.7"` and document the remaining Hermes runner-integration gap.

## 0.6.0

- Add v0.6 sandbox verifier implementations for BugFind, CLI, and HermesAgent using upstream-derived raw scenario metadata, deterministic rubric checks, safe command execution, and stateful mocked-tool tracing.
