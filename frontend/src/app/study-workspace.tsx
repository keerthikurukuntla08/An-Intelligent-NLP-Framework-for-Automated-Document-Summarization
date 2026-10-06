"use client";

import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { getSupabaseClient } from "./supabase";

type DocumentItem = { id: string; name: string; file_type: string; section_count?: number; chunk_count?: number };
type Source = { document: string; page: number | null; section: string; chunk_id: string };
type Point = { text: string; topic: string; source: Source };
type Question = { id: string; prompt: string; topic: string; source: Source };
type WeakTopic = { topic: string; attempts: number; score_percent: number; recommendation: string | null };
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const AUTH_CONFIGURED = Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const supabase = getSupabaseClient();
  if (supabase) {
    const { data } = await supabase.auth.getSession();
    if (data.session?.access_token) headers.set("Authorization", `Bearer ${data.session.access_token}`);
  }
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  const data = response.status === 204 ? undefined : await response.json();
  if (!response.ok) throw new Error(data?.detail ?? `Request failed (${response.status})`);
  return data as T;
}

function citation(source: Source) {
  return `${source.document}${source.page ? ` · p. ${source.page}` : ""} · ${source.section}`;
}

export default function StudyWorkspace() {
  const [session, setSession] = useState<import("@supabase/supabase-js").Session | null>(null);
  const [authReady, setAuthReady] = useState(!AUTH_CONFIGURED);
  const [authMode, setAuthMode] = useState<"sign_in" | "sign_up">("sign_in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authMessage, setAuthMessage] = useState("");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [activeId, setActiveId] = useState("");
  const [view, setView] = useState<"learn" | "practice">("learn");
  const [status, setStatus] = useState("Checking the learning API…");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<Point[]>([]);
  const [summaryMode, setSummaryMode] = useState("standard");
  const [learningLevel, setLearningLevel] = useState("intermediate");
  const [intent, setIntent] = useState("general");
  const [answer, setAnswer] = useState<{ answer: string; grounded: boolean; citations: Source[] } | null>(null);
  const [question, setQuestion] = useState("");
  const [revision, setRevision] = useState<{ minutes: number; priority_points: Point[]; self_test: string[] } | null>(null);
  const [revisionTime, setRevisionTime] = useState(10);
  const [quizId, setQuizId] = useState("");
  const [quizQuestions, setQuizQuestions] = useState<Question[]>([]);
  const [quizAnswers, setQuizAnswers] = useState<Record<string, string>>({});
  const [quizResult, setQuizResult] = useState<{ score_percent: number; correct: number; total: number; weak_topics: WeakTopic[] } | null>(null);
  const [weakTopics, setWeakTopics] = useState<WeakTopic[]>([]);

  useEffect(() => {
    const supabase = getSupabaseClient();
    if (!supabase) return;
    let cancelled = false;
    void supabase.auth.getSession().then(({ data }) => {
      if (!cancelled) {
        setSession(data.session);
        setAuthReady(true);
      }
    }).catch(() => {
      if (!cancelled) setAuthReady(true);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setAuthReady(true);
    });
    return () => { cancelled = true; subscription.unsubscribe(); };
  }, []);

  useEffect(() => {
    if (!authReady || (AUTH_CONFIGURED && !session)) return;
    let cancelled = false;
    async function initialize() {
      try {
        await request("/health");
        const items = await request<DocumentItem[]>("/api/documents");
        if (cancelled) return;
        setDocuments(items);
        setActiveId(items[0]?.id ?? "");
        setStatus("API connected");
      } catch (reason) {
        if (!cancelled) {
          setStatus("API unavailable");
          setError(reason instanceof Error ? reason.message : "Could not connect to the learning API.");
        }
      }
    }
    void initialize();
    return () => { cancelled = true; };
  }, [authReady, session]);

  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    request<{ weak_topics: WeakTopic[] }>(`/api/documents/${activeId}/progress`)
      .then((result) => { if (!cancelled) setWeakTopics(result.weak_topics); })
      .catch((reason: Error) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; };
  }, [activeId]);

  async function uploadDocument(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    setStatus(`Reading ${file.name}…`);
    try {
      const form = new FormData();
      form.append("file", file);
      const created = await request<DocumentItem & { processing_status: string }>("/api/documents", { method: "POST", body: form });
      setDocuments((current) => [created, ...current]);
      setActiveId(created.id);
      setStatus("Document ready to study");
    } catch (reason) {
      setStatus("Upload failed");
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally { setBusy(false); }
  }

  async function createSummary(event: FormEvent) {
    event.preventDefault();
    if (!activeId) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<{ key_points: Point[] }>(`/api/documents/${activeId}/summary`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: summaryMode, learning_level: learningLevel, intent }),
      });
      setSummary(result.key_points);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Summary could not be created.");
    } finally { setBusy(false); }
  }

  async function askDocument(event: FormEvent) {
    event.preventDefault();
    if (!activeId || !question.trim()) return;
    setBusy(true);
    setError("");
    try {
      setAnswer(await request<{ answer: string; grounded: boolean; citations: Source[] }>(`/api/documents/${activeId}/ask`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Question could not be answered.");
    } finally { setBusy(false); }
  }

  async function createRevision(event: FormEvent) {
    event.preventDefault();
    if (!activeId) return;
    setBusy(true);
    setError("");
    try {
      setRevision(await request(`/api/documents/${activeId}/revision`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ minutes: revisionTime }),
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Revision pack could not be created.");
    } finally { setBusy(false); }
  }

  async function createQuiz() {
    if (!activeId) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<{ id: string; questions: Question[] }>(`/api/documents/${activeId}/quizzes`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ count: 5 }),
      });
      setQuizId(result.id);
      setQuizQuestions(result.questions);
      setQuizAnswers({});
      setQuizResult(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Quiz could not be created.");
    } finally { setBusy(false); }
  }

  async function submitQuiz(event: FormEvent) {
    event.preventDefault();
    if (!quizId) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<{ score_percent: number; correct: number; total: number; weak_topics: WeakTopic[] }>(`/api/quizzes/${quizId}/submit`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answers: quizAnswers }),
      });
      setQuizResult(result);
      setWeakTopics(result.weak_topics);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Quiz could not be scored.");
    } finally { setBusy(false); }
  }

  async function deleteDocument() {
    if (!activeId || !window.confirm("Delete this document and its study session?")) return;
    setBusy(true);
    try {
      await request(`/api/documents/${activeId}`, { method: "DELETE" });
      setDocuments((current) => current.filter((document) => document.id !== activeId));
      setActiveId("");
      setSummary([]);
      setAnswer(null);
      setRevision(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Document could not be deleted.");
    } finally { setBusy(false); }
  }

  async function submitAuth(event: FormEvent) {
    event.preventDefault();
    const supabase = getSupabaseClient();
    if (!supabase) return;
    setBusy(true);
    setAuthMessage("");
    try {
      const result = authMode === "sign_in"
        ? await supabase.auth.signInWithPassword({ email, password })
        : await supabase.auth.signUp({ email, password });
      if (result.error) setAuthMessage(result.error.message);
      else if (authMode === "sign_up" && !result.data.session) setAuthMessage("Check your email to confirm your account, then sign in.");
    } catch {
      setAuthMessage("Authentication is temporarily unavailable. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    const supabase = getSupabaseClient();
    if (supabase) await supabase.auth.signOut();
  }

  if (AUTH_CONFIGURED && !authReady) {
    return <main className="auth-shell"><span className="brand-mark">f.</span><p className="eyebrow">FORMA / STUDY</p><h1>Checking your<br /><em>session.</em></h1></main>;
  }

  if (AUTH_CONFIGURED && !session) {
    return <main className="auth-shell"><span className="brand-mark">f.</span><p className="eyebrow">FORMA / STUDY</p><h1>{authMode === "sign_in" ? <>Return to<br /><em>your desk.</em></> : <>Make room for<br /><em>learning.</em></>}</h1><form className="auth-form" onSubmit={submitAuth}><label>Email<input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label><label>Password<input type="password" autoComplete={authMode === "sign_in" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} required /></label><button className="primary-button" type="submit" disabled={busy}>{busy ? "Please wait…" : authMode === "sign_in" ? "Sign in" : "Create account"}<span aria-hidden="true">↗</span></button></form>{authMessage && <p className="auth-message" role="status">{authMessage}</p>}<button className="text-button auth-switch" onClick={() => { setAuthMode(authMode === "sign_in" ? "sign_up" : "sign_in"); setAuthMessage(""); }}>{authMode === "sign_in" ? "Create an account" : "I already have an account"}</button></main>;
  }

  const activeDocument = documents.find((document) => document.id === activeId);

  return (
    <main className="workspace">
      <aside className="sidebar">
        <a className="wordmark" href="#home" aria-label="Forma study dashboard"><span className="brand-mark">f.</span><span>forma<span className="wordmark-light"> / study</span></span></a>
        <div className="sidebar-label">YOUR DESK</div>
        <nav className="primary-nav" aria-label="Workspace">
          <button className={view === "learn" ? "nav-item active" : "nav-item"} onClick={() => setView("learn")}><span className="nav-symbol">01</span> Understand</button>
          <button className={view === "practice" ? "nav-item active" : "nav-item"} onClick={() => setView("practice")}><span className="nav-symbol">02</span> Practice</button>
        </nav>
        <div className="library-heading"><span>LIBRARY</span><span className="library-count">{documents.length.toString().padStart(2, "0")}</span></div>
        <ul className="document-list">
          {documents.map((document) => <li key={document.id}><button className={activeId === document.id ? "document-link selected" : "document-link"} onClick={() => { setActiveId(document.id); setError(""); setSummary([]); setAnswer(null); setRevision(null); setQuizQuestions([]); setQuizResult(null); }}><span className="file-tag">{document.file_type.toUpperCase()}</span><span className="document-name">{document.name}</span></button></li>)}
          {!documents.length && <li className="empty-library">No documents yet</li>}
        </ul>
        <div className="sidebar-bottom"><span className={status === "API connected" ? "status-dot connected" : "status-dot"} /><span>{status}</span><span className="sidebar-version">LOCAL / 0.1</span></div>
      </aside>

      <section className="main-column" id="home">
        <header className="topbar"><div className="breadcrumb">LEARNING STUDIO <span>/</span> {view === "learn" ? "UNDERSTAND" : "PRACTICE"}</div><div className="topbar-actions">{session && <button className="text-button sign-out" onClick={signOut}>Sign out</button>}<label className="upload-button"><span aria-hidden="true">+</span> Add document<input type="file" accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" onChange={uploadDocument} disabled={busy} /></label></div></header>
        <div className="page-content">
          <div className="page-heading"><div><p className="eyebrow">A BETTER WAY THROUGH YOUR READING</p><h1>{view === "learn" ? <>Make knowledge<br /><em>stick.</em></> : <>Practice with<br /><em>purpose.</em></>}</h1><p className="page-intro">{view === "learn" ? "Turn course material into clear ideas, grounded answers, and a revision plan that fits your time." : "Test what you understand. Use your results to find what deserves another look."}</p></div><div className="heading-index"><span>LEARNING</span><strong>{view === "learn" ? "01" : "02"}</strong><span>OF 02</span></div></div>
          {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError("")}>Dismiss</button></div>}

          {!activeDocument ? <section className="empty-state" aria-labelledby="empty-title"><div className="empty-orbit" aria-hidden="true"><span>+</span><i /></div><p className="eyebrow">START WITH YOUR COURSE MATERIAL</p><h2 id="empty-title">Bring a document<br />to the desk.</h2><p>Upload lecture notes or a reading to build a source-linked study session. PDF, DOCX, and TXT up to 20 MB.</p><label className="primary-button">Choose a document <span aria-hidden="true">↗</span><input type="file" accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" onChange={uploadDocument} disabled={busy} /></label></section>
          : view === "learn" ? <>
            <section className="active-document-line"><div className="document-overview"><span className="large-file-tag">{activeDocument.file_type.toUpperCase()}</span><div><p className="eyebrow">CURRENT READING</p><h2>{activeDocument.name}</h2><p>{activeDocument.section_count ?? 0} sections <span>·</span> {activeDocument.chunk_count ?? 0} source passages</p></div></div><button className="text-button delete-button" onClick={deleteDocument} disabled={busy}>Remove document</button></section>
            <section className="study-grid">
              <div className="study-main">
                <div className="section-heading"><div><p className="eyebrow">01 / BUILD YOUR NOTES</p><h2>Shape a summary</h2></div><span className="section-rule" /></div>
                <form className="learning-controls" onSubmit={createSummary}>
                  <label>Format<select value={summaryMode} onChange={(event) => setSummaryMode(event.target.value)}><option value="concise">Concise</option><option value="standard">Standard</option><option value="detailed">Detailed</option><option value="key_points">Key points</option></select></label>
                  <label>Learning level<select value={learningLevel} onChange={(event) => setLearningLevel(event.target.value)}><option value="beginner">Beginner</option><option value="intermediate">Intermediate</option><option value="advanced">Advanced</option></select></label>
                  <label>Study intent<select value={intent} onChange={(event) => setIntent(event.target.value)}><option value="general">General learning</option><option value="university_exam">University exam</option><option value="viva">Viva</option><option value="interview">Interview</option><option value="assignment">Assignment</option></select></label>
                  <button className="primary-button generate-button" type="submit" disabled={busy}>{busy ? "Working…" : "Make my notes"}<span aria-hidden="true">↗</span></button>
                </form>
                <div className="result-area" aria-live="polite">{summary.length ? <ol className="key-point-list">{summary.map((point, index) => <li key={`${point.source.chunk_id}-${index}`}><span className="point-number">{String(index + 1).padStart(2, "0")}</span><div><p>{point.text}</p><small>{citation(point.source)}</small></div></li>)}</ol> : <p className="quiet-note">Your source-linked notes will appear here.</p>}</div>
                <div className="section-heading ask-heading"><div><p className="eyebrow">02 / STAY WITH THE SOURCE</p><h2>Ask your document</h2></div><span className="section-rule" /></div>
                <form className="ask-form" onSubmit={askDocument}><label className="sr-only" htmlFor="document-question">Question about the document</label><input id="document-question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="What should I understand about…" maxLength={2000} /><button type="submit" disabled={busy || !question.trim()}>Ask <span aria-hidden="true">↗</span></button></form>
                {answer && <div className="answer-block"><p className="eyebrow">{answer.grounded ? "FOUND IN YOUR DOCUMENT" : "NOT ENOUGH SOURCE EVIDENCE"}</p><p>{answer.answer}</p>{answer.citations.map((source) => <small key={source.chunk_id}>{citation(source)}</small>)}</div>}
              </div>
              <aside className="study-rail"><div className="rail-heading"><p className="eyebrow">A SHORTER ROUTE BACK IN</p><h2>Quick revision</h2><p>Prioritize essentials for the time you have.</p></div>
                <form className="revision-form" onSubmit={createRevision}><div className="time-options" role="group" aria-label="Revision time">{[5, 10, 15, 30].map((minutes) => <button type="button" className={revisionTime === minutes ? "time-option chosen" : "time-option"} key={minutes} onClick={() => setRevisionTime(minutes)} aria-pressed={revisionTime === minutes}>{minutes}<span>min</span></button>)}</div><button className="outline-button" type="submit" disabled={busy}>{busy ? "Preparing…" : "Build revision"}<span aria-hidden="true">↗</span></button></form>
                {revision && <div className="revision-result"><p className="eyebrow">{revision.minutes} MINUTE PLAN</p><ol>{revision.priority_points.slice(0, 5).map((point, index) => <li key={`${point.source.chunk_id}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><p>{point.text}</p></li>)}</ol>{revision.self_test.length > 0 && <div className="self-test"><strong>CHECK YOURSELF</strong><p>{revision.self_test[0]}</p></div>}</div>}
                <div className="rail-note"><span className="note-mark">i</span><p>When NVIDIA is configured, selected source passages are sent for summary generation. Otherwise notes are extractive. Document Q&amp;A stays local.</p></div>
              </aside>
            </section>
          </> : <section className="practice-view">
            <div className="practice-toolbar"><div><p className="eyebrow">ACTIVE DOCUMENT</p><h2>{activeDocument.name}</h2></div><button className="primary-button" onClick={createQuiz} disabled={busy || !activeId}>{busy ? "Preparing…" : "Create a quiz"}<span aria-hidden="true">↗</span></button></div>
            {quizQuestions.length ? <form className="quiz-form" onSubmit={submitQuiz}>{quizQuestions.map((item, index) => <label className="quiz-question" key={item.id}><span className="point-number">{String(index + 1).padStart(2, "0")}</span><span><strong>{item.prompt}</strong><small>{item.topic} · {citation(item.source)}</small><input value={quizAnswers[item.id] ?? ""} onChange={(event) => setQuizAnswers((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="Write a short answer" /></span></label>)}<button className="primary-button" type="submit" disabled={busy}>{busy ? "Scoring…" : "Check my answers"}<span aria-hidden="true">↗</span></button></form> : <div className="empty-practice"><p className="eyebrow">RECALL BEFORE YOU REVIEW</p><h2>See what stayed<br /><em>with you.</em></h2><p>Generate a short-answer quiz from this reading. Each prompt links back to its source passage.</p></div>}
            {quizResult && <div className="score-panel" aria-live="polite"><span className="score-number">{quizResult.score_percent}<small>%</small></span><div><p className="eyebrow">YOUR RESULT</p><h3>{quizResult.correct} of {quizResult.total} correct</h3><p>Topic scores below are calculated from your submitted answers.</p></div></div>}
            <section className="progress-section"><div className="section-heading"><div><p className="eyebrow">LEARN FROM YOUR ATTEMPTS</p><h2>Topic check-in</h2></div><span className="section-rule" /></div>{weakTopics.length ? <ul className="topic-list">{weakTopics.map((topic) => <li key={topic.topic}><div className="topic-title"><span>{topic.topic}</span><strong>{topic.score_percent}%</strong></div><div className="progress-track"><span style={{ width: `${topic.score_percent}%` }} /></div><small>{topic.attempts} {topic.attempts === 1 ? "attempt" : "attempts"}{topic.recommendation ? ` · ${topic.recommendation}` : " · looking steady"}</small></li>)}</ul> : <p className="quiet-note">Complete a quiz to see topic-level progress. No scores are inferred before you practice.</p>}</section>
          </section>}
          <footer className="page-footer"><span>FORMA / ADAPTIVE LEARNING</span><span>Source-grounded study, at your pace.</span></footer>
        </div>
      </section>
    </main>
  );
}