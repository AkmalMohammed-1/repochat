"""Retrieval and answer metrics. Pure functions so they are unit-tested."""

import re

from api.graph.citations import overlaps, parse_citations

IDK = re.compile(r"\bI (?:do not|don't|don’t) know\b", re.I)


def is_relevant(chunk: dict, expected: list[dict]) -> bool:
    return any(chunk["path"] == e["path"] and overlaps(chunk["start_line"], chunk["end_line"], e["start"], e["end"])
               for e in expected)


def recall_at_k(retrieved: list[dict], expected: list[dict], k: int) -> float:
    """Fraction of expected line ranges touched by at least one of the top-k chunks."""
    if not expected:
        return 0.0
    top = retrieved[:k]
    hit = sum(1 for e in expected if any(is_relevant(c, [e]) for c in top))
    return hit / len(expected)


def reciprocal_rank(retrieved: list[dict], expected: list[dict], k: int = 10) -> float:
    for rank, chunk in enumerate(retrieved[:k], start=1):
        if is_relevant(chunk, expected):
            return 1.0 / rank
    return 0.0


def citation_scores(answer: str, expected: list[dict]) -> tuple[int, int]:
    """(correct citations, total citations). Correct = cited range overlaps an expected range."""
    cites = parse_citations(answer)
    correct = sum(1 for _, path, s, e in cites
                  if any(path == x["path"] and overlaps(s, e, x["start"], x["end"]) for x in expected))
    return correct, len(cites)


def said_idk(answer: str) -> bool:
    return bool(IDK.search(answer[:300]))


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
