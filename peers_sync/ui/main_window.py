"""メインの画面：大きな「ぜんぶ同期」ボタンと、リポジトリごとの結果。"""
from __future__ import annotations

import gc
import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from ..config import Settings, save_settings
from ..credentials import LoginError
from ..engine import SyncAbort
from ..messages import LFS_MISSING, status_text
from ..models import RepoResult, Status, SyncContext

SYNC_LABEL = "🔄  ぜんぶ同期"
LOGIN_LABEL = "🔑  GitHub でログイン"

HELP_TEXT = """peers Sync の使い方

1. 「GitHub でログイン」を押すと、ブラウザが開きます。GitHub にログインしてください。
2. 「ぜんぶ同期」を押すと、すべてのリポジトリで次のことを自動で行います。
   ・このパソコンにないリポジトリをダウンロード（クローン）
   ・変更をセーブ（コミット）して GitHub に送る（プッシュ）
   ・ほかのパソコンでの変更を取り込む（プル）
3. 「⚠️ 競合あり」の行をダブルクリックすると、どちらの版を残すか選べます。
   選ばなかったほうも「(〜 の版)」という名前のコピーで残るので、作業は消えません。

・パスワードや鍵らしいファイル（.env など）は送りません。
・動画などの大きいファイルは Git LFS で送ります（無料枠：容量 10GB・月 10GB）。
"""


@dataclass
class Services:
    """画面が使う機能のまとめ。テストでは偽物に差しかえる。"""
    sync_all: Callable[..., list[RepoResult]]
    get_token: Callable[[], "tuple[str, str] | None"]
    login: Callable[[], "tuple[str, str]"]
    logout: Callable[[], None]
    make_api: Callable[[str], object]
    prerequisites: Callable[[], "tuple[str | None, bool]"]


def _font_family() -> str:
    if sys.platform == "win32":
        return "Yu Gothic UI"
    if sys.platform == "darwin":
        return "Hiragino Sans"
    return "Noto Sans CJK JP"


class MainWindow(tk.Tk):
    def __init__(self, settings: Settings, services: Services, settings_path: Path | None = None):
        super().__init__()
        self.settings = settings
        self.services = services
        self.settings_path = settings_path
        self.busy = False
        self.token: str | None = None
        self.lfs_available = False
        self.log: list = []
        self.results: dict[str, RepoResult] = {}
        self._queue: queue.Queue = queue.Queue()
        self._ctx: SyncContext | None = None

        self.title("peers Sync")
        self.geometry("640x560")
        self.minsize(480, 420)
        self._build()
        self._startup_checks()
        self.after(100, self._poll)

    # ---------- 画面づくり ----------
    def _build(self) -> None:
        family = _font_family()
        top = ttk.Frame(self, padding=(16, 12))
        top.pack(fill="x")
        ttk.Label(top, text="peers Sync", font=(family, 16, "bold")).pack(side="left")
        ttk.Button(top, text="?", width=3, command=self.show_help).pack(side="right")
        ttk.Button(top, text="設定", command=self.open_settings).pack(side="right", padx=6)

        self.main_button = tk.Button(self, text=SYNC_LABEL, font=(family, 20, "bold"),
                                     padx=24, pady=12, command=self.on_sync_clicked)
        self.main_button.pack(pady=(8, 4))
        self._last_sync = tk.StringVar(value="まだ同期していません")
        ttk.Label(self, textvariable=self._last_sync).pack()
        self._message = tk.StringVar(value="")
        ttk.Label(self, textvariable=self._message, foreground="#b45309",
                  wraplength=580, justify="left").pack(padx=16, pady=4)

        self.tree = ttk.Treeview(self, columns=("name", "status"), show="headings", height=10)
        self.tree.heading("name", text="リポジトリ")
        self.tree.heading("status", text="結果")
        self.tree.column("name", width=200)
        self.tree.column("status", width=380)
        self.tree.pack(fill="both", expand=True, padx=16, pady=4)
        self.tree.bind("<Double-1>", self._on_row_double_click)

        self._log_shown = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="▸ くわしいログ（実行した Git コマンド）",
                        variable=self._log_shown, command=self._toggle_log).pack(anchor="w", padx=16)
        self.log_box = tk.Text(self, height=8, wrap="none", font=("Courier", 10))

    def _startup_checks(self) -> None:
        problem, self.lfs_available = self.services.prerequisites()
        if problem:
            self._message.set(problem)
            self.main_button.config(state="disabled")
            return
        if not self.lfs_available:
            self._message.set(LFS_MISSING)
        stored = self.services.get_token()
        self._set_token(stored[1] if stored else None)

    def _set_token(self, token: str | None) -> None:
        self.token = token
        self.main_button.config(text=SYNC_LABEL if token else LOGIN_LABEL)
        if not token:
            self._message.set("最初に GitHub にログインしてください。")

    # ---------- テストや他の画面から使う小さな窓口 ----------
    def main_button_text(self) -> str:
        return str(self.main_button.cget("text"))

    def message_text(self) -> str:
        return self._message.get()

    def last_sync_text(self) -> str:
        return self._last_sync.get()

    def result_lines(self) -> list[str]:
        return [f"{self.tree.set(i, 'name')}  {self.tree.set(i, 'status')}" for i in self.tree.get_children()]

    def log_text(self) -> str:
        return self.log_box.get("1.0", "end").strip()

    def ask_yes_no(self, question: str) -> bool:
        return messagebox.askyesno("peers Sync", question, parent=self)

    # ---------- ボタン ----------
    def on_sync_clicked(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.main_button.config(state="disabled")
        # 裏の処理の最中に Python の後片付け（GC）が走ると、画面の部品がそのスレッドで壊されて
        # Mac でアプリが落ちることがある。裏の処理が終わるまで止めて、終わったら画面側で片付ける。
        gc.disable()
        if not self.token:
            self._message.set("ブラウザで GitHub にログインしてください…")
            threading.Thread(target=self._login_worker, daemon=True).start()
            return
        self._message.set("同期しています…")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.results.clear()
        self.log = []
        self._ctx = SyncContext(self.settings.machine_name, self.settings.github_login,
                                confirm=self._ask_from_worker, log=self.log)
        threading.Thread(target=self._sync_worker, args=(self._ctx,), daemon=True).start()

    # ---------- 別スレッドで動く処理 ----------
    def _login_worker(self) -> None:
        try:
            _, token = self.services.login()
            login = self.services.make_api(token).current_login()
            self._queue.put(("login", token, login))
        except LoginError as e:
            self._queue.put(("abort", str(e)))
        except Exception:
            self._queue.put(("abort", "GitHub へのログインができませんでした。もう一度お試しください。"))
        finally:
            self._queue.put(("done",))

    def _sync_worker(self, ctx: SyncContext) -> None:
        try:
            api = self.services.make_api(self.token)
            self.services.sync_all(self.settings, api, ctx,
                                   on_result=lambda r: self._queue.put(("result", r)),
                                   lfs_available=self.lfs_available)
            self._queue.put(("finished",))
        except SyncAbort as e:
            self._queue.put(("abort", str(e)))
        except Exception as e:
            self._queue.put(("abort", f"同期できませんでした（{type(e).__name__}）。くわしいログを見てください。"))
        finally:
            self._queue.put(("done",))

    def _ask_from_worker(self, question: str) -> bool:
        """別スレッドから質問を受け取り、画面のスレッドで聞いて、答えを返す。"""
        done = threading.Event()
        answer: list[bool] = []
        self._queue.put(("ask", question, done, answer))
        done.wait()
        return bool(answer and answer[0])

    # ---------- 画面のスレッドで、キューの中身を処理 ----------
    def _poll(self) -> None:
        self.process_queue()
        try:
            self.after(100, self._poll)
        except tk.TclError:
            pass

    def process_queue(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            kind = item[0]
            if kind == "result":
                self.show_result(item[1])
            elif kind == "ask":
                _, question, done, answer = item
                try:
                    answer.append(self.ask_yes_no(question))
                finally:
                    done.set()
            elif kind == "login":
                _, token, login = item
                self.settings.github_login = login
                self._save()
                self._set_token(token)
                self._message.set(f"{login} としてログインしました。「ぜんぶ同期」を押してください。")
            elif kind == "abort":
                self._message.set(item[1])
            elif kind == "finished":
                self._finish_sync()
            elif kind == "done":
                gc.enable()
                gc.collect()
                self.busy = False
                self.main_button.config(state="normal")
                self._render_log()

    def show_result(self, r: RepoResult) -> None:
        self.results[r.full_name] = r
        name = r.full_name.split("/")[-1]
        if not self.tree.exists(r.full_name):
            self.tree.insert("", "end", iid=r.full_name, values=(name, status_text(r)))
        else:
            self.tree.item(r.full_name, values=(name, status_text(r)))

    def _finish_sync(self) -> None:
        self._last_sync.set(f"最後の同期: 今日 {datetime.now():%H:%M}（このパソコン）")
        warnings = [w for r in self.results.values() for w in r.warnings]
        conflicts = [r for r in self.results.values() if r.status is Status.CONFLICT]
        errors = [r for r in self.results.values() if r.status is Status.ERROR]
        lines = []
        if errors:
            lines.append(f"{len(errors)} 個のリポジトリでエラーが起きました。")
        if conflicts:
            lines.append("「⚠️ 競合あり」の行をダブルクリックして、どちらを残すか選んでください。")
        lines += warnings[:5]
        self._message.set("\n".join(lines) if lines else "同期が終わりました。")

    def _render_log(self) -> None:
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        for entry in self.log:
            mark = "" if entry.returncode == 0 else f"  ❌ ({entry.returncode})"
            self.log_box.insert("end", f"$ git {' '.join(entry.args)}  — {entry.explanation}{mark}\n")
            if entry.returncode != 0 and entry.stderr.strip():
                self.log_box.insert("end", "    " + entry.stderr.strip().replace("\n", "\n    ") + "\n")
        self.log_box.config(state="disabled")

    def _toggle_log(self) -> None:
        if self._log_shown.get():
            self.log_box.pack(fill="both", expand=False, padx=16, pady=(0, 12))
        else:
            self.log_box.pack_forget()

    # ---------- ほかの画面 ----------
    def _on_row_double_click(self, _event) -> None:
        sel = self.tree.focus()
        r = self.results.get(sel)
        if r and r.status is Status.CONFLICT and not self.busy:
            from .conflict_dialog import ConflictDialog
            ctx = SyncContext(self.settings.machine_name, self.settings.github_login,
                              confirm=self.ask_yes_no, log=self.log)
            ConflictDialog(self, r, ctx, on_done=self.show_result, lfs_available=self.lfs_available)

    def open_settings(self) -> None:
        from .settings_dialog import SettingsDialog
        SettingsDialog(self)

    def show_help(self) -> None:
        messagebox.showinfo("peers Sync の使い方", HELP_TEXT, parent=self)

    def _save(self) -> None:
        save_settings(self.settings, self.settings_path)
