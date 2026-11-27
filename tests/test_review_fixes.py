"""最終レビューで見つかった問題を再現するテスト。"""
import subprocess

import pytest

from conftest import make_ctx
from helpers import clone, git, make_remote, write
from peers_sync.changes import commit_local_changes
from peers_sync.gitrunner import GitRunner
from peers_sync.models import Status
from peers_sync.sync import sync_repo


def s(path, remote, machine="PC-A"):
    return sync_repo(path, remote.as_uri(), "me/x", make_ctx(machine), True)


@pytest.fixture
def env(tmp_path):
    remote = make_remote(tmp_path)
    return remote, clone(remote, tmp_path / "a"), clone(remote, tmp_path / "b")


# Critical 1: 2 台がそれぞれ別の動画を足すと .gitattributes が競合して、LFS のルールが消える
def test_two_pcs_add_different_videos_both_stay_in_lfs(env):
    remote, a, b = env
    write(a / "v1.mp4", b"\x00one")
    s(a, remote)
    write(b / "v2.mp4", b"\x00two")
    r = s(b, remote, "PC-B")
    assert r.status is Status.PUSHED_AND_PULLED
    for name in ["v1.mp4", "v2.mp4"]:
        assert "version https://git-lfs" in git(remote, "cat-file", "-p", f"main:{name}")
    assert s(a, remote).status is Status.PULLED
    assert git(a, "status", "--porcelain") == ""


# Critical 2: 置き場所に「別のリポジトリ」があると、そこに勝手にコミットして送ってしまう
def test_folder_is_clone_of_other_repo_is_error_and_untouched(tmp_path):
    target = make_remote(tmp_path, "target")
    other = make_remote(tmp_path, "other")
    p = clone(other, tmp_path / "x")
    write(p / "private_notes.md", "secret")
    head = git(p, "rev-parse", "HEAD")
    r = sync_repo(p, target.as_uri(), "me/target", make_ctx("PC-A"), True)
    assert r.status is Status.ERROR and "別のリポジトリ" in r.error
    assert git(p, "rev-parse", "HEAD") == head
    assert "private_notes.md" not in git(other, "ls-tree", "-r", "--name-only", "main")


def test_folder_without_origin_is_error_and_untouched(tmp_path):
    target = make_remote(tmp_path, "target")
    p = tmp_path / "y"
    p.mkdir()
    git(p, "init", "-q")
    write(p / "a.md", "x")
    r = sync_repo(p, target.as_uri(), "me/target", make_ctx("PC-A"), True)
    assert r.status is Status.ERROR
    assert git(p, "rev-list", "--all", "--count") == "0"


def test_same_remote_normalizes_github_urls():
    from peers_sync.sync import same_remote
    assert same_remote("https://github.com/Me/Repo.git", "git@github.com:me/repo")
    assert same_remote("ssh://git@github.com/me/repo.git", "https://github.com/me/repo/")
    assert same_remote("https://user@github.com/me/repo", "https://github.com/me/repo.git")
    assert not same_remote("https://github.com/me/repo.git", "https://github.com/me/other.git")


# Important 3: 空のリポジトリを 2 台でクローンして、両方で最初のファイルを足すと、ずっとエラーになる
def test_unrelated_histories_from_empty_repo_merge(tmp_path):
    empty = make_remote(tmp_path, "empty", initial=False)
    p, q = tmp_path / "p", tmp_path / "q"
    sync_repo(p, empty.as_uri(), "me/e", make_ctx("PC-A"), True)
    sync_repo(q, empty.as_uri(), "me/e", make_ctx("PC-B"), True)
    write(p / "a.md", "A")
    sync_repo(p, empty.as_uri(), "me/e", make_ctx("PC-A"), True)
    write(q / "b.md", "B")
    r = sync_repo(q, empty.as_uri(), "me/e", make_ctx("PC-B"), True)
    assert r.status is Status.PUSHED_AND_PULLED
    assert (q / "a.md").exists() and (q / "b.md").exists()


# Important 4: すでに管理されている秘密ファイルを外すときに、削除として送ってしまう
def test_staged_tracked_secret_is_not_deleted_on_remote(env):
    remote, a, b = env
    write(a / "config.key", "old")
    git(a, "add", "config.key")
    git(a, "commit", "-qm", "key")
    git(a, "push", "-q", "origin", "main")
    write(a / "config.key", "new")
    git(a, "add", "config.key")
    s(a, remote)
    assert git(remote, "show", "main:config.key") == "old"
    assert (a / "config.key").read_text() == "new"


# Important 5: stash pop などの競合が残っていると、競合の印ごと送ってしまう
def test_unmerged_entries_without_merge_head_are_not_committed(env):
    remote, a, _ = env
    write(a / "README.md", "stashed")
    git(a, "stash")
    write(a / "README.md", "committed")
    git(a, "commit", "-qam", "c")
    subprocess.run(["git", "stash", "pop"], cwd=a, capture_output=True)
    assert git(a, "ls-files", "-u") != ""
    r = s(a, remote)
    assert r.status in (Status.CONFLICT, Status.ERROR)
    assert "<<<<<<<" not in git(remote, "show", "main:README.md")


# Minor→Important: リポジトリの中の別のリポジトリが、中身なしで送られる
def test_nested_repo_is_skipped_with_warning(env):
    remote, a, _ = env
    inner = a / "inner"
    inner.mkdir()
    git(inner, "init", "-q")
    write(inner / "x.md", "x")
    n, warns = commit_local_changes(GitRunner(a), a, make_ctx("PC-A"), True)
    assert n == 0 and any("inner" in w for w in warns)


# Minor→Important: 画像の競合を自動で解決したことを知らせない
def test_binary_auto_resolve_warns_about_copy(env):
    remote, a, b = env
    write(a / "img.png", b"\x89PNG\x00A")
    s(a, remote)
    s(b, remote, "PC-B")
    write(a / "img.png", b"\x89PNG\x00AA")
    s(a, remote)
    write(b / "img.png", b"\x89PNG\x00BB")
    r = s(b, remote, "PC-B")
    assert any("img (PC-B の版).png" in w for w in r.warnings)


# Minor→Important: 競合の解決で、ファイル名がワイルドカードとして扱われる
def test_conflict_on_glob_like_name(env):
    remote, a, b = env
    write(a / "a[1].png", b"\x00base")
    write(a / "a1.png", b"\x00other")
    s(a, remote)
    s(b, remote, "PC-B")
    write(a / "a[1].png", b"\x00A")
    s(a, remote)
    write(b / "a[1].png", b"\x00B")
    r = s(b, remote, "PC-B")
    assert r.status is Status.PUSHED_AND_PULLED
    assert (b / "a[1].png").read_bytes() == b"\x00A"
    assert (b / "a1.png").read_bytes() == b"\x00other"
