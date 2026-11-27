"""リポジトリ 1 つ分の同期：クローン → セーブ → フェッチ → マージ → プッシュ。"""
from __future__ import annotations

from pathlib import Path

from .changes import commit_local_changes, identity_config
from .conflicts import auto_resolve, merge_in_progress
from .gitrunner import GitError, GitRunner
from .messages import explain_error
from .models import RepoResult, Status, SyncContext


def _explain(stderr: str) -> str:
    return explain_error(stderr)


def _normalize_remote(url: str) -> str:
    """GitHub の URL を「ホスト/持ち主/名前」の形にそろえる（https と ssh のちがい、.git、大文字小文字を無視）。"""
    u = url.strip()
    if "://" in u:
        rest = u.split("://", 1)[1]
        host, _, path = rest.partition("/")
        host = host.rsplit("@", 1)[-1]
        if u.lower().startswith("file://"):
            host = ""
    elif "@" in u and ":" in u:  # git@github.com:owner/name.git
        host, _, path = u.rsplit("@", 1)[-1].partition(":")
    else:
        host, path = "", u
    path = path.strip("/")
    if path.lower().endswith(".git"):
        path = path[:-4]
    return f"{host}/{path}".lower()


def same_remote(a: str, b: str) -> bool:
    return _normalize_remote(a) == _normalize_remote(b)


def _in_progress_operation(git: GitRunner) -> bool:
    """マージ以外の途中の作業（stash pop・cherry-pick などの競合）が残っているか。"""
    if git.run("ls-files", "-u", check=False).stdout.strip():
        return True
    for name in ("CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        p = git.run("rev-parse", "--git-path", name, check=False).stdout.strip()
        if p and (git.cwd / p).exists():
            return True
    return False


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


def unrelated_flag(git: GitRunner, branch: str) -> list[str]:
    """空のリポジトリを 2 台で別々に始めたときは、歴史に共通の部分がない。それでも合体できるようにする。"""
    if not has_commits(git):
        return []
    rc = git.run("merge-base", "HEAD", f"origin/{branch}", check=False).returncode
    return ["--allow-unrelated-histories"] if rc != 0 else []


def merge_remote(git: GitRunner, repo: Path, branch: str, ctx: SyncContext,
                 notes: list[str] | None = None) -> bool:
    """GitHub の変更を合体させる。テキストの競合が残ったら合体を取りやめて False を返す。"""
    merger = GitRunner(repo, log=git.log, extra_config={**git.extra_config, **identity_config(git, ctx)})
    proc = merger.run("merge", "--no-edit", *unrelated_flag(git, branch), f"origin/{branch}", check=False)
    if proc.returncode == 0:
        return True
    if not merge_in_progress(git):
        raise GitError(["merge", f"origin/{branch}"], proc.returncode, proc.stderr or proc.stdout)
    try:
        remaining = auto_resolve(git, repo, ctx.machine_name, notes)
    except Exception:
        git.run("merge", "--abort", check=False)  # 中途半端な合体を残さない
        raise
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
        origin = git.run("remote", "get-url", "origin", check=False).stdout.strip()
        if not origin or not same_remote(origin, clone_url):
            result.status = Status.ERROR
            where = f"（{origin}）" if origin else ""
            result.error = (f"「{path}」は別のリポジトリ{where}につながっています。中身は変えていません。"
                            "設定で、このリポジトリの置き場所を変えてください。")
            return result
        if merge_in_progress(git):
            result.status = Status.CONFLICT
            return result
        if _in_progress_operation(git):
            result.status = Status.ERROR
            result.error = ("途中の作業（競合の解決など）が残っています。GitHub Desktop などで解決してから、"
                            "もう一度同期してください。")
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
            if not merge_remote(git, path, branch, ctx, result.warnings):
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
    except OSError as e:
        result.status = Status.ERROR
        result.error = f"ファイルの読み書きができませんでした（{e.strerror or e}）。空き容量や、ほかのアプリで開いていないかを確かめてください。"
        return result
