"""全リポジトリを順番に同期する係。1 つ失敗しても、ほかは止めない。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .config import Settings
from .gitrunner import git_env, popen_flags
from .github_api import GitHubError
from .models import RemoteRepo, RepoResult, Status, SyncContext
from .gitrunner import GitRunner
from .sync import same_remote, sync_repo

# Mac で Finder から起動したアプリは、Homebrew の場所を知らないので足してあげる
_MAC_EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin"]


class SyncAbort(Exception):
    """同期を始められないときのエラー（日本語のメッセージを持つ）。"""


def ensure_tool_path() -> None:
    """Finder から開いたアプリや、Git を入れた直後のアプリでも道具が見つかるように、PATH に場所を足す。"""
    from .installer import tool_bin_dir
    extras: list[str] = []
    if sys.platform == "win32":
        candidates = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "cmd"]
        if os.environ.get("LOCALAPPDATA"):  # ユーザー用にインストールした Git
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Git" / "cmd")
        extras += [str(c) for c in candidates if c.is_dir()]
    elif sys.platform == "darwin":
        extras += _MAC_EXTRA_PATHS
    else:
        return
    extras.append(str(tool_bin_dir()))
    parts = os.environ.get("PATH", "").split(os.pathsep)
    for extra in extras:
        if extra not in parts:
            parts.append(extra)
    os.environ["PATH"] = os.pathsep.join(p for p in parts if p)


def _run(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(list(args), capture_output=True, text=True, env=git_env(), **popen_flags())
    except OSError:
        return None


def check_prerequisites() -> tuple[str | None, bool]:
    """(Git がないときの日本語メッセージ, Git LFS が使えるか) を返す。"""
    ensure_tool_path()
    git = _run("git", "--version")
    if git is None or git.returncode != 0:
        return ("Git が見つかりません。https://git-scm.com/downloads から Git をインストールして、"
                "このアプリを開き直してください。"), False
    lfs = _run("git", "lfs", "version")
    if lfs is None or lfs.returncode != 0:
        return None, False
    _run("git", "lfs", "install", "--skip-repo")
    return None, True


def local_path_for(repo: RemoteRepo, settings: Settings) -> Path:
    return settings.repo_paths.get(repo.full_name) or settings.base_dir / repo.name


def default_search_roots() -> list[Path]:
    """ダウンロード済みのリポジトリがありそうな場所（GitHub Desktop の場所など）。"""
    home = Path.home()
    return [home / "Documents" / "GitHub", home / "OneDrive" / "Documents" / "GitHub",
            home / "OneDrive" / "ドキュメント" / "GitHub", home / "GitHub", home / "source" / "repos",
            home / "Desktop", home / "Documents", home / "Projects"]


def _origin(path: Path) -> str:
    proc = GitRunner(path).run("remote", "get-url", "origin", check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def discover_existing(repos: list[RemoteRepo], settings: Settings, roots: list[Path]) -> dict[str, Path]:
    """置き場所にまだないリポジトリを、よくある場所の 1 段下から探す。origin が同じものだけ使う。"""
    found: dict[str, Path] = {}
    wanted = [r for r in repos if r.full_name not in settings.repo_paths
              and not local_path_for(r, settings).exists()]
    if not wanted:
        return found
    for root in roots:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        children = []
        for c in entries:
            try:  # 読めないフォルダが 1 つあっても、ほかは探し続ける
                if (c / ".git").exists():
                    children.append(c)
            except OSError:
                continue
        for child in children:
            for repo in wanted:
                if repo.full_name in found or child.name.lower() != repo.name.lower():
                    continue
                if same_remote(_origin(child), repo.clone_url):
                    found[repo.full_name] = child
    return found


def _api_message(e: GitHubError) -> str:
    if e.status in (401, 403):
        return "GitHub のログインが切れています。設定から、もう一度ログインしてください。"
    if e.status == 0:
        return "GitHub につながりません。インターネットの接続を確かめてください。"
    return f"GitHub からリポジトリの一覧を取れませんでした（エラー {e.status}）。少し待ってから、もう一度お試しください。"


def _collect(settings: Settings, api) -> list[RemoteRepo]:
    try:
        repos = list(api.list_owned_repos())
        known = {r.full_name for r in repos}
        for full_name in settings.extra_repos:
            if full_name not in known:
                repos.append(api.get_repo(full_name))
                known.add(full_name)
    except GitHubError as e:
        raise SyncAbort(_api_message(e)) from e
    return repos


def sync_all(settings: Settings, api, ctx: SyncContext,
             on_result: Callable[[RepoResult], None], lfs_available: bool,
             search_roots: list[Path] | None = None) -> list[RepoResult]:
    results: list[RepoResult] = []
    used_paths: set[Path] = set()
    repos = _collect(settings, api)
    for full_name, p in list(settings.repo_paths.items()):
        if not p.exists():  # 前に見つけたフォルダが消されていたら、記録も消して通常の場所を使う
            del settings.repo_paths[full_name]
    roots = default_search_roots() if search_roots is None else search_roots
    settings.repo_paths.update(discover_existing(repos, settings, roots))
    for repo in repos:
        path = local_path_for(repo, settings)
        if path in used_paths:  # 別の持ち主の、同じ名前のリポジトリ
            owner = repo.full_name.split("/")[0]
            path = settings.base_dir / f"{owner}-{repo.name}"
        used_paths.add(path)

        if repo.full_name in settings.excluded:
            result = RepoResult(repo.full_name, path, Status.SKIPPED)
        else:
            try:
                result = sync_repo(path, repo.clone_url, repo.full_name, ctx, lfs_available)
            except Exception as e:  # 想定外のエラーでも、ほかのリポジトリは続ける
                result = RepoResult(repo.full_name, path, Status.ERROR,
                                    error=f"想定外のエラーが起きました（{type(e).__name__}）。くわしいログを見てください。")
        results.append(result)
        on_result(result)
    return results
