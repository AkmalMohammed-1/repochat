"""Split files into retrieval chunks.

Strategies (also the eval ablation steps):
  naive       fixed-size line windows for every file, embedded without a header (baseline)
  ast         tree-sitter: one chunk per function / class / method; markdown by heading; fixed fallback
  ast_header  like ast, but the embedded text starts with "File: <path> / Symbol: <name>"
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

from tree_sitter import Language, Node, Parser

MAX_LINES = 150  # definitions longer than this are split into overlapping windows
WINDOW = 100
OVERLAP = 20
FIXED_WINDOW = 60  # fallback / naive chunk size
FIXED_OVERLAP = 10
MAX_CHARS = 6000  # hard cap per chunk (minified or very long lines)


@dataclass(frozen=True)
class Strategy:
    ast: bool
    header: bool


STRATEGIES = {
    "naive": Strategy(ast=False, header=False),
    "ast": Strategy(ast=True, header=False),
    "ast_header": Strategy(ast=True, header=True),
}


@dataclass
class Chunk:
    path: str
    language: str
    symbol_name: str | None
    kind: str
    start_line: int  # 1-based, inclusive
    end_line: int
    content: str


LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".java": "java", ".go": "go",
    ".md": "markdown", ".mdx": "markdown", ".markdown": "markdown",
}


def detect_language(path: str) -> str:
    p = PurePosixPath(path)
    return LANG_BY_EXT.get(p.suffix.lower()) or (p.suffix.lower().lstrip(".") or p.name.lower())


# ---------------------------------------------------------------- tree-sitter language specs


@dataclass
class LangSpec:
    loader: Callable[[], object]
    defs: dict[str, str]  # node type -> chunk kind
    containers: set[str]  # definition types whose body holds methods
    wrappers: dict[str, tuple[str, ...]]  # wrapper node type -> fields that hold the wrapped definition
    var_decls: set[str] = frozenset()  # JS/TS: `const foo = () => {}` style declarations


def _py():
    import tree_sitter_python
    return tree_sitter_python.language()


def _js():
    import tree_sitter_javascript
    return tree_sitter_javascript.language()


def _ts():
    import tree_sitter_typescript
    return tree_sitter_typescript.language_typescript()


def _tsx():
    import tree_sitter_typescript
    return tree_sitter_typescript.language_tsx()


def _java():
    import tree_sitter_java
    return tree_sitter_java.language()


def _go():
    import tree_sitter_go
    return tree_sitter_go.language()


_JS_DEFS = {
    "function_declaration": "function", "generator_function_declaration": "function",
    "class_declaration": "class", "method_definition": "function",
}
_TS_DEFS = {
    **_JS_DEFS, "abstract_class_declaration": "class", "interface_declaration": "interface",
    "type_alias_declaration": "type", "enum_declaration": "enum",
}
_JS_WRAPPERS = {"export_statement": ("declaration", "value")}
_JS_VARS = {"lexical_declaration", "variable_declaration"}

SPECS: dict[str, LangSpec] = {
    "python": LangSpec(_py, {"function_definition": "function", "class_definition": "class"},
                       {"class_definition"}, {"decorated_definition": ("definition",)}),
    "javascript": LangSpec(_js, _JS_DEFS, {"class_declaration"}, _JS_WRAPPERS, _JS_VARS),
    "typescript": LangSpec(_ts, _TS_DEFS, {"class_declaration", "abstract_class_declaration"},
                           _JS_WRAPPERS, _JS_VARS),
    "tsx": LangSpec(_tsx, _TS_DEFS, {"class_declaration", "abstract_class_declaration"}, _JS_WRAPPERS, _JS_VARS),
    "java": LangSpec(_java, {
        "class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
        "record_declaration": "class", "method_declaration": "function", "constructor_declaration": "function",
    }, {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}, {}),
    "go": LangSpec(_go, {"function_declaration": "function", "method_declaration": "method",
                         "type_declaration": "type"}, set(), {}),
}

_COMMENT_TYPES = {"comment", "line_comment", "block_comment"}
_FUNC_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


@lru_cache
def _parser(lang: str) -> Parser:
    return Parser(Language(SPECS[lang].loader()))


def _text(node: Node | None) -> str | None:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else None


def _as_definition(node: Node, spec: LangSpec) -> tuple[str, str, Node, Node] | None:
    """If node is (or wraps) a definition, return (kind, name, outer_node_for_range, inner_def_node)."""
    if node.type in spec.wrappers:
        for field in spec.wrappers[node.type]:
            inner = node.child_by_field_name(field)
            if inner is not None:
                found = _as_definition(inner, spec)
                if found:
                    kind, name, _, inner_def = found
                    return kind, name, node, inner_def
        return None
    if node.type in spec.defs:
        name_node = node.child_by_field_name("name")
        if name_node is None and node.type == "type_declaration":  # Go: type Foo struct{...}
            spec_node = next((c for c in node.named_children if c.type in ("type_spec", "type_alias")), None)
            name_node = spec_node.child_by_field_name("name") if spec_node else None
        return spec.defs[node.type], _text(name_node) or "<anonymous>", node, node
    if node.type in spec.var_decls:
        for decl in node.named_children:
            if decl.type == "variable_declarator":
                value = decl.child_by_field_name("value")
                if value is not None and value.type in _FUNC_VALUES:
                    return "function", _text(decl.child_by_field_name("name")) or "<anonymous>", node, node
    return None


def _start_with_comments(node: Node) -> int:
    """Start row of node, extended upwards over directly preceding comments (docstrings in JS/Java/Go)."""
    start = node.start_point[0]
    prev = node.prev_named_sibling
    while prev is not None and prev.type in _COMMENT_TYPES and prev.end_point[0] >= start - 1:
        start = prev.start_point[0]
        prev = prev.prev_named_sibling
    return start


def _windows(lines: list[str], start: int, end: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """0-based inclusive (start, end) windows covering [start, end], each <= size lines and ~<= MAX_CHARS."""
    out: list[tuple[int, int]] = []
    i = start
    while i <= end:
        j, chars = i, 0
        while j <= end and j - i < size:
            chars += len(lines[j]) + 1
            if chars > MAX_CHARS and j > i:
                break
            j += 1
        out.append((i, j - 1))
        if j > end:
            break
        i = max(j - overlap, i + 1)
    return out


def _make(path: str, language: str, lines: list[str], s: int, e: int, kind: str, symbol: str | None) -> Chunk:
    content = "\n".join(lines[s:e + 1])
    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS]
    return Chunk(path, language, symbol, kind, s + 1, e + 1, content)


def _split(path, language, lines, s, e, kind, symbol) -> list[Chunk]:
    if e - s + 1 <= MAX_LINES:
        chunk = _make(path, language, lines, s, e, kind, symbol)
        if len(chunk.content) < MAX_CHARS:
            return [chunk]
    return [_make(path, language, lines, a, b, kind, symbol) for a, b in _windows(lines, s, e, WINDOW, OVERLAP)]


def _fixed(path: str, language: str, lines: list[str], s: int, e: int, kind: str) -> list[Chunk]:
    return [_make(path, language, lines, a, b, kind, None)
            for a, b in _windows(lines, s, e, FIXED_WINDOW, FIXED_OVERLAP)]


def _walk(node: Node, spec: LangSpec, ctx: tuple[str, str, list[str]], parent: str | None,
          out: list[Chunk]) -> None:
    path, language, lines = ctx
    for child in node.named_children:
        found = _as_definition(child, spec)
        if found is None:
            continue
        kind, name, outer, inner = found
        qual = f"{parent}.{name}" if parent else name
        if parent and kind == "function":
            kind = "method"
        start, end = _start_with_comments(outer), outer.end_point[0]
        body = inner.child_by_field_name("body") if inner.type in spec.containers else None
        if body is not None and end - start + 1 > MAX_LINES:
            members = [c for c in body.named_children if _as_definition(c, spec)]
            if members:
                # Big class: one chunk for its signature/docstring/fields, then one per method.
                header_end = max(start, _start_with_comments(members[0]) - 1)
                out.extend(_split(path, language, lines, start, header_end, kind, qual))
                _walk(body, spec, ctx, qual, out)
                continue
        out.extend(_split(path, language, lines, start, end, kind, qual))


def _fill_gaps(path: str, language: str, lines: list[str], chunks: list[Chunk]) -> list[Chunk]:
    """Module-level code not inside any definition (imports, constants, scripts) gets fixed-size chunks."""
    covered = [False] * len(lines)
    for c in chunks:
        for i in range(c.start_line - 1, min(c.end_line, len(lines))):
            covered[i] = True
    gaps: list[Chunk] = []
    i = 0
    while i < len(lines):
        if covered[i] or not lines[i].strip():
            i += 1
            continue
        j = i
        while j + 1 < len(lines) and not covered[j + 1]:
            j += 1
        while j > i and not lines[j].strip():
            j -= 1
        gaps.extend(_fixed(path, language, lines, i, j, "module"))
        i = j + 1
    return gaps


def chunk_code(path: str, language: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    tree = _parser(language).parse(text.encode("utf-8"))
    chunks: list[Chunk] = []
    _walk(tree.root_node, SPECS[language], (path, language, lines), None, chunks)
    chunks.extend(_fill_gaps(path, language, lines, chunks))
    return sorted(chunks, key=lambda c: (c.start_line, c.end_line))


_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def chunk_markdown(path: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    starts: list[tuple[int, str | None]] = [(0, None)]
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and (m := _HEADING.match(line)):
            starts.append((i, m.group(2)))
    out: list[Chunk] = []
    for idx, (s, title) in enumerate(starts):
        e = (starts[idx + 1][0] - 1) if idx + 1 < len(starts) else len(lines) - 1
        while e >= s and not lines[e].strip():
            e -= 1
        if e < s or not any(line.strip() for line in lines[s:e + 1]):
            continue
        out.extend(_split(path, "markdown", lines, s, e, "section", title))
    return out


def chunk_file(path: str, text: str, strategy: str = "ast_header") -> list[Chunk]:
    language = detect_language(path)
    lines = text.splitlines()
    if not lines:
        return []
    if not STRATEGIES[strategy].ast:
        return _fixed(path, language, lines, 0, len(lines) - 1, "text")
    if language in SPECS:
        try:
            return chunk_code(path, language, text)
        except Exception:  # never fail a whole repo on one odd file
            pass
    if language == "markdown":
        return chunk_markdown(path, text)
    return _fixed(path, language, lines, 0, len(lines) - 1, "text")


# ---------------------------------------------------------------- text used for embedding / keyword search

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def split_identifier(ident: str) -> list[str]:
    """processRefund -> [process, refund]; HTTPServer_v2 -> [http, server, v2]."""
    parts: list[str] = []
    for piece in ident.split("_"):
        parts.extend(p.lower() for p in _CAMEL.findall(piece))
    return [p for p in parts if len(p) > 1]


def embed_text(chunk: Chunk, header: bool) -> str:
    if not header:
        return chunk.content
    head = f"File: {chunk.path}"
    if chunk.symbol_name:
        head += f"\nSymbol: {chunk.symbol_name} ({chunk.kind})"
    return f"{head}\n\n{chunk.content}"


def search_text(chunk: Chunk) -> str:
    """Text for the Postgres tsvector: path pieces, symbol, content, plus camel/snake-case parts so that
    a query for 'refund' finds processRefund."""
    path_words = re.sub(r"[/._-]", " ", chunk.path)
    idents = set(_IDENT.findall(chunk.content)) | set(_IDENT.findall(chunk.symbol_name or ""))
    extra = {p for ident in idents for p in split_identifier(ident)}
    return " ".join([chunk.path, path_words, chunk.symbol_name or "", chunk.content, " ".join(sorted(extra))])
