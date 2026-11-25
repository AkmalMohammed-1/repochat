# RepoChat

Chat with any public GitHub repository. Replace `github.com` in a repo URL with RepoChat's domain and start asking
questions about the code. Every answer cites the exact lines it came from.

```
https://github.com/AkmalMohammed-1/QueueCTL  →  https://<repochat-domain>/AkmalMohammed-1/QueueCTL
```

<!-- Demo GIF / video goes here -->

## How it works

```
            ┌──────────────┐      ┌─────────────────────┐
 Browser ──►│  Next.js UI  │─────►│  FastAPI (api)      │──► GitHub API
            │  (Vercel)    │ SSE  │  /resolve /chat     │
            └──────────────┘◄─────┤  /events /webhooks  │
                                  └───┬────────────┬────┘
                                      │ enqueue    │ query
                                      ▼            ▼
                                 ┌────────┐  ┌──────────────────────┐
                                 │  SQS   │  │ Postgres + pgvector  │
                                 │ (+DLQ) │  │ chunks, embeddings,  │
                                 └───┬────┘  │ tsvector, snapshots  │
                                     ▼       └──────────────────────┘
                                ┌──────────┐        ▲
                                │  worker  │────────┘
                                │ (indexer)│──► S3 (repo tarballs)
                                └──────────┘
```

1. **Resolve.** `/{owner}/{repo}` (or `/tree/{branch}`, `/blob/{branch}/{path}`) is resolved to the latest
   commit SHA. Everything is keyed by `(repo, sha)`, so a commit that's already indexed opens instantly.
2. **Index.** For a new commit the API enqueues an SQS job. The worker then:
   - downloads the tarball for that exact SHA and caches it in S3
   - filters out binaries, lockfiles, vendored/build directories, `.gitignore`d files and files over 1 MB
   - chunks the code with **tree-sitter**: one chunk per function, class or method (Python, JS/TS, Java, Go),
     Markdown by heading, and fixed-size windows for everything else
   - embeds `File: <path> / Symbol: <name>` plus the code with `bge-small-en-v1.5`
   - writes vectors to pgvector and a keyword index to a Postgres `tsvector`

   Progress streams to the browser over Server-Sent Events. Failed jobs retry with backoff, then go to a DLQ.
3. **Answer.** A LangGraph pipeline runs **query rewrite** (resolves "it"/"that" from the chat history) →
   **hybrid retrieval** (pgvector + full-text, merged with Reciprocal Rank Fusion) → **cross-encoder rerank**
   (`ms-marco-MiniLM-L-6-v2`, keeps the top 8) → **answer**. The LLM may only use the retrieved chunks, must cite
   `[path:Lstart-Lend]`, and says "I don't know" when the context doesn't cover the question. Tokens stream to the
   UI, and citations become links to the exact lines on GitHub at that commit.
4. **Freshness.** A GitHub `push` webhook, or simply visiting after a new commit, re-indexes only the files that
   changed since the last indexed commit. Unchanged chunks and their embeddings are copied forward.

Repo content is treated as untrusted data: it is never executed, and the prompts tell the model to ignore
instructions found inside it.

## Evaluation

`make eval` runs an ablation over hand-written question sets in `eval/datasets/` (format:
[eval/datasets/README.md](eval/datasets/README.md)). Each step adds one improvement:

1. fixed-size chunks + vector search (baseline)
2. + tree-sitter chunking
3. + path/symbol header
4. + hybrid search (RRF)
5. + cross-encoder reranker

It reports Recall@5, Recall@10 and MRR. `make eval-answers` also reports citation accuracy and the "I don't know"
rate on unanswerable questions. Results are saved to `eval/results/` with the date and git SHA.

<!-- Paste the table from eval/results/<date>_<sha>_<dataset>.md here. Only numbers produced by `make eval` go here. -->

## Running locally

Requirements: Docker. For development without Docker you also need [uv](https://docs.astral.sh/uv/) and Node 22.

```bash
cp .env.example .env        # add GITHUB_TOKEN and GROQ_API_KEY (free at console.groq.com)
docker compose up --build   # Postgres+pgvector, LocalStack (SQS+S3), api, worker, web
```

Open http://localhost:3000. API docs are at http://localhost:8000/docs. If port 5432 is taken, run
`DB_PORT=5433 docker compose up`.

Development with hot reload (`make` targets; on Windows run the underlying commands from the Makefile, with
`PYTHONPATH=apps;.`):

```bash
make install
make dev       # db + localstack in Docker, migrations, queue + bucket
make api       # terminal 1: FastAPI on :8000
make worker    # terminal 2: SQS consumer
make web       # terminal 3: Next.js on :3000
```

Tests and lint:

```bash
make test                                     # unit + integration (integration needs `docker compose up -d db`)
uv run pytest tests/test_chunk.py -k python   # a single test
make lint
```

Configuration lives in environment variables; see `.env.example`. The LLM is `groq` by default and switchable
to `openai` or `anthropic`. Embeddings are `local` by default; the alternative is OpenAI `text-embedding-3-small`
at 384 dimensions.

## Deployment

The frontend runs on Vercel. The API and worker run on ECS Fargate with RDS Postgres (pgvector), SQS + DLQ and S3.
GitHub Actions runs lint and tests on every PR, and on `main` it builds the image, pushes it to ECR and rolls the
ECS services. Step-by-step instructions, including a $5 billing alarm and a cheaper single-EC2 option, are in
[infra/aws/README.md](infra/aws/README.md).

## Limits

Public repos only, at most 2,000 files and 50 MB of text after filtering (configurable). There are per-IP rate
limits on indexing and chat.
