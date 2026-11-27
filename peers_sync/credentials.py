"""GitHub へのログイン。

ログイン情報の保存は Git Credential Manager（GCM）に任せる。GCM はブラウザでのログイン画面を出し、
トークンをパソコン標準の安全な場所（Windows の資格情報マネージャー、Mac のキーチェーン）に保存する。
"""
from __future__ import annotations

import os
import subprocess
from typing import Callable

from .gitrunner import popen_flags

Runner = Callable[..., subprocess.CompletedProcess]

_QUERY = "protocol=https\nhost=github.com\n\n"


class LoginError(Exception):
    pass


def _parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def _credential(run: Runner, action: str, input_text: str, interactive: bool):
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not interactive:
        env["GCM_INTERACTIVE"] = "never"
    return run(["git", "credential", action], input=input_text, env=env,
               capture_output=True, text=True, encoding="utf-8", errors="replace",
               **popen_flags())


def get_stored_token(run: Runner = subprocess.run) -> tuple[str, str] | None:
    """保存済みのログイン情報を、画面を出さずに取り出す。なければ None。"""
    try:
        proc = _credential(run, "fill", _QUERY, interactive=False)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    data = _parse(proc.stdout)
    if not data.get("password"):
        return None
    return data.get("username", ""), data["password"]


def login(run: Runner = subprocess.run) -> tuple[str, str]:
    """GCM にログイン画面（ブラウザ）を出してもらい、成功したら保存する。"""
    try:
        proc = _credential(run, "fill", _QUERY, interactive=True)
    except OSError as e:
        raise LoginError("Git が見つかりません。Git をインストールしてください。") from e
    data = _parse(proc.stdout)
    if proc.returncode != 0 or not data.get("password"):
        raise LoginError("GitHub へのログインができませんでした。もう一度お試しください。")
    approve = f"protocol=https\nhost=github.com\nusername={data.get('username', '')}\npassword={data['password']}\n\n"
    _credential(run, "approve", approve, interactive=False)
    return data.get("username", ""), data["password"]


def logout(run: Runner = subprocess.run) -> None:
    _credential(run, "reject", _QUERY, interactive=False)
