import pytest

from conftest import make_ctx
from helpers import clone, git, make_remote, write
from peers_sync.conflicts import cancel_resolution, copy_name, finish_resolution, start_resolution
from peers_sync.models import Status
from peers_sync.sync import sync_repo


@pytest.fixture
def conflict(tmp_path):
    remote = make_remote(tmp_path)
    a = clone(remote, tmp_path / "a")
    b = clone(remote, tmp_path / "b")
    write(a / "README.md", "A")
    sync_repo(a, remote.as_uri(), "me/x", make_ctx("PC-A"), True)
    write(b / "README.md", "B")
    r = sync_repo(b, remote.as_uri(), "me/x", make_ctx("PC-B"), True)
    assert r.status is Status.CONFLICT
    return remote, b, make_ctx("PC-B")


def test_start_returns_text_conflicts_with_both_contents(conflict):
    _, b, ctx = conflict
    files = start_resolution(b, ctx)
    assert [(f.path, f.mine, f.theirs) for f in files] == [("README.md", "B", "A")]


def test_finish_mine_keeps_mine_and_saves_theirs_copy_and_pushes(conflict):
    remote, b, ctx = conflict
    start_resolution(b, ctx)
    r = finish_resolution(b, {"README.md": "mine"}, ctx, full_name="me/x")
    assert r.status is Status.PUSHED_AND_PULLED and r.full_name == "me/x"
    assert (b / "README.md").read_text() == "B"
    assert (b / "README (GitHub の版).md").read_text() == "A"
    assert git(remote, "show", "main:README.md") == "B"
    assert git(remote, "show", "main:README (GitHub の版).md") == "A"


def test_finish_theirs(conflict):
    remote, b, ctx = conflict
    start_resolution(b, ctx)
    finish_resolution(b, {"README.md": "theirs"}, ctx)
    assert (b / "README.md").read_text() == "A"
    assert (b / "README (PC-B の版).md").read_text() == "B"
    assert git(remote, "show", "main:README.md") == "A"


def test_cancel_restores_clean_state(conflict):
    _, b, ctx = conflict
    start_resolution(b, ctx)
    cancel_resolution(b)
    assert not (b / ".git/MERGE_HEAD").exists() and (b / "README.md").read_text() == "B"


def test_finish_requires_choice_for_every_file(conflict):
    _, b, ctx = conflict
    start_resolution(b, ctx)
    with pytest.raises(ValueError):
        finish_resolution(b, {}, ctx)
    assert (b / ".git/MERGE_HEAD").exists()
    assert not (b / "README (GitHub の版).md").exists()


def test_start_with_no_conflict_left_returns_empty(tmp_path):
    remote = make_remote(tmp_path)
    a = clone(remote, tmp_path / "a")
    assert start_resolution(a, make_ctx("PC-A")) == []


def test_copy_name_increments():
    assert copy_name("d/a.md", "PC", lambda p: p == "d/a (PC の版).md") == "d/a (PC の版 2).md"
    assert copy_name("noext", "PC", lambda p: False) == "noext (PC の版)"
    assert copy_name(".bashrc", "PC", lambda p: False) == ".bashrc (PC の版)"
