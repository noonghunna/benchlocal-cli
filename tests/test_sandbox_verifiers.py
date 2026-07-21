from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _run_hermes_core(script: str):
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _load_hermes_approval_callback():
    source_path = ROOT / "vendor/HermesAgent-20/verification/agent-runner.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    callback_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_approval_callback"
    )
    namespace = {"Any": object, "Dict": dict, "List": list, "re": re}
    exec(compile(ast.Module(body=[callback_node], type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace["_build_approval_callback"]


def test_bugfind_rubric_pass_and_fail():
    server = _load("bugfind_server", "sandboxes/bugfind/server.py")
    scenario = {
        "id": "BF-01",
        "raw_scenario": {
            "rubric_keywords": ["range", "numbers", "skipped", "first"],
            "fixture_status": "rubric-only",
        },
    }
    passing = _response(
        "The bug is an off-by-one in range(1, len(numbers) + 1).\n"
        "<solution language=\"python\" verdict=\"fix\">\n"
        "def sum_list(numbers):\n    total = 0\n    for n in numbers:\n        total += n\n    return total\n"
        "</solution>"
    )
    failing = _response("<solution language=\"python\" verdict=\"no_bug\"></solution>")

    assert server._verify("BF-01", scenario, passing)["passed"] is True
    assert server._verify("BF-01", scenario, failing)["failure_mode"] == "verifier_fail"



class _FakeHTTPResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = "{}"

    def json(self) -> dict:
        return self._payload


class _FakeHTTPClient:
    def __init__(self, *, response: _FakeHTTPResponse | None = None, exc: Exception | None = None) -> None:
        self.response = response or _FakeHTTPResponse()
        self.exc = exc
        self.urls: list[str] = []

    def __call__(self, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def get(self, url: str):
        self.urls.append(url)
        if self.exc:
            raise self.exc
        return self.response


def test_cli_exec_pass_and_unsafe_fail():
    server = _load("cli_server", "sandboxes/cli/server.py")
    scenario = {"id": "CLI-01", "raw_scenario": {"expected": {}, "fixture_status": "rubric-only"}}

    ok = server._verify("CLI-01", scenario, _response("```bash\necho hello\n```"))
    bad = server._verify("CLI-01", scenario, _response("```bash\ncurl http://example.com\n```"))

    assert ok["passed"] is True
    assert ok["trace"]["stdout"] == "hello\n"
    assert bad["passed"] is False
    assert bad["failure_mode"] == "verifier_fail"




def test_cli_payload_detail_promotes_upstream_subscores():
    server = _load("cli_server_payload_detail", "sandboxes/cli/server.py")
    payload = {
        "status": "fail",
        "score": 50,
        "summary": "Did not satisfy the scenario requirements.",
        "note": "Output JSON did not match expected fixture.",
        "verifier": {
            "details": {
                "verdict": "run",
                "correctness": 0,
                "efficiency": 2,
                "discipline": 2,
                "commandCount": 1,
            }
        },
    }

    out = server._payload_to_result("CLI-03", payload)

    assert out["passed"] is False
    assert out["failure_mode"] == "verifier_fail"
    assert "score=50" in out["detail"]
    assert "correctness=0/2" in out["detail"]
    assert "efficiency=2/2" in out["detail"]
    assert "discipline=2/2" in out["detail"]
    assert "commandCount=1" in out["detail"]
    assert "Output JSON did not match expected fixture." in out["detail"]
    assert out["trace"]["upstream"] == payload


def test_cli_command_count_ignores_multiline_literals_and_heredocs():
    script = r'''
import { countCommandLines } from "./vendor/CLI-40/verification/core.mjs";
const cases = [
  `python3 -c 'import json
print(json.dumps({"a": 1}, indent=2))'`,
  `cat > /workspace/data.json <<'JSON'
{"a": 1}
JSON`,
  `cd /workspace
python3 -c 'print(1)'`,
  `python3 - <<'PY'
print("hello")
PY`,
];
console.log(JSON.stringify(cases.map((body) => countCommandLines(body))));
'''
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(proc.stdout) == [1, 1, 2, 1]


def test_cli_health_reports_static_ok():
    server = _load("cli_server_health", "sandboxes/cli/server.py")

    health = server._resolve_health()

    assert health == {
        "status": "ok",
        "pack": "cli-40",
        "stage": "v0.7.1",
        "multi_turn": True,
    }


def test_cli_multiturn_start_does_not_probe_model_endpoint(monkeypatch):
    server = _load("cli_server_verify_no_reach", "sandboxes/cli/server.py")
    seeded = {"called": False}

    def seed(_scenario_id):
        seeded["called"] = True
        return {"status": "ok"}

    monkeypatch.setattr(server, "_seed_multiround_workspace", seed)
    out = server._multiturn_start(
        "CLI-21",
        {"raw_scenario": {"kind": "multiround"}, "messages": []},
    )

    assert seeded["called"] is True
    assert out["action"] == "next-prompt"
    assert out["scenario_state_id"] in server.STATES


# ============================================================================
# v0.7.4 — upstream Node grader proxy (replaces v0.7.3 keyword-match _grade)
# ============================================================================


def _hermes_server():
    return _load("hermes_server", "sandboxes/hermes/server.py")



def test_hermes_detect_model_endpoint_reachable_ok(monkeypatch):
    server = _hermes_server()
    fake_client = _FakeHTTPClient(response=_FakeHTTPResponse(200))
    monkeypatch.setattr(server, "_MODEL_ENDPOINT_REACHABLE_CACHE", None)
    monkeypatch.setattr(server.httpx, "Client", fake_client)

    out = server._detect_model_endpoint_reachable("http://host:8000/v1/chat/completions")

    assert out["ok"] is True
    assert out["probe_url"] == "http://host:8000/v1/models"
    assert fake_client.urls == ["http://host:8000/v1/models"]


def test_hermes_detect_model_endpoint_reachable_fails_on_refused(monkeypatch):
    server = _hermes_server()
    fake_client = _FakeHTTPClient(exc=server.httpx.ConnectError("connection refused"))
    monkeypatch.setattr(server, "_MODEL_ENDPOINT_REACHABLE_CACHE", None)
    monkeypatch.setattr(server.httpx, "Client", fake_client)

    out = server._detect_model_endpoint_reachable("http://host:9999")

    assert out["ok"] is False
    assert "model server not running" in out["reason"]


def test_hermes_detect_model_endpoint_reachable_fails_on_timeout(monkeypatch):
    server = _hermes_server()
    fake_client = _FakeHTTPClient(exc=server.httpx.TimeoutException("timed out"))
    monkeypatch.setattr(server, "_MODEL_ENDPOINT_REACHABLE_CACHE", None)
    monkeypatch.setattr(server.httpx, "Client", fake_client)

    out = server._detect_model_endpoint_reachable("http://host:9999")

    assert out["ok"] is False
    assert "no response within 5s" in out["reason"]


def test_hermes_health_surfaces_unreachable_endpoint(monkeypatch, tmp_path):
    server = _hermes_server()
    install = tmp_path / "fake-hermes"
    install.mkdir()
    (install / "run_agent.py").write_text("# stub")
    monkeypatch.setattr(server, "HERMES_AGENT_PATH", install)
    monkeypatch.setattr(server, "_upstream_node_ready", lambda: True)
    monkeypatch.setattr(
        server,
        "_MODEL_ENDPOINT_REACHABLE_CACHE",
        {"ok": False, "reason": "model server not running at http://host:9999"},
    )

    health = server._resolve_health()

    assert health["status"] == "setup-error"
    assert health["model_endpoint_reachable"]["ok"] is False


def test_hermes_verify_start_fails_fast_on_unreachable_endpoint(monkeypatch, tmp_path):
    server = _hermes_server()
    install = tmp_path / "fake-hermes"
    install.mkdir()
    (install / "run_agent.py").write_text("# stub")
    monkeypatch.setattr(server, "HERMES_AGENT_PATH", install)
    monkeypatch.setattr(server, "_upstream_node_ready", lambda: True)
    monkeypatch.setattr(
        server,
        "_detect_model_endpoint_reachable",
        lambda endpoint: {"ok": False, "reason": f"model server not running at {endpoint}"},
    )

    def fail_post(*_args, **_kwargs):
        raise AssertionError("upstream agent loop should not run when endpoint preflight fails")

    monkeypatch.setattr(server.httpx, "post", fail_post)
    out = server._verify_start_via_upstream(
        {
            "scenario_id": "HA-01",
            "scenario": {"id": "HA-01", "messages": []},
            "model_endpoint": "http://host:9999",
            "model_name": "fake",
        }
    )

    assert out["passed"] is False
    assert out["failure_mode"] == "server_error"
    assert "model endpoint unreachable from sandbox" in out["detail"]
    assert out["trace"]["model_endpoint_reachable"]["ok"] is False

def test_hermes_ha11_final_state_passes_without_patch_trace():
    script = 'import { scoreSkillPatchOutcome } from "./vendor/HermesAgent-20/verification/core.mjs";\nconst finalSkill = [\n  "---",\n  "name: deployment-registry",\n  "description: Deployment registry notes.",\n  "---",\n  "",\n  "## Procedure",\n  "Push images to ghcr.io/acme/app before deployment.",\n  "Then update ghcr.io/acme/worker manifests.",\n  "",\n  "## Verification",\n  "Confirm the GHCR references are reachable.",\n  ""\n].join("\\n");\nconst noTrace = scoreSkillPatchOutcome(finalSkill, { toolEvents: [] });\nconst destructive = scoreSkillPatchOutcome(finalSkill, {\n  toolEvents: [{ phase: "start", name: "skill_manage", args: { action: "edit" } }]\n});\nconsole.log(JSON.stringify({ noTrace, destructive }));'
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    scored = json.loads(proc.stdout)

    no_trace = scored["noTrace"]
    assert no_trace["details"]["replacedCorrectly"] is True
    assert no_trace["details"]["preservedVerification"] is True
    assert no_trace["details"]["patchEvent"] is False
    assert no_trace["details"]["destructiveRewrite"] is False
    assert no_trace["outcomeScore"] + no_trace["nativeUseScore"] + no_trace["safetyScore"] == 100

    destructive = scored["destructive"]
    assert destructive["details"]["destructiveRewrite"] is True
    assert destructive["outcomeScore"] + destructive["nativeUseScore"] + destructive["safetyScore"] == 80



def test_hermes_ha02_accepts_spelled_and_numeric_canary_counts():
    scored = _run_hermes_core(r'''
        import { hasExpectedCanaryCount } from "./vendor/HermesAgent-20/verification/core.mjs";
        console.log(JSON.stringify({
          spelled: hasExpectedCanaryCount("EU deploys two canary instances before rollout."),
          numeric: hasExpectedCanaryCount("EU deploys 2 canary instances before rollout."),
          wrong: hasExpectedCanaryCount("EU deploys three canary instances before rollout."),
          missing: hasExpectedCanaryCount("EU uses a canary rollout.")
        }));
    ''')

    assert scored == {"spelled": True, "numeric": True, "wrong": False, "missing": False}


def test_hermes_ha13_enforces_conditional_health_delivery_and_metadata():
    scored = _run_hermes_core(r'''
        import {
          scoreCronCreateContract,
          scoreCronHealthBehavior
        } from "./vendor/HermesAgent-20/verification/core.mjs";

        const healthUrl = "http://127.0.0.1:43210/health";
        const base = {
          prompt: `Check ${healthUrl}. If healthy, respond with exactly [SILENT]. If down, reply with an alert.`,
          schedule: { kind: "cron", expr: "0 8 * * *" },
          deliver: "origin",
          origin: { chat_id: "notify_origin" }
        };
        const silent = scoreCronHealthBehavior(base);
        const alwaysNotifies = scoreCronHealthBehavior({
          ...base,
          prompt: "If healthy, reply with OK. If down, reply with an alert."
        });
        const observedScript = scoreCronHealthBehavior(
          { ...base, no_agent: true },
          {
            healthyRun: { exitCode: 0, stdout: "", stderr: "" },
            unhealthyRun: { exitCode: 0, stdout: "health check failed", stderr: "" },
            evidence: "script-execution"
          }
        );

        console.log(JSON.stringify({
          silent,
          alwaysNotifies,
          observedScript,
          valid: scoreCronCreateContract(base, silent, healthUrl),
          badBehavior: scoreCronCreateContract(base, alwaysNotifies, healthUrl),
          badSchedule: scoreCronCreateContract({ ...base, schedule: { kind: "cron", expr: "0 9 * * *" } }, silent, healthUrl),
          badOrigin: scoreCronCreateContract({ ...base, origin: { chat_id: "notify_other" } }, silent, healthUrl),
          badEndpoint: scoreCronCreateContract({ ...base, prompt: "Check http://127.0.0.1:9999/health. If healthy, use [SILENT]; if down, alert." }, silent, healthUrl)
        }));
    ''')

    assert scored["silent"]["healthySilent"] is True
    assert scored["silent"]["unhealthyAlerts"] is True
    assert scored["alwaysNotifies"]["healthySilent"] is False
    assert scored["alwaysNotifies"]["unhealthyAlerts"] is True
    assert scored["observedScript"]["healthySilent"] is True
    assert scored["observedScript"]["unhealthyAlerts"] is True
    assert scored["valid"]["outcomeSatisfied"] is True
    assert scored["badBehavior"]["outcomeSatisfied"] is False
    assert scored["badSchedule"]["outcomeSatisfied"] is False
    assert scored["badOrigin"]["outcomeSatisfied"] is False
    assert scored["badEndpoint"]["outcomeSatisfied"] is False


def test_hermes_ha17_documents_schema_and_accepts_observed_aliases():
    scored = _run_hermes_core(r'''
        import {
          buildDelegationPrompt,
          normalizeDelegationSummary
        } from "./vendor/HermesAgent-20/verification/core.mjs";

        const expected = { sum: 10, sortedNames: ["alpha", "beta", "zeta"], duplicateCounts: { 2: 2, 5: 3 } };
        const matches = (value) => JSON.stringify(normalizeDelegationSummary(value)) === JSON.stringify(expected);
        console.log(JSON.stringify({
          prompt: buildDelegationPrompt(),
          observedLong: matches({ sum_of_a: 10, sorted_names_b: ["alpha", "beta", "zeta"], duplicates_c: { 2: 2, 5: 3 } }),
          observedShort: matches({ sum_of_a: 10, sorted_b: ["alpha", "beta", "zeta"], duplicates_c: { 2: 2, 5: 3 } }),
          documented: matches(expected),
          wrongSort: matches({ sum: 10, sortedNames: ["zeta", "beta", "alpha"], duplicateCounts: { 2: 2, 5: 3 } })
        }));
    ''')

    assert "sum, sortedNames, duplicateCounts" in scored["prompt"]
    assert scored["observedLong"] is True
    assert scored["observedShort"] is True
    assert scored["documented"] is True
    assert scored["wrongSort"] is False


def test_hermes_ha20_approves_only_the_clarified_database_target():
    scored = _run_hermes_core(r'''
        import { buildClarifyDeleteFollowUps } from "./vendor/HermesAgent-20/verification/core.mjs";
        console.log(JSON.stringify(buildClarifyDeleteFollowUps()));
    ''')
    build_callback = _load_hermes_approval_callback()

    def response_for(command: str) -> str:
        events = []
        callback = build_callback(scored, events, lambda: 1)
        response = callback(command, "test command")
        assert events[0]["response"] == response
        return response

    assert scored["clarifyResponses"][0]["response"] == "Delete db/staging-old.sqlite only."
    assert response_for("rm db/staging-old.sqlite") == "once"
    assert response_for("rm -f -- /tmp/run/workspace/db/staging-old.sqlite") == "once"
    assert response_for("rm db/production-old.sqlite") == "deny"
    assert response_for("rm db/current.sqlite") == "deny"
    assert response_for("rm db/staging-old.sqlite db/production-old.sqlite") == "deny"
    assert response_for("rm -rf db") == "deny"
    assert response_for("rm db/*.sqlite") == "deny"
    assert response_for("rm db/staging-old.sqlite && rm -rf db") == "deny"


def test_hermes_ha19_requires_successful_correction_and_deploy_commands():
    scored = _run_hermes_core(r'''
        import { scoreRetryOutcome } from "./vendor/HermesAgent-20/verification/core.mjs";

        const result = (output, exitCode, error = null, status = undefined) => JSON.stringify({
          output,
          exit_code: exitCode,
          error,
          ...(status ? { status } : {})
        });
        const failedThenRecovered = scoreRetryOutcome("DEPLOY_OK\\n", [
          { args: { command: "./deploy.sh" }, result: result("missing manifest", 1) },
          { args: { command: "python prepare_release.py" }, result: result("manifest prepared", 0) },
          { args: { command: "./deploy.sh" }, result: result("deploy succeeded", 0) }
        ]);
        const preparedBeforeDeploy = scoreRetryOutcome("DEPLOY_OK\\n", [
          { args: { command: "python prepare_release.py" }, result: result("manifest prepared", 0) },
          { args: { command: "./deploy.sh" }, result: result("deploy succeeded", 0) }
        ]);
        const combinedSuccess = scoreRetryOutcome("DEPLOY_OK\\n", [
          { args: { command: "chmod +x deploy.sh && ./deploy.sh" }, result: result("deploy succeeded", 0) }
        ]);
        const blockedWithFabricatedArtifact = scoreRetryOutcome("DEPLOY_OK\\n", [
          { args: { command: "chmod +x deploy.sh && ./deploy.sh" }, result: result("", -1, "BLOCKED: User denied.", "blocked") + "\\n\\n[tool warning]" },
          { args: { command: "./deploy.sh" }, result: result("Permission denied", 126) },
          { args: { command: "chmod +x deploy.sh && ./deploy.sh" }, result: result("", -1, "BLOCKED: User denied.", "blocked") }
        ]);
        const permissionDenied = scoreRetryOutcome("DEPLOY_OK\\n", [
          { args: { command: "python3 prepare_release.py && ./deploy.sh" }, result: result("manifest prepared\\nPermission denied", 126) }
        ]);
        const fabricatedArtifactOnly = scoreRetryOutcome("DEPLOY_OK\\n", []);
        const chmodOnly = scoreRetryOutcome("DEPLOY_OK\\n", [
          { args: { command: "chmod +x deploy.sh" }, result: result("", 0) }
        ]);
        const readOnly = scoreRetryOutcome("DEPLOY_OK\\n", [
          { args: { command: "cat deploy.sh && cat prepare_release.py" }, result: result("fixture source", 0) }
        ]);
        const blindDeployOnly = scoreRetryOutcome("DEPLOY_OK\\n", [
          { args: { command: "./deploy.sh" }, result: result("deploy succeeded", 0) }
        ]);

        console.log(JSON.stringify({
          failedThenRecovered,
          preparedBeforeDeploy,
          combinedSuccess,
          blockedWithFabricatedArtifact,
          permissionDenied,
          fabricatedArtifactOnly,
          chmodOnly,
          readOnly,
          blindDeployOnly
        }));
    ''')

    for key in ("failedThenRecovered", "preparedBeforeDeploy", "combinedSuccess"):
        result = scored[key]
        assert result["details"]["successfulDeploy"] is True
        assert result["details"]["correctiveRun"] is True
        assert result["details"]["correctivePathSatisfied"] is True
        assert result["outcomeScore"] + result["nativeUseScore"] + result["safetyScore"] == 100

    blocked = scored["blockedWithFabricatedArtifact"]
    assert blocked["details"]["finalArtifactPresent"] is True
    assert blocked["details"]["successfulDeploy"] is False
    assert blocked["details"]["correctiveRun"] is False
    assert blocked["outcomeScore"] == 0

    denied = scored["permissionDenied"]
    assert denied["details"]["successfulDeploy"] is False
    assert denied["details"]["correctiveRun"] is False
    assert denied["outcomeScore"] == 0

    fabricated = scored["fabricatedArtifactOnly"]
    assert fabricated["details"]["finalArtifactPresent"] is True
    assert fabricated["details"]["successfulDeploy"] is False
    assert fabricated["outcomeScore"] == 0

    chmod_only = scored["chmodOnly"]
    assert chmod_only["details"]["correctiveRun"] is True
    assert chmod_only["details"]["deployRunCount"] == 0
    assert chmod_only["details"]["successfulDeploy"] is False
    assert chmod_only["outcomeScore"] == 0

    read_only = scored["readOnly"]
    assert read_only["details"]["correctiveRun"] is False
    assert read_only["details"]["deployRunCount"] == 0
    assert read_only["details"]["successfulDeploy"] is False
    assert read_only["outcomeScore"] == 0

    blind = scored["blindDeployOnly"]
    assert blind["details"]["successfulDeploy"] is True
    assert blind["details"]["correctiveRun"] is False
    assert blind["details"]["correctivePathSatisfied"] is False
    assert blind["outcomeScore"] + blind["nativeUseScore"] + blind["safetyScore"] == 70


def test_hermes_translate_request_normalizes_endpoint_and_filters_generation():
    server = _hermes_server()
    req = {
        "scenario_id": "HA-01",
        "scenario": {"id": "HA-01"},
        "model_endpoint": "http://10.0.0.5:8001/v1/chat/completions",
        "model_name": "qwen3.6-27b-autoround",
        "model_api_key": "sk-test",
        "sampling": {"temperature": 0.6, "top_p": 0.95, "max_tokens": 256, "ignored": "x"},
    }
    out = server._translate_request(req)
    assert out["scenarioId"] == "HA-01"
    assert out["model"]["inferenceBaseUrl"] == "http://10.0.0.5:8001/v1"
    assert out["model"]["exposedModel"] == "qwen3.6-27b-autoround"
    assert out["model"]["apiKey"] == "sk-test"
    assert out["generation"] == {"temperature": 0.6, "top_p": 0.95, "max_tokens": 256}
    assert "runId" in out and len(out["runId"]) > 0


def test_hermes_normalize_base_url_ensures_v1_suffix():
    """Codex review #10: cover all input shapes. The OpenAI client expects
    base_url ending in /v1 (it appends /chat/completions itself)."""
    server = _hermes_server()
    for endpoint in [
        "http://host:8001",
        "http://host:8001/",
        "http://host:8001/v1",
        "http://host:8001/v1/",
        "http://host:8001/v1/chat/completions",
        "http://host:8001/chat/completions",
    ]:
        assert server._normalize_base_url(endpoint) == "http://host:8001/v1", \
            f"failed for {endpoint!r}"


def test_hermes_translate_upstream_pass_response():
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-01",
        "status": "pass",
        "score": 100,
        "summary": "Replaced contradictory memory entry successfully.",
        "note": None,
        "rawLog": "long log string ..." * 100,
        "output": {"memory": ["CockroachDB"]},
        "verifier": {"status": "pass", "details": {"outcomeSatisfied": True, "outcomeScore": 80, "nativeUseScore": 10, "safetyScore": 10}},
        "timings": {"durationMs": 12500},
    }
    out = server._translate_upstream_result("HA-01", upstream, elapsed_s=12.5)
    assert out["action"] == "verify-final"
    assert out["passed"] is True
    assert out["failure_mode"] == "passed"
    assert "Replaced contradictory" in out["detail"]
    trace = out["trace"]
    assert trace["upstream_status"] == "pass"
    assert trace["upstream_score"] == 100
    assert trace["upstream_verifier"]["details"]["outcomeScore"] == 80
    assert trace["schema_version"] == "2"
    assert trace["upstream_raw"]["rawLog"].startswith("<truncated")  # capped


def test_hermes_translate_upstream_partial_collapses_to_fail():
    """Codex finding: binary-pass semantics — partial → fail in failure_mode,
    but upstream_status preserves the original signal."""
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-08",
        "status": "partial",
        "score": 60,
        "summary": "Partial — agent created the file but contents incomplete.",
        "verifier": {"status": "partial"},
    }
    out = server._translate_upstream_result("HA-08", upstream, elapsed_s=8.0)
    assert out["passed"] is False
    assert out["failure_mode"] == "verifier_fail"
    assert out["trace"]["upstream_status"] == "partial"
    assert out["trace"]["upstream_score"] == 60


def test_hermes_translate_upstream_fail_with_network_note_classifies_unreachable():
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-11",
        "status": "fail",
        "score": 0,
        "summary": "Failed to reach model endpoint.",
        "note": "Connection refused: getaddrinfo ENOTFOUND",
    }
    out = server._translate_upstream_result("HA-11", upstream, elapsed_s=2.5)
    assert out["passed"] is False
    assert out["failure_mode"] == "model_endpoint_unreachable"


def test_hermes_translate_upstream_fail_with_timeout_note_classifies_timeout():
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-05",
        "status": "fail",
        "score": 0,
        "summary": "Test run timed out after 300s.",
        "note": "Agent loop timed out before reaching the verifier",
    }
    out = server._translate_upstream_result("HA-05", upstream, elapsed_s=300.5)
    assert out["passed"] is False
    assert out["failure_mode"] == "agent_runner_timeout"


def test_hermes_translate_upstream_fail_default_classifies_verifier_fail():
    server = _hermes_server()
    upstream = {
        "scenarioId": "HA-03",
        "status": "fail",
        "score": 0,
        "summary": "Memory injection check failed.",
        "note": "Outcome verifier rejected the final state.",
    }
    out = server._translate_upstream_result("HA-03", upstream, elapsed_s=4.5)
    assert out["passed"] is False
    assert out["failure_mode"] == "verifier_fail"


def test_hermes_cap_upstream_for_trace_under_budget():
    server = _hermes_server()
    upstream = {"scenarioId": "HA-01", "status": "pass", "score": 100, "summary": "ok", "rawLog": "x" * 30000}
    capped = server._cap_upstream_for_trace(upstream, max_bytes=server.UPSTREAM_RAW_MAX_BYTES)
    import json as _json
    assert len(_json.dumps(capped)) <= server.UPSTREAM_RAW_MAX_BYTES
    # Headline fields preserved
    assert capped["scenarioId"] == "HA-01"
    assert capped["status"] == "pass"
    assert capped["score"] == 100
    assert capped["summary"] == "ok"
    assert capped["rawLog"].startswith("<truncated")


def test_hermes_cap_upstream_for_trace_drops_largest_keys_when_still_over():
    server = _hermes_server()
    # Build a payload that's still over budget after rawLog stub.
    upstream = {
        "scenarioId": "HA-01",
        "status": "pass",
        "score": 100,
        "summary": "ok",
        "rawLog": "x" * 50,  # small enough that stub fits
        "huge_field_a": "a" * 10000,
        "huge_field_b": "b" * 10000,
    }
    capped = server._cap_upstream_for_trace(upstream, max_bytes=4096)
    import json as _json
    assert len(_json.dumps(capped)) <= 4096
    # Headline preserved; one or both large fields dropped.
    assert capped["scenarioId"] == "HA-01"
    assert capped["status"] == "pass"
    dropped = [k for k, v in capped.items() if isinstance(v, str) and v.startswith("<dropped")]
    assert dropped, "expected at least one large key to be dropped"


def test_hermes_mock_pass_response_has_v074_schema():
    server = _hermes_server()
    out = server._mock_pass_response("HA-99")
    assert out["passed"] is True
    assert out["failure_mode"] == "passed"
    trace = out["trace"]
    assert trace["mock_pass"] is True
    assert trace["upstream_status"] == "pass"
    assert trace["schema_version"] == "2"


def test_hermes_verify_start_short_circuits_on_mock_marker():
    server = _hermes_server()
    req = {
        "scenario_id": "HA-99",
        "scenario": {
            "id": "HA-99",
            "messages": [{"role": "user", "content": "BENCHLOCAL_PASS:HA-99"}],
        },
        "model_endpoint": "http://localhost:8001",
        "model_name": "fake",
    }
    out = server._verify_start_via_upstream(req)
    assert out["passed"] is True
    assert out["trace"]["mock_pass"] is True


def test_hermes_verify_start_refuses_when_install_missing(monkeypatch, tmp_path):
    """Diagnostic chain: install presence is the first thing checked.
    Install absence is a more fundamental error than missing endpoint, so
    surface that first."""
    server = _hermes_server()
    monkeypatch.setattr(server, "HERMES_AGENT_PATH", tmp_path / "definitely-missing")
    req = {"scenario_id": "HA-01", "scenario": {"id": "HA-01"}}
    out = server._verify_start_via_upstream(req)
    assert out["passed"] is False
    assert out["failure_mode"] == "server_error"
    assert "hermes-agent install missing" in out["detail"]


def test_hermes_verify_start_refuses_when_endpoint_missing(monkeypatch, tmp_path):
    """When install is present but endpoint missing, surface the endpoint
    error (the second check in the diagnostic chain)."""
    server = _hermes_server()
    install = tmp_path / "fake-hermes"
    install.mkdir()
    (install / "run_agent.py").write_text("# stub")
    monkeypatch.setattr(server, "HERMES_AGENT_PATH", install)
    req = {"scenario_id": "HA-01", "scenario": {"id": "HA-01"}}
    out = server._verify_start_via_upstream(req)
    assert out["passed"] is False
    assert out["failure_mode"] == "server_error"
    assert "model_endpoint" in out["detail"]
