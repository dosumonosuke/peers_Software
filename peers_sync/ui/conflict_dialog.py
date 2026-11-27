"""競合したファイルごとに「このパソコンの版」と「GitHub の版」を並べて、残すほうを選ぶ画面。

Git の操作（フェッチ・合体・送信）は時間がかかるので裏のスレッドで行い、画面が固まらないようにする。
"""
from __future__ import annotations

import gc
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from ..conflicts import cancel_resolution, finish_resolution, start_resolution
from ..models import RepoResult, SyncContext


class ConflictDialog(tk.Toplevel):
    def __init__(self, master, result: RepoResult, ctx: SyncContext,
                 on_done: Callable[[RepoResult], None], lfs_available: bool = True,
                 start=start_resolution, finish=finish_resolution, cancel=cancel_resolution):
        super().__init__(master)
        self.result, self.ctx, self.on_done = result, ctx, on_done
        self.lfs_available = lfs_available
        self._start, self._finish_fn, self._cancel = start, finish, cancel
        self.busy = False
        self.choices: dict[str, tk.StringVar] = {}
        self._queue: queue.Queue = queue.Queue()
        self.title(f"競合の確認 — {result.full_name.split('/')[-1]}")
        self.geometry("820x560")
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self._status = ttk.Label(self, padding=12, text="GitHub の版を取ってきています…")
        self._status.pack(fill="x")
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self._run(lambda: ("files", self._start(result.path, ctx, lfs_available)))
        self.after(100, self._poll)

    # ---------- 裏で動かす ----------
    def _run(self, job: Callable[[], tuple]) -> None:
        self.busy = True
        gc.disable()  # 裏の処理中に画面の部品が別スレッドで片付けられると、Mac で落ちるため

        def worker():
            try:
                self._queue.put(job())
            except Exception as e:
                self._queue.put(("error", f"うまくいきませんでした：{e}"))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            item = self._queue.get_nowait()
        except queue.Empty:
            try:
                self.after(100, self._poll)
            except tk.TclError:
                pass
            return
        self.busy = False
        gc.enable()
        gc.collect()
        kind, value = item
        if kind == "files":
            if value:
                self._build(value)
            else:
                self._finish({})
                return
        elif kind == "finished":
            self.on_done(value)
            if value.error:
                messagebox.showerror("peers Sync", value.error, parent=self)
            self.destroy()
            return
        else:
            messagebox.showerror("peers Sync", value, parent=self)
            self.destroy()
            return
        self.after(100, self._poll)

    # ---------- 画面 ----------
    def _build(self, files) -> None:
        self._status.config(wraplength=780, text=(
            "同じファイルが、このパソコンと GitHub の両方で変えられています。"
            "残すほうを選んでください。選ばなかったほうも「(〜 の版)」という名前のコピーで残ります。"
        ))
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12)
        for f in files:
            page = ttk.Frame(nb, padding=8)
            nb.add(page, text=f.path)
            var = tk.StringVar(value="mine")
            self.choices[f.path] = var
            for col, (side, label, text) in enumerate([
                ("mine", "このパソコンの版を残す", f.mine or ""),
                ("theirs", "GitHub の版を残す", f.theirs or ""),
            ]):
                page.columnconfigure(col, weight=1)
                ttk.Radiobutton(page, text=label, value=side, variable=var).grid(row=0, column=col, sticky="w")
                box = tk.Text(page, wrap="word", height=20)
                box.insert("1.0", text)
                box.config(state="disabled")
                box.grid(row=1, column=col, sticky="nsew", padx=4)
            page.rowconfigure(1, weight=1)
        bar = ttk.Frame(self, padding=12)
        bar.pack(fill="x")
        self._decide_btn = ttk.Button(bar, text="決定", command=self.on_decide)
        self._decide_btn.pack(side="right")
        ttk.Button(bar, text="キャンセル", command=self.on_cancel).pack(side="right", padx=8)

    def on_decide(self) -> None:
        if self.busy:
            return
        self._finish({path: var.get() for path, var in self.choices.items()})

    def _finish(self, choices) -> None:
        self._status.config(text="GitHub に送っています…")
        r = self.result
        self._run(lambda: ("finished", self._finish_fn(r.path, choices, self.ctx, full_name=r.full_name)))

    def on_cancel(self) -> None:
        if self.busy:
            return  # 途中でやめると合体が中途半端になるので、終わるまで待ってもらう
        self._cancel(self.result.path)
        self.destroy()
