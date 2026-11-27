import pytest

from helpers import clone, git, make_remote, write
from peers_sync.changes import commit_local_changes
from peers_sync.gitrunner import GitRunner


@pytest.fixture
def work(tmp_path):
    return clone(make_remote(tmp_path), tmp_path / "work")


def run(work, ctx, lfs=True):
    return commit_local_changes(GitRunner(work), work, ctx, lfs)


def test_commits_new_and_modified_with_message(work, ctx):
    write(work / "a.md", "x")
    write(work / "README.md", "changed")
    n, _ = run(work, ctx)
    assert n == 2
    assert git(work, "log", "-1", "--format=%s") == "[peers Sync] PC-A から同期 (2026-11-27 14:32) — 2 個のファイル"


def test_nothing_to_commit_returns_zero(work, ctx):
    assert run(work, ctx) == (0, [])


def test_secret_file_is_not_committed_and_warned(work, ctx):
    write(work / ".env", "TOKEN=1")
    write(work / "ok.md", "x")
    n, warns = run(work, ctx)
    assert n == 1 and ".env" not in git(work, "ls-files") and any(".env" in w for w in warns)


def test_staged_secret_is_unstaged(work, ctx):
    write(work / ".env", "TOKEN=1")
    git(work, "add", ".env")
    n, _ = run(work, ctx)
    assert n == 0 and ".env" not in git(work, "ls-files")


def test_video_goes_to_lfs_by_filename_rule(work, ctx):
    write(work / "動画/clip 1.mp4", b"\x00" * 100)
    n, _ = run(work, ctx)
    assert n == 2  # 動画と .gitattributes
    assert "clip" in (work / ".gitattributes").read_text(encoding="utf-8")
    assert "filter: lfs" in git(work, "check-attr", "filter", "--", "動画/clip 1.mp4")
    assert "version https://git-lfs" in git(work, "cat-file", "-p", "HEAD:動画/clip 1.mp4")


def test_existing_tracked_video_is_not_moved_to_lfs(work, ctx):
    write(work / "old.mp4", b"\x00" * 10)
    git(work, "add", "old.mp4")
    git(work, "commit", "-m", "old")
    write(work / "old.mp4", b"\x01" * 10)
    run(work, ctx)
    assert "lfs" not in git(work, "check-attr", "filter", "--", "old.mp4")


def test_lfs_needed_but_unavailable_skips_with_warning(work, ctx):
    write(work / "v.mp4", b"\x00")
    n, warns = run(work, ctx, lfs=False)
    assert n == 0 and any("Git LFS" in w for w in warns)


def _make_files(work, count):
    for i in range(count):
        write(work / f"f{i}.txt", "x")
    git(work, "add", ".")
    git(work, "commit", "-m", "files")
    for i in range(count):
        (work / f"f{i}.txt").unlink()


def test_many_deletions_ask_and_respect_no(work, ctx):
    _make_files(work, 20)
    asked = []
    ctx.confirm = lambda q: asked.append(q) or False
    n, _ = run(work, ctx)
    assert asked and "20" in asked[0] and n == 0
    assert "f0.txt" in git(work, "ls-files")


def test_many_deletions_yes_commits_them(work, ctx):
    _make_files(work, 20)
    n, _ = run(work, ctx)
    assert n == 20 and "f0.txt" not in git(work, "ls-files")


def test_19_deletions_do_not_ask(work, ctx):
    _make_files(work, 19)
    ctx.confirm = lambda q: pytest.fail("should not ask")
    n, _ = run(work, ctx)
    assert n == 19


def test_large_lfs_batch_asks_before_commit(work, ctx, monkeypatch):
    monkeypatch.setattr("peers_sync.safety.LFS_BATCH_WARN_BYTES", 10)
    write(work / "v.mp4", b"\x00" * 11)
    asked = []
    ctx.confirm = lambda q: asked.append(q) or False
    n, warns = run(work, ctx)
    assert n == 0 and asked and warns


def test_too_large_file_skipped(work, ctx, monkeypatch):
    monkeypatch.setattr("peers_sync.safety.MAX_FILE_SIZE", 5)
    write(work / "big.mp4", b"\x00" * 6)
    n, warns = run(work, ctx)
    assert n == 0 and any("2GB" in w for w in warns)


def test_tracked_non_lfs_file_over_100mb_skipped(work, ctx, monkeypatch):
    monkeypatch.setattr("peers_sync.safety.NON_LFS_HARD_LIMIT", 5)
    write(work / "README.md", "much longer than five bytes")
    n, warns = run(work, ctx)
    assert n == 0 and any("100MB" in w for w in warns)


def test_japanese_and_space_filenames(work, ctx):
    write(work / "メモ/日本語 ノート.md", "こんにちは")
    n, _ = run(work, ctx)
    assert n == 1 and "日本語 ノート.md" in git(work, "-c", "core.quotepath=false", "ls-files")


def test_glob_like_filename_is_literal(work, ctx):
    write(work / "a[1].md", "x")
    write(work / "a1.md", "y")
    n, _ = run(work, ctx)
    assert n == 2


def test_commit_without_git_identity(work, ctx):
    git(work, "config", "--global", "--unset", "user.name")
    git(work, "config", "--global", "--unset", "user.email")
    write(work / "a.md", "x")
    n, _ = run(work, ctx)
    assert n == 1 and git(work, "log", "-1", "--format=%ae") == "me@users.noreply.github.com"
