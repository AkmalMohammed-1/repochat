import pytest

from api.core.repo_ref import RepoRef, github_line_url, parse_repo_path


@pytest.mark.parametrize("text,expected", [
    ("AkmalMohammed-1/QueueCTL", RepoRef("AkmalMohammed-1", "QueueCTL")),
    ("/owner/repo", RepoRef("owner", "repo")),
    ("https://github.com/owner/repo", RepoRef("owner", "repo")),
    ("https://github.com/owner/repo.git", RepoRef("owner", "repo")),
    ("github.com/owner/repo/", RepoRef("owner", "repo")),
    ("/owner/repo/tree/dev", RepoRef("owner", "repo", branch="dev")),
    ("/owner/repo/tree/feature/x", RepoRef("owner", "repo", branch="feature/x")),
    ("https://github.com/o/r/blob/main/src/app.py", RepoRef("o", "r", branch="main", path="src/app.py")),
    ("https://repochat.vercel.app/o/r", RepoRef("o", "r")),
])
def test_parse_valid(text, expected):
    assert parse_repo_path(text) == expected


@pytest.mark.parametrize("text", ["", "owner", "/owner", "-bad/repo", "owner/..", "o/r/issues/1", "o/r/tree"])
def test_parse_invalid(text):
    assert parse_repo_path(text) is None


def test_github_line_url():
    assert github_line_url("o", "r", "abc", "a/b.py", 3, 9) == "https://github.com/o/r/blob/abc/a/b.py#L3-L9"
    assert github_line_url("o", "r", "abc", "a.py", 4, 4).endswith("#L4")
