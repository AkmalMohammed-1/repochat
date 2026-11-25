"""Parse `[path:Lstart-Lend]` citations out of an answer and resolve them to GitHub links."""

import re
from dataclasses import asdict, dataclass

from api.core.repo_ref import github_line_url

# [path:L1-L2] as instructed. Also accepted, because models drift from the format: `path:L1-L2` (formatted
# as code) and 【path:L1-L2】 (gpt-oss's native citation brackets). Keep in sync with apps/web/lib/citations.ts.
CITATION = re.compile(r"[\[`【]([^\[\]`【】\s:][^\[\]`【】:]*?):L(\d+)(?:\s*-\s*L?(\d+))?[\]`】]")


@dataclass
class Citation:
    raw: str
    path: str
    start: int
    end: int
    url: str
    valid: bool  # the cited range overlaps a chunk that was actually retrieved

    def to_dict(self) -> dict:
        return asdict(self)


def parse_citations(text: str) -> list[tuple[str, str, int, int]]:
    out = []
    for m in CITATION.finditer(text):
        start = int(m.group(2))
        end = int(m.group(3) or start)
        if end < start:
            start, end = end, start
        out.append((m.group(0), m.group(1).strip().lstrip("./"), start, end))
    return out


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def resolve_citations(text: str, chunks: list[dict], owner: str, repo: str, sha: str) -> list[Citation]:
    seen: set[str] = set()
    result: list[Citation] = []
    for raw, path, start, end in parse_citations(text):
        if raw in seen:
            continue
        seen.add(raw)
        valid = any(c["path"] == path and overlaps(start, end, c["start_line"], c["end_line"]) for c in chunks)
        result.append(Citation(raw, path, start, end, github_line_url(owner, repo, sha, path, start, end), valid))
    return result
