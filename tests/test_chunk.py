from pathlib import Path

from worker.pipeline.chunk import (
    MAX_LINES,
    chunk_file,
    embed_text,
    search_text,
    split_identifier,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_repo"


def _symbols(path: str) -> list[tuple[str, str | None, int, int]]:
    text = (FIXTURE / path).read_text()
    return [(c.kind, c.symbol_name, c.start_line, c.end_line) for c in chunk_file(path, text)]


def test_python_functions_and_classes():
    chunks = _symbols("shop/payments.py")
    assert ("function", "validate_amount", 15, 20) in chunks
    assert ("class", "PaymentService", 23, 38) in chunks
    assert ("class", "Payment", 8, 12) in chunks  # decorator included
    assert chunks[0][0] == "module"  # imports / constants are not lost


def test_typescript():
    chunks = _symbols("web/cart.ts")
    assert ("function", "formatPrice", 9, 12) in chunks  # JSDoc comment included
    assert ("class", "Cart", 14, 29) in chunks
    assert ("interface", "CartItem", 3, 7) in chunks
    assert ("module", None, 31, 31) in chunks


def test_java_and_go():
    assert ("class", "Server", 8, 27) in _symbols("api/Server.java")
    go = _symbols("worker/queue.go")
    assert ("method", "Pop", 22, 30) in go
    assert ("type", "Job", 5, 10) in go


def test_markdown_by_heading():
    assert [(k, s) for k, s, _, _ in _symbols("README.md")] == [
        ("section", "TinyShop"), ("section", "Setup"), ("section", "Architecture")]


def test_large_class_split_into_methods():
    methods = "\n".join(f"    def m{i}(self):\n" + "        x = 1\n" * 10 for i in range(20))
    src = f'class Big:\n    """Doc."""\n    attr = 1\n\n{methods}\n'
    chunks = chunk_file("big.py", src)
    assert chunks[0].symbol_name == "Big" and chunks[0].end_line < 5  # header only
    assert {c.symbol_name for c in chunks[1:]} == {f"Big.m{i}" for i in range(20)}
    assert all(c.kind == "method" for c in chunks[1:])


def test_long_function_split_with_overlap():
    body = "\n".join(f"    x{i} = {i}" for i in range(MAX_LINES + 50))
    chunks = chunk_file("long.py", f"def long():\n{body}\n")
    assert len(chunks) >= 2
    assert all(c.symbol_name == "long" for c in chunks)
    assert chunks[1].start_line < chunks[0].end_line  # overlap


def test_naive_strategy_ignores_structure():
    text = (FIXTURE / "shop/payments.py").read_text()
    chunks = chunk_file("shop/payments.py", text, strategy="naive")
    assert all(c.kind == "text" and c.symbol_name is None for c in chunks)


def test_unknown_language_and_empty():
    assert chunk_file("notes.txt", "") == []
    chunks = chunk_file("config.yaml", "a: 1\nb: 2\n")
    assert chunks[0].language == "yaml" and chunks[0].kind == "text"


def test_header_and_search_text():
    chunk = chunk_file("shop/payments.py", (FIXTURE / "shop/payments.py").read_text())[-1]
    assert embed_text(chunk, header=True).startswith("File: shop/payments.py\nSymbol: PaymentService")
    assert embed_text(chunk, header=False) == chunk.content
    words = search_text(chunk).split()
    assert "refund" in words and "process" in words  # from process_refund


def test_split_identifier():
    assert split_identifier("processRefund") == ["process", "refund"]
    assert split_identifier("HTTPServer_v2") == ["http", "server"]  # 1-char parts dropped
