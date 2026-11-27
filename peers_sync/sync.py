"""リポジトリ 1 つ分の同期：クローン → セーブ → フェッチ → マージ → プッシュ。"""
from __future__ import annotations

from pathlib import Path

from .changes import commit_local_changes, identity_config
from .conflicts import auto_resolve
from .gitrunner import GitError, GitRunner
from .messages import explain_error
from .models import RepoResult, Status, SyncContext


def _explain(stderr: str) -> str:
    return explain_error(stderr)


def current_branch(git: GitRunner) -> str | None:
    out = git.run("symbolic-ref", "--short", "-q", "HEAD", check=False)
    name = out.stdout.strip()
    return name if out.returncode == 0 and name else None


def has_commits(git: GitRunner) -> bool:
    return git.run("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def ahead_behind(git: GitRunner, branch: str) -> tuple[int, int] | None:
    ref = f"refs/remotes/origin/{branch}"
    if git.run("rev-parse", "--verify", "-q", ref, check=False).returncode != 0:
        return None
    if not has_commits(git):
        behind = int(git.run("rev-list", "--count", ref).stdout.strip())
        return 0, behind
    out = git.run("rev-list", "--left-right", "--count", f"HEAD...{ref}").stdout.split()
    return int(out[0]), int(out[1])


def merge_remote(git: GitRunner, repo: Path, branch: str, ctx: SyncContext) -> bool:
    """GitHub の変更を合体させる。テキストの競合が残ったら合体を取りやめて False を返す。"""
    merger = GitRunner(repo, log=git.log, extra_config={**git.extra_config, **identity_config(git, ctx)})
    proc = merger.run("merge", "--no-edit", f"origin/{branch}", check=False)
    if proc.returncode == 0:
        return True
    if not (repo / ".git" / "MERGE_HEAD").exists():
        raise GitError(["merge", f"origin/{branch}"], proc.returncode, proc.stderr or proc.stdout)
    remaining = auto_resolve(git, repo, ctx.machine_name)
    if remaining:
        git.run("merge", "--abort")
        return False
    merger.run("commit", "--no-edit", "-q")
    return True


def sync_repo(path: Path, clone_url: str, full_name: str, ctx: SyncContext,
              lfs_available: bool) -> RepoResult:
    path = Path(path)
    result = RepoResult(full_name, path, Status.UP_TO_DATE)
    try:
        if not path.exists() or (path.is_dir() and not any(path.iterdir())):
            path.parent.mkdir(parents=True, exist_ok=True)
            GitRunner(path.parent, log=ctx.log).run("clone", clone_url, str(path))
            result.status = Status.CLONED
            return result
        if not (path / ".git").exists():
            result.status = Status.ERROR
            result.error = (f"「{path}」に、同じ名前の別のフォルダがあります。中身は変えていません。"
                            "設定で、このリポジトリの置き場所を変えてください。")
            return result

        git = GitRunner(path, log=ctx.log)
        if (path / ".git" / "MERGE_HEAD").exists():
            result.status = Status.CONFLICT
            return result
        branch = current_branch(git)
        if branch is None:
            result.status = Status.ERROR
            result.error = "どのブランチも開かれていません。GitHub Desktop などでブランチを開いてください。"
            return result

        result.committed_files, result.warnings = commit_local_changes(git, path, ctx, lfs_available)

        git.run("fetch", "origin")
        pulled = 0
        counts = ahead_behind(git, branch)
        if counts and counts[1] > 0:
            if not merge_remote(git, path, branch, ctx):
                result.status = Status.CONFLICT
                return result
            pulled = counts[1]

        counts = ahead_behind(git, branch)
        pushed = False
        if (counts and counts[0] > 0) or (counts is None and has_commits(git)):
            git.run("push", "-u", "origin", branch)
            pushed = True

        result.pulled_commits = pulled
        if pushed and pulled:
            result.status = Status.PUSHED_AND_PULLED
        elif pushed:
            result.status = Status.PUSHED
        elif pulled:
            result.status = Status.PULLED
        return result
    except GitError as e:
        result.status = Status.ERROR
        result.error = _explain(e.stderr)
        return result
