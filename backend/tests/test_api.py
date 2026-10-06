import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app, documents, performances, quizzes
from app.nvidia import NvidiaProviderError


SAMPLE = b"""Chapter 1: Neural Networks
Neural networks learn patterns from examples. A training dataset contains examples and labels.
The model adjusts its weights during training to reduce prediction error.

Chapter 2: Evaluation
Precision measures the fraction of predicted positives that are correct. Recall measures the fraction of actual positives that are found.
"""


class ApiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        documents.clear()
        quizzes.clear()
        performances.clear()

    def test_upload_summarize_ask_quiz_score_and_delete(self):
        upload = self.client.post(
            "/api/documents", files={"file": ("notes.txt", SAMPLE, "text/plain")}
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        document_id = upload.json()["id"]

        summary = self.client.post(
            f"/api/documents/{document_id}/summary", json={"intent": "university_exam"}
        )
        self.assertEqual(summary.status_code, 200)
        self.assertTrue(summary.json()["key_points"])

        answer = self.client.post(
            f"/api/documents/{document_id}/ask",
            json={"question": "How do neural networks learn patterns?"},
        )
        self.assertTrue(answer.json()["grounded"])
        self.assertTrue(answer.json()["citations"])

        quiz = self.client.post(f"/api/documents/{document_id}/quizzes", json={"count": 2})
        self.assertEqual(quiz.status_code, 201)
        quiz_data = quiz.json()
        self.assertTrue(all("_answer" not in item for item in quiz_data["questions"]))
        submission = self.client.post(
            f"/api/quizzes/{quiz_data['id']}/submit", json={"answers": {}}
        )
        self.assertEqual(submission.json()["total"], len(quiz_data["questions"]))
        self.assertTrue(submission.json()["weak_topics"])

        deleted = self.client.delete(f"/api/documents/{document_id}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/documents/{document_id}").status_code, 404)

    def test_configured_nvidia_provider_handles_summary_requests(self):
        upload = self.client.post(
            "/api/documents", files={"file": ("notes.txt", SAMPLE, "text/plain")}
        )
        document_id = upload.json()["id"]
        generated = {"generation_mode": "nvidia", "key_points": []}

        with patch("app.main.NVIDIA_API_KEY", "test-key"), patch(
            "app.main.make_personalized_summary", new_callable=AsyncMock, return_value=generated
        ) as provider:
            response = self.client.post(
                f"/api/documents/{document_id}/summary",
                json={"learning_level": "beginner"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["generation_mode"], "nvidia")
        self.assertEqual(provider.await_args.kwargs["api_key"], "test-key")
        self.assertEqual(provider.await_args.kwargs["learning_level"], "beginner")

    def test_nvidia_failure_is_reported_without_extractive_fallback(self):
        upload = self.client.post(
            "/api/documents", files={"file": ("notes.txt", SAMPLE, "text/plain")}
        )
        document_id = upload.json()["id"]

        with patch("app.main.NVIDIA_API_KEY", "test-key"), patch(
            "app.main.make_personalized_summary",
            new_callable=AsyncMock,
            side_effect=NvidiaProviderError("NVIDIA summarization failed. Try again later."),
        ):
            response = self.client.post(f"/api/documents/{document_id}/summary", json={})

        self.assertEqual(response.status_code, 502)
        self.assertIn("NVIDIA summarization failed", response.json()["detail"])

    def test_unsupported_upload_is_a_safe_client_error(self):
        response = self.client.post(
            "/api/documents", files={"file": ("malware.exe", b"not executable", "application/octet-stream")}
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("Traceback", response.text)

    def test_document_from_another_owner_is_not_readable_or_deletable(self):
        upload = self.client.post(
            "/api/documents", files={"file": ("notes.txt", SAMPLE, "text/plain")}
        )
        document_id = upload.json()["id"]
        documents[document_id]["owner_id"] = "another-user"

        self.assertEqual(self.client.get(f"/api/documents/{document_id}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/documents/{document_id}").status_code, 404)
        self.assertIn(document_id, documents)


if __name__ == "__main__":
    unittest.main()