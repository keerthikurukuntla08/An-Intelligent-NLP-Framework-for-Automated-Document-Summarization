import json
import unittest
from unittest.mock import patch

import httpx

from app.core import parse_document
from app.nvidia import (
    DEFAULT_NVIDIA_MODEL,
    NVIDIA_API_URL,
    NvidiaProviderError,
    make_personalized_summary,
)


SAMPLE = b"""Chapter 1: Neural Networks
Neural networks learn patterns from examples. A training dataset contains examples and labels.
The model adjusts its weights during training to reduce prediction error.

Chapter 2: Evaluation
Precision measures the fraction of predicted positives that are correct. Recall measures the fraction of actual positives that are found.
"""


class NvidiaSummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.document = parse_document("notes.txt", SAMPLE)

    def client_factory(self, handler):
        transport = httpx.MockTransport(handler)
        original_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            return original_client(*args, transport=transport, **kwargs)

        return factory

    async def test_summary_uses_nvidia_and_rebuilds_citations_from_valid_chunk_ids(self):
        requests = []

        def handler(request):
            requests.append(request)
            payload = json.loads(request.content)
            user_content = json.loads(payload["messages"][1]["content"])
            chunk_id = user_content["evidence"][0]["chunk_id"]
            content = json.dumps(
                {"key_points": [{"text": "A beginner-friendly explanation.", "source_chunk_id": chunk_id}]}
            )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": content}}]},
                request=request,
            )

        with patch("app.nvidia.httpx.AsyncClient", side_effect=self.client_factory(handler)):
            result = await make_personalized_summary(
                self.document,
                mode="concise",
                learning_level="beginner",
                intent="general",
                api_key="test-key",
            )

        self.assertEqual(requests[0].url, NVIDIA_API_URL)
        self.assertEqual(requests[0].headers["authorization"], "Bearer test-key")
        self.assertEqual(json.loads(requests[0].content)["model"], DEFAULT_NVIDIA_MODEL)
        self.assertEqual(result["generation_mode"], "nvidia")
        self.assertEqual(result["key_points"][0]["source"]["document"], "notes.txt")
        self.assertEqual(result["key_points"][0]["text"], "A beginner-friendly explanation.")

    async def test_summary_rejects_unprovided_source_ids(self):
        def handler(request):
            content = json.dumps(
                {"key_points": [{"text": "Unsupported claim.", "source_chunk_id": "invented-id"}]}
            )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": content}}]},
                request=request,
            )

        with patch("app.nvidia.httpx.AsyncClient", side_effect=self.client_factory(handler)):
            with self.assertRaisesRegex(NvidiaProviderError, "valid evidence"):
                await make_personalized_summary(
                    self.document,
                    mode="concise",
                    learning_level="beginner",
                    intent="general",
                    api_key="test-key",
                )


if __name__ == "__main__":
    unittest.main()