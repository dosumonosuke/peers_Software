"""手元の変更を調べて、安全なものだけをコミット（セーブ）する。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import safety
from .gitrunner import GitRunner
from .models import SyncContext


@dataclass
class LocalChanges:
    to_stage: list[str] = field(default_factory=list)   # 追加・変更（安全なものだけ）
    deleted: list[str] = field(default_factory=list)
    lfs_new: list[str] = field(default_factory=list)    # これから LFS に登録する新しいファイル
    lfs_bytes: int = 0
    warnings: list[str] = field(default_factory=list)   # 日本語
    unstage: list[str] = field(default_factory=list)    # すでにステージされていたが、送らないもの


def _parse_status(out: str) -> list[tuple[str, str]]:
    """`git status --porcelain=v1 -z` の出力を (XY, パス) のリストにする。"""
    entries = []
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        if xy[0] in "RC":
            i += 1  # 名前を変える前のパスを読み飛ばす
        entries.append((xy, path))
    return entries


def _size(repo: Path, path: str) -> int:
    try:
        return os.lstat(repo / path).st_size
    except OSError:
        return 0


def _is_lfs(git: GitRunner, path: str) -> bool:
    out = git.run("check-attr", "filter", "--", path, check=False).stdout
    return out.strip().endswith(": lfs")


def scan_changes(git: GitRunner, repo: Path, lfs_available: bool) -> LocalChanges:
    ch = LocalChanges()
    out = git.run("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    for xy, path in _parse_status(out):
        staged = xy[0] not in " ?"
        if "D" in xy:
            ch.deleted.append(path)
            continue
        is_new = xy == "??" or xy[0] == "A"
        size = _size(repo, path)
        skip_reason = None
        if safety.is_secret_path(path):
            skip_reason = f"「{path}」はパスワードや鍵のファイルのようなので、送りませんでした。"
        elif safety.is_too_large(size):
            skip_reason = f"「{path}」は 2GB を超えているので、GitHub に送れません。"
        elif is_new and safety.needs_lfs(path, size):
            if lfs_available:
                ch.lfs_new.append(path)
                ch.lfs_bytes += size
                continue
            skip_reason = (f"「{path}」は大きいファイルなので Git LFS が必要です。"
                           "Git LFS をインストールしてください。")
        elif not is_new and size > safety.NON_LFS_HARD_LIMIT and not _is_lfs(git, path):
            skip_reason = (f"「{path}」は 100MB を超えていて、普通の Git では送れません。"
                           "このファイルは送りませんでした。")
        if skip_reason:
            ch.warnings.append(skip_reason)
            if staged:
                ch.unstage.append(path)
            continue
        ch.to_stage.append(path)
    return ch


def _literal(paths: list[str]) -> str:
    return "\0".join(":(literal)" + p for p in paths)


def _identity(git: GitRunner, ctx: SyncContext) -> dict[str, str]:
    email = git.run("config", "user.email", check=False).stdout.strip()
    if email:
        return {}
    login = ctx.github_login
    return {
        "user.name": login or ctx.machine_name,
        "user.email": f"{login}@users.noreply.github.com" if login else "peers-sync@localhost",
    }


def commit_local_changes(git: GitRunner, repo: Path, ctx: SyncContext,
                         lfs_available: bool) -> tuple[int, list[str]]:
    ch = scan_changes(git, repo, lfs_available)
    warnings = list(ch.warnings)
    name = Path(repo).name

    deleted = ch.deleted
    if len(deleted) >= safety.DELETE_CONFIRM_THRESHOLD:
        if not ctx.confirm(f"{name} で {len(deleted)} 個のファイルが消えています。消したままにしますか？"):
            warnings.append(f"消えた {len(deleted)} 個のファイルは、今回は GitHub に反映しませんでした。")
            deleted = []

    lfs_new = ch.lfs_new
    if lfs_new and ch.lfs_bytes >= safety.LFS_BATCH_WARN_BYTES:
        gb = ch.lfs_bytes / 1024**3
        if not ctx.confirm(f"{name} で大きいファイルを約 {gb:.1f}GB 送ります。"
                           "GitHub の無料枠（10GB）を使い切るかもしれません。送りますか？"):
            warnings.append("大きいファイルは、今回は送りませんでした。")
            lfs_new = []

    for path in ch.unstage:
        git.run("rm", "--cached", "-q", "--", ":(literal)" + path, check=False)

    to_stage = ch.to_stage + deleted
    for path in lfs_new:
        git.run("lfs", "track", "--filename", "--", path)
    if lfs_new:
        to_stage = to_stage + lfs_new + [".gitattributes"]

    if to_stage:
        git.run("add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul",
                input=_literal(to_stage))

    staged = [p for p in git.run("diff", "--cached", "--name-only", "-z").stdout.split("\0") if p]
    if not staged:
        return 0, warnings

    when = ctx.now().strftime("%Y-%m-%d %H:%M")
    msg = f"[peers Sync] {ctx.machine_name} から同期 ({when}) — {len(staged)} 個のファイル"
    committer = GitRunner(repo, log=git.log, extra_config={**git.extra_config, **_identity(git, ctx)})
    committer.run("commit", "-q", "-m", msg)
    return len(staged), warnings
