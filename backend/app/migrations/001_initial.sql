-- 所有元数据和向量属于同一数据库，版本激活与旧索引清理可以放入一个事务。
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE logical_documents (
    id uuid PRIMARY KEY,
    normalized_filename text NOT NULL UNIQUE
);

CREATE TABLE documents (
    id uuid PRIMARY KEY,
    logical_document_id uuid NOT NULL REFERENCES logical_documents(id) ON DELETE CASCADE,
    filename text NOT NULL,
    sha256 text NOT NULL,
    storage_path text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    is_active boolean NOT NULL DEFAULT false,
    replaces_document_id uuid,
    attempt_count integer NOT NULL DEFAULT 0,
    error_code text,
    error_message text,
    embedding_profile text,
    embedding_config jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- 数据库层也限制每份逻辑文件只能有一个有效版本和一个正在处理的版本。
CREATE UNIQUE INDEX one_active_version ON documents(logical_document_id) WHERE is_active;
CREATE UNIQUE INDEX one_pending_version ON documents(logical_document_id)
    WHERE status IN ('pending', 'processing');
CREATE INDEX pending_tasks ON documents(created_at) WHERE status = 'pending';

CREATE TABLE chunks (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section text NOT NULL,
    content text NOT NULL,
    question text,
    answer text,
    line_start integer NOT NULL CHECK (line_start >= 1),
    line_end integer NOT NULL CHECK (line_end >= line_start),
    embedding vector(768) NOT NULL
);
CREATE INDEX chunks_document ON chunks(document_id);
-- MVP 数据量小，先采用精确余弦检索；无需先为近似索引牺牲召回率。

CREATE TABLE query_logs (
    request_id uuid PRIMARY KEY,
    query_time timestamptz NOT NULL DEFAULT now(),
    retrieved_chunk_ids jsonb NOT NULL,
    retrieval_ms double precision NOT NULL,
    generation_ms double precision NOT NULL,
    result_status text NOT NULL CHECK (result_status IN ('answered', 'insufficient', 'error')),
    error_code text,
    model_name text NOT NULL
);

-- 文件系统无法参加数据库事务。用持久化清理队列补偿，而不是假设 unlink 总会成功。
CREATE TABLE file_gc (
    storage_path text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
