import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from openai import NotFoundError

from src.workflow.llm import create_conversation, supports_provider_conversation


class ConversationFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_local_id_when_endpoint_has_no_conversations_api(self):
        request = httpx.Request("POST", "https://openai.example.test/conversations")
        response = httpx.Response(404, request=request)
        client = SimpleNamespace(
            conversations=SimpleNamespace(
                create=AsyncMock(
                    side_effect=NotFoundError(
                        "Resource not found",
                        response=response,
                        body=None,
                    )
                )
            )
        )

        with patch("src.workflow.llm.create_openai_client", return_value=client), patch(
            "src.workflow.llm.logger.warning"
        ) as warning:
            conversation_id = await create_conversation()

        self.assertTrue(conversation_id.startswith("local_"))
        self.assertFalse(supports_provider_conversation(conversation_id))
        warning.assert_called_once()

    async def test_keeps_provider_conversation_when_supported(self):
        client = SimpleNamespace(
            conversations=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(id="conv_1"))
            )
        )

        with patch("src.workflow.llm.create_openai_client", return_value=client):
            conversation_id = await create_conversation()

        self.assertEqual(conversation_id, "conv_1")
        self.assertTrue(supports_provider_conversation(conversation_id))


if __name__ == "__main__":
    unittest.main()
