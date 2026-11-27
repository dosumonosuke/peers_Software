import io
import json
import os
import subprocess
import zipfile

import pytest

import peers_sync.installer as installer
from peers_sync.installer import InstallError, install_missing, missing_tools


def fake(calls, rc=0, missing=()):
    def run(args, **kw):
        calls.append(list(args))
        joined = " ".join(args)
        if any(m in joined for m in missing):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        return subprocess.CompletedProcess(args, rc, "", "")
    return run


def test_missing_tools_lists_in_order():
    assert missing_tools(fake([], missing=("lfs version", "credential-manager"))) == ["git-lfs", "gcm"]


def test_missing_tools_when_git_absent():
    def run(args, **kw):
        raise FileNotFoundError("git")
    assert missing_tools(run) == ["git", "git-lfs", "gcm"]


def test_windows_uses_winget_for_everything():
    calls = []
    msg = install_missing(["git", "git-lfs", "gcm"], run=fake(calls), platform="win32")
    assert calls[0][:3] == ["winget", "install", "--id"] and "Git.Git" in calls[0]
    assert "--silent" in calls[0] and "--accept-package-agreements" in calls[0]
    assert len(calls) == 1 and "入れました" in msg


def test_windows_without_winget_opens_download_page(monkeypatch):
    opened = []
    monkeypatch.setattr(installer, "open_url", opened.append)

    def run(args, **kw):
        raise FileNotFoundError("winget")

    with pytest.raises(InstallError) as e:
        install_missing(["git"], run=run, platform="win32")
    assert opened == ["https://git-scm.com/download/win"] and "ダウンロードページ" in str(e.value)


def test_windows_winget_failure_raises():
    with pytest.raises(InstallError):
        install_missing(["git"], run=fake([], rc=1), platform="win32")


def test_mac_missing_git_runs_xcode_select():
    calls = []
    msg = install_missing(["git"], run=fake(calls), platform="darwin")
    assert calls == [["xcode-select", "--install"]] and "インストール" in msg


def _release(assets):
    return json.dumps({"assets": [{"name": n, "browser_download_url": f"https://dl/{n}"} for n in assets]}).encode()


def test_mac_git_lfs_is_downloaded_into_app_bin(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "tool_bin_dir", lambda: tmp_path)
    monkeypatch.setattr(installer.platform_mod, "machine", lambda: "arm64")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("git-lfs-3.6.0/git-lfs", "#!/bin/sh\necho lfs\n")
    urls = []

    def download(url):
        urls.append(url)
        if "api.github.com" in url:
            return _release(["git-lfs-darwin-amd64-v3.6.0.zip", "git-lfs-darwin-arm64-v3.6.0.zip",
                             "git-lfs-windows-amd64-v3.6.0.zip"])
        return buf.getvalue()

    install_missing(["git-lfs"], run=fake([]), download=download, platform="darwin")
    assert urls == ["https://api.github.com/repos/git-lfs/git-lfs/releases/latest",
                    "https://dl/git-lfs-darwin-arm64-v3.6.0.zip"]
    exe = tmp_path / "git-lfs"
    assert exe.exists()
    if os.name != "nt":  # Windows には「実行できる」印がない
        assert os.stat(exe).st_mode & 0o111


def test_mac_gcm_pkg_is_downloaded_and_opened(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "tool_bin_dir", lambda: tmp_path)
    monkeypatch.setattr(installer.platform_mod, "machine", lambda: "x86_64")
    calls = []

    def download(url):
        if "api.github.com" in url:
            return _release(["gcm-osx-arm64-2.6.0.pkg", "gcm-osx-x64-2.6.0.pkg", "gcm-linux_amd64.deb"])
        return b"PKG"

    install_missing(["gcm"], run=fake(calls), download=download, platform="darwin")
    pkg = tmp_path / "gcm-osx-x64-2.6.0.pkg"
    assert pkg.read_bytes() == b"PKG" and calls == [["open", str(pkg)]]


def test_mac_download_failure_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "tool_bin_dir", lambda: tmp_path)

    def download(url):
        raise OSError("offline")

    with pytest.raises(InstallError):
        install_missing(["git-lfs"], run=fake([]), download=download, platform="darwin")


def test_linux_raises_with_instructions():
    with pytest.raises(InstallError) as e:
        install_missing(["git"], run=fake([]), platform="linux")
    assert "apt" in str(e.value)


def test_windows_path_gets_git_cmd_dir(monkeypatch, tmp_path):
    import peers_sync.engine as engine
    (tmp_path / "Git" / "cmd").mkdir(parents=True)
    monkeypatch.setattr(engine.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    engine.ensure_tool_path()
    parts = os.environ["PATH"].split(os.pathsep)
    assert str(tmp_path / "Git" / "cmd") in parts and str(installer.tool_bin_dir()) in parts


def test_mac_path_gets_app_bin_dir(monkeypatch):
    import peers_sync.engine as engine
    monkeypatch.setattr(engine.sys, "platform", "darwin")
    monkeypatch.setenv("PATH", "/usr/bin")
    engine.ensure_tool_path()
    assert str(installer.tool_bin_dir()) in os.environ["PATH"].split(os.pathsep)
