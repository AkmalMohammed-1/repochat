from api.graph.citations import parse_citations, resolve_citations
from api.graph.retrieval import build_tsquery, extract_identifiers, reciprocal_rank_fusion
from api.routers.repos import suggest_questions
from api.routers.webhooks import verify_signature
from eval.metrics import citation_scores, recall_at_k, reciprocal_rank, said_idk


def test_rrf_rewards_agreement():
    # 3 is ranked well by both lists, so it beats items that only one list liked.
    assert reciprocal_rank_fusion([[1, 3, 4], [2, 3, 5]])[0] == 3
    assert reciprocal_rank_fusion([[1, 2], []]) == [1, 2]
    assert reciprocal_rank_fusion([]) == []


def test_rrf_scores():
    order = reciprocal_rank_fusion([[10, 20], [20, 10]], k=60)
    assert set(order) == {10, 20}  # tie broken deterministically by id
    assert order == [10, 20]


def test_extract_identifiers():
    ids = extract_identifiers("Where is processRefund called from `shop/db.py` and what does save_order do?")
    assert ids == ["processRefund", "shop/db.py", "save_order"]


def test_build_tsquery():
    q = build_tsquery("How does the refund work?", ["processRefund"])
    assert set(q.split(" | ")) == {"refund", "processrefund", "process"}
    assert build_tsquery("how is it?", []) is None


def test_citations():
    text = "Refunds expire [shop/payments.py:L33-L38] and [shop/payments.py:L5]. Bad: [nope.py:L1-L2] [x](y)"
    assert [(p, s, e) for _, p, s, e in parse_citations(text)] == [
        ("shop/payments.py", 33, 38), ("shop/payments.py", 5, 5), ("nope.py", 1, 2)]
    chunks = [{"path": "shop/payments.py", "start_line": 30, "end_line": 40}]
    cites = resolve_citations(text, chunks, "o", "r", "sha1")
    assert [c.valid for c in cites] == [True, False, False]
    assert cites[0].url == "https://github.com/o/r/blob/sha1/shop/payments.py#L33-L38"


def test_metrics():
    expected = [{"path": "a.py", "start": 10, "end": 20}, {"path": "b.py", "start": 1, "end": 5}]
    got = [{"path": "c.py", "start_line": 1, "end_line": 9}, {"path": "a.py", "start_line": 15, "end_line": 30}]
    assert recall_at_k(got, expected, 1) == 0.0
    assert recall_at_k(got, expected, 5) == 0.5
    assert reciprocal_rank(got, expected) == 0.5
    assert citation_scores("see [a.py:L12-L13] and [b.py:L9]", expected) == (1, 2)
    assert said_idk("I don't know - the code doesn't mention OAuth.")
    assert not said_idk("Refunds are processed in [a.py:L1-L2].")


def test_webhook_signature():
    body = b'{"zen": "x"}'
    good = "sha256=" + __import__("hmac").new(b"s3cret", body, "sha256").hexdigest()
    assert verify_signature("s3cret", body, good)
    assert not verify_signature("s3cret", body, "sha256=deadbeef")
    assert not verify_signature("", body, good)  # no secret configured -> reject everything


def test_suggestions():
    qs = suggest_questions(["src/main.py", "tests/test_main.py", "Dockerfile", "migrations/001.sql"])
    assert qs[:2] == ["What does this project do?", "Where is the entry point?"]
    assert "How is data stored?" in qs and "How are the tests organised and run?" in qs


def test_citations_in_backticks_are_accepted():
    text = "Defined in `src/signer.py:L182-L213` and [src/signer.py:L5]."
    assert [(p, s, e) for _, p, s, e in parse_citations(text)] == [("src/signer.py", 182, 213), ("src/signer.py", 5, 5)]
    assert parse_citations("`config.py` and `x = a[0]`") == []  # ordinary inline code is not a citation


def test_citations_in_other_bracket_styles():
    text = "Validated here【src/timed.py:L22-L167】 and [[src/timed.py:L44-L55]]."
    assert [(p, s, e) for _, p, s, e in parse_citations(text)] == [("src/timed.py", 22, 167), ("src/timed.py", 44, 55)]
