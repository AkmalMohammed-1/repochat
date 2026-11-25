"""Evaluation + ablation runner.

    python eval/run_eval.py eval/datasets/queuectl.jsonl [--answers] [--reindex]

For each dataset it indexes the repo once per chunking strategy (in-process, no queue), then runs every
ablation variant over the answerable questions and reports Recall@5, Recall@10 and MRR. With --answers it
also runs the full chat graph with the final variant and reports citation accuracy and the "I don't know"
rate on unanswerable questions (needs an LLM API key).

Results are written to eval/results/<date>_<git sha>_<dataset>.{json,md}. Only numbers produced by this
script belong in the README or on a resume.
"""

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps"), str(ROOT)]

from api.core import snapshots  # noqa: E402
from api.core.compat import run  # noqa: E402
from api.core.config import get_settings  # noqa: E402
from api.core.db import create_pool  # noqa: E402
from api.core.embeddings import get_embedder  # noqa: E402
from api.core.github import GitHubClient  # noqa: E402
from api.core.migrate import migrate  # noqa: E402
from api.graph.retrieval import RetrievalConfig, extract_identifiers, retrieve  # noqa: E402
from eval.metrics import citation_scores, mean, recall_at_k, reciprocal_rank, said_idk  # noqa: E402
from worker.pipeline.run import index_snapshot  # noqa: E402

VECTOR_ONLY = RetrievalConfig(hybrid=False, rerank=False)
VARIANTS = [
    # (name, chunking strategy, retrieval config) - each row adds one improvement to the previous one
    ("1. fixed-size chunks + vector", "naive", VECTOR_ONLY),
    ("2. + tree-sitter chunking", "ast", VECTOR_ONLY),
    ("3. + path/symbol header", "ast_header", VECTOR_ONLY),
    ("4. + hybrid search (RRF)", "ast_header", RetrievalConfig(hybrid=True, rerank=False)),
    ("5. + cross-encoder rerank", "ast_header", RetrievalConfig(hybrid=True, rerank=True)),
]


def load_dataset(path: Path) -> tuple[dict, list[dict]]:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines or "_meta" not in lines[0]:
        raise SystemExit(f"{path}: first line must be a {{\"_meta\": {{...}}}} header")
    return lines[0]["_meta"], lines[1:]


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "nogit"


async def ensure_index(pool, meta: dict, strategy: str, reindex: bool, gh: GitHubClient | None) -> int:
    owner, name = meta["repo"].split("/", 1)
    source = meta.get("source", "github")
    repo_id = await snapshots.upsert_repo(pool, owner, name, None)
    sha = meta["sha"]
    if reindex:
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM snapshots WHERE repo_id = %s AND sha = %s AND strategy = %s",
                               (repo_id, sha, strategy))
    snapshot_id = await snapshots.ensure_indexed(pool, repo_id, sha, strategy, enqueue=False)
    snap = await snapshots.get_snapshot(pool, snapshot_id)
    if snap["status"] != "ready":
        print(f"  indexing {meta['repo']}@{sha[:7]} with strategy={strategy} ...", flush=True)
        started = time.monotonic()
        source_dir = ROOT / source.split(":", 1)[1] if source.startswith("local:") else None
        await index_snapshot(pool, snapshot_id, embedder=get_embedder(), gh=gh, source_dir=source_dir,
                             summarize=False)
        print(f"  done in {time.monotonic() - started:.0f}s", flush=True)
    return snapshot_id


async def evaluate(dataset_path: Path, answers: bool, reindex: bool) -> dict:
    s = get_settings()
    meta, questions = load_dataset(dataset_path)
    answerable = [q for q in questions if q.get("answerable", True)]
    unanswerable = [q for q in questions if not q.get("answerable", True)]
    await migrate()
    pool = await create_pool()
    gh = None if meta.get("source", "github").startswith("local:") else GitHubClient()
    embedder = get_embedder()

    snapshot_ids = {}
    for strategy in dict.fromkeys(v[1] for v in VARIANTS):
        snapshot_ids[strategy] = await ensure_index(pool, meta, strategy, reindex, gh)

    rows = []
    for name, strategy, cfg in VARIANTS:
        r5, r10, rr, latency = [], [], [], []
        for q in answerable:
            started = time.monotonic()
            got = await retrieve(pool, embedder, snapshot_ids[strategy], q["question"],
                                 extract_identifiers(q["question"]), cfg, top_n=10)
            latency.append(time.monotonic() - started)
            r5.append(recall_at_k(got, q["expected"], 5))
            r10.append(recall_at_k(got, q["expected"], 10))
            rr.append(reciprocal_rank(got, q["expected"]))
        rows.append({"variant": name, "recall@5": mean(r5), "recall@10": mean(r10), "mrr": mean(rr),
                     "avg_retrieval_s": mean(latency)})
        print(f"  {name:32s} R@5={mean(r5):.3f} R@10={mean(r10):.3f} MRR={mean(rr):.3f}", flush=True)

    answer_metrics = None
    if answers:
        answer_metrics = await evaluate_answers(pool, embedder, meta, snapshot_ids["ast_header"],
                                                answerable, unanswerable)

    if gh:
        await gh.aclose()
    await pool.close()
    return {
        "dataset": dataset_path.name, "repo": meta["repo"], "repo_sha": meta["sha"],
        "date": dt.date.today().isoformat(), "git_sha": git_sha(),
        "n_answerable": len(answerable), "n_unanswerable": len(unanswerable),
        "embedding": f"{s.embedding_provider}:{s.embedding_model if s.embedding_provider == 'local' else ''}",
        "reranker": s.reranker_model, "retrieval": rows, "answers": answer_metrics,
    }


async def evaluate_answers(pool, embedder, meta, snapshot_id, answerable, unanswerable) -> dict:
    from api.core.llm import get_llm
    from api.graph.graph import Deps, build_graph

    s = get_settings()
    graph = build_graph(Deps(pool=pool, embedder=embedder, llm=get_llm()))
    owner, name = meta["repo"].split("/", 1)
    overview = await snapshots.get_repo_map(pool, snapshot_id)
    base = {"history": [], "overview": overview, "focus_path": None,
            "snapshot": {"id": snapshot_id, "owner": owner, "repo": name, "sha": meta["sha"]}}
    correct = total = false_idk = 0
    for q in answerable:
        out = await graph.ainvoke({**base, "question": q["question"]})
        c, t = citation_scores(out["answer"], q["expected"])
        correct, total = correct + c, total + t
        false_idk += said_idk(out["answer"])
    idk = 0
    for q in unanswerable:
        out = await graph.ainvoke({**base, "question": q["question"]})
        idk += said_idk(out["answer"])
    result = {
        "llm": f"{s.llm_provider}:{s.llm_model or 'default'}",
        "citation_accuracy": correct / total if total else 0.0,
        "citations_total": total,
        "idk_rate_unanswerable": idk / len(unanswerable) if unanswerable else None,
        "idk_rate_answerable": false_idk / len(answerable) if answerable else None,
    }
    print(f"  answers: citation accuracy={result['citation_accuracy']:.3f} ({total} citations), "
          f"IDK on unanswerable={result['idk_rate_unanswerable']}", flush=True)
    return result


def to_markdown(r: dict) -> str:
    lines = [
        f"## {r['repo']} @ `{r['repo_sha'][:7]}` ({r['dataset']})",
        "",
        f"Run {r['date']} at RepoChat commit `{r['git_sha']}`. {r['n_answerable']} answerable / "
        f"{r['n_unanswerable']} unanswerable questions. Embeddings: `{r['embedding']}`.",
        "",
        "| Variant | Recall@5 | Recall@10 | MRR |",
        "|---|---|---|---|",
    ]
    for row in r["retrieval"]:
        lines.append(f"| {row['variant']} | {row['recall@5']:.3f} | {row['recall@10']:.3f} | {row['mrr']:.3f} |")
    if r.get("answers"):
        a = r["answers"]
        lines += ["", f"Answers ({a['llm']}): citation accuracy **{a['citation_accuracy']:.3f}** "
                      f"over {a['citations_total']} citations; \"I don't know\" rate on unanswerable questions "
                      f"**{a['idk_rate_unanswerable']}**, on answerable questions {a['idk_rate_answerable']}."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("datasets", nargs="*", type=Path, help="JSONL datasets (default: all in eval/datasets)")
    parser.add_argument("--answers", action="store_true", help="also evaluate generated answers (uses the LLM)")
    parser.add_argument("--reindex", action="store_true", help="rebuild the eval snapshots from scratch")
    args = parser.parse_args()
    datasets = args.datasets or sorted((ROOT / "eval" / "datasets").glob("*.jsonl"))
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in datasets:
        print(f"== {path.name}", flush=True)
        result = run(evaluate(path, args.answers, args.reindex))
        stem = f"{result['date']}_{result['git_sha']}_{path.stem}"
        (out_dir / f"{stem}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(to_markdown(result), encoding="utf-8")
        print(to_markdown(result))
        print(f"saved eval/results/{stem}.json")


if __name__ == "__main__":
    main()
