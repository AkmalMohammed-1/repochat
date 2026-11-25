# RepoChat — Project Spec

> Chat with any public GitHub repository. Replace `github.com` in a repo URL with RepoChat's domain and start asking questions about the code.
>
> `https://github.com/AkmalMohammed-1/QueueCTL` → `https://repochat.vercel.app/AkmalMohammed-1/QueueCTL`
>
> (Domain is a placeholder. Use whatever domain/subdomain is actually available.)

Owner: Mohammed Akmal (GitHub: AkmalMohammed-1). Portfolio project for SDE / AI-ML / Forward Deployed Engineer roles.

---

## 1. Working rules for the assistant (read first)

- Build in **small, reviewable steps**. After each step, explain what was written and why, so I can explain every line in an interview.
- **I make the commits.** Suggest a commit message after each step; never add `Co-Authored-By` or any AI attribution to commits, PRs or files.
- No fake numbers anywhere (README, resume bullets, UI). Every metric must come from a script in this repo that I can re-run.
- Prefer boring, well-known tools. Don't add a dependency without saying why.
- Keep secrets in env vars; commit only `.env.example`.

---

## 2. Core user flow

1. User opens `/{owner}/{repo}` (or `/{owner}/{repo}/tree/{branch}`), or pastes a GitHub URL on the landing page.
2. Backend resolves the default branch and **latest commit SHA** via the GitHub API.
3. If that `(repo, sha)` is already indexed → open chat immediately.
4. Otherwise enqueue an indexing job and show **live progress** (files scanned / chunks embedded) via Server-Sent Events.
5. User asks questions. Answers stream token by token and cite sources as clickable links to the exact lines on GitHub:
   `https://github.com/{owner}/{repo}/blob/{sha}/{path}#L{start}-L{end}`
6. Suggested starter questions per repo ("What does this project do?", "Where is the entry point?", "How is data stored?").

### URL routing rules
- `/{owner}/{repo}` → default branch
- `/{owner}/{repo}/tree/{branch}` → that branch
- `/{owner}/{repo}/blob/{branch}/{path}` → open chat with that file pre-selected as context
- Anything invalid / private / too large → friendly error page explaining why

---

## 3. Architecture

```
            ┌──────────────┐      ┌────────────────────┐
 Browser ──►│  Next.js UI  │─────►│  FastAPI (api)     │──► GitHub API
            │  (Vercel)    │ SSE  │  /index /chat /status│
            └──────────────┘◄─────┤                    │
                                  └───┬───────────┬────┘
                                      │ enqueue   │ query
                                      ▼           ▼
                                 ┌────────┐  ┌──────────────────────┐
                                 │  SQS   │  │ Postgres + pgvector  │
                                 └───┬────┘  │ chunks, embeddings,  │
                                     ▼       │ tsvector, repos, jobs│
                                ┌─────────┐  └──────────────────────┘
                                │ worker  │──► S3 (raw repo snapshots)
                                │ (indexer)│
                                └─────────┘
```

| Piece | Tech |
|---|---|
| Frontend | Next.js (App Router) + TypeScript + Tailwind, deployed on Vercel |
| API | FastAPI (Python 3.11+), async, Pydantic models |
| Orchestration | LangGraph (query rewrite → retrieve → rerank → answer) |
| Worker | Python process consuming SQS, runs the indexing pipeline |
| DB | PostgreSQL 16 + pgvector; full-text search via `tsvector` |
| Queue | AWS SQS (LocalStack in local dev) |
| Storage | AWS S3 for downloaded repo tarballs (LocalStack locally) |
| LLM | Provider-configurable (OpenAI / Anthropic / Groq) via env var |
| Embeddings | Configurable: `bge-small-en-v1.5` (local, free) or OpenAI `text-embedding-3-small` |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` (local) |
| Infra | Docker, docker-compose, GitHub Actions, AWS (ECR, ECS Fargate, RDS, SQS, S3) |

---

## 4. Indexing pipeline (worker)

1. **Fetch:** download the repo tarball for the exact SHA from the GitHub API; store in S3 at `repos/{owner}/{repo}/{sha}.tar.gz`.
2. **Filter:** skip binaries, images, lockfiles, `node_modules/`, `dist/`, `build/`, `.git/`, vendored code, files > 1 MB. Respect `.gitignore`.
3. **Limits:** public repos only; max ~2,000 files / 50 MB after filtering (configurable). Reject with a clear message above that.
4. **Chunk (the important part):**
   - Code files → **tree-sitter**: one chunk per function / class / method, with its docstring and signature. Large functions split with overlap. Languages to start: Python, JavaScript/TypeScript, Java, Go.
   - Markdown/docs → split by heading.
   - Other text → fixed-size fallback with overlap.
   - Every chunk stores: `repo_id, sha, path, language, symbol_name, kind, start_line, end_line, content`.
   - Prepend a small header to the embedded text: file path + symbol name (improves retrieval a lot; measure it).
5. **Repo map:** also store a compact file tree + README summary, used to answer "what is this project / where is X" questions.
6. **Embed** in batches; write to `pgvector`; populate `tsvector` column for keyword search.
7. **Progress:** update job row (`files_total, files_done, chunks_done, status`) so the API can stream it.
8. **Idempotent:** re-running the same `(repo, sha)` job must not create duplicates.

---

## 5. Retrieval + answering (LangGraph)

1. **Query rewrite:** turn the user's question (+ short chat history) into a standalone search query; extract likely identifiers (e.g. `processRefund`).
2. **Hybrid search:** top-k from vector search **and** top-k from Postgres full-text search, merged with **Reciprocal Rank Fusion**.
3. **Rerank** merged candidates with the cross-encoder, keep top ~8.
4. **Answer:** LLM answers **only from retrieved chunks**, citing each claim as `[path:Lstart-Lend]`. If the context doesn't contain the answer, say so instead of guessing.
5. **Render citations** in the UI as links to GitHub lines; clicking shows the snippet in a side panel.
6. Stream tokens to the browser via SSE.

---

## 6. Evaluation (makes the resume numbers real)

- `eval/datasets/{repo}.jsonl` — 50 hand-written questions per repo (start with 2 repos, e.g. QueueCTL and a mid-size open-source repo), each with the **expected file(s) / line ranges**.
- `make eval` reports:
  - **Retrieval:** Recall@5, Recall@10, MRR
  - **Answer:** citation accuracy (cited lines overlap expected lines), "I don't know" rate on unanswerable questions
- Run it as an **ablation** and save results to `eval/results/` with date + git SHA:
  1. naive fixed-size chunks + vector only (baseline)
  2. + tree-sitter chunking
  3. + path/symbol header
  4. + hybrid search (RRF)
  5. + reranker
- README shows this table. Only these measured numbers go on the resume.

---

## 7. Freshness (phase 2)

- `POST /webhooks/github`: verifies the HMAC signature; on `push`, re-indexes **only changed files** (diff between old and new SHA) and copies unchanged chunks forward.
- Works for repos where the owner adds the webhook (e.g. my own repos); public repos otherwise refresh on visit when the SHA changes.

---

## 8. Safety & limits

- **Never execute** code from indexed repos. Treat repo content as untrusted text (prompt-injection aware: instructions inside repo files are data, not commands).
- Per-IP rate limits on `/index` and `/chat`.
- GitHub token server-side only; handle GitHub API rate limits with backoff.
- Max concurrent indexing jobs; jobs time out and are retried with backoff, then dead-lettered (SQS DLQ).

---

## 9. Repo structure

```
repochat/
├── apps/
│   ├── web/                 # Next.js frontend
│   └── api/                 # FastAPI app
│       ├── routers/         # repos, chat, status, webhooks
│       ├── graph/           # LangGraph nodes
│       └── core/            # config, db, github client
├── worker/
│   ├── pipeline/            # fetch, filter, chunk, embed
│   └── main.py              # SQS consumer
├── eval/
│   ├── datasets/
│   ├── results/
│   └── run_eval.py
├── infra/
│   ├── docker/              # Dockerfiles
│   └── aws/                 # deploy notes / optional Terraform
├── migrations/              # SQL / Alembic
├── tests/
├── docker-compose.yml       # postgres+pgvector, localstack, api, worker, web
├── Makefile                 # dev, test, eval, lint
├── .env.example
└── README.md
```

---

## 10. Local dev

- `docker compose up` brings up Postgres (pgvector), LocalStack (SQS + S3), api, worker, web.
- `make test` — pytest (unit tests for chunker, filters, RRF, citation parser; one integration test indexing a tiny fixture repo).
- `make eval` — runs the evaluation suite.

---

## 11. Deployment (AWS)

- **CI (GitHub Actions):** lint + tests on every PR; on `main`: build Docker images → push to **ECR** → deploy.
- **API + worker:** ECS Fargate services (or, cheaper for a student: one EC2 instance running docker-compose; document both).
- **DB:** RDS PostgreSQL with pgvector extension.
- **Queue/storage:** SQS (+ DLQ), S3.
- **Frontend:** Vercel, pointing at the API URL.
- **Cost guardrails:** AWS billing alarm at $5 before anything else; free-tier sizes; stop RDS when not demoing.

---

## 12. Milestones

| Week | Deliverable |
|---|---|
| 1 | docker-compose up; GitHub fetch + filter; tree-sitter chunker for Python/JS with unit tests; chunks stored in Postgres |
| 1 | Embeddings + pgvector; simple `/chat` endpoint answering with citations (vector search only) |
| 2 | Next.js UI: landing page, `/{owner}/{repo}` route, indexing progress (SSE), streaming chat, clickable citations |
| 2 | Hybrid search + RRF + reranker; LangGraph flow; eval dataset + `make eval` baseline vs improved |
| 3 | SQS + worker separation, S3 snapshots, retries/DLQ, rate limits; GitHub Actions CI |
| 3 | AWS deploy; README with architecture diagram, eval table, demo GIF/video |
| later | Webhook incremental re-index; more languages; private repos via GitHub OAuth |

## 13. Definition of done (portfolio-ready)

- Live URL where `github.com → <domain>` works for any small/medium public repo
- README: what it does, architecture diagram, how to run locally, **eval results table**, demo video
- Tests pass in CI; clean commit history in small steps
- I can explain every component and design choice without notes
