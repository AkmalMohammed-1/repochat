"""LangGraph chat pipeline: rewrite -> retrieve -> rerank -> answer.

Nodes push UI events through LangGraph's custom stream ("sources" after rerank, "token" while answering),
so the API can forward them as Server-Sent Events.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool

from api.core.embeddings import Embedder
from api.core.llm import LLM
from api.graph.prompts import ANSWER_SYSTEM, REWRITE_SYSTEM, format_context
from api.graph.retrieval import RetrievalConfig, extract_identifiers, search_candidates, select_final

MAX_HISTORY_TURNS = 6


class ChatState(TypedDict, total=False):
    # inputs
    question: str
    history: list[dict]  # [{"role": "user"|"assistant", "content": str}]
    snapshot: dict  # {"id", "owner", "repo", "sha"}
    focus_path: str | None
    overview: dict | None
    # produced by nodes
    query: str
    identifiers: list[str]
    candidates: list[dict]
    focus_chunk: dict | None
    chunks: list[dict]
    answer: str


@dataclass
class Deps:
    pool: AsyncConnectionPool
    embedder: Embedder
    llm: LLM
    cfg: RetrievalConfig = RetrievalConfig()


def _trim_history(history: list[dict]) -> list[dict]:
    return [{"role": m["role"], "content": m["content"][:4000]} for m in history[-MAX_HISTORY_TURNS * 2:]
            if m.get("role") in ("user", "assistant") and m.get("content")]


def build_graph(deps: Deps):
    async def rewrite(state: ChatState) -> dict[str, Any]:
        question = state["question"]
        identifiers = extract_identifiers(question)
        history = _trim_history(state.get("history") or [])
        if not history:
            return {"query": question, "identifiers": identifiers}
        convo = "\n".join(f"{m['role']}: {m['content'][:1000]}" for m in history)
        try:
            raw = await deps.llm.complete(
                REWRITE_SYSTEM, [{"role": "user", "content": f"{convo}\nuser: {question}"}], max_tokens=1000)
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            query = str(data.get("query") or question)
            extra = [str(i) for i in data.get("identifiers", []) if isinstance(i, str)]
            return {"query": query, "identifiers": list(dict.fromkeys(identifiers + extra))[:10]}
        except Exception:  # rewrite is an optimisation; fall back to the raw question
            return {"query": question, "identifiers": identifiers}

    async def retrieve(state: ChatState) -> dict[str, Any]:
        candidates, focus = await search_candidates(
            deps.pool, deps.embedder, state["snapshot"]["id"], state["query"], state.get("identifiers", []),
            deps.cfg, state.get("focus_path"))
        return {"candidates": candidates, "focus_chunk": focus}

    async def rerank(state: ChatState) -> dict[str, Any]:
        chunks = await select_final(state["query"], state["candidates"], deps.cfg, state.get("focus_chunk"))
        get_stream_writer()({"type": "sources", "chunks": chunks})
        return {"chunks": chunks}

    async def answer(state: ChatState) -> dict[str, Any]:
        snap = state["snapshot"]
        system = ANSWER_SYSTEM.format(owner=snap["owner"], repo=snap["repo"], short_sha=snap["sha"][:7])
        context = format_context(state["chunks"], state.get("overview"))
        focus = f"\nThe user has the file {state['focus_path']} open.\n" if state.get("focus_path") else ""
        messages = [
            *_trim_history(state.get("history") or []),
            {"role": "user", "content": f"Context:\n{context}\n{focus}\nQuestion: {state['question']}"},
        ]
        writer = get_stream_writer()
        parts: list[str] = []
        async for token in deps.llm.stream(system, messages):
            parts.append(token)
            writer({"type": "token", "text": token})
        return {"answer": "".join(parts)}

    g = StateGraph(ChatState)
    g.add_node("rewrite", rewrite)
    g.add_node("retrieve", retrieve)
    g.add_node("rerank", rerank)
    g.add_node("answer", answer)
    g.add_edge(START, "rewrite")
    g.add_edge("rewrite", "retrieve")
    g.add_edge("retrieve", "rerank")
    g.add_edge("rerank", "answer")
    g.add_edge("answer", END)
    return g.compile()
