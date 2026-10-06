import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.core import parse_document
from app.repository import SupabaseRepository


class SupabaseRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def repository_with_handler(self, handler):
        transport = httpx.MockTransport(handler)
        original_client = httpx.AsyncClient

        def client_factory(*args, **kwargs):
            return original_client(*args, transport=transport, **kwargs)

        return SupabaseRepository("https://learning.example", "server-only-key"), client_factory

    async def test_upload_persists_owner_scoped_private_file_and_source_rows(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/document_sections"):
                return httpx.Response(201, json=[{"id": "section-id", "section_order": 0}], request=request)
            return httpx.Response(204, request=request)

        repository, client_factory = self.repository_with_handler(handler)
        document = parse_document("notes.txt", b"Chapter 1: Study\nImportant concepts should be reviewed regularly.")
        document_id = str(uuid4())
        with patch("app.repository.httpx.AsyncClient", side_effect=client_factory):
            await repository.save_document("owner-id", document_id, document, b"source bytes")

        storage = next(request for request in requests if request.url.path.startswith("/storage/"))
        self.assertIn(f"owner-id/{document_id}.txt", storage.url.path)
        self.assertEqual(storage.headers["authorization"], "Bearer server-only-key")
        self.assertIn(b"source bytes", storage.content)

        sections_request = next(request for request in requests if request.url.path.endswith("/document_sections"))
        section_payload = json.loads(sections_request.content)
        self.assertEqual(section_payload[0]["document_id"], document_id)
        self.assertEqual(section_payload[0]["title"], "Chapter 1: Study")
        self.assertIn(b"owner-id", next(request for request in requests if request.url.path == "/rest/v1/documents").content)
        self.assertTrue(any(request.method == "PATCH" and "owner_id=eq.owner-id" in request.url.query.decode() for request in requests))

    async def test_document_reads_are_owner_filtered_and_rebuild_citations(self):
        def handler(request):
            if request.url.path.endswith("/documents"):
                if request.method == "GET" and request.url.query.startswith(b"select=id%2Cname"):
                    return httpx.Response(200, json=[{"id": "document-id", "name": "notes.txt", "file_type": "txt", "status": "ready"}], request=request)
                return httpx.Response(200, json=[{"id": "document-id", "name": "notes.txt", "file_type": "txt", "status": "ready", "metadata": {"section_count": 1, "chunk_count": 1}}], request=request)
            if request.url.path.endswith("/document_sections"):
                return httpx.Response(200, json=[{"id": "section-id", "title": "Chapter 1", "section_order": 0, "page_start": None, "body_text": "Learning happens through practice."}], request=request)
            if request.url.path.endswith("/document_chunks"):
                return httpx.Response(200, json=[{"id": "chunk-id", "section_id": "section-id", "chunk_order": 0, "page_number": None, "content": "Learning happens through practice."}], request=request)
            return httpx.Response(404, request=request)

        repository, client_factory = self.repository_with_handler(handler)
        with patch("app.repository.httpx.AsyncClient", side_effect=client_factory):
            listed = await repository.list_documents("owner-id")
            document = await repository.get_document("owner-id", "document-id")

        self.assertEqual(listed[0]["chunk_count"], 1)
        self.assertEqual(document.chunks[0].id, "chunk-id")
        self.assertEqual(document.chunks[0].section, "Chapter 1")

    async def test_quiz_answer_keys_stay_private_and_attempts_persist(self):
        def handler(request):
            path = request.url.path
            if request.method == "POST" and path.endswith("/quizzes"):
                return httpx.Response(201, json=[{"id": "quiz-id"}], request=request)
            if request.method == "POST" and path.endswith("/quiz_questions"):
                return httpx.Response(201, json=[{"id": "question-id", "question_order": 0}], request=request)
            if request.method == "GET" and path.endswith("/quizzes"):
                self.assertIn(b"owner_id=eq.owner-id", request.url.query)
                return httpx.Response(200, json=[{"id": "quiz-id", "document_id": "document-id"}], request=request)
            if request.method == "GET" and path.endswith("/quiz_questions"):
                return httpx.Response(200, json=[{"id": "question-id", "question_order": 0, "topic": "weights", "prompt": "Fill the blank", "source_chunk_id": "chunk-id", "answer": {"answer": "weights"}}], request=request)
            if request.method == "GET" and path.endswith("/document_chunks"):
                return httpx.Response(200, json=[{"id": "chunk-id", "page_number": 4, "section": {"title": "Training"}, "document": {"name": "notes.pdf"}}], request=request)
            if request.method == "POST" and path.endswith("/quiz_attempts"):
                return httpx.Response(201, json=[{"id": "attempt-id"}], request=request)
            return httpx.Response(204, request=request)

        repository, client_factory = self.repository_with_handler(handler)
        source = {"document": "notes.pdf", "page": 4, "section": "Training", "chunk_id": "chunk-id"}
        source_question = {"id": "q-1", "type": "short_answer", "prompt": "Fill the blank", "topic": "weights", "source": source, "_answer": "weights"}
        with patch("app.repository.httpx.AsyncClient", side_effect=client_factory):
            quiz_id, public_questions = await repository.create_quiz("owner-id", "document-id", [source_question])
            stored_document_id, private_questions = await repository.get_quiz("owner-id", quiz_id)
            await repository.save_attempt("owner-id", quiz_id, {"score_percent": 100, "results": [{"question_id": "question-id", "topic": "weights", "correct": True}]})

        self.assertEqual(quiz_id, "quiz-id")
        self.assertNotIn("_answer", public_questions[0])
        self.assertEqual(stored_document_id, "document-id")
        self.assertEqual(private_questions[0]["_answer"], "weights")
        self.assertEqual(private_questions[0]["source"]["page"], 4)


if __name__ == "__main__":
    unittest.main()