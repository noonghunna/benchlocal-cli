#!/usr/bin/env python3
import json
import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path("/opt/hermes-agent")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_state import SessionDB
from run_agent import AIAgent
from tools.terminal_tool import set_approval_callback


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _guess_provider(base_url: str, requested_provider: str) -> str:
    normalized = (base_url or "").strip().lower()
    provider = (requested_provider or "").strip().lower()

    if "openrouter.ai" in normalized:
        return "openrouter"
    if "api.openai.com" in normalized:
        return "openai"
    if "api.anthropic.com" in normalized or normalized.rstrip("/").endswith("/anthropic"):
        return "anthropic"
    if normalized.startswith("http://127.0.0.1") or normalized.startswith("http://localhost") or "host.docker.internal" in normalized:
        return "custom"
    if provider:
        return provider
    return "custom"


def _request_overrides(generation: Dict[str, Any]) -> Dict[str, Any]:
    overrides: Dict[str, Any] = {}

    # The pinned Hermes runtime forwards request_overrides directly into its
    # OpenAI-compatible completions client. That client rejects
    # request_timeout_seconds as an unexpected kwarg, so keep timeout
    # enforcement at the verifier process level instead of passing it through
    # as a model override.
    for key in (
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repetition_penalty",
        "max_tokens",
    ):
        value = generation.get(key)
        if value is not None:
            overrides[key] = value

    return overrides


def _seed_session_db(session_db: SessionDB, request: Dict[str, Any]) -> None:
    for session in request.get("sessionSeed") or []:
        session_id = str(session.get("sessionId") or uuid.uuid4())
        source = str(session.get("source") or "benchlocal-hermesagent-20-seed")
        model = session.get("model")

        session_db.create_session(
            session_id=session_id,
            source=source,
            model=str(model) if model else None,
            user_id=session.get("userId"),
            parent_session_id=session.get("parentSessionId"),
        )

        for message in session.get("messages") or []:
            session_db.append_message(
                session_id,
                role=str(message.get("role") or "user"),
                content=message.get("content"),
                tool_name=message.get("tool_name"),
                tool_calls=message.get("tool_calls"),
                tool_call_id=message.get("tool_call_id"),
                finish_reason=message.get("finish_reason"),
                reasoning=message.get("reasoning"),
                reasoning_details=message.get("reasoning_details"),
                codex_reasoning_items=message.get("codex_reasoning_items"),
            )


def _build_clarify_callback(follow_ups: Dict[str, Any], clarify_events: List[Dict[str, Any]], next_order):
    scripted = list(follow_ups.get("clarifyResponses") or [])

    def _callback(question: str, choices: Optional[List[str]]) -> str:
        response = ""

        for index, item in enumerate(scripted):
            if isinstance(item, dict):
                question_contains = str(item.get("questionContains") or "").strip().lower()
                if question_contains and question_contains not in question.lower():
                    continue
                response = str(item.get("response") or "")
                scripted.pop(index)
                break

            response = str(item)
            scripted.pop(index)
            break

        clarify_events.append({
            "order": next_order(),
            "question": question,
            "choices": list(choices or []),
            "response": response,
        })
        return response

    return _callback


def _build_approval_callback(follow_ups: Dict[str, Any], approval_events: List[Dict[str, Any]], next_order):
    scripted = list(follow_ups.get("approvals") or [])
    default_response = str(follow_ups.get("defaultApprovalResponse") or "deny")

    def _callback(command: str, description: str, allow_permanent: bool = True) -> str:
        response = default_response

        for index, item in enumerate(scripted):
            if isinstance(item, dict):
                contains = str(item.get("commandContains") or "").strip().lower()
                if contains and contains not in command.lower():
                    continue
                response = str(item.get("response") or default_response)
                scripted.pop(index)
                break

            response = str(item)
            scripted.pop(index)
            break

        approval_events.append({
            "order": next_order(),
            "command": command,
            "description": description,
            "allow_permanent": bool(allow_permanent),
            "response": response,
        })
        return response

    return _callback


def main() -> int:
    request_path = Path(sys.argv[1]).resolve()
    request = _read_json(request_path)
    result_path = Path(request["resultPath"]).resolve()

    os.environ["HERMES_HOME"] = str(Path(request["hermesHomeDir"]).resolve())
    os.environ["HERMES_WRITE_SAFE_ROOT"] = str(Path(request["workspaceDir"]).resolve())
    os.environ["HERMES_INTERACTIVE"] = "1"

    session_id = str(request.get("sessionId") or uuid.uuid4())
    os.environ["HERMES_SESSION_KEY"] = session_id

    tool_events: List[Dict[str, Any]] = []
    approval_events: List[Dict[str, Any]] = []
    clarify_events: List[Dict[str, Any]] = []
    event_order = 0

    session_db = SessionDB(Path(os.environ["HERMES_HOME"]) / "state.db")

    try:
        _seed_session_db(session_db, request)

        def _tool_started(tool_call_id: str, name: str, args: Dict[str, Any]) -> None:
            nonlocal event_order
            event_order += 1
            tool_events.append({
                "phase": "start",
                "order": event_order,
                "toolCallId": tool_call_id,
                "name": name,
                "args": _jsonable(args),
            })

        def _tool_completed(tool_call_id: str, name: str, args: Dict[str, Any], result: Any) -> None:
            nonlocal event_order
            event_order += 1
            tool_events.append({
                "phase": "complete",
                "order": event_order,
                "toolCallId": tool_call_id,
                "name": name,
                "args": _jsonable(args),
                "result": _jsonable(result),
            })

        follow_ups = request.get("followUps") or {}
        def _next_order() -> int:
            nonlocal event_order
            event_order += 1
            return event_order

        clarify_callback = _build_clarify_callback(follow_ups, clarify_events, _next_order)
        approval_callback = _build_approval_callback(follow_ups, approval_events, _next_order)
        set_approval_callback(approval_callback)

        model = request.get("model") or {}
        inference_base_url = str(model.get("inferenceBaseUrl") or "")
        provider = _guess_provider(inference_base_url, str(model.get("provider") or ""))

        agent = AIAgent(
            base_url=inference_base_url,
            api_key=model.get("apiKey"),
            provider=provider,
            model=str(model.get("exposedModel") or model.get("providerModel") or ""),
            max_iterations=int(request.get("maxTurns") or 10),
            enabled_toolsets=list(request.get("toolsets") or []),
            quiet_mode=True,
            platform="cli",
            skip_context_files=True,
            session_id=session_id,
            session_db=session_db,
            request_overrides=_request_overrides(request.get("generation") or {}),
            tool_start_callback=_tool_started,
            tool_complete_callback=_tool_completed,
            clarify_callback=clarify_callback,
            persist_session=True,
        )

        result = agent.run_conversation(str(request.get("prompt") or ""))

        _write_json(result_path, {
            "ok": True,
            "sessionId": session_id,
            "finalResponse": result.get("final_response"),
            "completed": bool(result.get("completed")),
            "partial": bool(result.get("partial")),
            "messages": _jsonable(result.get("messages") or []),
            "toolEvents": tool_events,
            "approvalEvents": approval_events,
            "clarifyEvents": clarify_events,
            "storedMessages": _jsonable(session_db.get_messages(session_id)),
            "apiCalls": result.get("api_calls"),
            "provider": provider,
            "model": result.get("model"),
            "inputTokens": result.get("input_tokens"),
            "outputTokens": result.get("output_tokens"),
        })
        return 0
    except BaseException as error:
        _write_json(result_path, {
            "ok": False,
            "sessionId": session_id,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "toolEvents": tool_events,
            "approvalEvents": approval_events,
            "clarifyEvents": clarify_events,
        })
        return 1
    finally:
        try:
            session_db.close()
        except Exception:
            pass
        set_approval_callback(None)


if __name__ == "__main__":
    sys.exit(main())
