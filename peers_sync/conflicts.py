"""競合（2 台のパソコンで同じファイルを別々に変えた状態）の扱い。

いちばん大事なルール：どちらの版も消さない。残さないほうは、名前を変えたコピーとして残す。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .gitrunner import GitRunner

Side = Literal["mine", "theirs"]


@dataclass
class ConflictFile:
    path: str
    kind: Literal["text", "binary", "deleted"]
    mine: str | None = None      # テキストのときだけ中身
    theirs: str | None = None


def conflicted_paths(git: GitRunner) -> list[str]:
    out = git.run("diff", "--name-only", "--diff-filter=U", "-z").stdout
    return [p for p in out.split("\0") if p]


def _stages(git: GitRunner, path: str) -> set[int]:
    out = git.run("ls-files", "-u", "-z", "--", ":(literal)" + path).stdout
    stages = set()
    for entry in out.split("\0"):
        if "\t" in entry:
            meta = entry.split("\t", 1)[0].split()
            stages.add(int(meta[2]))
    return stages


def is_lfs(git: GitRunner, path: str) -> bool:
    return git.run("check-attr", "filter", "--", path, check=False).stdout.strip().endswith(": lfs")


def _decode_text(data: bytes) -> str | None:
    if b"\0" in data[:8000]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def classify(git: GitRunner, path: str) -> ConflictFile:
    stages = _stages(git, path)
    if 2 not in stages or 3 not in stages:
        return ConflictFile(path, "deleted")
    if is_lfs(git, path):
        return ConflictFile(path, "binary")
    mine = _decode_text(git.run_bytes("cat-file", "-p", f":2:{path}"))
    theirs = _decode_text(git.run_bytes("cat-file", "-p", f":3:{path}"))
    if mine is None or theirs is None:
        return ConflictFile(path, "binary")
    return ConflictFile(path, "text", mine, theirs)


def copy_name(path: str, label: str, exists: Callable[[str], bool]) -> str:
    directory, _, name = path.rpartition("/")
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    prefix = f"{directory}/" if directory else ""
    n = 1
    while True:
        suffix = f" ({label} の版)" if n == 1 else f" ({label} の版 {n})"
        candidate = f"{prefix}{stem}{suffix}{ext}"
        if not exists(candidate):
            return candidate
        n += 1


def keep_both(git: GitRunner, repo: Path, path: str, machine: str, keep: Side) -> str:
    """`keep` の版を元の名前で残し、もう片方を名前を変えたコピーとして残す。コピーのパスを返す。"""
    repo = Path(repo)
    other = "theirs" if keep == "mine" else "mine"
    flag = {"mine": "--ours", "theirs": "--theirs"}
    label = machine if other == "mine" else "GitHub"
    lfs = is_lfs(git, path)

    git.run("checkout", flag[other], "--", path)
    data = (repo / path).read_bytes()
    copy = copy_name(path, label, lambda p: (repo / p).exists())
    (repo / copy).write_bytes(data)
    git.run("checkout", flag[keep], "--", path)

    to_add = [path, copy]
    if lfs:
        git.run("lfs", "track", "--filename", "--", copy)
        to_add.append(".gitattributes")
    git.run("add", "--", *to_add)
    return copy


def _resolve_deleted(git: GitRunner, path: str) -> None:
    """片方で消され、片方で変えられたファイル。変えたほう（中身があるほう）を残す。"""
    stages = _stages(git, path)
    if 2 in stages:
        git.run("checkout", "--ours", "--", path)
    elif 3 in stages:
        git.run("checkout", "--theirs", "--", path)
    git.run("add", "--", path)


def auto_resolve(git: GitRunner, repo: Path, machine: str) -> list[ConflictFile]:
    """画像・動画などと、消された/変えられたファイルの競合を自動で解決する。残ったテキストの競合を返す。"""
    remaining = []
    for path in conflicted_paths(git):
        cf = classify(git, path)
        if cf.kind == "deleted":
            _resolve_deleted(git, path)
        elif cf.kind == "binary":
            keep_both(git, repo, path, machine, keep="theirs")
        else:
            remaining.append(cf)
    return remaining
