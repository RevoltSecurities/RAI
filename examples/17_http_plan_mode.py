"""17_http_plan_mode.py — Plan mode over the HTTP harness.

Demonstrates the full plan mode lifecycle using RAIHTTPServer + SSE client:

  1. Start an HTTP server with a plan-mode-enabled agent
  2. Submit a run with plan_mode=True
  3. Stream SSE events — catch plan_ready
  4. Approve (or reject+revise) the plan via the REST API
  5. Stream the task-execution phase — step_start / step_complete events
  6. Observe self-learning phase on completion

This is the HTTP equivalent of the Claude SDK claude/07_plan_then_execute.py.
The key difference: plan approval is a REST call, not a local callback.

Usage:
    python examples/17_http_plan_mode.py
    python examples/17_http_plan_mode.py --task "pentest login.example.com"
    python examples/17_http_plan_mode.py --auto-approve  # skip interactive prompt
    python examples/17_http_plan_mode.py --port 9100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import threading
import time
from typing import Any

import httpx
import uvicorn

from rai.sdk import (
    RAIAgent,
    RAIAgentBuilder,
    get_security_tools,
    get_builtin_tools,
    DEFAULT_MODEL,
)
from rai.sdk.harness import HTTPConfig, RAIHTTPServer


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def _build_server(model: str, host: str, port: int) -> RAIHTTPServer:
    builder = (
        RAIAgent.builder()
        .model(model)
        .agent_name("planner")
        .add_tools(get_security_tools() + get_builtin_tools())
        .without_hitl()
        .system_prompt(
            "You are a methodical security operator. When given a target, "
            "always enter plan mode to produce a step-by-step engagement plan "
            "before executing any tools. Each plan step should be a concrete, "
            "measurable action with a clear success criterion."
        )
    )
    config = HTTPConfig(host=host, port=port)
    server = RAIHTTPServer(config)
    server.register(builder)
    return server


def _start_background(server: RAIHTTPServer, host: str, port: int) -> uvicorn.Server:
    log_file = os.path.join(tempfile.gettempdir(), "rai-plan-demo.log")
    log_cfg = {
        "version": 1, "disable_existing_loggers": False,
        "formatters": {"d": {"fmt": "%(levelname)s %(message)s"}},
        "handlers": {"f": {
            "class": "logging.FileHandler", "filename": log_file, "mode": "a", "formatter": "d"
        }},
        "loggers": {
            "uvicorn": {"handlers": ["f"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["f"], "level": "INFO", "propagate": False},
        },
    }
    app = server._build_app()
    cfg = uvicorn.Config(app, host=host, port=port, log_config=log_cfg, loop="asyncio")
    uv = uvicorn.Server(cfg)

    def _run() -> None:
        import asyncio as _aio
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
        loop.run_until_complete(uv.serve())

    threading.Thread(target=_run, daemon=True).start()
    return uv


def _wait_ready(base: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/ok", timeout=0.5).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# SSE consumer helpers
# ---------------------------------------------------------------------------

def _iter_sse(response: httpx.Response):
    """Yield parsed SSE events from a streaming response."""
    event_type = "message"
    data_lines: list[str] = []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line == "" and data_lines:
            raw = "\n".join(data_lines)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"text": raw}
            yield event_type, payload
            event_type = "message"
            data_lines = []


# ---------------------------------------------------------------------------
# Main plan flow
# ---------------------------------------------------------------------------

async def run_plan_demo(
    base: str,
    agent: str,
    task: str,
    auto_approve: bool,
) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=None) as client:

        # 1. Create a run with plan_mode=True
        resp = await client.post(
            f"/agents/{agent}/runs",
            json={"input": task, "plan_mode": True},
        )
        resp.raise_for_status()
        run_id = resp.json()["run_id"]
        print(f"Run started: {run_id}")
        print("Waiting for plan...\n")

        plan_ready = False
        plan_steps: list[dict[str, Any]] = []

        # 2. Stream SSE events — collect tokens until plan_ready fires
        async with client.stream("GET", f"/agents/{agent}/runs/{run_id}/stream") as stream:
            event_type = "message"
            data_lines: list[str] = []

            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line == "" and data_lines:
                    raw = "\n".join(data_lines)
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = {"text": raw}
                    data_lines = []

                    if event_type == "token":
                        print(payload.get("text", ""), end="", flush=True)

                    elif event_type == "plan_ready":
                        print("\n\n--- PLAN READY ---")
                        plan_steps = payload.get("steps", [])
                        for i, step in enumerate(plan_steps, 1):
                            print(f"  {i}. {step.get('title', step.get('description', '?'))}")
                        plan_ready = True
                        break  # exit SSE loop — need to approve/reject

                    elif event_type == "run_end":
                        print("\n--- RUN ENDED (no plan produced) ---")
                        return

        if not plan_ready:
            print("Agent did not produce a plan.")
            return

        # 3. Approve or reject
        if auto_approve:
            decision = "approve"
        else:
            print("\nApprove this plan? [approve/reject/edit] ", end="", flush=True)
            decision = input().strip().lower() or "approve"

        if decision == "reject":
            feedback = input("Rejection feedback: ").strip()
            resp = await client.post(
                f"/agents/{agent}/runs/{run_id}/plan/reject",
                json={"feedback": feedback},
            )
            resp.raise_for_status()
            print("Plan rejected — agent will revise and resubmit.")
            return

        if decision == "edit":
            # Edit individual step titles inline
            edited_steps = []
            for step in plan_steps:
                new_title = input(
                    f"Step '{step.get('title', '?')}' → (enter to keep): "
                ).strip()
                edited_steps.append({**step, "title": new_title or step.get("title", "")})
            resp = await client.post(
                f"/agents/{agent}/runs/{run_id}/plan/approve",
                json={"edited_steps": edited_steps},
            )
        else:
            resp = await client.post(
                f"/agents/{agent}/runs/{run_id}/plan/approve",
                json={},
            )
        resp.raise_for_status()
        print("\n--- PLAN APPROVED — execution starting ---\n")

        # 4. Re-attach SSE stream for task execution
        async with client.stream("GET", f"/agents/{agent}/runs/{run_id}/stream") as stream:
            event_type = "message"
            data_lines = []
            current_step = ""

            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                elif line == "" and data_lines:
                    raw = "\n".join(data_lines)
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = {"text": raw}
                    data_lines = []

                    if event_type == "token":
                        print(payload.get("text", ""), end="", flush=True)

                    elif event_type == "step_start":
                        current_step = payload.get("title", payload.get("step_number", "?"))
                        print(f"\n\n▶ Step {current_step}")

                    elif event_type == "step_complete":
                        print(f"  ✓ Step {current_step} done")

                    elif event_type == "step_blocked":
                        print(f"  ✗ Step {current_step} BLOCKED: {payload.get('reason', '')}")

                    elif event_type == "tool_start":
                        print(f"  [tool] {payload.get('name', '?')} …", end="", flush=True)

                    elif event_type == "tool_end":
                        print(" done")

                    elif event_type == "plan_completed":
                        print("\n\n--- ALL STEPS COMPLETE ---")

                    elif event_type == "run_end":
                        status = payload.get("status", "")
                        cost = payload.get("cost_usd", 0)
                        turns = payload.get("turns", 0)
                        print(f"\nRun finished  status={status}  cost=${cost:.4f}  turns={turns}")
                        break


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Plan mode via HTTP harness")
    p.add_argument("--task", default="Perform a security assessment of https://example.com")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=9100, type=int)
    p.add_argument("--auto-approve", action="store_true",
                   help="Skip interactive plan approval prompt")
    args = p.parse_args()

    agent = "planner"
    base = f"http://{args.host}:{args.port}"

    server = _build_server(args.model, args.host, args.port)
    uv = _start_background(server, args.host, args.port)

    print(f"Starting server at {base} …", end="", flush=True)
    if not _wait_ready(base):
        print(" timed out")
        return
    print(" ready\n")

    try:
        asyncio.run(run_plan_demo(base, agent, args.task, args.auto_approve))
    finally:
        uv.should_exit = True


if __name__ == "__main__":
    main()
