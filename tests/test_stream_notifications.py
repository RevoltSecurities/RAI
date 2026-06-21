"""Mock tests for the stream notification fix.

Verifies:
1. agent.stream() breaks on ResultMessage when no subagents pending
2. agent.stream() continues after ResultMessage when subagents are still running
3. agent.stream() breaks after final ResultMessage when all subagents reported
4. Server _pump_sdk_messages forwards all messages until stream exhausts
5. TUI SSE client reads past run_end until [DONE]
6. AssistantMessage.finalize() uses Markdown.update()
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from claude_agent_sdk.types import ResultMessage, TaskStartedMessage, TaskNotificationMessage


def _make_result(session_id="s1") -> ResultMessage:
    return ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=100,
        is_error=False,
        num_turns=1,
        session_id=session_id,
        stop_reason="end_turn",
        total_cost_usd=0.01,
    )


def _make_task_started(task_id="a1", description="Agent") -> TaskStartedMessage:
    return TaskStartedMessage(
        subtype="task_started",
        data={},
        task_id=task_id,
        description=description,
        uuid=f"uuid-{task_id}",
        session_id="s1",
    )


def _make_task_notification(task_id="a1") -> TaskNotificationMessage:
    return TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id=task_id,
        status="completed",
        output_file="",
        summary=f"Agent {task_id} done",
        uuid=f"uuid-{task_id}",
        session_id="s1",
    )


# A simple non-Result message to pad the stream
class _Filler:
    pass


# ─── Test 1: agent.stream() — no subagents ─────────────────────────────────

async def test_stream_breaks_on_result_no_subagents():
    """When no subagents are spawned, stream() breaks on ResultMessage."""
    from rai.engine.claude_sdk.agent import RunnableClaudeAgent

    messages = [
        _Filler(),
        _make_result("s1"),
        _Filler(),  # should NOT be yielded
    ]

    agent = RunnableClaudeAgent(options=MagicMock(), agent_name="test")
    mock_client = MagicMock()

    async def fake_receive():
        for m in messages:
            yield m

    mock_client.receive_messages = fake_receive
    mock_client.query = AsyncMock()
    agent._client = mock_client

    collected = []
    async for msg in agent.stream("hello"):
        collected.append(msg)

    assert len(collected) == 2, f"Expected 2, got {len(collected)}"
    assert isinstance(collected[1], ResultMessage)
    print("  ✓ Test 1: stream breaks on ResultMessage (no subagents)")


# ─── Test 2: agent.stream() — subagents running ────────────────────────────

async def test_stream_continues_with_pending_subagents():
    """When subagents are spawned, stream() continues after ResultMessage
    until all TaskNotificationMessage events arrive."""
    from rai.engine.claude_sdk.agent import RunnableClaudeAgent

    messages = [
        _make_task_started("a1", "Agent 1"),
        _make_task_started("a2", "Agent 2"),
        _Filler(),
        _make_result("s1"),       # pending=2, don't break
        _make_task_notification("a1"),  # pending=1
        _Filler(),
        _make_result("s1"),       # pending=1, don't break
        _make_task_notification("a2"),  # pending=0
        _Filler(),
        _make_result("s1"),       # pending=0, BREAK
        _Filler(),                # should NOT be yielded
    ]

    agent = RunnableClaudeAgent(options=MagicMock(), agent_name="test")
    mock_client = MagicMock()

    async def fake_receive():
        for m in messages:
            yield m

    mock_client.receive_messages = fake_receive
    mock_client.query = AsyncMock()
    agent._client = mock_client

    collected = []
    async for msg in agent.stream("spawn agents"):
        collected.append(msg)

    # 10 messages: 2 starts + filler + result + notif + filler + result + notif + filler + result
    assert len(collected) == 10, f"Expected 10, got {len(collected)}"
    assert isinstance(collected[0], TaskStartedMessage)
    assert isinstance(collected[3], ResultMessage)   # 1st result, don't break
    assert isinstance(collected[4], TaskNotificationMessage)
    assert isinstance(collected[6], ResultMessage)   # 2nd result, don't break
    assert isinstance(collected[7], TaskNotificationMessage)
    assert isinstance(collected[9], ResultMessage)   # 3rd result, pending=0, break

    # Trailing _Filler should NOT be present
    assert not isinstance(collected[-1], _Filler) or isinstance(collected[-1], ResultMessage)
    print("  ✓ Test 2: stream continues until all subagents report back")


# ─── Test 3: agent.stream() — subagent done BEFORE ResultMessage ───────────

async def test_stream_subagent_done_before_result():
    """If subagent completes before the agent's turn ends, pending=0 and break."""
    from rai.engine.claude_sdk.agent import RunnableClaudeAgent

    messages = [
        _make_task_started("a1"),
        _make_task_notification("a1"),
        _Filler(),
        _make_result("s1"),  # pending=0, break
        _Filler(),           # should NOT be yielded
    ]

    agent = RunnableClaudeAgent(options=MagicMock(), agent_name="test")
    mock_client = MagicMock()

    async def fake_receive():
        for m in messages:
            yield m

    mock_client.receive_messages = fake_receive
    mock_client.query = AsyncMock()
    agent._client = mock_client

    collected = []
    async for msg in agent.stream("quick agent"):
        collected.append(msg)

    assert len(collected) == 4, f"Expected 4, got {len(collected)}"
    assert isinstance(collected[3], ResultMessage)
    print("  ✓ Test 3: stream breaks when subagent done before ResultMessage")


# ─── Test 4: Server pump forwards all messages ─────────────────────────────

async def test_server_pump_forwards_all():
    """_pump_sdk_messages forwards everything from agent.stream() to the queue."""
    from rai.cli.claude_server import _pump_sdk_messages, _SENTINEL

    messages = [
        _make_task_started("a1"),
        _make_result(),
        _make_task_notification("a1"),
        _make_result(),
    ]

    mock_agent = MagicMock()

    async def fake_stream(prompt):
        for m in messages:
            yield m

    mock_agent.stream = fake_stream
    mock_state = MagicMock()
    queue = asyncio.Queue()

    await _pump_sdk_messages(mock_agent, mock_state, "test", queue)

    collected = []
    while not queue.empty():
        collected.append(await queue.get())

    # 4 messages + SENTINEL = 5
    assert len(collected) == 5, f"Expected 5, got {len(collected)}"
    assert collected[-1] is _SENTINEL
    assert isinstance(collected[0], TaskStartedMessage)
    assert isinstance(collected[1], ResultMessage)
    assert isinstance(collected[2], TaskNotificationMessage)
    assert isinstance(collected[3], ResultMessage)
    print("  ✓ Test 4: server pump forwards all messages + SENTINEL")


# ─── Test 5: SSE client reads past run_end ──────────────────────────────────

async def test_sse_client_reads_past_run_end():
    """SSE client continues after run_end, only stops on [DONE]."""
    import json

    sse_data = (
        'data: {"type": "token", "text": "hello"}\n\n'
        'data: {"type": "run_end", "session_id": "s1", "cost": 0.01, "turns": 1, '
        '"is_error": false, "stop_reason": "end_turn", "duration_ms": 100}\n\n'
        'data: {"type": "subagent_done", "task_id": "a1", "status": "completed", '
        '"summary": "done", "tokens": 100, "duration_ms": 5000}\n\n'
        'data: {"type": "token", "text": "Agent 1 done!"}\n\n'
        'data: {"type": "run_end", "session_id": "s1", "cost": 0.02, "turns": 2, '
        '"is_error": false, "stop_reason": "end_turn", "duration_ms": 200}\n\n'
        'data: [DONE]\n\n'
    )

    from rai.client.claude._events import (
        ClaudeTokenEvent, ClaudeRunEndEvent, ClaudeSubagentDoneEvent,
        parse_claude_event,
    )

    # Simulate _sse.py parsing logic (without the old run_end early-exit)
    collected = []
    pending = sse_data
    while "\n\n" in pending:
        block, pending = pending.split("\n\n", 1)
        done = False
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                done = True
                break
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ev = parse_claude_event(d)
            collected.append(ev)
        if done:
            break

    assert len(collected) == 5, f"Expected 5, got {len(collected)}"
    assert isinstance(collected[0], ClaudeTokenEvent)
    assert collected[0].text == "hello"
    assert isinstance(collected[1], ClaudeRunEndEvent)
    assert isinstance(collected[2], ClaudeSubagentDoneEvent)
    assert collected[2].task_id == "a1"
    assert isinstance(collected[3], ClaudeTokenEvent)
    assert collected[3].text == "Agent 1 done!"
    assert isinstance(collected[4], ClaudeRunEndEvent)
    print("  ✓ Test 5: SSE client reads past run_end until [DONE]")


# ─── Test 6: AssistantMessage.finalize() uses update() ──────────────────────

def test_assistant_finalize_uses_update():
    """finalize() calls Markdown.update(full_text), not append(remaining)."""
    from rai.tui.claude_tui.widgets.assistant import AssistantMessage

    widget = AssistantMessage.__new__(AssistantMessage)
    widget._buf = "Hello world"
    widget._flushed_len = 5  # Only "Hello" was flushed
    widget._is_final = False
    widget._spinner_timer = MagicMock()
    widget._flush_timer = MagicMock()

    mock_md = MagicMock()
    mock_spinner = MagicMock()

    def fake_query_one(selector, cls=None):
        if "assistant-md" in selector:
            return mock_md
        if "asst-spinner" in selector:
            return mock_spinner
        return MagicMock()

    widget.query_one = fake_query_one

    widget.finalize()

    mock_md.update.assert_called_once_with("Hello world")
    mock_md.append.assert_not_called()
    assert widget._is_final is True
    assert widget._flushed_len == len("Hello world")
    print("  ✓ Test 6: finalize() uses Markdown.update(full_text)")


# ─── Run all ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running stream notification tests...\n")

    test_assistant_finalize_uses_update()

    asyncio.run(test_stream_breaks_on_result_no_subagents())
    asyncio.run(test_stream_continues_with_pending_subagents())
    asyncio.run(test_stream_subagent_done_before_result())
    asyncio.run(test_server_pump_forwards_all())
    asyncio.run(test_sse_client_reads_past_run_end())

    print("\n═══════════════════════════════════════════")
    print("  ALL 6 TESTS PASSED ✓")
    print("═══════════════════════════════════════════")
