import io
import tarfile

from worker.pipeline.fetch import extract_tarball
from worker.pipeline.filter import filter_files, is_binary


def _write(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())


def test_filter_skips_junk(tmp_path):
    _write(tmp_path, "src/app.py", "print('hi')\n")
    _write(tmp_path, "README.md", "# hi\n")
    _write(tmp_path, "node_modules/left-pad/index.js", "module.exports = 1\n")
    _write(tmp_path, "dist/bundle.js", "x\n")
    _write(tmp_path, "package-lock.json", "{}\n")
    _write(tmp_path, "static/app.min.js", "x\n")
    _write(tmp_path, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00")
    _write(tmp_path, "data.bin.txt", b"abc\x00def")
    _write(tmp_path, "big.txt", "x" * 2000)
    _write(tmp_path, "empty.py", "")
    _write(tmp_path, ".gitignore", "secrets/\n*.log\n")
    _write(tmp_path, "secrets/key.txt", "k\n")
    _write(tmp_path, "debug.log", "x\n")
    _write(tmp_path, "pkg/.gitignore", "generated.py\n")
    _write(tmp_path, "pkg/generated.py", "x = 1\n")
    _write(tmp_path, "pkg/real.py", "x = 2\n")

    result = filter_files(tmp_path, max_file_bytes=1000)
    assert sorted(result.files) == [".gitignore", "README.md", "pkg/.gitignore", "pkg/real.py", "src/app.py"]
    assert result.total_bytes > 0


def test_is_binary():
    assert is_binary(b"abc\x00")
    assert not is_binary("héllo wörld".encode())
    assert is_binary(b"\xff\xfe\xfa" + b"a" * 100)


def _tar(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_extract_strips_top_dir_and_blocks_traversal(tmp_path):
    data = _tar({
        "owner-repo-abc123/src/a.py": b"a = 1\n",
        "owner-repo-abc123/../../evil.py": b"x\n",
        "owner-repo-abc123/huge.txt": b"x" * 5000,
    })
    dest = tmp_path / "out"
    dest.mkdir()
    extract_tarball(data, dest, max_file_bytes=1000)
    assert (dest / "src" / "a.py").read_text() == "a = 1\n"
    assert not (dest / "huge.txt").exists()
    assert not (tmp_path / "evil.py").exists()
