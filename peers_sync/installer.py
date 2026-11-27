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


def _helper_configured(run) -> bool:
    try:
        proc = run(["git", "config", "--get-all", "credential.helper"], capture_output=True, text=True,
                   env=git_env(), **popen_flags())
    except OSError:
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def missing_tools(run=subprocess.run) -> list[str]:
    from .engine import ensure_tool_path
    ensure_tool_path()  # 入れた直後の道具も見つかるように、毎回 PATH を見直す
    if not _ok(run, ["git", "--version"]):
        return ["git", "git-lfs", "gcm"]
    missing = []
    if not _ok(run, ["git", "lfs", "version"]):
        missing.append("git-lfs")
    if not _ok(run, ["git", "credential-manager", "--version"]):
        missing.append("gcm")
    elif not _helper_configured(run):
        # 部品はあるのに、Git に登録されていない（Git より先に入れた場合など）。登録だけ自動で行う
        _ok(run, ["git", "credential-manager", "configure"])
        if not _helper_configured(run):
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


def _install_lfs_binary(download, os_name: str, exe_name: str) -> None:
    arch = "arm64" if _arm() else "amd64"
    _, url = _pick_asset(download, LFS_RELEASE, [os_name, arch, ".zip"])
    with zipfile.ZipFile(io.BytesIO(download(url))) as z:
        member = next(n for n in z.namelist() if n.rsplit("/", 1)[-1] == exe_name)
        bin_dir = tool_bin_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        exe = bin_dir / exe_name
        exe.write_bytes(z.read(member))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_windows_parts(missing: list[str], run, download) -> str:
    """Git はあるのに、LFS やログインの部品だけがないとき。winget では足せないので、個別に入れる。"""
    notes = []
    try:
        if "git-lfs" in missing:
            _install_lfs_binary(download, "windows", "git-lfs.exe")
            notes.append("Git LFS を入れました。")
        if "gcm" in missing:
            name, url = _pick_asset(download, GCM_RELEASE, ["gcmuser-win", ".exe"])
            bin_dir = tool_bin_dir()
            bin_dir.mkdir(parents=True, exist_ok=True)
            exe = bin_dir / name
            exe.write_bytes(download(url))
            proc = run([str(exe), "/VERYSILENT", "/NORESTART"], capture_output=True, timeout=600,
                       **popen_flags())
            if proc.returncode != 0:
                raise InstallError("ログインの部品を入れられませんでした。もう一度ボタンを押してください。")
            notes.append("ログインの部品を入れました。")
    except InstallError:
        raise
    except (OSError, ValueError, KeyError, StopIteration, zipfile.BadZipFile, subprocess.TimeoutExpired) as e:
        raise InstallError("ダウンロードがうまくいきませんでした。インターネットの接続を確かめて、"
                           f"もう一度ボタンを押してください。（{type(e).__name__}）") from e
    return "\n".join(notes)


def _install_windows(missing: list[str], run, download) -> str:
    if "git" not in missing:
        return _install_windows_parts(missing, run, download)
    cmd = ["winget", "install", "--id", "Git.Git", "-e", "--source", "winget", "--silent",
           "--accept-package-agreements", "--accept-source-agreements"]
    try:
        # 出力は使わないので文字に直さない（日本語の Windows で文字化けして失敗あつかいにならないように）
        proc = run(cmd, capture_output=True, timeout=1800, **popen_flags())
    except subprocess.TimeoutExpired:
        raise InstallError("Git のインストールが終わりませんでした。パソコンを再起動してから、"
                           "もう一度ボタンを押してください。")
    except OSError:
        open_url(GIT_WIN_PAGE)
        raise InstallError("自動では入れられませんでした。Git のダウンロードページを開いたので、"
                           "そこからインストールして、このアプリを開き直してください。")
    if proc.returncode != 0:
        raise InstallError("Git のインストールがうまくいきませんでした。もう一度ボタンを押すか、"
                           f"{GIT_WIN_PAGE} から入れてください。")
    return "Git を入れました（Git LFS とログインの部品も入っています）。"


def _install_mac(missing: list[str], run, download) -> str:
    if "git" in missing:
        # Git が入るまでは、ほかのものは入れない（ログインの部品の設定には Git が必要なため）
        run(["xcode-select", "--install"], capture_output=True, text=True)
        return ("Apple の画面が出たら「インストール」を押してください。"
                "終わったら、もう一度このボタンを押してください（残りのものを入れます）。")
    notes = []
    bin_dir = tool_bin_dir()
    try:
        if "git-lfs" in missing:
            _install_lfs_binary(download, "darwin", "git-lfs")
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
        return _install_windows(missing, run, download)
    if platform == "darwin":
        return _install_mac(missing, run, download)
    raise InstallError("このパソコンでは自動で入れられません。ターミナルで "
                       "`sudo apt install git git-lfs` などを実行して、Git を入れてください。")
