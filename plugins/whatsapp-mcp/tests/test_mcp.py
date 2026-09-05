"""Exercise the actual stdio protocol with a synthetic WhatsApp database."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import test_repository
from whatsapp_mcp import server
from whatsapp_mcp.audio import AudioError


class MCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = test_repository.RepositoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    @asynccontextmanager
    async def connect(self):
        root = Path(__file__).resolve().parents[1]
        synthetic_home = (self.fixture.root / "Synthetic Home").resolve()
        synthetic_home.mkdir(exist_ok=True)
        parameters = StdioServerParameters(
            command=sys.executable, args=["-B", str(root / "scripts/server.py")],
            cwd=str(self.fixture.root),
            env={**os.environ, "HOME": str(synthetic_home),
                 "WHATSAPP_MCP_DB_PATH": str(self.fixture.db),
                 "WHATSAPP_MCP_MEDIA_ROOT": str(self.fixture.media),
                 "WHATSAPP_MCP_WHISPER_MODEL": "/nonexistent/test-model.bin"},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                self.session = session
                await session.initialize()
                yield session

    async def test_stdio_protocol(self):
        async with self.connect():
            await self.verify_tools_are_read_only_and_have_no_send_or_sql_entrypoint()
            await self.verify_full_read_flow_works_without_whisper()
            await self.verify_bad_arguments_return_safe_errors()

    async def call(self, name, arguments):
        result = await self.session.call_tool(name, arguments)
        self.assertFalse(result.isError)
        self.assertIsInstance(result.structuredContent, dict)
        return result.structuredContent

    async def verify_tools_are_read_only_and_have_no_send_or_sql_entrypoint(self):
        result = await self.session.list_tools()
        self.assertEqual({t.name for t in result.tools}, {
            "whatsapp_status", "whatsapp_list_chats", "whatsapp_read_chat",
            "whatsapp_search_messages", "whatsapp_get_message", "whatsapp_transcribe_audio",
            "whatsapp_onboarding", "whatsapp_setup", "whatsapp_get_media_url",
        })
        for tool in result.tools:
            self.assertEqual(tool.annotations.readOnlyHint, tool.name != "whatsapp_setup")
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertEqual(tool.annotations.openWorldHint, tool.name == "whatsapp_setup")

    async def verify_full_read_flow_works_without_whisper(self):
        status = await self.call("whatsapp_status", {})
        self.assertTrue(status["database"]["available"])
        self.assertFalse(status["audio"]["available"])
        self.assertIn("whisper_model", status["audio"]["missing"])
        chats = await self.call("whatsapp_list_chats", {"query": "Sample", "kind": "group"})
        chat_id = chats["chats"][0]["chat_id"]
        first = await self.call("whatsapp_read_chat", {"chat_id": chat_id, "limit": 2})
        second = await self.call("whatsapp_read_chat", {
            "chat_id": chat_id, "limit": 2, "before_id": first["next_before_id"],
        })
        self.assertEqual([m["id"] for m in first["messages"]], [12, 1])
        self.assertEqual([m["id"] for m in second["messages"]], [3, 9])
        found = await self.call("whatsapp_search_messages", {
            "query": "match", "chat_id": chat_id,
            "since": "2001-01-01T00:00:02Z", "until": "2001-01-01T00:00:03Z",
        })
        self.assertEqual([m["id"] for m in found["messages"]], [3])
        message = await self.call("whatsapp_get_message", {"message_id": 3})
        self.assertEqual(message["sender"]["jid"], "sender@lid")
        media = await self.call("whatsapp_get_media_url", {"message_id": 3})
        self.assertFalse(media["available"])

    async def verify_bad_arguments_return_safe_errors(self):
        invalid = await self.call("whatsapp_get_message", {"message_id": -1})
        self.assertFalse(invalid["ok"])
        invalid = await self.call("whatsapp_get_message", {"message_id": 2**80})
        self.assertFalse(invalid["ok"])
        audio = await self.call("whatsapp_transcribe_audio", {"message_id": 3})
        self.assertFalse(audio["ok"])


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_setup_cleanup(self):
        started, cleaned = threading.Event(), threading.Event()
        def fake_setup(database_index, enable_audio, cancel_event):
            started.set()
            if not cancel_event.wait(timeout=3):
                raise AssertionError("Cancellation did not reach setup worker")
            cleaned.set()
            raise AudioError("cancelled")
        with patch.object(server, "setup", fake_setup):
            task = asyncio.create_task(server.whatsapp_setup(enable_audio=True))
            await asyncio.to_thread(started.wait, 2)
            self.assertTrue(server._audio_lock.locked())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(cleaned.is_set())
            self.assertFalse(server._audio_lock.locked())

    async def test_cancel_waits_for_audio_cleanup_before_releasing_lock(self):
        started, cleaned = threading.Event(), threading.Event()
        def fake_transcribe(path, language, cancel_event):
            started.set()
            if not cancel_event.wait(timeout=3):
                raise AssertionError("Cancellation did not reach audio worker")
            time.sleep(0.05)
            cleaned.set()
            raise AudioError("cancelled")
        source_closed = threading.Event()
        @contextmanager
        def open_audio(message_id):
            try:
                yield 123
            finally:
                self.assertTrue(cleaned.is_set())
                source_closed.set()
        repository = SimpleNamespace(open_audio=open_audio)
        with patch.object(server, "Repository", return_value=repository), patch.object(server, "transcribe_audio", fake_transcribe):
            task = asyncio.create_task(server.whatsapp_transcribe_audio(1))
            await asyncio.to_thread(started.wait, 2)
            self.assertTrue(server._audio_lock.locked())
            busy = await server.whatsapp_transcribe_audio(2)
            self.assertFalse(busy["ok"])
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(cleaned.is_set())
            self.assertTrue(source_closed.is_set())
            self.assertFalse(server._audio_lock.locked())


if __name__ == "__main__":
    unittest.main()
