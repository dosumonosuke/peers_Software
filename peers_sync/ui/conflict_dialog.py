"""競合したファイルごとに「このパソコンの版」と「GitHub の版」を並べて、残すほうを選ぶ画面。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from ..conflicts import cancel_resolution, finish_resolution, start_resolution
from ..models import RepoResult, SyncContext


class ConflictDialog(tk.Toplevel):
    def __init__(self, master, result: RepoResult, ctx: SyncContext,
                 on_done: Callable[[RepoResult], None]):
        super().__init__(master)
        self.result, self.ctx, self.on_done = result, ctx, on_done
        self.title(f"競合の確認 — {result.full_name.split('/')[-1]}")
        self.geometry("820x560")
        self.choices: dict[str, tk.StringVar] = {}
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        try:
            files = start_resolution(result.path, ctx)
        except Exception as e:
            messagebox.showerror("peers Sync", f"競合を開けませんでした：{e}", parent=self)
            self.destroy()
            return
        if not files:
            self._finish({})
            return
        self._build(files)

    def _build(self, files) -> None:
        ttk.Label(self, padding=12, wraplength=780, text=(
            "同じファイルが、このパソコンと GitHub の両方で変えられています。"
            "残すほうを選んでください。選ばなかったほうも「(〜 の版)」という名前のコピーで残ります。"
        )).pack(fill="x")
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
        ttk.Button(bar, text="決定", command=self.on_decide).pack(side="right")
        ttk.Button(bar, text="キャンセル", command=self.on_cancel).pack(side="right", padx=8)

    def on_decide(self) -> None:
        self._finish({path: var.get() for path, var in self.choices.items()})

    def _finish(self, choices) -> None:
        r = finish_resolution(self.result.path, choices, self.ctx, full_name=self.result.full_name)
        self.on_done(r)
        if r.error:
            messagebox.showerror("peers Sync", r.error, parent=self)
        self.destroy()

    def on_cancel(self) -> None:
        cancel_resolution(self.result.path)
        self.destroy()
