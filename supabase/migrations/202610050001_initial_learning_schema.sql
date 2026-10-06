create extension if not exists pgcrypto;

create table public.learner_profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    learning_level text not null default 'intermediate'
        check (learning_level in ('beginner', 'intermediate', 'advanced')),
    subject text,
    learning_goal text,
    explanation_style text,
    preferred_language text not null default 'English',
    exam_type text,
    target_answer_length text,
    available_study_minutes integer check (available_study_minutes > 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table public.documents (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    name text not null check (length(name) between 1 and 255),
    file_type text not null check (file_type in ('pdf', 'docx', 'txt')),
    storage_path text,
    status text not null default 'uploaded'
        check (status in ('uploaded', 'processing', 'ready', 'failed')),
    error_code text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index documents_owner_created_idx on public.documents (owner_id, created_at desc);
create index documents_status_idx on public.documents (status) where status in ('uploaded', 'processing');

create table public.document_sections (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.documents(id) on delete cascade,
    parent_section_id uuid,
    title text not null,
    section_order integer not null check (section_order >= 0),
    page_start integer check (page_start > 0),
    page_end integer check (page_end >= page_start),
    body_text text not null default '',
    created_at timestamptz not null default now(),
    unique (document_id, section_order),
    unique (document_id, id),
    foreign key (document_id, parent_section_id)
        references public.document_sections(document_id, id) on delete cascade
);

create index document_sections_document_idx on public.document_sections (document_id, section_order);

create table public.document_chunks (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.documents(id) on delete cascade,
    section_id uuid,
    chunk_order integer not null check (chunk_order >= 0),
    page_number integer check (page_number > 0),
    content text not null,
    search_vector tsvector generated always as (to_tsvector('english', content)) stored,
    created_at timestamptz not null default now(),
    foreign key (document_id, section_id)
        references public.document_sections(document_id, id) on delete cascade,
    unique (document_id, chunk_order)
);

create index document_chunks_search_idx on public.document_chunks using gin (search_vector);
create index document_chunks_document_order_idx on public.document_chunks (document_id, chunk_order);

create table public.summaries (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.documents(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    mode text not null check (mode in ('concise', 'standard', 'detailed', 'key_points')),
    learning_level text not null check (learning_level in ('beginner', 'intermediate', 'advanced')),
    intent text not null check (intent in ('general', 'university_exam', 'viva', 'interview', 'assignment')),
    content jsonb not null,
    created_at timestamptz not null default now()
);

create index summaries_document_created_idx on public.summaries (document_id, created_at desc);

create table public.quizzes (
    id uuid primary key default gen_random_uuid(),
    document_id uuid not null references public.documents(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    mode text not null default 'source_grounded',
    created_at timestamptz not null default now()
);

create table public.quiz_questions (
    id uuid primary key default gen_random_uuid(),
    quiz_id uuid not null references public.quizzes(id) on delete cascade,
    question_order integer not null check (question_order >= 0),
    question_type text not null check (question_type in ('mcq', 'short_answer', 'true_false', 'viva')),
    prompt text not null,
    topic text not null,
    source_chunk_id uuid references public.document_chunks(id) on delete set null,
    created_at timestamptz not null default now(),
    unique (quiz_id, question_order)
);

create table public.quiz_answer_keys (
    question_id uuid primary key references public.quiz_questions(id) on delete cascade,
    answer text not null
);

create table public.quiz_attempts (
    id uuid primary key default gen_random_uuid(),
    quiz_id uuid not null references public.quizzes(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    score_percent integer not null check (score_percent between 0 and 100),
    submitted_at timestamptz not null default now()
);

create index quiz_attempts_owner_date_idx on public.quiz_attempts (owner_id, submitted_at desc);

create table public.quiz_attempt_answers (
    id uuid primary key default gen_random_uuid(),
    attempt_id uuid not null references public.quiz_attempts(id) on delete cascade,
    question_id uuid not null references public.quiz_questions(id) on delete cascade,
    topic text not null,
    correct boolean not null,
    unique (attempt_id, question_id)
);

create index quiz_answers_attempt_topic_idx on public.quiz_attempt_answers (attempt_id, topic);

alter table public.learner_profiles enable row level security;
alter table public.documents enable row level security;
alter table public.document_sections enable row level security;
alter table public.document_chunks enable row level security;
alter table public.summaries enable row level security;
alter table public.quizzes enable row level security;
alter table public.quiz_questions enable row level security;
alter table public.quiz_answer_keys enable row level security;
alter table public.quiz_attempts enable row level security;
alter table public.quiz_attempt_answers enable row level security;

create policy "Learners manage own profile" on public.learner_profiles
    for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "Learners manage own documents" on public.documents
    for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy "Learners read own sections" on public.document_sections
    for select using (exists (
        select 1 from public.documents d where d.id = document_id and d.owner_id = auth.uid()
    ));
create policy "Learners read own chunks" on public.document_chunks
    for select using (exists (
        select 1 from public.documents d where d.id = document_id and d.owner_id = auth.uid()
    ));
create policy "Learners read own summaries" on public.summaries
    for select using (owner_id = auth.uid());
create policy "Learners manage own quizzes" on public.quizzes
    for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy "Learners read own quiz questions" on public.quiz_questions
    for select using (exists (
        select 1 from public.quizzes q where q.id = quiz_id and q.owner_id = auth.uid()
    ));
create policy "Learners read own attempts" on public.quiz_attempts
    for select using (owner_id = auth.uid());
create policy "Learners read own attempt answers" on public.quiz_attempt_answers
    for select using (exists (
        select 1 from public.quiz_attempts a where a.id = attempt_id and a.owner_id = auth.uid()
    ));

insert into storage.buckets (id, name, public)
values ('academic-documents', 'academic-documents', false)
on conflict (id) do update set public = false;

create policy "Learners manage files in their own folder" on storage.objects
    for all using (
        bucket_id = 'academic-documents'
        and (storage.foldername(name))[1] = auth.uid()::text
    ) with check (
        bucket_id = 'academic-documents'
        and (storage.foldername(name))[1] = auth.uid()::text
    );