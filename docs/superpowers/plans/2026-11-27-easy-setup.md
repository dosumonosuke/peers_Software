# 初期設定を減らす 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ダウンロード → 開く →（必要なら）自動インストール → ログインの 4 ステップで使い始められるようにする。

**Architecture:**
- 新しいモジュール `peers_sync/installer.py` が、足りない道具を調べて OS ごとに入れる。
- `engine.py` に、ダウンロード済みのリポジトリを探す機能を足す。
- 画面は、足りないものがあるときに大きなボタンを「自動で入れる」に切りかえる。ログインのあとは、そのまま同期を始める。
- 配布は GitHub Release で行う（Windows は 1 ファイルの exe）。

**Tech Stack:** 既存と同じ（Python 3.11 以上、tkinter、git、PyInstaller、GitHub Actions）＋ `softprops/action-gh-release@v2`

**Spec:** `docs/superpowers/specs/2026-11-27-easy-setup-design.md`（元の設計書：`2026-11-27-all-repo-sync-design.md`）

## Global Constraints
- 元の計画書（`2026-11-27-peers-sync.md`）の Global Constraints は、すべてそのまま守る。
- インストールやダウンロードの失敗で、アプリが落ちてはいけない。失敗したら、日本語のメッセージを返す。
- 自動で探して見つけたフォルダは、`same_remote(origin, clone_url)` が真のときだけ使う。
- 外部コマンドとダウンロードは、すべて関数の引数（`run`, `download`）で差しかえられるようにする。テストでは、本物のネットワークも winget も使わない。

## Review Focus
1. **winget で Git を入れた直後**は、今動いているアプリの PATH に Git がまだない。→ `ensure_tool_path` が `C:\Program Files\Git\cmd` を足すこと（Task 1 のテスト `test_windows_path_gets_git_cmd_dir`）
2. **探す場所に、同じ名前の別のリポジトリがある**。→ 使わずに、通常のクローンに進むこと（Task 2 のテスト `test_discovery_ignores_other_remote`）
3. **探す場所が存在しない、または読めない**。→ 何も起きずに続けること（Task 2 のテスト `test_discovery_missing_roots_are_ignored`）
4. **インストールが途中で失敗した**（winget が 0 以外で終わる、ダウンロードに失敗）。→ 日本語のメッセージが出て、ボタンが元に戻ること（Task 3 のテスト `test_install_failure_shows_message_and_reenables`）
5. **ログインに失敗した**。→ 自動の同期を始めないこと（既存のテスト `test_not_logged_in_shows_login_prompt` で確認できている）

---

### Task 1: 足りない道具を調べて入れる（installer.py）

**Files:**
- Create: `peers_sync/installer.py`, `tests/test_installer.py`
- Modify: `peers_sync/engine.py`（`ensure_tool_path` に Windows の Git の場所と、アプリ専用の bin フォルダを足す）

**Interfaces:**
- Produces:
  - `def missing_tools(run=subprocess.run) -> list[str]`
    - 足りないものを順番どおりに返す。値は `"git"`, `"git-lfs"`, `"gcm"` のどれか。
    - 確かめ方
      - git：`git --version`
      - git-lfs：`git lfs version`
      - gcm：`git credential-manager --version`
  - `def tool_bin_dir() -> Path`
    - アプリ専用の bin フォルダ（`config_path().parent / "bin"`）
  - `def install_missing(missing: list[str], run=subprocess.run, download=None, platform: str | None = None) -> str`
    - 戻り値は、ユーザーに見せる日本語のメッセージ。
    - 失敗したら `InstallError`（日本語のメッセージを持つ）を投げる。
  - `class InstallError(Exception)`

- [ ] **Step 1: 失敗するテストを書く**（`run` と `download` は偽物）

```python
def test_missing_tools_lists_in_order(): ...
    # 偽の run：git は ok、lfs は失敗、gcm は失敗 → ["git-lfs", "gcm"]

def test_missing_tools_when_git_absent(): ...
    # run が FileNotFoundError を出す → ["git", "git-lfs", "gcm"]

def test_windows_uses_winget_for_everything():
    calls = []
    msg = install_missing(["git", "git-lfs", "gcm"], run=fake(calls, rc=0), platform="win32")
    assert calls[0][:3] == ["winget", "install", "--id"] and "Git.Git" in calls[0]
    assert "--silent" in calls[0] and "--accept-package-agreements" in calls[0]
    assert len(calls) == 1 and "入れました" in msg

def test_windows_without_winget_opens_download_page(): ...
    # winget で FileNotFoundError → webbrowser.open に git-scm.com/download/win を渡し、
    # InstallError のメッセージに「ダウンロードページ」が入る

def test_windows_winget_failure_raises(): ...  # rc=1 → InstallError

def test_mac_missing_git_runs_xcode_select():
    calls = []
    msg = install_missing(["git"], run=fake(calls, rc=0), platform="darwin")
    assert calls == [["xcode-select", "--install"]] and "インストール" in msg

def test_mac_git_lfs_is_downloaded_into_app_bin(tmp_path, monkeypatch):
    # tool_bin_dir を tmp_path に差しかえる
    # download(url) は、git-lfs-*/git-lfs を中に持つ zip のバイト列を返す
    # 最初に GitHub API の releases/latest（JSON）を読み、
    # machine に合う darwin の zip（arm64 / amd64）を選んでいること
    # → tmp_path/"git-lfs" ができて、実行できる（mode & 0o111）

def test_mac_gcm_pkg_is_downloaded_and_opened(tmp_path, monkeypatch): ...
    # latest の JSON から gcm-osx-(arm64|x64)-*.pkg を選ぶ → ダウンロードして保存
    # → run(["open", <pkgのパス>]) が呼ばれる

def test_linux_raises_with_instructions(): ...  # InstallError。メッセージに「apt」が入る

def test_windows_path_gets_git_cmd_dir(monkeypatch, tmp_path): ...
    # sys.platform を win32 に、ProgramFiles を tmp_path にする。tmp_path/Git/cmd を作る
    # ensure_tool_path() のあと、PATH に tmp_path/Git/cmd と tool_bin_dir() が入っていること

def test_mac_path_gets_app_bin_dir(monkeypatch): ...  # darwin のとき tool_bin_dir() が入る
```

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`.venv/bin/python -m pytest tests/test_installer.py -q`
  - 期待：ImportError で FAIL

- [ ] **Step 3: `installer.py` を書き、`engine.ensure_tool_path` を直す**
  - 標準の `download(url) -> bytes` は `urllib` を使い、`github_api._ssl_context()` を流用する。
  - リリースの JSON は次の 2 つから読む。
    - `https://api.github.com/repos/git-lfs/git-lfs/releases/latest`
    - `https://api.github.com/repos/git-ecosystem/git-credential-manager/releases/latest`
  - CPU の判定は `platform.machine()` で行う。`arm64` ならそのまま、それ以外は lfs が `amd64`、gcm が `x64`。
  - PATH の区切りは `os.pathsep` を使う。

- [ ] **Step 4: テストを実行して通ることを確かめる**
  - 実行：`.venv/bin/python -m pytest tests/test_installer.py tests/test_engine.py -q`
  - 期待：全部 PASS

- [ ] **Step 5: コミット**（`feat: 足りない道具を自動で入れる機能を追加`）

---

### Task 2: ダウンロード済みのリポジトリを自動で探す（engine.py）

**Files:**
- Modify: `peers_sync/engine.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `same_remote`（sync.py）、`local_path_for`
- Produces:
  - `def default_search_roots() -> list[Path]`
    - `~/Documents/GitHub`, `~/GitHub`, `~/source/repos`, `~/Desktop`, `~/Documents`, `~/Projects`
  - `def discover_existing(repos: list[RemoteRepo], settings: Settings, roots: list[Path]) -> dict[str, Path]`
  - `sync_all(..., search_roots: list[Path] | None = None)`
    - None のときは `default_search_roots()` を使う。
    - 見つかったものは `settings.repo_paths` に書き足す（保存は画面側で行う）。

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_sync_all_uses_existing_clone_found_in_roots(tmp_path): ...
    # make_remote "x"。tmp/"Documents/GitHub/x" にクローンし、ファイルを 1 つ足す
    # settings.base_dir = tmp/"GitHub"（ここは空）、roots=[tmp/"Documents/GitHub"]
    # → status が PUSHED で、path がそのクローン。settings.repo_paths["me/x"] も同じ
    # → base_dir/x は作られない

def test_discovery_ignores_other_remote(tmp_path): ...
    # roots に "x" という名前の、別のリモートのクローンがある → 使わない。base_dir/x にクローンされる（CLONED）

def test_discovery_missing_roots_are_ignored(tmp_path): ...  # 存在しない root があってもエラーにならない

def test_discovery_skips_repos_already_present_at_default(tmp_path): ...  # base_dir/x があれば探さない
```

- [ ] **Step 2: 失敗することを確かめる**
  - 実行：`.venv/bin/python -m pytest tests/test_discovery.py -q`
  - 期待：FAIL

- [ ] **Step 3: 作る**
  - 探すのは各 root の **1 段下だけ**。
  - フォルダ名は、リポジトリ名と同じもの（大文字と小文字は区別しない）だけを見る。
  - そのフォルダで `git remote get-url origin` を実行して、`same_remote` で比べる。
  - root が読めないときの `OSError` は無視する。

- [ ] **Step 4: 通ることを確かめる**
  - 実行：`.venv/bin/python -m pytest -q`
  - 期待：全部 PASS

- [ ] **Step 5: コミット**（`feat: ダウンロード済みのリポジトリを自動で見つける`）

---

### Task 3: 画面（自動インストールのボタン・ログイン後の自動同期・場所の保存）

**Files:**
- Modify: `peers_sync/ui/main_window.py`, `peers_sync/__main__.py`
- Test: `tests/test_ui_smoke.py`

**Interfaces:**
- Consumes: `missing_tools`, `install_missing`, `InstallError`（Task 1）
- Produces:
  - `Services` に 2 つの項目を足す（どちらも初期値ありで、既存のテストを壊さない）
    - `missing_tools: Callable[[], list[str]] = lambda: []`
    - `install: Callable[[list[str]], str] = lambda m: ""`
  - `INSTALL_LABEL = "🛠  必要なものを自動で入れる"`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_missing_tools_turn_button_into_installer(...):
    # missing_tools=lambda: ["git"]
    # → ボタンの文字が INSTALL_LABEL で、押せる状態（normal）
    # → メッセージに「Git」が入る

def test_install_then_recheck_restores_sync_button(...):
    # 1 回目の missing_tools は ["git-lfs"]、2 回目は []
    # install は "入れました"
    # → on_sync_clicked のあと、ボタンが SYNC_LABEL になる（ログイン済みの場合）

def test_install_failure_shows_message_and_reenables(...):
    # install が InstallError("失敗しました") を出す → メッセージに出る、ボタンが normal

def test_login_success_starts_first_sync_automatically(...):
    # get_token は最初 None。login が成功したら sync_all が呼ばれる（calls == 1）

def test_discovered_paths_are_saved_after_sync(...):
    # 偽の sync_all が settings.repo_paths["me/x"] = tmp/"found" をセットする
    # → 同期のあと、settings_path の JSON に "me/x" が入っている
```

  - 既存の `test_missing_git_shows_install_message` は、prerequisites が問題を返すケースとして、そのまま残す。

- [ ] **Step 2: 失敗することを確かめる**
  - 実行：`xvfb-run -a .venv/bin/python -m pytest tests/test_ui_smoke.py -q`
  - 期待：FAIL

- [ ] **Step 3: 作る**
  - 起動時の順番
    1. `missing_tools()` が空でなければ、ボタンを `INSTALL_LABEL` にして、メッセージで足りないものを日本語で伝える。
    2. 空なら、今までどおり `prerequisites()` → ログインの確認。
  - インストールは裏のスレッドで動かす（gc を止める既存のやり方と同じ）。
  - 終わったら `_startup_checks()` をやり直す。
  - ログインに成功したら `_auto_sync_after_login=True` にして、`done` のときに `on_sync_clicked()` を呼ぶ。
  - 同期の `done` のときに `_save()` を呼ぶ。
  - `__main__.py` の Services に `missing_tools=installer.missing_tools`, `install=installer.install_missing` を渡す。

- [ ] **Step 4: 全部のテストが通ることを確かめる**
  - 実行：`xvfb-run -a .venv/bin/python -m pytest -q`
  - 期待：全部 PASS

- [ ] **Step 5: コミット**（`feat: 自動インストールのボタンと、ログイン後の自動同期`）

---

### Task 4: Releases で配る＋ README

**Files:**
- Modify: `.github/workflows/build.yml`, `README.md`

- [ ] **Step 1: build.yml を書きかえる**
  - Windows：`pyinstaller --noconfirm --onefile --windowed --name peers-Sync --collect-data certifi run_peers_sync.py` で `dist/peers-Sync.exe` を作る。
  - Mac：今のまま `.app` を zip にして、`peers-Sync-mac-<arch>.zip` という名前にする。
  - artifact は今までどおり残す。
  - タグ（`refs/tags/v*`）のときだけ、`softprops/action-gh-release@v2` で、3 つのファイルを同じ Release に置く。
  - `permissions: contents: write` を付ける。

- [ ] **Step 2: README の「1〜3 章」を、新しい 4 ステップに書きかえる**
  - Releases の URL は `https://github.com/dosumonosuke/peers_Software/releases/latest`。

- [ ] **Step 3: 確かめる**
  - YAML が正しく読めること。
  - Linux で `--onefile` のビルドが通ること。

- [ ] **Step 4: コミット → push → タグ `v0.2.0` を push**
  - Release ができて、3 つのファイルが置かれていることを、GitHub API で確かめる。
