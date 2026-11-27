import pytest

from conftest import make_ctx
from helpers import clone, make_remote, write
from peers_sync.config import default_settings
from peers_sync.engine import sync_all
from peers_sync.models import RemoteRepo, Status


class Api:
    def __init__(self, repos):
        self.repos = repos

    def list_owned_repos(self):
        return self.repos

    def get_repo(self, full_name):
        raise AssertionError("not used")


@pytest.fixture
def settings(tmp_path):
    s = default_settings()
    s.base_dir = tmp_path / "GitHub"
    return s


def run(settings, repos, roots):
    return sync_all(settings, Api(repos), make_ctx("PC-A"), on_result=lambda r: None,
                    lfs_available=True, search_roots=roots)


def test_sync_all_uses_existing_clone_found_in_roots(tmp_path, settings):
    remote = make_remote(tmp_path, "x")
    found = clone(remote, tmp_path / "Documents" / "GitHub" / "x")
    write(found / "new.md", "hi")
    [r] = run(settings, [RemoteRepo("me/x", "x", remote.as_uri(), False, False)], [tmp_path / "Documents" / "GitHub"])
    assert r.status is Status.PUSHED and r.path == found
    assert settings.repo_paths["me/x"] == found
    assert not (settings.base_dir / "x").exists()


def test_discovery_matches_folder_name_case_insensitively(tmp_path, settings):
    remote = make_remote(tmp_path, "Repo")
    found = clone(remote, tmp_path / "Projects" / "repo")
    [r] = run(settings, [RemoteRepo("me/Repo", "Repo", remote.as_uri(), False, False)], [tmp_path / "Projects"])
    assert r.path == found and r.status is Status.UP_TO_DATE


def test_discovery_ignores_other_remote(tmp_path, settings):
    remote = make_remote(tmp_path, "x")
    other = make_remote(tmp_path, "other")
    clone(other, tmp_path / "Documents" / "x")
    [r] = run(settings, [RemoteRepo("me/x", "x", remote.as_uri(), False, False)], [tmp_path / "Documents"])
    assert r.status is Status.CLONED and r.path == settings.base_dir / "x"
    assert "me/x" not in settings.repo_paths


def test_discovery_missing_roots_are_ignored(tmp_path, settings):
    remote = make_remote(tmp_path, "x")
    [r] = run(settings, [RemoteRepo("me/x", "x", remote.as_uri(), False, False)], [tmp_path / "nope"])
    assert r.status is Status.CLONED


def test_discovery_skips_repos_already_present_at_default(tmp_path, settings):
    remote = make_remote(tmp_path, "x")
    clone(remote, settings.base_dir / "x")
    elsewhere = clone(remote, tmp_path / "Documents" / "x")
    [r] = run(settings, [RemoteRepo("me/x", "x", remote.as_uri(), False, False)], [tmp_path / "Documents"])
    assert r.path == settings.base_dir / "x" and "me/x" not in settings.repo_paths
