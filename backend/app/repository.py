from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from .core import DocumentError, ParsedDocument, Section, SourceChunk


class RepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredDocument:
    id: str
    document: ParsedDocument


class SupabaseRepository:
    def __init__(self, url: str, service_role_key: str):
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    async def _request(self, method: str, path: str, *, json: Any = None, content: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(
                    method,
                    f"{self.url}{path}",
                    headers=request_headers,
                    json=json,
                    content=content,
                )
        except httpx.HTTPError as exc:
            raise RepositoryError("The learning database could not be reached.") from exc
        if response.status_code >= 400:
            raise RepositoryError("The learning database operation failed.")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise RepositoryError("The learning database returned an invalid response.") from exc

    async def save_document(self, owner_id: str, document_id: str, parsed: ParsedDocument, payload: bytes) -> None:
        extension = parsed.file_type
        storage_path = f"{owner_id}/{document_id}.{extension}"
        await self._request(
            "POST",
            "/rest/v1/documents",
            json={
                "id": document_id,
                "owner_id": owner_id,
                "name": parsed.name,
                "file_type": parsed.file_type,
                "status": "processing",
            },
            headers={"Prefer": "return=minimal"},
        )
        try:
            await self._request(
                "POST",
                "/storage/v1/object/academic-documents/" + quote(storage_path, safe="/"),
                content=payload,
                headers={"Content-Type": _mime_type(parsed.file_type), "x-upsert": "false"},
            )
            section_rows = [
                {
                    "document_id": document_id,
                    "title": section.title,
                    "section_order": order,
                    "page_start": section.page,
                    "page_end": section.page,
                    "body_text": section.text,
                }
                for order, section in enumerate(parsed.sections)
            ]
            created_sections = await self._request(
                "POST",
                "/rest/v1/document_sections?select=id,section_order",
                json=section_rows,
                headers={"Prefer": "return=representation"},
            )
            section_ids = {row["section_order"]: row["id"] for row in created_sections}
            chunk_rows = []
            for order, chunk in enumerate(parsed.chunks):
                section_order = next(
                    index
                    for index, section in enumerate(parsed.sections)
                    if section.title == chunk.section and section.page == chunk.page
                )
                chunk_rows.append(
                    {
                        "document_id": document_id,
                        "section_id": section_ids[section_order],
                        "chunk_order": order,
                        "page_number": chunk.page,
                        "content": chunk.text,
                    }
                )
            await self._request("POST", "/rest/v1/document_chunks", json=chunk_rows)
            await self._request(
                "PATCH",
                f"/rest/v1/documents?id=eq.{document_id}&owner_id=eq.{owner_id}",
                json={
                    "status": "ready",
                    "storage_path": storage_path,
                    "metadata": {"section_count": len(section_rows), "chunk_count": len(chunk_rows)},
                },
            )
        except RepositoryError:
            await self._request(
                "DELETE",
                "/rest/v1/documents?id=eq." + document_id + "&owner_id=eq." + owner_id,
            )
            raise

    async def list_documents(self, owner_id: str) -> list[dict[str, Any]]:
        rows = await self._request(
            "GET",
            "/rest/v1/documents?select=id,name,file_type,status,metadata,created_at"
            f"&owner_id=eq.{owner_id}&order=created_at.desc",
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "file_type": row["file_type"],
                "processing_status": row["status"],
                "section_count": row.get("metadata", {}).get("section_count", 0),
                "chunk_count": row.get("metadata", {}).get("chunk_count", 0),
            }
            for row in rows
        ]

    async def get_document(self, owner_id: str, document_id: str) -> ParsedDocument:
        rows = await self._request(
            "GET",
            "/rest/v1/documents?select=id,name,file_type,status"
            f"&id=eq.{document_id}&owner_id=eq.{owner_id}&status=eq.ready",
        )
        if not rows:
            raise DocumentError("Document not found.")
        section_rows = await self._request(
            "GET",
            "/rest/v1/document_sections?select=id,title,section_order,page_start,body_text"
            f"&document_id=eq.{document_id}&order=section_order.asc",
        )
        chunk_rows = await self._request(
            "GET",
            "/rest/v1/document_chunks?select=id,section_id,chunk_order,page_number,content"
            f"&document_id=eq.{document_id}&order=chunk_order.asc",
        )
        sections_by_id = {row["id"]: row for row in section_rows}
        sections = [
            Section(row["title"], row["body_text"], row["page_start"])
            for row in section_rows
        ]
        chunks = [
            SourceChunk(
                row["id"],
                row["content"],
                sections_by_id[row["section_id"]]["title"],
                row["page_number"],
            )
            for row in chunk_rows
        ]
        row = rows[0]
        return ParsedDocument(row["name"], row["file_type"], sections, chunks)

    async def delete_document(self, owner_id: str, document_id: str) -> None:
        rows = await self._request(
            "GET",
            "/rest/v1/documents?select=storage_path"
            f"&id=eq.{document_id}&owner_id=eq.{owner_id}",
        )
        if not rows:
            raise DocumentError("Document not found.")
        storage_path = rows[0].get("storage_path")
        if storage_path:
            await self._request(
                "DELETE",
                "/storage/v1/object/academic-documents",
                json={"prefixes": [storage_path]},
            )
        await self._request(
            "DELETE",
            f"/rest/v1/documents?id=eq.{document_id}&owner_id=eq.{owner_id}",
        )

    async def save_summary(self, owner_id: str, document_id: str, summary: dict[str, Any]) -> None:
        await self._request(
            "POST",
            "/rest/v1/summaries",
            json={
                "document_id": document_id,
                "owner_id": owner_id,
                "mode": summary["mode"],
                "learning_level": summary["learning_level"],
                "intent": summary["intent"],
                "content": summary,
            },
        )

    async def create_quiz(self, owner_id: str, document_id: str, questions: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        rows = await self._request(
            "POST",
            "/rest/v1/quizzes?select=id",
            json={"document_id": document_id, "owner_id": owner_id},
            headers={"Prefer": "return=representation"},
        )
        quiz_id = rows[0]["id"]
        question_rows = [
            {
                "quiz_id": quiz_id,
                "question_order": index,
                "question_type": question["type"],
                "prompt": question["prompt"],
                "topic": question["topic"],
                "source_chunk_id": question["source"]["chunk_id"],
            }
            for index, question in enumerate(questions)
        ]
        saved = await self._request(
            "POST",
            "/rest/v1/quiz_questions?select=id,question_order",
            json=question_rows,
            headers={"Prefer": "return=representation"},
        )
        question_ids = {row["question_order"]: row["id"] for row in saved}
        answer_rows = [
            {"question_id": question_ids[index], "answer": question["_answer"]}
            for index, question in enumerate(questions)
        ]
        await self._request("POST", "/rest/v1/quiz_answer_keys", json=answer_rows)
        public_questions = []
        for index, question in enumerate(questions):
            public_questions.append(
                {
                    key: value
                    for key, value in {**question, "id": question_ids[index]}.items()
                    if not key.startswith("_")
                }
            )
        return quiz_id, public_questions

    async def get_quiz(self, owner_id: str, quiz_id: str) -> tuple[str, list[dict[str, Any]]]:
        quiz_rows = await self._request(
            "GET",
            "/rest/v1/quizzes?select=id,document_id"
            f"&id=eq.{quiz_id}&owner_id=eq.{owner_id}",
        )
        if not quiz_rows:
            raise DocumentError("Quiz not found.")
        rows = await self._request(
            "GET",
            "/rest/v1/quiz_questions?select=id,question_order,topic,prompt,source_chunk_id,"
            f"answer:quiz_answer_keys(answer)&quiz_id=eq.{quiz_id}&order=question_order.asc",
        )
        document_id = quiz_rows[0]["document_id"]
        chunks_by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            source_id = row.get("source_chunk_id")
            if source_id and source_id not in chunks_by_id:
                source_rows = await self._request(
                    "GET",
                    "/rest/v1/document_chunks?select=id,page_number,section:document_sections(title),"
                    f"document:documents(name)&id=eq.{source_id}",
                )
                if source_rows:
                    source = source_rows[0]
                    chunks_by_id[source_id] = {
                        "document": source["document"]["name"],
                        "page": source["page_number"],
                        "section": source["section"]["title"],
                        "chunk_id": source_id,
                    }
        questions = []
        for row in rows:
            answer = row["answer"]
            if isinstance(answer, list):
                answer = answer[0]
            questions.append(
                {
                    "id": row["id"],
                    "prompt": row["prompt"],
                    "topic": row["topic"],
                    "source": chunks_by_id.get(row.get("source_chunk_id"), {
                        "document": "Source unavailable", "page": None, "section": "", "chunk_id": ""
                    }),
                    "_answer": answer["answer"],
                }
            )
        return document_id, questions

    async def save_attempt(self, owner_id: str, quiz_id: str, result: dict[str, Any]) -> None:
        rows = await self._request(
            "POST",
            "/rest/v1/quiz_attempts?select=id",
            json={"quiz_id": quiz_id, "owner_id": owner_id, "score_percent": result["score_percent"]},
            headers={"Prefer": "return=representation"},
        )
        attempt_id = rows[0]["id"]
        answers = [
            {
                "attempt_id": attempt_id,
                "question_id": item["question_id"],
                "topic": item["topic"],
                "correct": item["correct"],
            }
            for item in result["results"]
        ]
        if answers:
            await self._request("POST", "/rest/v1/quiz_attempt_answers", json=answers)

    async def weak_topics(self, owner_id: str, document_id: str) -> list[dict[str, Any]]:
        attempts = await self._request(
            "GET",
            "/rest/v1/quiz_attempts?select=id,quiz:quizzes!inner(document_id)"
            f"&owner_id=eq.{owner_id}&quiz.document_id=eq.{document_id}",
        )
        attempt_ids = [row["id"] for row in attempts]
        if not attempt_ids:
            return []
        rows = await self._request(
            "GET",
            "/rest/v1/quiz_attempt_answers?select=topic,correct"
            f"&attempt_id=in.({','.join(attempt_ids)})",
        )
        grouped: dict[str, list[bool]] = {}
        for row in rows:
            grouped.setdefault(row["topic"], []).append(row["correct"])
        return [
            {
                "topic": topic,
                "attempts": len(results),
                "score_percent": round(sum(results) / len(results) * 100),
                "recommendation": "Review the cited source and try a focused practice quiz."
                if sum(results) / len(results) < 0.7
                else None,
            }
            for topic, results in sorted(grouped.items())
        ]


def _mime_type(file_type: str) -> str:
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain; charset=utf-8",
    }[file_type]