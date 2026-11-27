import pytest

from peers_sync.github_api import GitHubClient, GitHubError


def repo_json(full_name, private=False, archived=False):
    return {"full_name": full_name, "name": full_name.split("/")[1],
            "clone_url": f"https://github.com/{full_name}.git",
            "private": private, "archived": archived}


def test_list_owned_repos_paginates_and_drops_archived():
    pages = {
        "https://api.github.com/user/repos?affiliation=owner&per_page=100":
            (200, [repo_json("me/a"), repo_json("me/old", archived=True)],
             {"Link": '<https://api.github.com/user/repos?page=2>; rel="next", <https://api.github.com/user/repos?page=2>; rel="last"'}),
        "https://api.github.com/user/repos?page=2": (200, [repo_json("me/b", private=True)], {}),
    }
    c = GitHubClient("tok", http_get=lambda url, h: pages[url])
    repos = c.list_owned_repos()
    assert [r.full_name for r in repos] == ["me/a", "me/b"]
    assert repos[1].private and repos[0].clone_url == "https://github.com/me/a.git"


def test_sends_bearer_token():
    seen = {}

    def get(url, h):
        seen.update(h)
        return (200, {"login": "me"}, {})

    assert GitHubClient("tok", http_get=get).current_login() == "me"
    assert seen["Authorization"] == "Bearer tok"


def test_get_repo():
    c = GitHubClient("tok", http_get=lambda u, h: (200, repo_json("o/x"), {}))
    assert c.get_repo("o/x").name == "x"


def test_http_error_raises():
    c = GitHubClient("tok", http_get=lambda u, h: (401, {"message": "Bad"}, {}))
    with pytest.raises(GitHubError) as e:
        c.current_login()
    assert e.value.status == 401
