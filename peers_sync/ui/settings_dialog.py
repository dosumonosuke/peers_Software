"""設定画面：ログイン、置き場所、同期しないリポジトリ、このパソコンの名前など。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import gcguard


class SettingsDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.app = master
        s = master.settings
        self.title("設定")
        self.geometry("560x640")
        frm = ttk.Frame(self, padding=16)
        frm.pack(fill="both", expand=True)

        # ログイン
        login = ttk.LabelFrame(frm, text="GitHub", padding=8)
        login.pack(fill="x")
        who = f"{s.github_login} としてログイン中" if master.token else "ログインしていません"
        ttk.Label(login, text=who).pack(side="left")
        ttk.Button(login, text="ログアウト", command=self.on_logout).pack(side="right")

        # 置き場所・パソコン名
        ttk.Label(frm, text="リポジトリを置く場所").pack(anchor="w", pady=(12, 0))
        row = ttk.Frame(frm)
        row.pack(fill="x")
        self.base_dir = tk.StringVar(value=str(s.base_dir))
        ttk.Entry(row, textvariable=self.base_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="選ぶ…", command=self.pick_dir).pack(side="left", padx=4)
        ttk.Label(frm, text="このパソコンの名前（コミットのメモに入ります）").pack(anchor="w", pady=(12, 0))
        self.machine = tk.StringVar(value=s.machine_name)
        ttk.Entry(frm, textvariable=self.machine).pack(fill="x")

        # 同期しないリポジトリ
        ttk.Label(frm, text="同期するリポジトリ（チェックを外すと同期しません）").pack(anchor="w", pady=(12, 0))
        self.repo_frame = ttk.Frame(frm)
        self.repo_frame.pack(fill="x")
        self.repo_vars: dict[str, tk.BooleanVar] = {}
        ttk.Label(self.repo_frame, text="一覧を読み込んでいます…").pack(anchor="w")
        # 一覧の取得は裏で行い、画面への反映は画面のスレッドで行う（Mac で落ちないように）
        self._loaded: queue.Queue = queue.Queue()
        self._gc_paused = True
        gcguard.pause()  # 一覧を読み込むあいだは後片付けを止める（Mac で落ちないように）
        threading.Thread(target=self._load_repos, daemon=True).start()
        self.after(100, self._check_loaded)

        # 追加のリポジトリ・場所の登録
        ttk.Label(frm, text="自分以外のリポジトリ（1 行に 1 つ、例：owner/name）").pack(anchor="w", pady=(12, 0))
        self.extra = tk.Text(frm, height=3)
        self.extra.insert("1.0", "\n".join(s.extra_repos))
        self.extra.pack(fill="x")
        ttk.Label(frm, text="すでにクローンしてある場所（1 行に 1 つ、例：owner/name = C:\\work\\name）").pack(anchor="w", pady=(12, 0))
        self.paths = tk.Text(frm, height=3)
        self.paths.insert("1.0", "\n".join(f"{k} = {v}" for k, v in s.repo_paths.items()))
        self.paths.pack(fill="x")

        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(16, 0))
        ttk.Button(bar, text="保存", command=self.on_save).pack(side="right")
        ttk.Button(bar, text="閉じる", command=self.destroy).pack(side="right", padx=8)

    def _load_repos(self) -> None:
        names: list[str] = []
        if self.app.token:
            try:
                names = [r.full_name for r in self.app.services.make_api(self.app.token).list_owned_repos()]
            except Exception:
                names = []
        self._loaded.put(sorted(set(names) | set(self.app.settings.excluded)))

    def _resume_gc(self) -> None:
        if getattr(self, "_gc_paused", False):
            self._gc_paused = False
            gcguard.resume()

    def destroy(self) -> None:
        # 一覧が届く前に閉じても、止めた後片付けを必ず元に戻す
        self._resume_gc()
        super().destroy()

    def _check_loaded(self) -> None:
        try:
            names = self._loaded.get_nowait()
        except queue.Empty:
            try:
                self.after(100, self._check_loaded)
            except tk.TclError:
                pass
            return
        self._resume_gc()
        self._show_repos(names)

    def _show_repos(self, names: list[str]) -> None:
        for child in self.repo_frame.winfo_children():
            child.destroy()
        if not names:
            ttk.Label(self.repo_frame, text="（ログインすると一覧が出ます）").pack(anchor="w")
        for name in names:
            var = tk.BooleanVar(value=name not in self.app.settings.excluded)
            self.repo_vars[name] = var
            ttk.Checkbutton(self.repo_frame, text=name, variable=var).pack(anchor="w")

    def pick_dir(self) -> None:
        chosen = filedialog.askdirectory(parent=self, initialdir=self.base_dir.get())
        if chosen:
            self.base_dir.set(chosen)

    def on_logout(self) -> None:
        self.app.services.logout()
        self.app.settings.github_login = None
        self.app._save()
        self.app._set_token(None)
        self.destroy()

    def on_save(self) -> None:
        s = self.app.settings
        s.base_dir = Path(self.base_dir.get().strip() or s.base_dir)
        s.machine_name = self.machine.get().strip() or s.machine_name
        if self.repo_vars:
            s.excluded = {n for n, v in self.repo_vars.items() if not v.get()}
        s.extra_repos = [l.strip() for l in self.extra.get("1.0", "end").splitlines() if "/" in l]
        paths = {}
        for line in self.paths.get("1.0", "end").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() and v.strip():
                    paths[k.strip()] = Path(v.strip())
        s.repo_paths = paths
        self.app._save()
        messagebox.showinfo("peers Sync", "設定を保存しました。", parent=self)
        self.destroy()
