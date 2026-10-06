from __future__ import annotations

import os
from collections import defaultdict
from typing import Literal
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .core import (
    DocumentError,
    ParsedDocument,
    answer_question,
    generate_quiz,
    make_summary,
    parse_document,
    public_question,
    quick_revision,
    score_quiz,
    serialize_document,
)
from .nvidia import NvidiaProviderError, make_personalized_summary
from .repository import RepositoryError, SupabaseRepository


load_dotenv()

APP_ENV = os.getenv("APP_ENV", "development").lower()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
repository = (
    SupabaseRepository(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    if APP_ENV == "production" and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
    else None
)
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]

app = FastAPI(title="Adaptive Academic Learning API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

documents: dict[str, dict] = {}
quizzes: dict[str, dict] = {}
performances: dict[str, list[dict]] = defaultdict(list)


class SummaryRequest(BaseModel):
    mode: Literal["concise", "standard", "detailed", "key_points"] = "standard"
    learning_level: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    intent: Literal["general", "university_exam", "viva", "interview", "assignment"] = "general"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class RevisionRequest(BaseModel):
    minutes: Literal[5, 10, 15, 30]


class QuizRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=20)


class QuizSubmission(BaseModel):
    answers: dict[str, str]


async def current_user(authorization: str | None = Header(default=None)) -> str:
    if APP_ENV != "production":
        return "local-user"
    if not SUPABASE_URL or not SUPABASE_ANON_KEY or repository is None:
        raise HTTPException(status_code=503, detail="Authentication is not configured.")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="A valid sign-in session is required.")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": authorization},
            )
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="The sign-in session is invalid or expired.")
        user_id = response.json().get("id")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=401, detail="The sign-in session is invalid.")
        return user_id
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Authentication could not be verified.") from exc


def owned_document(document_id: str, user_id: str) -> ParsedDocument:
    record = documents.get(document_id)
    if record is None or record["owner_id"] != user_id:
        raise HTTPException(status_code=404, detail="Document not found.")
    return record["document"]


async def repository_call(operation):
    try:
        return await operation
    except DocumentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def get_owned_document(document_id: str, user_id: str) -> ParsedDocument:
    if repository is not None:
        return await repository_call(repository.get_document(user_id, document_id))
    return owned_document(document_id, user_id)


def run_core(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except DocumentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health")
async def health():
    return {"status": "ok", "environment": APP_ENV}


@app.post("/api/documents", status_code=201)
async def upload_document(file: UploadFile = File(...), user_id: str = Depends(current_user)):
    payload = await file.read(20 * 1024 * 1024 + 1)
    await file.close()
    document = run_core(parse_document, file.filename or "upload", payload)
    document_id = str(uuid4())
    if repository is not None:
        await repository_call(repository.save_document(user_id, document_id, document, payload))
    else:
        documents[document_id] = {"owner_id": user_id, "document": document}
    return {
        "id": document_id,
        "name": document.name,
        "file_type": document.file_type,
        "processing_status": "ready",
        "section_count": len(document.sections),
        "chunk_count": len(document.chunks),
    }


@app.get("/api/documents")
async def list_documents(user_id: str = Depends(current_user)):
    if repository is not None:
        return await repository_call(repository.list_documents(user_id))
    return [
        {
            "id": document_id,
            "name": record["document"].name,
            "file_type": record["document"].file_type,
            "section_count": len(record["document"].sections),
            "chunk_count": len(record["document"].chunks),
        }
        for document_id, record in documents.items()
        if record["owner_id"] == user_id
    ]


@app.get("/api/documents/{document_id}")
async def get_document(document_id: str, user_id: str = Depends(current_user)):
    document = await get_owned_document(document_id, user_id)
    return {"id": document_id, **serialize_document(document)}


@app.delete("/api/documents/{document_id}", status_code=204)
async def delete_document(document_id: str, user_id: str = Depends(current_user)):
    if repository is not None:
        await repository_call(repository.delete_document(user_id, document_id))
        return
    owned_document(document_id, user_id)
    del documents[document_id]
    performances.pop(document_id, None)
    for quiz_id in [key for key, value in quizzes.items() if value["document_id"] == document_id]:
        del quizzes[quiz_id]


@app.post("/api/documents/{document_id}/summary")
async def summarize(document_id: str, request: SummaryRequest, user_id: str = Depends(current_user)):
    document = await get_owned_document(document_id, user_id)
    if NVIDIA_API_KEY:
        try:
            summary = await make_personalized_summary(
                document,
                mode=request.mode,
                learning_level=request.learning_level,
                intent=request.intent,
                api_key=NVIDIA_API_KEY,
                model=NVIDIA_MODEL,
            )
        except NvidiaProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        summary = run_core(
            make_summary,
            document,
            mode=request.mode,
            learning_level=request.learning_level,
            intent=request.intent,
        )
    if repository is not None:
        await repository_call(repository.save_summary(user_id, document_id, summary))
    return summary


@app.post("/api/documents/{document_id}/ask")
async def ask_document(document_id: str, request: AskRequest, user_id: str = Depends(current_user)):
    return run_core(answer_question, await get_owned_document(document_id, user_id), request.question)


@app.post("/api/documents/{document_id}/revision")
async def create_revision(document_id: str, request: RevisionRequest, user_id: str = Depends(current_user)):
    document = await get_owned_document(document_id, user_id)
    result = run_core(quick_revision, document, request.minutes)
    result["weak_topics"] = await get_weak_topics(document_id, user_id)
    return result


@app.post("/api/documents/{document_id}/quizzes", status_code=201)
async def create_quiz(document_id: str, request: QuizRequest, user_id: str = Depends(current_user)):
    document = await get_owned_document(document_id, user_id)
    questions = run_core(generate_quiz, document, request.count)
    if repository is not None:
        quiz_id, questions = await repository_call(
            repository.create_quiz(user_id, document_id, questions)
        )
        return {"id": quiz_id, "questions": [public_question(question) for question in questions]}
    quiz_id = str(uuid4())
    quizzes[quiz_id] = {"document_id": document_id, "owner_id": user_id, "questions": questions}
    return {"id": quiz_id, "questions": [public_question(question) for question in questions]}


@app.post("/api/quizzes/{quiz_id}/submit")
async def submit_quiz(quiz_id: str, submission: QuizSubmission, user_id: str = Depends(current_user)):
    if repository is not None:
        document_id, questions = await repository_call(repository.get_quiz(user_id, quiz_id))
        result = score_quiz(questions, submission.answers)
        await repository_call(repository.save_attempt(user_id, quiz_id, result))
        result["weak_topics"] = await get_weak_topics(document_id, user_id)
        return result
    quiz = quizzes.get(quiz_id)
    if quiz is None or quiz["owner_id"] != user_id:
        raise HTTPException(status_code=404, detail="Quiz not found.")
    result = score_quiz(quiz["questions"], submission.answers)
    for item in result["results"]:
        performances[quiz["document_id"]].append(
            {"topic": item["topic"], "correct": item["correct"]}
        )
    result["weak_topics"] = local_weak_topics(quiz["document_id"])
    return result


@app.get("/api/documents/{document_id}/progress")
async def get_progress(document_id: str, user_id: str = Depends(current_user)):
    await get_owned_document(document_id, user_id)
    return {"weak_topics": await get_weak_topics(document_id, user_id)}


async def get_weak_topics(document_id: str, user_id: str) -> list[dict]:
    if repository is not None:
        return await repository_call(repository.weak_topics(user_id, document_id))
    return local_weak_topics(document_id)


def local_weak_topics(document_id: str) -> list[dict]:
    grouped: dict[str, list[bool]] = defaultdict(list)
    for attempt in performances[document_id]:
        grouped[attempt["topic"]].append(attempt["correct"])
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