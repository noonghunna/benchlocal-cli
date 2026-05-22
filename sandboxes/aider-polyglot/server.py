"""Aider Polyglot lite verifier server — v0.9.0 single-scoreboard architecture.

One /verify-start call → spawn upstream's `benchmark.py` once → grade
aggregate. Per-exercise breakdown lives in
`verifier_trace.upstream_per_exercise`. No cache, no batch protocol,
no scenario fan-out — there is exactly 1 scenario per pack
(`aider-polyglot-30-batch`).

Failure modes (preserved from prior packs for back-compat):
- passed                         — pass_rate >= threshold
- verifier_fail                  — pass_rate < threshold (real fail)
- agent_runner_timeout           — benchmark.py exceeded wall-clock cap
- agent_runner_crashed           — nonzero exit; stderr in detail
- result_json_malformed          — couldn't parse upstream result file
- model_endpoint_unreachable     — aider reported network error to model
- server_error                   — server-side bug or missing setup
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 9000

# Schema bumped in v0.9.0 because new `pass_rate`/`passed_count`/`total_count`
# fields are first-class on the ScenarioResult. Existing v0.8.x readers that
# don't know about these fields will see them ignored gracefully.
SCHEMA_VERSION = "3"

# Where the upstream Aider source + polyglot-benchmark live (set by Dockerfile).
AIDER_DIR = Path(os.environ.get("AIDER_DIR", "/aider"))
POLYGLOT_DIR = Path(os.environ.get("POLYGLOT_DIR", "/polyglot-benchmark"))
BENCHMARK_PY = AIDER_DIR / "benchmark" / "benchmark.py"
# Default to the in-container path; fall back to the vendor source-of-truth
# when running outside Docker (tests, dev iteration). Codex review #5: the
# canonical list lives in vendor/; sandbox build copies it to /app/.
def _default_exercises_manifest() -> Path:
    in_container = Path("/app/exercises.json")
    if in_container.is_file():
        return in_container
    # Test/dev fallback: walk up from this file to the repo root, look for
    # vendor/AiderPolyglot-30/exercises.json
    here = Path(__file__).resolve()
    for parent in (here.parent.parent.parent, here.parent.parent):
        candidate = parent / "vendor" / "AiderPolyglot-30" / "exercises.json"
        if candidate.is_file():
            return candidate
    return in_container  # original; will fail at startup with clear message


EXERCISES_MANIFEST = Path(os.environ.get("EXERCISES_MANIFEST") or _default_exercises_manifest())

AIDER_PINNED_COMMIT = os.environ.get("AIDER_PINNED_COMMIT", "unknown")
POLYGLOT_PINNED_COMMIT = os.environ.get("POLYGLOT_PINNED_COMMIT", "unknown")

# Wall-clock cap for the entire batch. The runner-side per-pack timeout
# (SandboxConfig.request_timeout_s) is 3000; this is the inner subprocess cap.
SUBPROCESS_TIMEOUT_S = float(os.environ.get("AIDER_BENCHMARK_TIMEOUT_S", "2700"))

# Per-pack threshold: pass if >= this fraction of exercises pass.
DEFAULT_PASS_THRESHOLD = 0.5
_LITELLM_PROVIDERS = {
    "openai",
    "anthropic",
    "azure",
    "vertex_ai",
    "gemini",
    "huggingface",
    "together_ai",
    "openrouter",
    "bedrock",
    "ollama",
}

# Default edit format. `whole` is the broadest model-compat choice; the
# runner can override via raw_scenario / sampling_overrides.
DEFAULT_EDIT_FORMAT = "whole"


# ============================================================================
# Exercise list — canonical 30 from vendor/AiderPolyglot-30/exercises.json
# Loaded at startup (Codex 2nd-pass #5: exact-id match, not count).
# ============================================================================

def _load_canonical_exercises() -> list[dict]:
    if not EXERCISES_MANIFEST.is_file():
        sys.stderr.write(f"[aider-polyglot] FATAL: exercises manifest not found at {EXERCISES_MANIFEST}\n")
        sys.exit(1)
    data = json.loads(EXERCISES_MANIFEST.read_text(encoding="utf-8"))
    exercises = data.get("exercises") or []
    if not isinstance(exercises, list) or not exercises:
        sys.stderr.write(f"[aider-polyglot] FATAL: exercises manifest is empty or malformed\n")
        sys.exit(1)
    return exercises


CANONICAL_EXERCISES = _load_canonical_exercises()
CANONICAL_KEYS = sorted({(e["language"], e["name"]) for e in CANONICAL_EXERCISES})


def _resolve_exercise_dirs() -> tuple[list[Path], list[tuple[str, str]]]:
    """Walk POLYGLOT_DIR and return (resolved Path list, missing list).
    Per Codex 2nd-pass #5: assert resolved set == canonical set EXACTLY,
    not just count. Substring keyword collisions could otherwise pick 30
    wrong exercises and look correct."""
    resolved: list[Path] = []
    missing: list[tuple[str, str]] = []
    for entry in CANONICAL_EXERCISES:
        path = POLYGLOT_DIR / entry["language"] / "exercises" / "practice" / entry["name"]
        if path.is_dir():
            resolved.append(path)
        else:
            missing.append((entry["language"], entry["name"]))
    return resolved, missing


def _exercise_count_status() -> dict:
    """Surface exact-match resolution state in /health + verifier_trace."""
    resolved, missing = _resolve_exercise_dirs()
    return {
        "canonical_count": len(CANONICAL_EXERCISES),
        "resolved_count": len(resolved),
        "missing": missing,
        "exact_match": len(missing) == 0 and len(resolved) == len(CANONICAL_EXERCISES),
    }


# ============================================================================
# Upstream CLI signature contract test (Codex 2nd-pass #4)
# ============================================================================

REQUIRED_BENCHMARK_FLAGS = (
    "--num-tests",
    "--keywords",
    "--model",
    "--edit-format",
    "--exercises-dir",
)


_BENCHMARK_CLI_SIGNATURE_CACHE: dict | None = None


def _detect_benchmark_cli_signature() -> dict:
    """Run `benchmark.py --help` and check for required flags. Surfaces in
    /health so pin-bump regressions are visible BEFORE first /verify-start.

    Cached at module level — aider's import surface is heavy (~10-30s
    cold start). Re-running per /health probe makes /health unresponsive
    under repeated polling. The result doesn't change at runtime since
    aider source is baked into the image."""
    global _BENCHMARK_CLI_SIGNATURE_CACHE
    if _BENCHMARK_CLI_SIGNATURE_CACHE is not None:
        return _BENCHMARK_CLI_SIGNATURE_CACHE
    if not BENCHMARK_PY.is_file():
        _BENCHMARK_CLI_SIGNATURE_CACHE = {"ok": False, "reason": f"benchmark.py not found at {BENCHMARK_PY}"}
        return _BENCHMARK_CLI_SIGNATURE_CACHE
    try:
        proc = subprocess.run(
            ["python3", str(BENCHMARK_PY), "--help"],
            cwd=str(AIDER_DIR),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,  # was 20 — cold-import of aider can take longer
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        _BENCHMARK_CLI_SIGNATURE_CACHE = {"ok": False, "reason": f"benchmark.py --help failed: {exc}"}
        return _BENCHMARK_CLI_SIGNATURE_CACHE
    if proc.returncode != 0:
        _BENCHMARK_CLI_SIGNATURE_CACHE = {
            "ok": False,
            "reason": f"benchmark.py --help rc={proc.returncode}: {(proc.stderr or '')[-400:]}",
        }
        return _BENCHMARK_CLI_SIGNATURE_CACHE
    help_text = (proc.stdout or "") + (proc.stderr or "")
    missing = [f for f in REQUIRED_BENCHMARK_FLAGS if f not in help_text]
    _BENCHMARK_CLI_SIGNATURE_CACHE = {
        "ok": not missing,
        "missing_flags": missing,
        "help_size_chars": len(help_text),
    }
    return _BENCHMARK_CLI_SIGNATURE_CACHE


# ============================================================================
# Args builder (Codex 2nd-pass #4: centralized, testable)
# ============================================================================

def _build_benchmark_args(
    *,
    run_name: str,
    model: str,
    edit_format: str = DEFAULT_EDIT_FORMAT,
    threads: int = 1,
    num_tests: int | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the argv for `python benchmark/benchmark.py ...`. Pure function;
    no env / subprocess access. Tested by tests/test_aider_polyglot.py.

    The exercise selection happens via cwd: aider's benchmark.py walks
    `--exercises-dir` for ALL exercises matching `--keywords`. We instead
    pre-stage a workspace dir with ONLY the canonical 30 exercise dirs
    (per the manifest) and point --exercises-dir at it. That way we don't
    rely on substring-match keyword behavior at all (Codex 2nd-pass #5).
    """
    argv = [
        "python3", str(BENCHMARK_PY),
        run_name,
        "--model", model,
        "--edit-format", edit_format,
        "--threads", str(threads),
        "--exercises-dir", "polyglot-benchmark",  # relative to staged workspace
        "--new",  # don't try to resume from a prior run dir
    ]
    if num_tests is not None:
        argv.extend(["--num-tests", str(num_tests)])
    if extra_args:
        argv.extend(extra_args)
    return argv


def _qualify_aider_model(model_name: str) -> str:
    head = model_name.split("/", 1)[0]
    if not model_name.startswith("/") and head in _LITELLM_PROVIDERS:
        return model_name
    return f"openai/{model_name}"


def _link_or_copy(src: str, dst: str) -> None:
    """Hard-link a file (cheap, no disk duplication), falling back to a real
    copy when the link would cross filesystems.

    #6: when --sandbox-log-dir is set, the job dir lives on a host bind-mount,
    so os.link from the container's overlayfs raises OSError (EXDEV, "Invalid
    cross-device link"). The staged exercises are small text files, so copying
    them is cheap. Without this fallback the whole batch fails at staging."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _stage_exercises_workspace(stage_dir: Path) -> Path:
    """Create a `polyglot-benchmark` tree under `stage_dir` containing
    ONLY the canonical 30 exercises. Avoids relying on --keywords substring
    matching (Codex 2nd-pass #5).

    Also `git init` the stage_dir — upstream's benchmark.py calls
    git.Repo(search_parent_directories=True) early on and aborts if the
    cwd isn't inside a git repo. Our staged dir at /tmp/... isn't, so we
    stand up a minimal one. No commit needed; the .git dir alone
    satisfies the check.
    """
    # benchmark.py looks for exercises at BENCHMARK_DNAME/exercises_dir where
    # BENCHMARK_DNAME defaults to tmp.benchmarks (overridable via AIDER_BENCHMARK_DIR
    # env). We set AIDER_BENCHMARK_DIR=<stage_dir>/tmp.benchmarks at run time
    # so our staged exercises live at <stage_dir>/tmp.benchmarks/polyglot-benchmark/.
    benchmark_root = stage_dir / "tmp.benchmarks"
    benchmark_root.mkdir(parents=True, exist_ok=True)
    stage_root = benchmark_root / "polyglot-benchmark"
    stage_root.mkdir(parents=True, exist_ok=True)
    for entry in CANONICAL_EXERCISES:
        src = POLYGLOT_DIR / entry["language"] / "exercises" / "practice" / entry["name"]
        dst = stage_root / entry["language"] / "exercises" / "practice" / entry["name"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not src.is_dir():
            continue  # missing exercise — surfaced in _exercise_count_status()
        # Hard-link individual files (cheap, doesn't duplicate disk); fall back
        # to a copy when the job dir is a host bind-mount (#6) and links would
        # cross devices. cp -al equivalent with EXDEV resilience.
        shutil.copytree(src, dst, copy_function=_link_or_copy, dirs_exist_ok=True)
    # Stand up a minimal git repo so benchmark.py's git.Repo() succeeds.
    # benchmark.py reads repo.head.object.hexsha[:7] — needs an actual
    # commit, not just `git init`. Make one empty commit.
    try:
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(stage_dir)],
            check=False, capture_output=True, timeout=10,
        )
        for k, v in (("user.email", "benchlocal@example.com"), ("user.name", "benchlocal")):
            subprocess.run(
                ["git", "-C", str(stage_dir), "config", k, v],
                check=False, capture_output=True, timeout=5,
            )
        subprocess.run(
            ["git", "-C", str(stage_dir), "commit", "--allow-empty", "-q", "-m", "benchlocal-cli stage"],
            check=False, capture_output=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pass  # git missing in container would have failed earlier; non-fatal.
    return stage_root


# ============================================================================
# Result grading (single-scoreboard)
# ============================================================================

def _walk_per_exercise_results(run_dir: Path) -> dict[str, dict]:
    """Walk tmp.benchmarks/<run>/<lang>/exercises/practice/<exercise>/.aider.results.json
    and return {<lang>/<name>: {parsed json}}."""
    out: dict[str, dict] = {}
    if not run_dir.is_dir():
        return out
    for results_path in run_dir.rglob(".aider.results.json"):
        try:
            data = json.loads(results_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # The exercise key is its path relative to the practice/ dir.
        try:
            rel = results_path.parent.relative_to(run_dir)
            # Path looks like <lang>/exercises/practice/<exercise>; collapse.
            parts = rel.parts
            lang = parts[0] if parts else "?"
            name = parts[-1] if parts else "?"
            key = f"{lang}/{name}"
        except ValueError:
            key = str(results_path.parent)
        out[key] = data
    return out


def _summarize_exercise_result(data: dict) -> dict:
    """Pull the headline fields out of one .aider.results.json."""
    outcomes = data.get("tests_outcomes") or []
    last_pass = bool(outcomes and outcomes[-1])
    return {
        "passed": last_pass,
        "tests_outcomes": outcomes,
        "tries": len(outcomes),
        "duration_s": data.get("duration"),
        "cost": data.get("cost"),
        # Diagnostic fields — surface enough to tell whether aider made
        # real model calls (prompt_tokens > 0) vs failed early (0 tokens
        # + non-zero num_error_outputs).
        "model": data.get("model"),
        "prompt_tokens": data.get("prompt_tokens"),
        "completion_tokens": data.get("completion_tokens"),
        "num_error_outputs": data.get("num_error_outputs"),
        "num_user_asks": data.get("num_user_asks"),
        "num_malformed_responses": data.get("num_malformed_responses"),
        "model_errors": data.get("model_errors") or 0,
        "edit_format": data.get("edit_format"),
        "commands": data.get("commands"),
    }


def _grade_aider_batch_result(
    per_exercise: dict[str, dict],
    threshold: float,
    *,
    score_completed_only: bool = False,
) -> dict:
    """Aggregate per-exercise outcomes into pass_rate + summary. Pure;
    tested without docker.

    Full runs score against the canonical 30-exercise denominator. Timeout-
    truncated runs can score only completed exercises while still surfacing the
    canonical denominator separately for traceability.
    """
    canonical_keys = {f"{e['language']}/{e['name']}" for e in CANONICAL_EXERCISES}
    summaries = {k: _summarize_exercise_result(v) for k, v in per_exercise.items()}
    passed = sum(1 for s in summaries.values() if s["passed"])
    canonical_total = len(canonical_keys)
    found = len(summaries)
    score_total = found if score_completed_only else canonical_total
    pass_rate = passed / score_total if score_total > 0 else 0.0

    missing_results = sorted(canonical_keys - set(summaries))
    extra_results = sorted(set(summaries) - canonical_keys)

    overall_passed = pass_rate >= threshold
    failure_mode = "passed" if overall_passed else "verifier_fail"

    return {
        "passed": overall_passed,
        "failure_mode": failure_mode,
        "pass_rate": pass_rate,
        "passed_count": passed,
        "total_count": score_total,
        "found_count": found,
        "canonical_total_count": canonical_total,
        "score_completed_only": score_completed_only,
        "threshold": threshold,
        "missing_results": missing_results,
        "extra_results": extra_results,
        "per_exercise": summaries,
    }


# ============================================================================
# /verify-start handler
# ============================================================================

def _has_marker(scenario_id: str, text: str) -> bool:
    if text and (f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text):
        sys.stderr.write(f"[aider-polyglot] WARNING mock pass marker used for {scenario_id}\n")
        return True
    return False


def _mock_pass_response(scenario_id: str) -> dict:
    return {
        "action": "verify-final",
        "passed": True,
        "failure_mode": "passed",
        "detail": f"{scenario_id}: mock-pass marker honored before subprocess",
        "passed_count": len(CANONICAL_EXERCISES),
        "total_count": len(CANONICAL_EXERCISES),
        "pass_rate": 1.0,
        "trace": {
            "schema_version": SCHEMA_VERSION,
            "stage": "v0.9.0",
            "mock_pass": True,
            "aider_pinned_commit": AIDER_PINNED_COMMIT,
            "polyglot_pinned_commit": POLYGLOT_PINNED_COMMIT,
        },
    }


def _verify_start(req: dict) -> dict:
    scenario = req.get("scenario") or {}
    scenario_id = req.get("scenario_id") or scenario.get("id") or "?"

    # Mock-pass short-circuit
    last_user = ""
    for m in reversed(scenario.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user = str(m.get("content") or "")
            break
    if _has_marker(scenario_id, last_user) or req.get("use_mock_pass"):
        return _mock_pass_response(scenario_id)

    # Validate setup
    cli_sig = _detect_benchmark_cli_signature()
    if not cli_sig.get("ok"):
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "server_error",
            "detail": f"upstream benchmark.py CLI signature broken: {cli_sig.get('reason') or cli_sig.get('missing_flags')}",
            "trace": {"schema_version": SCHEMA_VERSION, "cli_signature": cli_sig},
        }

    ex_status = _exercise_count_status()
    if not ex_status["exact_match"]:
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "server_error",
            "detail": (
                f"exercises manifest mismatch: {ex_status['resolved_count']} of "
                f"{ex_status['canonical_count']} resolved; missing {ex_status['missing']}. "
                f"Likely upstream rename/removal — re-sync vendor/AiderPolyglot-30/exercises.json."
            ),
            "trace": {"schema_version": SCHEMA_VERSION, "exercises": ex_status},
        }

    model_endpoint = req.get("model_endpoint") or ""
    model_name = req.get("model_name") or ""
    if not model_endpoint or not model_name:
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "server_error",
            "detail": "aider-polyglot /verify-start requires model_endpoint and model_name",
            "trace": {"schema_version": SCHEMA_VERSION},
        }

    # Stage workspace with only the canonical 30 exercises
    job_id = str(uuid.uuid4())
    job_dir = Path("/tmp") / "aider-polyglot-runs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        _stage_exercises_workspace(job_dir)

        # Build args
        run_name = f"benchlocal-{scenario_id}-{job_id[:8]}"
        edit_format = (
            (scenario.get("raw_scenario") or {}).get("default_edit_format")
            or DEFAULT_EDIT_FORMAT
        )
        threshold = float(
            (scenario.get("raw_scenario") or {}).get("default_pass_threshold")
            or DEFAULT_PASS_THRESHOLD
        )
        # litellm (which aider uses under the hood) requires a provider
        # prefix in the model name to route to OpenAI-compatible custom
        # endpoints. Without `openai/`, litellm tries to resolve the model
        # against its built-in catalog → no match → silent fallback that
        # makes zero model calls (duration ~0.02s/exercise, cost $0.00).
        # Don't double-prefix if user already passed e.g. "openai/<name>".
        # A slash alone is not enough: llama.cpp/ik_llama often reports a
        # GGUF path (/models/...) as the model id, and bare HF repo ids
        # (org/model) are not litellm provider-qualified either.
        aider_model = _qualify_aider_model(model_name)

        # Disable Qwen3-style thinking via per-request `extra_body`.
        # vLLM doesn't accept --chat-template-kwargs at the CLI; the
        # per-request mechanism is the supported path. Aider reads
        # `.aider.model.settings.yml` from cwd (job_dir) and forwards
        # extra_params to litellm, which forwards to vLLM as extra_body.
        #
        # Written for ALL models (not just Qwen):
        #   - Gemma 4 default thinking=off already, so the kwarg is a no-op
        #   - llama.cpp ignores unknown chat_template_kwargs
        # If a future bench wants thinking ON, set
        # BENCHLOCAL_AIDER_ENABLE_THINKING=1 in the runner env.
        if os.environ.get("BENCHLOCAL_AIDER_ENABLE_THINKING") != "1":
            (job_dir / ".aider.model.settings.yml").write_text(
                "- name: " + aider_model + "\n"
                "  edit_format: " + edit_format + "\n"
                "  extra_params:\n"
                "    extra_body:\n"
                "      chat_template_kwargs:\n"
                "        enable_thinking: false\n"
            )

        argv = _build_benchmark_args(
            run_name=run_name,
            model=aider_model,
            edit_format=edit_format,
            threads=int(os.environ.get("AIDER_BENCHMARK_THREADS", "1")),
        )

        # Build env: pass model endpoint via BOTH OPENAI_BASE_URL and
        # OPENAI_API_BASE (Codex 2nd-pass concern about which the upstream
        # litellm/openai SDK pins read from).
        env = os.environ.copy()
        # Normalize: aider's litellm/openai client expects base_url ending in /v1
        # (it appends /chat/completions itself). Same fix we hit in v0.7.4 hermes.
        # Without /v1, the client hits /chat/completions → 404 → empty response,
        # which aider counts as num_error_outputs without prompt_tokens.
        normalized_endpoint = model_endpoint.rstrip("/")
        for suffix in ("/v1/chat/completions", "/chat/completions"):
            if normalized_endpoint.endswith(suffix):
                normalized_endpoint = normalized_endpoint[: -len(suffix)]
                break
        normalized_endpoint = normalized_endpoint.rstrip("/")
        if not normalized_endpoint.endswith("/v1"):
            normalized_endpoint = normalized_endpoint + "/v1"
        env["OPENAI_BASE_URL"] = normalized_endpoint
        env["OPENAI_API_BASE"] = normalized_endpoint
        env["OPENAI_API_KEY"] = str(req.get("model_api_key") or "benchlocal-cli-aider-polyglot-30")
        env["AIDER_NO_PRETTY"] = "1"
        env["AIDER_NO_AUTO_COMMITS"] = "1"
        # benchmark.py refuses to run without AIDER_DOCKER set (safety guard
        # against running unvetted model code on a host) — set it since we
        # ARE inside a container.
        env["AIDER_DOCKER"] = "1"
        # Make BENCHMARK_DNAME point at our staged tmp.benchmarks, so
        # benchmark.py finds <stage>/tmp.benchmarks/polyglot-benchmark/<lang>/...
        # and writes results to <stage>/tmp.benchmarks/<run_name>/.
        env["AIDER_BENCHMARK_DIR"] = str(job_dir / "tmp.benchmarks")

        # Spawn with process-group isolation (same hardening as v0.7.3 hermes)
        started = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=str(job_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=SUBPROCESS_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            stdout, stderr = proc.communicate()
        elapsed = time.monotonic() - started

        # Find the run dir aider created — it lives under tmp.benchmarks/
        # within our staged workspace. (Moved BEFORE timeout-handling so we
        # can recover partial data when subprocess gets killed by SIGKILL.)
        tmp_benchmarks = job_dir / "tmp.benchmarks"
        run_dirs = sorted(tmp_benchmarks.glob(f"*{run_name}*"))
        run_dir = run_dirs[-1] if run_dirs else tmp_benchmarks

        per_exercise = _walk_per_exercise_results(run_dir)

        if timed_out:
            # Recover partial per-exercise data. Aider's benchmark.py writes
            # one .aider.results.json per exercise as it completes, so even if
            # we SIGKILL'd mid-batch, exercises that finished BEFORE the kill
            # have data on disk. Score the completed subset only; unrun
            # exercises are reported as missing/incomplete, not as failures.
            graded_partial = _grade_aider_batch_result(
                per_exercise, threshold=threshold, score_completed_only=True
            )
            partial_pass_count = graded_partial["passed_count"]
            partial_total = graded_partial["total_count"]
            partial_found = graded_partial["found_count"]
            canonical_total = graded_partial["canonical_total_count"]
            return {
                "action": "verify-final",
                "passed": False,
                "failure_mode": "agent_runner_timeout",
                "detail": (
                    f"{scenario_id}: aider benchmark.py exceeded {SUBPROCESS_TIMEOUT_S:.0f}s "
                    f"(killed at {elapsed:.0f}s; {partial_found}/{canonical_total} exercises "
                    f"completed before kill; partial score {partial_pass_count}/{partial_total})"
                ),
                # Surface partial counts as first-class fields so the runner can
                # log progress even on timeout — matches the success path shape.
                "pass_rate": graded_partial["pass_rate"],
                "passed_count": partial_pass_count,
                "total_count": partial_total,
                "trace": {
                    "schema_version": SCHEMA_VERSION,
                    "stage": "v0.9.0",
                    "elapsed_s": elapsed,
                    "batch_wall_clock_s": elapsed,
                    "aider_pinned_commit": AIDER_PINNED_COMMIT,
                    "polyglot_pinned_commit": POLYGLOT_PINNED_COMMIT,
                    "edit_format": edit_format,
                    "threshold": threshold,
                    "timed_out": True,
                    "found_count": partial_found,
                    "canonical_total_count": canonical_total,
                    "score_completed_only": True,
                    "missing_results": graded_partial["missing_results"],
                    "extra_results": graded_partial["extra_results"],
                    "upstream_per_exercise": graded_partial["per_exercise"],
                    "stderr_tail": (stderr or "")[-2000:],
                },
            }
        if not per_exercise and proc.returncode != 0:
            # Detect network-error pattern in stderr
            net_pat = re.compile(
                r"connection|unreachable|refused|resolve|timed out|getaddrinfo|ENOTFOUND",
                re.IGNORECASE,
            )
            return {
                "action": "verify-final",
                "passed": False,
                "failure_mode": (
                    "model_endpoint_unreachable"
                    if net_pat.search(stderr or "") else "agent_runner_crashed"
                ),
                "detail": f"{scenario_id}: aider benchmark.py exited rc={proc.returncode}",
                "trace": {
                    "schema_version": SCHEMA_VERSION,
                    "elapsed_s": elapsed,
                    "returncode": proc.returncode,
                    "stderr_tail": (stderr or "")[-2000:],
                    "stdout_tail": (stdout or "")[-1000:],
                },
            }

        graded = _grade_aider_batch_result(per_exercise, threshold=threshold)

        return {
            "action": "verify-final",
            "passed": graded["passed"],
            "failure_mode": graded["failure_mode"],
            "detail": (
                f"{scenario_id}: {graded['passed_count']}/{graded['total_count']} = "
                f"{graded['pass_rate']:.0%}  (threshold {threshold:.0%})"
            ),
            # First-class metrics (Codex 2nd-pass #1 — promote out of trace)
            "pass_rate": graded["pass_rate"],
            "passed_count": graded["passed_count"],
            "total_count": graded["total_count"],
            "trace": {
                "schema_version": SCHEMA_VERSION,
                "stage": "v0.9.0",
                "elapsed_s": elapsed,
                "batch_wall_clock_s": elapsed,
                "aider_pinned_commit": AIDER_PINNED_COMMIT,
                "polyglot_pinned_commit": POLYGLOT_PINNED_COMMIT,
                "edit_format": edit_format,
                "threshold": threshold,
                "found_count": graded["found_count"],
                "missing_results": graded["missing_results"],
                "extra_results": graded["extra_results"],
                "upstream_per_exercise": graded["per_exercise"],
                "subprocess_returncode": proc.returncode,
                "stderr_tail": (stderr or "")[-2000:],
            },
        }
    finally:
        # Best-effort job dir cleanup. Hard-linked exercises so cheap to remove.
        # Set BENCHLOCAL_AIDER_KEEP_JOBDIRS=1 to retain for forensic inspection.
        if os.environ.get("BENCHLOCAL_AIDER_KEEP_JOBDIRS") != "1":
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass


# ============================================================================
# /health
# ============================================================================

def _resolve_health() -> dict:
    cli_sig = _detect_benchmark_cli_signature()
    ex_status = _exercise_count_status()
    overall = "ok" if (cli_sig.get("ok") and ex_status["exact_match"]) else "setup-error"
    return {
        "status": overall,
        "pack": "aider-polyglot-30",
        "stage": "v0.9.0",
        "multi_turn": False,
        "single_scoreboard": True,
        "aider_dir": str(AIDER_DIR),
        "polyglot_dir": str(POLYGLOT_DIR),
        "aider_pinned_commit": AIDER_PINNED_COMMIT,
        "polyglot_pinned_commit": POLYGLOT_PINNED_COMMIT,
        "exercises": ex_status,
        "benchmark_cli_signature": cli_sig,
        "subprocess_timeout_s": SUBPROCESS_TIMEOUT_S,
        "default_edit_format": DEFAULT_EDIT_FORMAT,
        "default_pass_threshold": DEFAULT_PASS_THRESHOLD,
    }


# ============================================================================
# HTTP Handler
# ============================================================================

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[aider-polyglot-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(_resolve_health())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in ("/verify", "/verify-start", "/verify-turn", "/verify-end"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            req = self._json_body()
        except json.JSONDecodeError as exc:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"invalid JSON: {exc}".encode())
            return

        try:
            if self.path == "/verify-start":
                result = _verify_start(req)
            else:
                result = {
                    "action": "verify-final",
                    "passed": False,
                    "failure_mode": "server_error",
                    "detail": (
                        f"aider-polyglot {self.path} is unsupported; runner should use /verify-start "
                        "(single-scoreboard pack)."
                    ),
                    "trace": {"schema_version": SCHEMA_VERSION},
                }
        except Exception as exc:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            sys.stderr.write(f"[aider-polyglot] verifier exception on {self.path}: {exc}\n{tb}\n")
            result = {
                "action": "verify-final",
                "passed": False,
                "failure_mode": "server_error",
                "detail": f"{type(exc).__name__}: {exc}",
                "trace": {"schema_version": SCHEMA_VERSION, "traceback": tb[-2000:]},
            }
        self._send(result)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(body)
        return data if isinstance(data, dict) else {}

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    sys.stderr.write(
        f"[aider-polyglot-sandbox] listening on :{PORT} "
        f"(stage=v0.9.0, aider={AIDER_PINNED_COMMIT[:12]}, polyglot={POLYGLOT_PINNED_COMMIT[:12]}, "
        f"timeout={SUBPROCESS_TIMEOUT_S:.0f}s)\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[aider-polyglot-sandbox] shutdown\n")


if __name__ == "__main__":
    main()
