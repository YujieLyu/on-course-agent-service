import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.models import ReplyDraft
from src.workflow.llm import parse_structured_response, request_structured_output


class StructuredResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_stateless_call_returns_validated_output(self):
        draft = ReplyDraft(reply='A reply', status='answered', cited_source_ids=[])
        response = SimpleNamespace(output=[], output_parsed=draft, output_text='')
        client = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(return_value=response)))
        with patch('src.workflow.llm.create_openai_client', return_value=client):
            result = await request_structured_output(model='test-model', instructions='Answer', input='Question', output_type=ReplyDraft)
        self.assertEqual(result, draft)
        arguments = client.responses.parse.await_args.kwargs
        self.assertFalse(arguments['store'])
        self.assertNotIn('conversation', arguments)
        self.assertNotIn('previous_response_id', arguments)

    def test_refusals_and_missing_structured_output_are_errors(self):
        for response in [
            SimpleNamespace(output=[SimpleNamespace(type='message', content=[SimpleNamespace(type='refusal', refusal='Refused')])]),
            SimpleNamespace(output=[], output_parsed=None, output_text='Malformed output'),
        ]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                parse_structured_response(response, ReplyDraft)
