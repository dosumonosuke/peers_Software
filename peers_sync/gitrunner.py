"""Git コマンドを呼び出す係。実行したコマンドは、日本語の説明つきで記録する。"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# サブコマンド → やさしい日本語の説明（「くわしいログ」に表示する）
_EXPLANATIONS = {
    "clone": "GitHub からリポジトリをまるごとダウンロードする（クローン）",
    "add": "セーブするファイルを選んで封筒に入れる（ステージ）",
    "commit": "選んだファイルを、メモ付きでセーブする（コミット）",
    "fetch": "GitHub に新しい変更があるか確認して、取ってくる（フェッチ）",
    "merge": "GitHub の変更と手元の変更を合体させる（マージ）",
    "push": "セーブしたものを GitHub に送る（プッシュ）",
    "status": "いまのファイルの状態を確認する",
    "lfs": "大きいファイル（動画など）を専用の置き場で扱う（Git LFS）",
    "diff": "変更の中身や一覧を調べる",
    "rev-list": "コミットの数を数える",
    "rev-parse": "コミットやブランチの情報を調べる",
    "symbolic-ref": "いま開いているブランチの名前を調べる",
    "checkout": "どちらかの版のファイルを取り出す",
    "config": "Git の設定を読む・書く",
    "credential": "GitHub のログイン情報を扱う",
    "cat-file": "保存されているファイルの中身を読む",
    "check-attr": "ファイルにどんなルールがあるか調べる",
    "init": "新しいリポジトリを作る",
    "ls-files": "Git が管理しているファイルの一覧を見る",
    "show": "保存されている内容を表示する",
    "hash-object": "ファイルを Git に保存する",
}


def explain_command(args: list[str]) -> str:
    sub = next((a for a in args if not a.startswith("-")), "")
    return _EXPLANATIONS.get(sub, "Git の操作")


@dataclass
class CommandLog:
    cwd: Path
    args: list[str]
    explanation: str
    returncode: int
    stdout: str
    stderr: str


class GitError(Exception):
    def __init__(self, args_list: list[str], returncode: int, stderr: str):
        super().__init__(f"git {' '.join(args_list)} failed ({returncode}): {stderr.strip()}")
        self.args_list = args_list
        self.returncode = returncode
        self.stderr = stderr


SAFE_DEFAULTS = {"merge.ff": "true", "commit.gpgsign": "false"}


def git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def popen_flags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


class GitRunner:
    def __init__(self, cwd: Path, log: list[CommandLog] | None = None,
                 extra_config: dict[str, str] | None = None):
        self.cwd = Path(cwd)
        self.log = log if log is not None else []
        # ユーザーの設定（merge.ff=only・コミットの署名など）で同期が止まらないように、毎回上書きする
        self.extra_config = {**SAFE_DEFAULTS, **(extra_config or {})}

    def _cmd(self, args: tuple[str, ...]) -> list[str]:
        cmd = ["git"]
        for k, v in self.extra_config.items():
            cmd += ["-c", f"{k}={v}"]
        return cmd + list(args)

    def run(self, *args: str, check: bool = True,
            input: str | None = None) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            self._cmd(args), cwd=self.cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=git_env(), input=input,
            **popen_flags(),
        )
        self.log.append(CommandLog(self.cwd, list(args), explain_command(list(args)),
                                   proc.returncode, proc.stdout, proc.stderr))
        if check and proc.returncode != 0:
            raise GitError(list(args), proc.returncode, proc.stderr)
        return proc

    def run_bytes(self, *args: str) -> bytes:
        proc = subprocess.run(self._cmd(args), cwd=self.cwd, capture_output=True,
                              env=git_env(), **popen_flags())
        stderr = proc.stderr.decode("utf-8", "replace")
        self.log.append(CommandLog(self.cwd, list(args), explain_command(list(args)),
                                   proc.returncode, "<binary>", stderr))
        if proc.returncode != 0:
            raise GitError(list(args), proc.returncode, stderr)
        return proc.stdout
