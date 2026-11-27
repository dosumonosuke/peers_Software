"""あとまわしにしていた小さな問題 7 つを再現するテスト。"""
from pathlib import Path

import pytest

from conftest import make_ctx
from helpers import clone, git, make_remote, write
from peers_sync.changes import commit_local_changes
from peers_sync.gitrunner import GitRunner
from peers_sync.messages import explain_error
from peers_sync.models import Status
from peers_sync.sync import sync_repo


def s(path, remote, machine="PC-A"):
    return sync_repo(path, remote.as_uri(), "me/x", make_ctx(machine), True)


@pytest.fixture
def env(tmp_path):
    remote = make_remote(tmp_path)
    return remote, clone(remote, tmp_path / "a"), clone(remote, tmp_path / "b")


def _binary_conflict(remote, a, b, machine_b="PC-B"):
    write(a / "img.png", b"\x89PNG\x00A")
    s(a, remote)
    s(b, remote, machine_b)
    write(a / "img.png", b"\x89PNG\x00AA")
    s(a, remote)
    write(b / "img.png", b"\x89PNG\x00BB")
    return s(b, remote, machine_b)


# 1. 手元のファイルの書きこみエラーを「ログイン切れ」とまちがえる
def test_local_permission_error_is_not_login_error():
    msg = explain_error("error: unable to unlink old 'a.psd': Permission denied")
    assert "ログイン" not in msg and "ほかのアプリ" in msg


def test_github_403_is_login_error():
    assert "ログイン" in explain_error("fatal: unable to access 'https://github.com/x/': The requested URL returned error: 403")
    assert "ログイン" in explain_error("git@github.com: Permission denied (publickey).")


# 2. パソコン名に記号があると、コピーのファイル名が作れずに合体が中途半端になる
def test_machine_name_with_symbols_is_sanitized(env):
    remote, a, b = env
    r = _binary_conflict(remote, a, b, machine_b='PC/1:2*?')
    assert r.status is Status.PUSHED_AND_PULLED
    assert (b / "img (PC_1_2__ の版).png").read_bytes() == b"\x89PNG\x00BB"


def test_oserror_during_conflict_resolution_aborts_merge(env, monkeypatch):
    remote, a, b = env
    import peers_sync.conflicts as conflicts

    def boom(*a, **k):
        raise OSError("disk full")

    write(a / "img.png", b"\x89PNG\x00A")
    s(a, remote)
    s(b, remote, "PC-B")
    write(a / "img.png", b"\x89PNG\x00AA")
    s(a, remote)
    write(b / "img.png", b"\x89PNG\x00BB")
    monkeypatch.setattr(conflicts, "keep_both", boom)
    r = s(b, remote, "PC-B")
    assert r.status is Status.ERROR
    assert not (b / ".git/MERGE_HEAD").exists()
    assert (b / "img.png").read_bytes() == b"\x89PNG\x00BB"


# 3. 合体の途中で手で直したファイルが、上書きで消える
def _merge_in_progress(remote, a, b):
    write(a / "README.md", "A\n")
    s(a, remote)
    write(b / "README.md", "B\n")
    git(b, "commit", "-qam", "B")
    git(b, "fetch", "-q", "origin")
    GitRunner(b).run("merge", "origin/main", check=False)
    assert (b / ".git/MERGE_HEAD").exists()


def test_manual_edit_survives_resolution(env):
    from peers_sync.conflicts import finish_resolution, start_resolution
    remote, a, b = env
    _merge_in_progress(remote, a, b)
    write(b / "README.md", "手で合わせた A と B\n")
    ctx = make_ctx("PC-B")
    start_resolution(b, ctx)
    finish_resolution(b, {"README.md": "theirs"}, ctx)
    copies = [p for p in b.iterdir() if "手で直した版" in p.name]
    assert copies and copies[0].read_text(encoding="utf-8") == "手で合わせた A と B\n"


def test_manual_edit_survives_cancel(env):
    from peers_sync.conflicts import cancel_resolution
    remote, a, b = env
    _merge_in_progress(remote, a, b)
    write(b / "README.md", "手で合わせた\n")
    cancel_resolution(b)
    copies = [p for p in b.iterdir() if "手で直した版" in p.name]
    assert copies and copies[0].read_text(encoding="utf-8") == "手で合わせた\n"


def test_untouched_conflict_makes_no_manual_copy(env):
    from peers_sync.conflicts import cancel_resolution
    remote, a, b = env
    _merge_in_progress(remote, a, b)
    cancel_resolution(b)
    assert not [p for p in b.iterdir() if "手で直した版" in p.name]


# 4. .git がファイルのとき（worktree など）に、合体中かどうかを見まちがえる
def test_merge_in_progress_detected_in_worktree(env, tmp_path):
    remote, a, b = env
    write(a / "README.md", "A")
    s(a, remote)
    wt = tmp_path / "wt"
    git(b, "worktree", "add", "-q", "-b", "side", str(wt), "main")
    git(wt, "branch", "-q", "--set-upstream-to=origin/main")
    write(wt / "README.md", "B")
    git(wt, "commit", "-qam", "B")
    git(wt, "fetch", "-q", "origin")
    GitRunner(wt).run("merge", "origin/main", check=False)
    write(wt / "README.md", "resolved by hand")
    git(wt, "add", "README.md")  # 競合は解決済み、でもまだ合体のコミット前
    r = s(wt, remote)
    assert r.status is Status.CONFLICT


# 5. ユーザーの Git 設定（merge.ff=only・commit.gpgsign=true）で同期できなくなる
def test_user_configs_do_not_break_sync(env):
    remote, a, b = env
    git(a, "config", "--global", "merge.ff", "only")
    git(a, "config", "--global", "commit.gpgsign", "true")
    git(a, "config", "--global", "gpg.program", "/nonexistent-gpg")
    write(a / "x.md", "A")
    assert s(a, remote).status is Status.PUSHED
    write(b / "y.md", "B")
    assert s(b, remote, "PC-B").status is Status.PUSHED_AND_PULLED


# 6. Git LFS がないときのメッセージに、インストールの場所がない（UI テストは test_ui_smoke 側）
def test_lfs_missing_message_has_install_link():
    from peers_sync.messages import LFS_MISSING
    assert "https://git-lfs.com" in LFS_MISSING


# 7. Keynote のファイル（.key）が、鍵ファイルとまちがわれて送られない
def test_keynote_file_is_synced_but_private_key_is_not(env):
    remote, a, _ = env
    write(a / "発表.key", b"PK\x03\x04keynote-zip-data")
    write(a / "server.key", "-----BEGIN PRIVATE KEY-----\nabc\n")
    n, warns = commit_local_changes(GitRunner(a), a, make_ctx("PC-A"), True)
    files = git(a, "-c", "core.quotepath=false", "ls-files")
    assert "発表.key" in files and "server.key" not in files
