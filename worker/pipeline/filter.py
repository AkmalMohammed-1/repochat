"""Decide which files in a checked-out repo are worth indexing."""

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pathspec

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "out", "target", "vendor", "vendors", "third_party",
    "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache", ".next", ".nuxt",
    "coverage", "bower_components", "Pods", ".gradle", ".idea", ".vscode", "site-packages",
}
LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "uv.lock",
    "Cargo.lock", "Gemfile.lock", "composer.lock", "go.sum", "mix.lock", "pubspec.lock", "packages.lock.json",
}
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".tiff", ".psd", ".pdf",
    ".zip", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war", ".class",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib", ".bin", ".pyc", ".pyo", ".wasm",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg",
    ".webm", ".sqlite", ".db", ".pkl", ".npy", ".npz", ".parquet", ".onnx", ".pt", ".h5", ".ckpt",
}
SKIP_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".snap")


@dataclass
class FilterResult:
    files: list[str] = field(default_factory=list)  # posix paths relative to repo root
    total_bytes: int = 0
    skipped: int = 0


def _skip_by_name(rel: PurePosixPath) -> bool:
    if any(part in SKIP_DIRS for part in rel.parts[:-1]):
        return True
    name = rel.name
    if name in LOCKFILES or name.endswith(SKIP_SUFFIXES):
        return True
    return rel.suffix.lower() in BINARY_EXTS


def is_binary(data: bytes) -> bool:
    head = data[:8192]
    if b"\x00" in head:
        return True
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # A multi-byte char cut at the 8192 boundary is fine; anything earlier is not UTF-8 text.
        return e.start < len(head) - 4
    return False


class GitignoreMatcher:
    """Applies every .gitignore in the tree, each relative to its own directory."""

    def __init__(self, root: Path):
        self.specs: list[tuple[PurePosixPath, pathspec.PathSpec]] = []
        for gi in root.rglob(".gitignore"):
            rel_dir = PurePosixPath(gi.parent.relative_to(root).as_posix())
            if any(part in SKIP_DIRS for part in rel_dir.parts):
                continue
            lines = gi.read_text(encoding="utf-8", errors="ignore").splitlines()
            self.specs.append((rel_dir, pathspec.GitIgnoreSpec.from_lines(lines)))

    def ignored(self, rel: PurePosixPath) -> bool:
        for base, spec in self.specs:
            if base == PurePosixPath(".") or rel.is_relative_to(base):
                sub = rel if base == PurePosixPath(".") else rel.relative_to(base)
                if spec.match_file(str(sub)):
                    return True
        return False


def _walk(root: Path) -> list[Path]:
    """All files under root, sorted, without descending into SKIP_DIRS (e.g. huge node_modules)."""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        out.extend(Path(dirpath) / f for f in filenames)
    return sorted(out)


def filter_files(root: Path, max_file_bytes: int) -> FilterResult:
    result = FilterResult()
    gitignore = GitignoreMatcher(root)
    for path in _walk(root):
        if not path.is_file() or path.is_symlink():
            continue
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if _skip_by_name(rel) or gitignore.ignored(rel):
            result.skipped += 1
            continue
        size = path.stat().st_size
        if size == 0 or size > max_file_bytes:
            result.skipped += 1
            continue
        with path.open("rb") as f:
            if is_binary(f.read(8192)):
                result.skipped += 1
                continue
        result.files.append(str(rel))
        result.total_bytes += size
    return result
