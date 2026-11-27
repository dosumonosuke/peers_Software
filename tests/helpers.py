"""テスト用の小さな道具。本物の GitHub の代わりに、手元に bare リポジトリを作る。"""
import subprocess
from pathlib import Path


def git(path: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    return out.stdout.strip()


def write(path: Path, content: "str | bytes") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def make_remote(tmp: Path, name: str = "origin", initial: bool = True) -> Path:
    remote = tmp / f"{name}.git"
    remote.mkdir(parents=True)
    git(remote, "init", "--bare", "-b", "main")
    if initial:
        seed = tmp / f"{name}-seed"
        seed.mkdir()
        git(seed, "init", "-b", "main")
        write(seed / "README.md", "hello")
        git(seed, "add", ".")
        git(seed, "commit", "-m", "init")
        git(seed, "remote", "add", "origin", remote.as_uri())
        git(seed, "push", "origin", "main")
    return remote


def clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", remote.as_uri(), str(dest)], check=True, capture_output=True)
    return dest
