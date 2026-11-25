CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE repos (
    id              BIGSERIAL PRIMARY KEY,
    owner           TEXT NOT NULL,
    name            TEXT NOT NULL,
    default_branch  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- GitHub names are case-insensitive; keep GitHub's display casing but match case-insensitively.
CREATE UNIQUE INDEX repos_owner_name ON repos (lower(owner), lower(name));

-- One row per indexed (repo, commit, chunking strategy). Doubles as the job/progress row.
-- strategy lets the eval ablation keep several chunkings of the same commit side by side.
CREATE TABLE snapshots (
    id                BIGSERIAL PRIMARY KEY,
    repo_id           BIGINT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    sha               TEXT NOT NULL,
    strategy          TEXT NOT NULL DEFAULT 'ast_header',
    status            TEXT NOT NULL DEFAULT 'queued',  -- queued | running | ready | failed | rejected
    error             TEXT,
    attempts          INT NOT NULL DEFAULT 0,
    files_total       INT NOT NULL DEFAULT 0,
    files_done        INT NOT NULL DEFAULT 0,
    chunks_done       INT NOT NULL DEFAULT 0,
    base_snapshot_id  BIGINT REFERENCES snapshots(id) ON DELETE SET NULL,  -- for incremental re-index
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    UNIQUE (repo_id, sha, strategy)
);
CREATE INDEX snapshots_repo_ready ON snapshots (repo_id, strategy, finished_at DESC) WHERE status = 'ready';

CREATE TABLE chunks (
    id            BIGSERIAL PRIMARY KEY,
    snapshot_id   BIGINT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    path          TEXT NOT NULL,
    language      TEXT NOT NULL,
    symbol_name   TEXT,
    kind          TEXT NOT NULL,           -- function | class | method | module | section | text ...
    start_line    INT NOT NULL,
    end_line      INT NOT NULL,
    content       TEXT NOT NULL,
    embedding     vector(384) NOT NULL,    -- bge-small-en-v1.5 / text-embedding-3-small@384 dims
    tsv           tsvector NOT NULL        -- built in Python: path + symbol + content + split identifiers
);
CREATE INDEX chunks_snapshot ON chunks (snapshot_id, path);
CREATE INDEX chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv ON chunks USING gin (tsv);

-- Compact overview used for "what is this project / where is X" questions.
CREATE TABLE repo_maps (
    snapshot_id     BIGINT PRIMARY KEY REFERENCES snapshots(id) ON DELETE CASCADE,
    file_tree       TEXT NOT NULL,
    readme_summary  TEXT NOT NULL,
    paths           TEXT[] NOT NULL
);
