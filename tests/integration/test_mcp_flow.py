import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastmcp import Client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from main import app
from mcp_server import create_mcp_server
from src.models import ReplyDraft, SearchResult, SearchResults, SearchSource
from tests.fakes import FakeSessionContainer, make_session_service
from tests.integration.test_reply_flow import TEST_ENV, fake_llm_client


class McpFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_after_restart_returns_each_revision_outcome_and_citations(self):
        container = FakeSessionContainer()
        source = SearchResult(source='course_materials', content='Original course evidence')
        drafts = [
            ReplyDraft(reply='Supported answer', status='answered', cited_source_ids=['source_1']),
            ReplyDraft(reply='Which assignment?', status='needs_clarification', cited_source_ids=[]),
        ]
        with patch('main.reply_sessions', make_session_service(container)), patch(
            'src.services.reply_sessions.search', new=AsyncMock(return_value=SearchResults(query='Question', results=[source])),
        ), patch('src.agents.reply_agent.request_structured_output', new=AsyncMock(side_effect=drafts)):
            async with Client(create_mcp_server(app)) as client:
                created = await client.call_tool('create_reply_session', {'question': 'Question', 'course_id': 'course-1'})
                args = {'conversation_id': created.structured_content['conversation_id']}
                await client.call_tool('generate_session_reply', args)
                await client.call_tool('request_reply_revision', {**args, 'feedback': 'Which assignment?'})
                with patch('main.reply_sessions', make_session_service(container)):
                    restored = (await client.call_tool('get_reply_session', args)).structured_content
        self.assertEqual(restored['citations'], [])
        self.assertEqual([revision['outcome'] for revision in restored['revisions']], ['answered', 'needs_clarification'])
        self.assertEqual([revision['citations'] for revision in restored['revisions']], [[source.model_dump(mode='json')], []])
        for revision in restored['revisions']:
            self.assertIsNotNone(revision['completed_at'])
            self.assertNotIn('evidence', revision)
        self.assertNotIn('retrieved_context', restored)

    async def test_complete_workflow_through_api_and_external_boundaries(self):
        initial = ReplyDraft(reply="Initial answer", status="answered", cited_source_ids=[])
        revision = ReplyDraft(reply="Revised answer", status="answered", cited_source_ids=[])
        llm = fake_llm_client(initial, revision)
        container = Mock()
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        container.read_item.side_effect = CosmosResourceNotFoundError(status_code=404)
        settings = {"embedding_dimensions": 3}

        with patch.dict(os.environ, TEST_ENV), patch("main.reply_sessions", make_session_service()), patch("src.workflow.llm.AsyncOpenAI", return_value=llm), patch("src.services.reply_sessions.search", new=AsyncMock(return_value=SearchResults(query="Question", results=[]))), patch("src.bootstrap.load_cosmos_qna_settings", return_value=settings), patch("src.repositories.historical_qna._get_container", return_value=container):
            async with Client(create_mcp_server(app)) as client:
                created = await client.call_tool("create_reply_session", {"question": "Question", "course_id": "course-1"})
                conversation_id = created.structured_content["conversation_id"]
                args = {"conversation_id": conversation_id}
                generated = await client.call_tool("generate_session_reply", args)
                self.assertEqual(generated.structured_content["status"], "awaiting_review")
                revised = await client.call_tool("request_reply_revision", {**args, "feedback": "Shorter"})
                self.assertEqual(revised.structured_content["revisions"][-1]["revision_content"], "Revised answer")
                await client.call_tool("accept_reply_session", args)
                final = await client.call_tool("get_reply_session", args)
                self.assertEqual(final.structured_content["status"], "accepted")
                conflict = await client.call_tool("generate_session_reply", args, raise_on_error=False)
                self.assertTrue(conflict.is_error)
                self.assertIn("HTTP 409", conflict.content[0].text)

        container.create_item.assert_called_once()
        item = container.create_item.call_args.kwargs["body"]
        self.assertEqual(item["accepted_response"], "Revised answer")
        self.assertEqual(item["id"], conversation_id)
        self.assertEqual(llm.responses.parse.await_count, 2)

    async def test_streamable_http_discovers_and_calls_typed_tools(self):
        server = create_mcp_server(app)
        http_app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
        citation = SearchResult(source=SearchSource.COURSE_MATERIALS, content="Submit a PDF report.")
        answer = ReplyDraft(reply="Submit a PDF report.", status="answered", cited_source_ids=["source_1"])
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
        with patch("main.reply_sessions", make_session_service()), patch(
            "src.services.reply_sessions.search", new=AsyncMock(return_value=SearchResults(query="Question", results=[citation])),
        ), patch("src.agents.reply_agent.request_structured_output", new=AsyncMock(return_value=answer)):
            async with http_app.router.lifespan_context(http_app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=http_app), base_url="http://mcp.test", headers=headers) as client:
                    initialized = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
                    }})
                    self.assertEqual(initialized.status_code, 200)
                    discovered = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                    self.assertEqual(len(discovered.json()["result"]["tools"]), 5)
                    result = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                        "name": "create_reply_session", "arguments": {"question": "Question", "course_id": "c1", "user_id": "u1"},
                    }})
                    created = result.json()["result"]["structuredContent"]
                    self.assertEqual(created["status"], "pending_generation")
                    generated = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                        "name": "generate_session_reply", "arguments": {"conversation_id": created["conversation_id"]},
                    }})
                    session = generated.json()["result"]["structuredContent"]
                    self.assertEqual(session["status"], "awaiting_review")
                    self.assertEqual(session["revisions"][-1]["revision_content"], answer.reply)
                    self.assertEqual(session["citations"], [citation.model_dump(mode="json")])
                    self.assertEqual(session["revisions"][-1]["citations"], session["citations"])
                    self.assertEqual(session["revisions"][-1]["outcome"], 'answered')
                    self.assertIsNotNone(session["revisions"][-1]["completed_at"])
                    self.assertNotIn('evidence', session['revisions'][-1])

    async def test_stdio_process_keeps_protocol_and_logs_separate(self):
        root = Path(__file__).resolve().parents[2]
        params = StdioServerParameters(
            command=sys.executable, args=[str(root / "mcp_server.py")], cwd=str(root),
            env={"LOG_FORMAT": "json", "LOG_LEVEL": "INFO", "AZURE_COSMOS_REPLY_SESSIONS_CONTAINER": "qa_memory"},
        )
        with tempfile.TemporaryFile(mode="w+") as stderr:
            async with asyncio.timeout(15):
                async with stdio_client(params, errlog=stderr) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertEqual(len(tools.tools), 5)
                        failed = await session.call_tool("get_reply_session", {"conversation_id": "missing"})
                        self.assertTrue(failed.isError)
                        self.assertIn("HTTP 503", failed.content[0].text)
            stderr.seek(0)
            events = [json.loads(line) for line in stderr if line.startswith("{")]
        self.assertTrue(any(event["event"] == "mcp.tool.started" for event in events))
        self.assertTrue(any(event.get("status_code") == 503 for event in events))
