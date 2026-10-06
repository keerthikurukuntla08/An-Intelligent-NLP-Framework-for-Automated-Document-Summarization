# Adaptive Academic Learning

An early, source-grounded academic document learning platform. The backend supports PDF, DOCX, and UTF-8 TXT ingestion, section-aware extraction, evidence-retrieval Q&A, timed revision packs, source-derived short-answer quizzes, and quiz-based topic performance. Summaries use an extractive baseline by default; optionally configure NVIDIA NIM for learner-level-aware generated summary wording.

## Local development

1. Create a Python environment and install the backend dependencies:

   ```powershell
   python -m venv backend/.venv
   backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
   ```

2. Run backend tests:

   ```powershell
   Push-Location backend
   ./.venv/Scripts/python.exe -m unittest discover -s tests -v
   Pop-Location
   ```

3. Start the API:

   ```powershell
   backend/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --reload --reload-dir backend/app --reload-dir backend/tests
   ```

   Interactive API documentation is at `http://localhost:8000/docs`.

4. Install Node.js LTS and start the Next.js frontend:

   ```powershell
   npm --prefix frontend install
   npm --prefix frontend run dev
   ```

   The frontend is available at `http://localhost:3000`. In the current workspace Node.js was installed project-locally; use `.tools/node/npm.cmd` if Node.js is not on `PATH`.

## Database setup

Apply `supabase/migrations/202610050001_initial_learning_schema.sql` to a Supabase project using the Supabase CLI or SQL editor. The migration defines learner/document structure, full-text searchable chunks, summaries, quiz records, private answer keys, attempt history, and owner-scoped RLS policies.

For production mode, set `APP_ENV=production`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` in the backend environment, and set `NEXT_PUBLIC_SUPABASE_URL` plus `NEXT_PUBLIC_SUPABASE_ANON_KEY` in the frontend environment. The service-role key must remain server-side; the browser receives only the project's public anon key. The frontend supports Supabase email/password sign-in and sends the access token to FastAPI, which verifies the user and applies owner filters to server-side database operations.

For optional NVIDIA-hosted summaries, copy `.env.example` to a git-ignored root `.env` file and set `NVIDIA_API_KEY` there. FastAPI loads this file at startup. `NVIDIA_MODEL` defaults to `nvidia/nemotron-3.5-lightning-30b-a3b` and can be overridden if NVIDIA changes the catalog model identifier. Never use a `NEXT_PUBLIC_` variable for this key. When configured, only evidence passages selected by the extractive summary pipeline are sent to NVIDIA; generated wording is source-linked but not independently verified. Provider failures return an API error rather than silently falling back to extractive output. Without a key, the existing extractive summary path remains active. The NVIDIA key is not required for local development or any other current feature.

## Current limits

- Local development keeps documents, quizzes, and performance in process memory. Production mode uses private Supabase Storage and durable Postgres records.
- Q&A remains an extractive/lexical baseline, not vector RAG. NVIDIA-generated summaries are optional and require a backend key; no provider key is required for local development.
- Image-only/scanned PDFs are rejected with an OCR-needed message. PPT/PPTX, tables, figures, OCR, and formula-aware parsing are not implemented.
- Production deployment is not ready. Durable Supabase document, summary, quiz, and performance operations are wired but require a configured project and have not been live-verified. Queueing, rate limiting, and deployment hardening remain outstanding.
- Quiz performance is process-local and based only on submitted quiz answers; it is not a persisted learner profile.

See [docs/architecture.md](docs/architecture.md) for implementation boundaries, security notes, and the validation evidence matrix.