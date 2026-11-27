from pathlib import Path

import pytest

from conftest import make_ctx
from helpers import make_remote
from peers_sync.config import default_settings
from peers_sync.engine import SyncAbort, local_path_for, sync_all
from peers_sync.github_api import GitHubError
from peers_sync.models import RemoteRepo, Status


def rr(full_name, url):
    return RemoteRepo(full_name, full_name.split("/")[1], url, False, False)


class FakeApi:
    def __init__(self, owned, extra=None, fail=None):
        self.owned, self.extra, self.fail = owned, extra or {}, fail
        self.get_calls = []

    def list_owned_repos(self):
        if self.fail:
            raise self.fail
        return self.owned

    def get_repo(self, full_name):
        self.get_calls.append(full_name)
        return self.extra[full_name]


@pytest.fixture
def settings(tmp_path):
    s = default_settings()
    s.base_dir = tmp_path / "GitHub"
    return s


def test_sync_all_skips_excluded_and_continues_after_error(tmp_path, settings):
    ok = make_remote(tmp_path, "ok")
    settings.excluded = {"me/skip"}
    api = FakeApi([rr("me/ok", ok.as_uri()),
                   rr("me/broken", (tmp_path / "nope.git").as_uri()),
                   rr("me/skip", ok.as_uri())])
    seen = []
    results = sync_all(settings, api, make_ctx("PC-A"), on_result=seen.append, lfs_available=True)
    assert {r.full_name: r.status for r in results} == {
        "me/ok": Status.CLONED, "me/broken": Status.ERROR, "me/skip": Status.SKIPPED}
    assert len(seen) == 3 and (settings.base_dir / "ok" / "README.md").exists()


def test_sync_all_continues_after_unexpected_exception(tmp_path, settings, monkeypatch):
    ok = make_remote(tmp_path, "ok")

    def boom(*a, **k):
        raise RuntimeError("bug")

    import peers_sync.engine as engine
    calls = {"n": 0}
    real = engine.sync_repo

    def flaky(*a, **k):
        calls["n"] += 1
        return boom() if calls["n"] == 1 else real(*a, **k)

    monkeypatch.setattr(engine, "sync_repo", flaky)
    api = FakeApi([rr("me/first", ok.as_uri()), rr("me/ok", ok.as_uri())])
    results = sync_all(settings, api, make_ctx("PC-A"), on_result=lambda r: None, lfs_available=True)
    assert [r.status for r in results] == [Status.ERROR, Status.CLONED]


def test_sync_all_includes_extra_repos_once(tmp_path, settings):
    ok = make_remote(tmp_path, "ok")
    other = make_remote(tmp_path, "other")
    settings.extra_repos = ["me/ok", "friend/other"]
    api = FakeApi([rr("me/ok", ok.as_uri())], extra={"friend/other": rr("friend/other", other.as_uri())})
    results = sync_all(settings, api, make_ctx("PC-A"), on_result=lambda r: None, lfs_available=True)
    assert [r.full_name for r in results] == ["me/ok", "friend/other"]
    assert api.get_calls == ["friend/other"]


def test_sync_all_list_failure_raises_syncabort(settings):
    api = FakeApi([], fail=GitHubError(401, "Bad credentials"))
    with pytest.raises(SyncAbort) as e:
        sync_all(settings, api, make_ctx("PC-A"), on_result=lambda r: None, lfs_available=True)
    assert "ログイン" in str(e.value)


def test_local_path_for_uses_override(tmp_path, settings):
    settings.repo_paths = {"me/x": tmp_path / "elsewhere"}
    assert local_path_for(rr("me/x", "u"), settings) == tmp_path / "elsewhere"
    assert local_path_for(rr("me/y", "u"), settings) == settings.base_dir / "y"


def test_same_name_from_two_owners_get_different_folders(tmp_path, settings):
    a = make_remote(tmp_path, "a")
    b = make_remote(tmp_path, "b")
    settings.extra_repos = ["friend/x"]
    api = FakeApi([rr("me/x", a.as_uri())], extra={"friend/x": rr("friend/x", b.as_uri())})
    results = sync_all(settings, api, make_ctx("PC-A"), on_result=lambda r: None, lfs_available=True)
    assert [r.status for r in results] == [Status.CLONED, Status.CLONED]
    assert results[0].path != results[1].path


def test_macos_gui_path_gets_homebrew_dirs(monkeypatch):
    import peers_sync.engine as engine
    monkeypatch.setattr(engine.sys, "platform", "darwin")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    engine.ensure_tool_path()
    parts = engine.os.environ["PATH"].split(":")
    assert "/opt/homebrew/bin" in parts and "/usr/local/bin" in parts
