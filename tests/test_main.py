import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from src.models import ReplyResponse


class ReplyEndpointTests(unittest.TestCase):
    def test_accepts_minimal_reply_request(self):
        async def fake_run_orchestration(request):
            return ReplyResponse(reply="ok", status="answered", citations=[])

        with patch("main.run_orchestration", fake_run_orchestration):
            client = TestClient(app)
            response = client.post(
                "/reply",
                json={
                    "question": "What is due this week?",
                    "user_id": "u1",
                    "course_id": "c1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "ok")

    def test_accepts_chat_history_without_created_at(self):
        async def fake_run_orchestration(request):
            return ReplyResponse(reply="ok", status="answered", citations=[])

        with patch("main.run_orchestration", fake_run_orchestration):
            client = TestClient(app)
            response = client.post(
                "/reply",
                json={
                    "question": "What is due this week?",
                    "user_id": "u1",
                    "course_id": "c1",
                    "chat_history": [{"role": "user", "content": "hello"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "ok")


if __name__ == "__main__":
    unittest.main()