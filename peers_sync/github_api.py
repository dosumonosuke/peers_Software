"""GitHub API から、リポジトリの一覧などを取ってくる。"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from typing import Any, Callable

from .models import RemoteRepo

API = "https://api.github.com"
HttpGet = Callable[[str, dict], "tuple[int, Any, dict[str, str]]"]


class GitHubError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


def _ssl_context() -> ssl.SSLContext:
    # 配布用のアプリ（PyInstaller）では、パソコンの証明書が見つからないことがあるので certifi を優先する
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def urllib_get(url: str, headers: dict) -> tuple[int, Any, dict[str, str]]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except ValueError:
            body = {}
        return e.code, body, dict(e.headers or {})
    except urllib.error.URLError as e:
        raise GitHubError(0, str(e.reason)) from e


_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


def _to_repo(d: dict) -> RemoteRepo:
    return RemoteRepo(full_name=d["full_name"], name=d["name"], clone_url=d["clone_url"],
                      private=bool(d.get("private")), archived=bool(d.get("archived")))


class GitHubClient:
    def __init__(self, token: str, http_get: HttpGet | None = None):
        self.token = token
        self.http_get = http_get or urllib_get

    def _get(self, url: str) -> tuple[Any, dict[str, str]]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "peers-sync",
        }
        status, body, resp_headers = self.http_get(url, headers)
        if status != 200:
            msg = body.get("message", "") if isinstance(body, dict) else ""
            raise GitHubError(status, msg)
        return body, resp_headers

    def current_login(self) -> str:
        body, _ = self._get(f"{API}/user")
        return body["login"]

    def list_owned_repos(self) -> list[RemoteRepo]:
        url: str | None = f"{API}/user/repos?affiliation=owner&per_page=100"
        repos: list[RemoteRepo] = []
        while url:
            body, headers = self._get(url)
            repos += [_to_repo(d) for d in body if not d.get("archived")]
            link = next((v for k, v in headers.items() if k.lower() == "link"), "")
            m = _NEXT.search(link)
            url = m.group(1) if m else None
        return repos

    def get_repo(self, full_name: str) -> RemoteRepo:
        body, _ = self._get(f"{API}/repos/{full_name}")
        return _to_repo(body)
