# Architecture and status

## Current implementation

The implemented boundary is Next.js/TypeScript for the student workspace, FastAPI/Python for ingestion and learning operations, and Supabase/PostgreSQL plus private Storage for durable user-owned records. The local dashboard can run without external credentials; it uses process memory. With `APP_ENV=production` and Supabase settings configured, FastAPI verifies Supabase bearer tokens and uses its server-only service-role key for database/storage operations. Every record lookup and mutation is filtered by the verified owner ID; the service-role key never reaches the browser.

Ingestion validates the extension, file size, basic file signature, UTF-8 text encoding, and DOCX archive structure. PDF text is extracted by page with pypdf; DOCX heading styles and text-file heading conventions are retained as sections. Chunks carry section and page metadata. Retrieval uses lexical overlap over chunks and only returns excerpts with citations; low-overlap questions receive an explicit refusal. The current baseline does not use embeddings or an LLM.

Summaries select source sentences using term frequency. Intent adds exam/viva/interview-specific fields; learning level is recorded but does not yet change the underlying explanations. The source sentences themselves are not paraphrased. Quick revision prioritizes source sentences into duration-specific packs. Quiz questions are cloze prompts derived from source sentences, and topic scores are computed only from recorded answers.

## Security and limitations

- Uploads are size-limited to 20 MiB, parsed in memory, never executed or written to a user-controlled filesystem path, and unsupported formats are rejected.
- CORS defaults to the local frontend origins. In `APP_ENV=production`, the API verifies bearer tokens through Supabase Auth before serving routes. A backend-only Supabase service-role key bypasses database RLS, so API methods apply explicit verified-owner filters; never expose this key to frontend code. The development mode intentionally uses a shared local user and must not be exposed publicly.
- Documents can contain hostile instructions. No model currently consumes document text as instructions; the Q&A path treats it only as retrievable source data.
- Local mode is single-process and non-durable. Production persistence is implemented but has not been checked against a live Supabase project. There is no background worker, rate limiting, OCR, vector index, provider timeout/retry policy, or durable audit history.
- Lexical extraction may miss meaning, produce weak topics, or fail on unusual formatting. OCR, tables, diagrams, formulas, multilingual text, and factuality evaluations remain future work.
- Competitors including NotebookLM, Acrobat AI, Scholarcy, Consensus, AskYourPDF, Smallpdf AI, and UPDF AI already offer overlapping document capabilities. The proposed contribution is the orchestration of intent, personalization, revision time, testing, and feedback, not feature novelty.

## API surface

- `POST /api/documents`, `GET /api/documents`, `GET /api/documents/{id}`, `DELETE /api/documents/{id}`
- `POST /api/documents/{id}/summary`, `/ask`, `/revision`, `/quizzes`
- `POST /api/quizzes/{id}/submit`, `GET /api/documents/{id}/progress`
- `GET /health`; OpenAPI UI at `/docs`

## Evidence matrix

| Requirement | Implementation | Validation | Result |
|---|---|---|---|
| PDF/DOCX/TXT upload parsing | `backend/app/core.py`, `backend/app/main.py` | TXT and DOCX fixtures; malformed/unsupported input tests | PARTIAL (PDF path lacks a generated text-PDF fixture; OCR unsupported) |
| Source-grounded summary and intent modes | `make_summary` | Unit test and live browser flow | PASS for extractive baseline |
| Grounded Q&A, citations, and refusal | `answer_question` | Supported/unsupported tests; browser flow | PASS for lexical retrieval baseline |
| Time-aware quick revision | `quick_revision` | Duration tests and browser flow | PASS |
| Quiz, scoring, weak topics | Quiz API and attempt aggregation | API test and browser quiz submission | PASS locally; score labels are generated answer terms, not an NLP topic taxonomy |
| Upload validation and owner isolation | File validation, bounded reads, API ownership checks | Unsupported upload and cross-owner read/delete regression tests | PASS in local API tests |
| Supabase storage and persistence contract | `backend/app/repository.py`, SQL migration | Mocked PostgREST/Storage contract tests | PASS mocked; live Supabase not configured |
| Supabase authentication | `frontend/src/app/supabase.ts`, production API guard | Frontend build/lint; no live project credentials | PARTIAL / NOT LIVE-VERIFIED |
| Responsive Next.js dashboard | `frontend/src/app/study-workspace.tsx` | Production build, lint, browser upload-to-quiz run | PASS for tested desktop workflow; mobile automated checks outstanding |
| OCR, embeddings, LLM, advanced personalization | Not implemented | Not evaluated | NOT IMPLEMENTED |

No summary-quality metrics or user-study results have been measured. Schema migration application and production-mode storage/authentication still require a real Supabase project to verify.