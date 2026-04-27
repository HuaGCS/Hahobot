import pytest

from hahobot.agent.tools.message import MessageTool


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
async def test_message_tool_records_cross_session_delivery() -> None:
    sent = []
    recorded = []

    async def send_callback(message):
        sent.append(message)

    async def record_callback(message):
        recorded.append(message)

    tool = MessageTool(send_callback=send_callback, record_callback=record_callback)
    tool.set_context("telegram", "source", session_key="cron:job-1")

    result = await tool.execute(content="hello", channel="telegram", chat_id="target")

    assert result == "Message sent to telegram:target"
    assert len(sent) == 1
    assert recorded == sent


@pytest.mark.asyncio
async def test_message_tool_does_not_record_current_session_delivery() -> None:
    sent = []
    recorded = []

    async def send_callback(message):
        sent.append(message)

    async def record_callback(message):
        recorded.append(message)

    tool = MessageTool(send_callback=send_callback, record_callback=record_callback)
    tool.set_context("telegram", "target", session_key="telegram:target")

    result = await tool.execute(content="hello")

    assert result == "Message sent to telegram:target"
    assert len(sent) == 1
    assert recorded == []
