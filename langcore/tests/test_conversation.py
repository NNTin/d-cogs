import unittest
from typing import Optional

try:
    from langchain_core.messages import AIMessage

    from langcore.conversation import ConversationManager
    from langcore.models import GuildConfig
except Exception as e:  # pragma: no cover
    AIMessage = None  # type: ignore[assignment]
    ConversationManager = None  # type: ignore[assignment]
    GuildConfig = None  # type: ignore[assignment]
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    def bind_tools(self, functions):
        return self

    async def ainvoke(self, messages):
        response = self._responses[self._idx]
        self._idx += 1
        return response


class FakeProvider:
    def __init__(self, llm):
        self._llm = llm

    async def get_chat_llm(self, guild_id: int, member_id: Optional[int] = None):
        return self._llm


@unittest.skipIf(ConversationManager is None, f"Optional dependencies unavailable: {_IMPORT_ERROR}")
class TestConversationAgentChat(unittest.IsolatedAsyncioTestCase):
    async def test_agent_chat_tool_call_object_does_not_crash(self):
        manager = ConversationManager()
        guild_id = 123
        member_id = 456
        channel_id = 789
        key = (member_id, channel_id, guild_id)

        conversation = manager.get_conversation(member_id, channel_id, guild_id)
        conversation.messages = [{"role": "user", "content": "hello"}]

        class FakeToolCall:
            def __init__(self, name: str, args: dict, id: str):
                self.name = name
                self.args = args
                self.id = id

        first = AIMessage(content="Calling tool")
        fake_tool_calls = [FakeToolCall(name="echo", args={"text": "hi"}, id="call_1")]
        try:
            first.tool_calls = fake_tool_calls  # type: ignore[assignment]
        except Exception:
            object.__setattr__(first, "tool_calls", fake_tool_calls)

        llm = FakeLLM([first, AIMessage(content="Done")])
        provider = FakeProvider(llm)

        async def echo(text: str):
            return f"echo:{text}"

        result = await manager.agent_chat(
            key=key,
            provider=provider,
            functions=[{"name": "echo"}],
            callbacks={"echo": echo},
            guild_id=guild_id,
            member_id=member_id,
            config=GuildConfig(),
        )

        self.assertEqual(result, "Done")
        self.assertTrue(any(m.get("role") == "tool" for m in conversation.messages))

    async def test_agent_chat_tool_call_updates_conversation_and_returns_final_response(self):
        manager = ConversationManager()
        guild_id = 123
        member_id = 456
        channel_id = 789
        key = (member_id, channel_id, guild_id)

        conversation = manager.get_conversation(member_id, channel_id, guild_id)
        conversation.messages = [{"role": "user", "content": "hello"}]

        tool_calls = [{"name": "echo", "args": {"text": "hi"}, "id": "call_1"}]
        llm = FakeLLM(
            [
                AIMessage(content="Calling tool", tool_calls=tool_calls),
                AIMessage(content="Done"),
            ]
        )
        provider = FakeProvider(llm)

        async def echo(text: str):
            return f"echo:{text}"

        result = await manager.agent_chat(
            key=key,
            provider=provider,
            functions=[{"name": "echo"}],
            callbacks={"echo": echo},
            guild_id=guild_id,
            member_id=member_id,
            config=GuildConfig(),
        )

        self.assertEqual(result, "Done")
        self.assertTrue(conversation.messages)
        self.assertTrue(any(m.get("role") == "tool" for m in conversation.messages))

    async def test_agent_chat_missing_callback_generates_error_tool_message(self):
        manager = ConversationManager()
        guild_id = 1
        member_id = 2
        channel_id = 3
        key = (member_id, channel_id, guild_id)

        conversation = manager.get_conversation(member_id, channel_id, guild_id)
        conversation.messages = [{"role": "user", "content": "hello"}]

        tool_calls = [{"name": "missing_tool", "args": {}, "id": "call_1"}]
        llm = FakeLLM(
            [
                AIMessage(content="Calling tool", tool_calls=tool_calls),
                AIMessage(content="Done"),
            ]
        )
        provider = FakeProvider(llm)

        result = await manager.agent_chat(
            key=key,
            provider=provider,
            functions=[{"name": "missing_tool"}],
            callbacks={},
            guild_id=guild_id,
            member_id=member_id,
            config=GuildConfig(),
        )

        self.assertEqual(result, "Done")
        tool_messages = [m for m in conversation.messages if m.get("role") == "tool"]
        self.assertTrue(tool_messages)
        self.assertIn("not found", str(tool_messages[-1].get("content", "")).lower())
