"""初期設定を減らした変更の、最終チェックで見つかった問題を再現するテスト。"""
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

import peers_sync.engine as engine
import peers_sync.installer as installer
from conftest import make_ctx
from helpers import clone, make_remote
from peers_sync.config import default_settings
from peers_sync.installer import InstallError, install_missing, missing_tools
from peers_sync.models import RemoteRepo, Status


def ok(args, **kw):
    return subprocess.CompletedProcess(args, 0, "manager\n", "")


# Critical 1: winget で入れた直後は PATH に Git がなく、見つからないまま止まる
def test_missing_tools_refreshes_path_first(monkeypatch, tmp_path):
    git_cmd = tmp_path / "Git" / "cmd"
    git_cmd.mkdir(parents=True)
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")

    def run(args, **kw):
        found = str(git_cmd) in os.environ["PATH"].split(os.pathsep)
        return subprocess.CompletedProcess(args, 0 if found else 1, "manager\n", "")

    assert missing_tools(run) == []


# Important 2: Git はあって、ログインの部品や LFS だけがないとき、winget では入らない
def _release(names):
    return json.dumps({"assets": [{"name": n, "browser_download_url": f"https://dl/{n}"} for n in names]}).encode()


def test_windows_only_gcm_missing_downloads_user_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "tool_bin_dir", lambda: tmp_path)
    calls = []

    def download(url):
        if "api.github.com" in url:
            return _release(["gcm-win-x86-2.6.1.exe", "gcmuser-win-x86-2.6.1.exe", "gcm-osx-x64-2.6.1.pkg"])
        return b"EXE"

    def run(args, **kw):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    install_missing(["gcm"], run=run, download=download, platform="win32")
    exe = tmp_path / "gcmuser-win-x86-2.6.1.exe"
    assert exe.read_bytes() == b"EXE"
    assert calls == [[str(exe), "/VERYSILENT", "/NORESTART"]]


def test_windows_only_lfs_missing_downloads_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "tool_bin_dir", lambda: tmp_path)
    monkeypatch.setattr(installer.platform_mod, "machine", lambda: "AMD64")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("git-lfs-3.6.0/git-lfs.exe", "MZ")
    calls = []

    def download(url):
        if "api.github.com" in url:
            return _release(["git-lfs-windows-arm64-v3.6.0.zip", "git-lfs-windows-amd64-v3.6.0.zip"])
        assert "windows-amd64" in url
        return buf.getvalue()

    install_missing(["git-lfs"], run=lambda a, **k: calls.append(a), download=download, platform="win32")
    assert (tmp_path / "git-lfs.exe").read_bytes() == b"MZ" and calls == []


# Important 3: winget の出力の文字コードで、成功したのに失敗あつかいになる
def test_winget_output_is_not_decoded_as_locale_text():
    seen = {}

    def run(args, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    install_missing(["git", "git-lfs", "gcm"], run=run, platform="win32")
    assert not seen.get("text") and "encoding" not in seen


# Minor 10: winget が止まったままだと、ボタンがずっと押せない
def test_winget_timeout_becomes_install_error():
    seen = {}

    def run(args, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(args, 1)

    with pytest.raises(InstallError):
        install_missing(["git"], run=run, platform="win32")
    assert seen.get("timeout")


# Important 4: Mac で Git もないときに全部いっぺんに入れると、ログインの部品の設定がされない
def test_mac_without_git_only_runs_xcode_select_first():
    calls = []

    def download(url):
        raise AssertionError("Git が入るまでは、ほかのものをダウンロードしない")

    install_missing(["git", "git-lfs", "gcm"], run=lambda a, **k: calls.append(list(a)), download=download,
                    platform="darwin")
    assert calls == [["xcode-select", "--install"]]


def test_gcm_present_but_not_configured_gets_configured():
    calls = []
    state = {"configured": False}

    def run(args, **kw):
        calls.append(list(args))
        if args[:3] == ["git", "config", "--get-all"]:
            out = "manager\n" if state["configured"] else ""
            return subprocess.CompletedProcess(args, 0 if out else 1, out, "")
        if args[:3] == ["git", "credential-manager", "configure"]:
            state["configured"] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    assert missing_tools(run) == []
    assert ["git", "credential-manager", "configure"] in calls


# Important 5: 読めないフォルダが 1 つあるだけで、その場所全体を探さなくなる
def test_unreadable_sibling_does_not_hide_root(tmp_path, monkeypatch):
    remote = make_remote(tmp_path, "x")
    root = tmp_path / "Desktop"
    found = clone(remote, root / "x")
    (root / "locked").mkdir()
    real_exists = Path.exists

    def exists(self, *a, **k):
        if self.parent.name == "locked":
            raise PermissionError("denied")
        return real_exists(self, *a, **k)

    monkeypatch.setattr(Path, "exists", exists)
    s = default_settings()
    s.base_dir = tmp_path / "GitHub"
    repo = RemoteRepo("me/x", "x", remote.as_uri(), False, False)
    assert engine.discover_existing([repo], s, [root]) == {"me/x": found}


# Minor 6: ユーザー用にインストールした Git の場所と、OneDrive のドキュメントも見る
def test_windows_path_includes_per_user_git(monkeypatch, tmp_path):
    user_git = tmp_path / "Programs" / "Git" / "cmd"
    user_git.mkdir(parents=True)
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "none"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    engine.ensure_tool_path()
    assert str(user_git) in os.environ["PATH"].split(os.pathsep)


def test_search_roots_include_onedrive_documents():
    roots = engine.default_search_roots()
    assert Path.home() / "OneDrive" / "Documents" / "GitHub" in roots
    assert Path.home() / "OneDrive" / "ドキュメント" / "GitHub" in roots


# Minor 9: 見つけたフォルダが消されたら、置き場所の記録も使わない
def test_stale_discovered_path_falls_back_to_base_dir(tmp_path):
    remote = make_remote(tmp_path, "x")
    s = default_settings()
    s.base_dir = tmp_path / "GitHub"
    s.repo_paths = {"me/x": tmp_path / "Desktop" / "x"}  # もう存在しない

    class Api:
        def list_owned_repos(self):
            return [RemoteRepo("me/x", "x", remote.as_uri(), False, False)]

    [r] = engine.sync_all(s, Api(), make_ctx("PC-A"), on_result=lambda r: None, lfs_available=True,
                          search_roots=[])
    assert r.status is Status.CLONED and r.path == s.base_dir / "x" and "me/x" not in s.repo_paths
