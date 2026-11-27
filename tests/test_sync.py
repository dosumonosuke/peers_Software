import pytest

from conftest import make_ctx
from helpers import clone, git, make_remote, write
from peers_sync.gitrunner import GitRunner
from peers_sync.models import Status
from peers_sync.sync import sync_repo


@pytest.fixture
def env(tmp_path):
    remote = make_remote(tmp_path)
    a = clone(remote, tmp_path / "a")
    b = clone(remote, tmp_path / "b")
    return remote, a, b


def s(path, remote, machine="PC-A"):
    return sync_repo(path, remote.as_uri(), "me/x", make_ctx(machine), True)


def test_clone_when_missing(tmp_path, env):
    remote, _, _ = env
    r = s(tmp_path / "new", remote)
    assert r.status is Status.CLONED and (tmp_path / "new/README.md").exists()


def test_clone_into_empty_existing_dir(tmp_path, env):
    remote, _, _ = env
    (tmp_path / "emptydir").mkdir()
    assert s(tmp_path / "emptydir", remote).status is Status.CLONED


def test_clone_target_exists_not_repo(tmp_path, env):
    remote, _, _ = env
    write(tmp_path / "x/mine.txt", "keep")
    r = s(tmp_path / "x", remote)
    assert r.status is Status.ERROR and r.error
    assert (tmp_path / "x/mine.txt").read_text() == "keep"


def test_a_to_b_roundtrip(env):
    remote, a, b = env
    write(a / "n.md", "A")
    ra = s(a, remote)
    assert ra.status is Status.PUSHED and ra.committed_files == 1
    r = s(b, remote, "PC-B")
    assert r.status is Status.PULLED and r.pulled_commits == 1 and (b / "n.md").read_text() == "A"


def test_up_to_date(env):
    remote, a, _ = env
    assert s(a, remote).status is Status.UP_TO_DATE


def test_both_changed_different_files_merges(env):
    remote, a, b = env
    write(a / "x.md", "A")
    s(a, remote)
    write(b / "y.md", "B")
    r = s(b, remote, "PC-B")
    assert r.status is Status.PUSHED_AND_PULLED and (b / "x.md").exists()
    assert git(b, "rev-list", "--count", "--merges", "HEAD") == "1"
    assert git(remote, "show", "main:y.md") == "B"


def test_text_conflict_aborts_and_reports(env):
    remote, a, b = env
    write(a / "README.md", "A")
    s(a, remote)
    write(b / "README.md", "B")
    r = s(b, remote, "PC-B")
    assert r.status is Status.CONFLICT
    assert not (b / ".git/MERGE_HEAD").exists() and (b / "README.md").read_text() == "B"
    assert git(b, "status", "--porcelain") == ""
    assert git(remote, "show", "main:README.md") == "A"


def test_binary_conflict_keeps_both_and_finishes(env):
    remote, a, b = env
    write(a / "img.png", b"\x89PNG\x00A")
    s(a, remote)
    s(b, remote, "PC-B")
    write(a / "img.png", b"\x89PNG\x00AA")
    s(a, remote)
    write(b / "img.png", b"\x89PNG\x00BB")
    r = s(b, remote, "PC-B")
    assert r.status is Status.PUSHED_AND_PULLED
    assert (b / "img.png").read_bytes() == b"\x89PNG\x00AA"
    assert (b / "img (PC-B の版).png").read_bytes() == b"\x89PNG\x00BB"


def test_lfs_video_conflict_copy_also_goes_to_lfs(env):
    remote, a, b = env
    write(a / "v.mp4", b"\x00V1")
    s(a, remote)
    s(b, remote, "PC-B")
    write(a / "v.mp4", b"\x00V2A")
    s(a, remote)
    write(b / "v.mp4", b"\x00V2B")
    r = s(b, remote, "PC-B")
    assert r.status is Status.PUSHED_AND_PULLED
    assert (b / "v.mp4").read_bytes() == b"\x00V2A"
    assert (b / "v (PC-B の版).mp4").read_bytes() == b"\x00V2B"
    assert "filter: lfs" in git(b, "check-attr", "filter", "--", "v (PC-B の版).mp4")


def test_delete_vs_modify_keeps_modified(env):
    remote, a, b = env
    (a / "README.md").unlink()
    s(a, remote)
    write(b / "README.md", "edited")
    r = s(b, remote, "PC-B")
    assert r.status is Status.PUSHED_AND_PULLED and (b / "README.md").read_text() == "edited"


def test_existing_merge_in_progress_reports_conflict(env):
    remote, a, b = env
    write(a / "README.md", "A")
    s(a, remote)
    write(b / "README.md", "B")
    git(b, "commit", "-qam", "B")
    git(b, "fetch", "-q", "origin")
    GitRunner(b).run("merge", "origin/main", check=False)
    head = git(b, "rev-parse", "HEAD")
    r = s(b, remote, "PC-B")
    assert r.status is Status.CONFLICT and git(b, "rev-parse", "HEAD") == head
    assert (b / ".git/MERGE_HEAD").exists()


def test_detached_head_is_error(env):
    remote, a, _ = env
    git(a, "checkout", "-q", "--detach")
    r = s(a, remote)
    assert r.status is Status.ERROR and "ブランチ" in r.error


def test_never_force_pushes_or_resets(env, monkeypatch):
    remote, a, b = env
    calls = []
    orig = GitRunner.run

    def spy(self, *args, **kw):
        calls.append(args)
        return orig(self, *args, **kw)

    monkeypatch.setattr(GitRunner, "run", spy)
    write(a / "README.md", "A")
    s(a, remote)
    write(b / "README.md", "B")
    write(b / "img.png", b"\x00")
    s(b, remote, "PC-B")
    flat = [x for c in calls for x in c]
    assert not any(x.startswith("--force") or x == "-f" or x.startswith("+") for x in flat)
    assert "reset" not in [c[0] for c in calls if c] and "rebase" not in flat


def test_push_rejected_is_error_not_force(tmp_path, env, monkeypatch):
    remote, a, _ = env
    c = clone(remote, tmp_path / "c")
    orig = GitRunner.run
    state = {"done": False}

    def racing(self, *args, **kw):
        if args and args[0] == "push" and not state["done"]:
            state["done"] = True
            write(c / "race.md", "C")
            git(c, "add", ".")
            git(c, "commit", "-qm", "race")
            git(c, "push", "-q", "origin", "main")
        return orig(self, *args, **kw)

    monkeypatch.setattr(GitRunner, "run", racing)
    write(a / "n.md", "A")
    r = s(a, remote)
    assert r.status is Status.ERROR
    assert git(remote, "show", "main:race.md") == "C"


def test_empty_remote_clone_then_first_push(tmp_path):
    empty = make_remote(tmp_path, "empty", initial=False)
    p = tmp_path / "e"
    assert sync_repo(p, empty.as_uri(), "me/e", make_ctx("PC-A"), True).status is Status.CLONED
    write(p / "first.md", "1")
    assert sync_repo(p, empty.as_uri(), "me/e", make_ctx("PC-A"), True).status is Status.PUSHED
    assert git(empty, "log", "--format=%s", "main").startswith("[peers Sync]")


def test_empty_clone_then_other_pc_pushed_first(tmp_path):
    empty = make_remote(tmp_path, "empty", initial=False)
    p, q = tmp_path / "p", tmp_path / "q"
    sync_repo(p, empty.as_uri(), "me/e", make_ctx("PC-A"), True)
    sync_repo(q, empty.as_uri(), "me/e", make_ctx("PC-B"), True)
    write(p / "first.md", "1")
    sync_repo(p, empty.as_uri(), "me/e", make_ctx("PC-A"), True)
    r = sync_repo(q, empty.as_uri(), "me/e", make_ctx("PC-B"), True)
    assert r.status is Status.PULLED and (q / "first.md").read_text() == "1"


def test_lfs_video_roundtrip(env):
    remote, a, b = env
    write(a / "v.mp4", b"\x00VIDEO")
    s(a, remote)
    s(b, remote, "PC-B")
    assert (b / "v.mp4").read_bytes() == b"\x00VIDEO"
