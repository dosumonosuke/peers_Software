"""足りない道具（Git・Git LFS・ログインの部品）を調べて、OS に合った方法で入れる。"""
from __future__ import annotations

import io
import json
import os
import platform as platform_mod
import stat
import subprocess
import sys
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from typing import Callable

from .config import config_path
from .gitrunner import git_env, popen_flags

LFS_RELEASE = "https://api.github.com/repos/git-lfs/git-lfs/releases/latest"
GCM_RELEASE = "https://api.github.com/repos/git-ecosystem/git-credential-manager/releases/latest"
GIT_WIN_PAGE = "https://git-scm.com/download/win"

open_url: Callable[[str], object] = webbrowser.open

_NAMES = {"git": "Git", "git-lfs": "Git LFS（大きいファイル用）", "gcm": "ログインの部品（Git Credential Manager）"}


class InstallError(Exception):
    """インストールできなかったときのエラー（日本語のメッセージを持つ）。"""


def describe(missing: list[str]) -> str:
    return "、".join(_NAMES.get(m, m) for m in missing)


def tool_bin_dir() -> Path:
    """アプリが自分でダウンロードした道具を置く場所。"""
    return config_path().parent / "bin"


def _ok(run, args: list[str]) -> bool:
    try:
        return run(args, capture_output=True, text=True, env=git_env(), **popen_flags()).returncode == 0
    except OSError:
        return False


def missing_tools(run=subprocess.run) -> list[str]:
    if not _ok(run, ["git", "--version"]):
        return ["git", "git-lfs", "gcm"]
    missing = []
    if not _ok(run, ["git", "lfs", "version"]):
        missing.append("git-lfs")
    if not _ok(run, ["git", "credential-manager", "--version"]):
        missing.append("gcm")
    return missing


def _download(url: str) -> bytes:
    from .github_api import _ssl_context
    req = urllib.request.Request(url, headers={"User-Agent": "peers-sync"})
    with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
        return resp.read()


def _pick_asset(download, release_url: str, must: list[str]) -> tuple[str, str]:
    data = json.loads(download(release_url).decode("utf-8"))
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if all(m in name for m in must):
            return name, asset["browser_download_url"]
    raise InstallError(f"ダウンロードするファイルが見つかりませんでした（{' '.join(must)}）。")


def _arm() -> bool:
    return platform_mod.machine().lower() in ("arm64", "aarch64")


def _install_windows(run) -> str:
    cmd = ["winget", "install", "--id", "Git.Git", "-e", "--source", "winget", "--silent",
           "--accept-package-agreements", "--accept-source-agreements"]
    try:
        proc = run(cmd, capture_output=True, text=True, **popen_flags())
    except OSError:
        open_url(GIT_WIN_PAGE)
        raise InstallError("自動では入れられませんでした。Git のダウンロードページを開いたので、"
                           "そこからインストールして、このアプリを開き直してください。")
    if proc.returncode != 0:
        raise InstallError("Git のインストールがうまくいきませんでした。もう一度ボタンを押すか、"
                           f"{GIT_WIN_PAGE} から入れてください。")
    return "Git を入れました（Git LFS とログインの部品も入っています）。"


def _install_mac(missing: list[str], run, download) -> str:
    notes = []
    bin_dir = tool_bin_dir()
    try:
        if "git" in missing:
            run(["xcode-select", "--install"], capture_output=True, text=True)
            notes.append("Apple の画面が出たら「インストール」を押してください。")
        if "git-lfs" in missing:
            arch = "arm64" if _arm() else "amd64"
            _, url = _pick_asset(download, LFS_RELEASE, ["darwin", arch, ".zip"])
            with zipfile.ZipFile(io.BytesIO(download(url))) as z:
                member = next(n for n in z.namelist() if n.endswith("/git-lfs") or n == "git-lfs")
                bin_dir.mkdir(parents=True, exist_ok=True)
                exe = bin_dir / "git-lfs"
                exe.write_bytes(z.read(member))
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            notes.append("Git LFS を入れました。")
        if "gcm" in missing:
            arch = "arm64" if _arm() else "x64"
            name, url = _pick_asset(download, GCM_RELEASE, ["gcm-osx", arch, ".pkg"])
            bin_dir.mkdir(parents=True, exist_ok=True)
            pkg = bin_dir / name
            pkg.write_bytes(download(url))
            run(["open", str(pkg)], capture_output=True, text=True)
            notes.append("ログインの部品のインストーラを開きました。「続ける」→「インストール」と進めて、"
                         "パスワードを入れてください。")
    except InstallError:
        raise
    except (OSError, ValueError, KeyError, StopIteration, zipfile.BadZipFile) as e:
        raise InstallError("ダウンロードがうまくいきませんでした。インターネットの接続を確かめて、"
                           f"もう一度ボタンを押してください。（{type(e).__name__}）") from e
    notes.append("終わったら、もう一度ボタンを押してください。")
    return "\n".join(notes)


def install_missing(missing: list[str], run=subprocess.run, download=None,
                    platform: str | None = None) -> str:
    platform = platform or sys.platform
    download = download or _download
    if not missing:
        return "必要なものは、すべてそろっています。"
    if platform == "win32":
        return _install_windows(run)
    if platform == "darwin":
        return _install_mac(missing, run, download)
    raise InstallError("このパソコンでは自動で入れられません。ターミナルで "
                       "`sudo apt install git git-lfs` などを実行して、Git を入れてください。")
