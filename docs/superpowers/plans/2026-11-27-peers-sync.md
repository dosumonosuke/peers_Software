# peers Sync 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ボタン 1 つで、GitHub アカウントの全リポジトリを、このパソコンと同期するデスクトップアプリを作る。

**Architecture:**
- 中身は Python のパッケージ `peers_sync/` にまとめる。
- Git の操作は、パソコンに入っている `git` コマンドを `subprocess` で呼び出して行う。
- 同期のしくみ（`sync.py`・`conflicts.py`・`engine.py`）は画面と切り離す。そうすることで、テストを手元の練習用リポジトリ（`file://` のリモート）だけで回せる。
- 画面は tkinter で作る。同期は別スレッドで動かし、結果をキューで画面に渡す。
- ログインは Git Credential Manager（GCM）に任せる。保存されたトークンは `git credential fill` で取り出し、GitHub API にも使う。

**Tech Stack:**
- Python 3.11 以上（標準ライブラリだけで動かす。実行時の追加ライブラリはなし）
- tkinter（画面）
- git / git-lfs コマンド
- pytest（テスト）
- PyInstaller（配布ファイル作り）
- GitHub Actions（テストの自動実行と配布ファイル作り）

**Spec:** `docs/superpowers/specs/2026-11-27-all-repo-sync-design.md`

## Global Constraints

- 次の 3 つは、コードのどこでも絶対に使わない。
  - `git push --force` / `--force-with-lease` / `+refspec`
  - `git reset --hard`
  - `git checkout -- .` / `git clean`
- 合体には必ず `git merge` を使う。`git rebase` / `git pull --rebase` は使わない。
- ユーザーに見せる文章はすべて日本語にする。Git が出す英語のエラー文は「くわしいログ」の中だけに出す。
- Git のファイル一覧は必ず `-z`（NUL 区切り）で読む。日本語やスペースの入ったファイル名に対応するため。
- Git を呼ぶときは、環境変数に `GIT_TERMINAL_PROMPT=0` を必ず付ける。Windows では `creationflags=subprocess.CREATE_NO_WINDOW` も付ける。
- コミットメッセージの書式は `[peers Sync] {machine} から同期 ({YYYY-MM-DD HH:MM}) — {n} 個のファイル` に固定する。
- コピーを残すときのファイル名は `{名前} ({出どころ} の版){拡張子}` とする。
  - 出どころは、コピーの中身がこのパソコンの版なら `machine_name`、GitHub の版なら `GitHub`。
  - 同じ名前がすでにあれば `{名前} ({出どころ} の版 2){拡張子}`、`… 3` と番号を増やしていく。
- 数値はすべて `peers_sync/safety.py` の定数にまとめる。値は次のとおり。
  - LFS にするサイズ：50MiB
  - 送れないサイズ：2GiB
  - LFS なしで送れる上限：100MB
  - LFS の送信量で確認を出す量：1GiB
  - 削除の確認を出す数：20 個
- 1 つのリポジトリで例外が起きても、`sync_all` は止まらず次のリポジトリへ進む。
- 作業用ブランチは `claude/dreamy-planck-okuevw`。コミットはタスクごとに行う。

## Review Focus

1. **中身が空のリポジトリ**（GitHub 上でコミット 0 件。または手元に初めてのコミットがまだない）。クローンも初めてのプッシュも、エラーにならずに通ること。→ Task 6 のテスト `test_empty_remote_clone_then_first_push`
2. **日本語やスペースの入ったファイル名**（例：`メモ/日本語 ノート.md`）。普通に同期されること。→ Task 5 のテスト `test_japanese_and_space_filenames`
3. **片方で消し、もう片方で変えたファイルの競合**。変えたほうのファイルが残ること。→ Task 6 のテスト `test_delete_vs_modify_keeps_modified`
4. **新しいパソコンで Git の名前とメールが未設定**。それでもコミットが成功すること（GitHub の noreply アドレスを使う）。→ Task 5 のテスト `test_commit_without_git_identity`
5. **置き場所に、同じ名前の別のフォルダがすでにある**。中身を上書きせずに ERROR になること。→ Task 6 のテスト `test_clone_target_exists_not_repo`

---

## ファイル構成

```
pyproject.toml                 パッケージ情報・pytest の設定
run_peers_sync.py              PyInstaller 用の入口（main() を呼ぶだけ）
peers_sync/
  __init__.py
  __main__.py                  python -m peers_sync
  models.py                    RemoteRepo, Status, RepoResult, SyncContext
  gitrunner.py                 GitRunner, GitError, CommandLog, explain_command
  safety.py                    定数, is_secret_path, needs_lfs, is_too_large
  config.py                    Settings, load_settings, save_settings
  credentials.py               get_stored_token, login, logout
  github_api.py                GitHubClient, GitHubError
  changes.py                   scan_changes, commit_local_changes（手元の変更→コミット）
  conflicts.py                 競合の分類・両方残す・解決のやり直し
  sync.py                      sync_repo（クローン／フェッチ／マージ／プッシュ）
  engine.py                    sync_all, local_path_for, check_prerequisites
  messages.py                  status_text, explain_error
  ui/
    __init__.py
    main_window.py             MainWindow
    settings_dialog.py         SettingsDialog
    conflict_dialog.py         ConflictDialog
tests/
  conftest.py                  Git の設定を一時的なものに切りかえる autouse fixture
  helpers.py                   make_remote, clone, write, git
  test_*.py
.github/workflows/test.yml     ubuntu / windows / macos で pytest
.github/workflows/build.yml    Windows 用 .exe と Mac 用 .app を作る
README.md                      日本語の使い方
```

## 共通の型（Task 1 で作り、これ以降のすべてのタスクで使う）

`peers_sync/models.py`:

```python
@dataclass(frozen=True)
class RemoteRepo:
    full_name: str      # "dosumonosuke/muscle_app"
    name: str           # "muscle_app"
    clone_url: str      # https://... または file://...（テスト用）
    private: bool
    archived: bool

class Status(Enum):
    CLONED = "cloned"; PUSHED = "pushed"; PULLED = "pulled"
    PUSHED_AND_PULLED = "pushed_and_pulled"; UP_TO_DATE = "up_to_date"
    CONFLICT = "conflict"; SKIPPED = "skipped"; ERROR = "error"

@dataclass
class RepoResult:
    full_name: str
    path: Path
    status: Status
    committed_files: int = 0
    pulled_commits: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None            # 日本語の説明

@dataclass
class SyncContext:
    machine_name: str
    github_login: str | None
    confirm: Callable[[str], bool]      # 日本語の質問 → はい/いいえ
    now: Callable[[], datetime] = datetime.now
    log: list["CommandLog"] = field(default_factory=list)
```

---

### Task 1: 土台と GitRunner

**Files:**
- Create: `pyproject.toml`, `peers_sync/__init__.py`, `peers_sync/models.py`, `peers_sync/gitrunner.py`, `tests/conftest.py`, `tests/helpers.py`, `tests/test_gitrunner.py`, `.github/workflows/test.yml`, `.gitignore`

**Interfaces:**
- Produces:
  - `class GitError(Exception)`（属性：`args_list: list[str]`, `returncode: int`, `stderr: str`）
  - `@dataclass CommandLog(cwd: Path, args: list[str], explanation: str, returncode: int, stdout: str, stderr: str)`
  - `def explain_command(args: list[str]) -> str`
  - `class GitRunner(cwd: Path, log: list[CommandLog] | None = None, extra_config: dict[str, str] | None = None)`
    - `.run(*args: str, check: bool = True, input: str | None = None) -> subprocess.CompletedProcess[str]`
    - `.run_bytes(*args: str) -> bytes`
  - `tests/helpers.py` の次の関数
    - `git(path: Path, *args: str) -> str`
    - `make_remote(tmp: Path, name: str = "origin", initial: bool = True) -> Path`（bare リポジトリ。`initial=True` のときは `main` に `README.md` を 1 コミット入れる）
    - `clone(remote: Path, dest: Path) -> Path`
    - `write(path: Path, content: str | bytes) -> Path`

- [ ] **Step 1: `pyproject.toml` を作る**
  - `requires-python = ">=3.11"`、依存なし。
  - `[project.optional-dependencies] dev = ["pytest", "pyinstaller"]`。
  - `[tool.pytest.ini_options] testpaths = ["tests"]`。
  - `[project.scripts] peers-sync = "peers_sync.__main__:main"`。
  - `.gitignore` に `__pycache__/`, `build/`, `dist/`, `*.spec`, `.pytest_cache/` を入れる。

- [ ] **Step 2: `tests/conftest.py` と `tests/helpers.py` を作る**
  - autouse fixture `isolated_git(tmp_path_factory, monkeypatch)` が、一時的な global config ファイルを作る。中身は次のとおり。
    - `user.name=Test`
    - `user.email=test@example.com`
    - `init.defaultBranch=main`
    - `protocol.file.allow=always`
    - `git lfs install --skip-repo` で書き込まれる filter 設定
  - そのうえで `GIT_CONFIG_GLOBAL` にそのファイルを、`GIT_CONFIG_NOSYSTEM=1` を設定する。

- [ ] **Step 3: 失敗するテストを書く（`tests/test_gitrunner.py`）**

```python
def test_run_logs_command_with_japanese_explanation(tmp_path):
    log = []
    GitRunner(tmp_path, log=log).run("init")
    assert log[0].args == ["init"]
    assert log[0].returncode == 0
    assert log[0].explanation != ""

def test_run_raises_giterror_with_stderr(tmp_path):
    with pytest.raises(GitError) as e:
        GitRunner(tmp_path).run("rev-parse", "HEAD")
    assert e.value.returncode != 0 and e.value.stderr

def test_check_false_does_not_raise(tmp_path):
    assert GitRunner(tmp_path).run("rev-parse", "HEAD", check=False).returncode != 0

def test_extra_config_is_passed(tmp_path):
    out = GitRunner(tmp_path, extra_config={"user.name": "X"}).run("config", "user.name").stdout
    assert out.strip() == "X"

def test_explain_command():
    assert "ダウンロード" in explain_command(["clone", "u", "d"])
    assert "セーブ" in explain_command(["commit", "-m", "x"])
    assert "送" in explain_command(["push", "origin", "main"])
    assert "確認" in explain_command(["fetch", "origin"])
    assert "合体" in explain_command(["merge", "origin/main"])
    assert explain_command(["unknown-sub"]) != ""
```

- [ ] **Step 4: 実行して失敗することを確かめる**
  - 実行：`python -m pytest tests/test_gitrunner.py -v`
  - 期待：`ModuleNotFoundError: peers_sync` で FAIL

- [ ] **Step 5: `models.py`（上の「共通の型」そのまま）と `gitrunner.py` を書く**
  - `run` は `subprocess.run(["git", *(-c k=v ...), *args], cwd=..., capture_output=True, text=True, encoding="utf-8", errors="replace", env=...)` で呼ぶ。
  - env には `GIT_TERMINAL_PROMPT=0` を足す。Windows では `CREATE_NO_WINDOW` も付ける。
  - コマンドを実行するたびに `CommandLog` を 1 つ追加する。
  - `explain_command` は、サブコマンド名から日本語の説明を引く辞書にする。対象：clone / add / commit / fetch / merge / push / status / lfs / diff / rev-list / symbolic-ref / checkout / config / credential。辞書にないものは「Git の操作」と返す。

- [ ] **Step 6: テストを実行して通ることを確かめる**
  - 実行：`python -m pytest tests/test_gitrunner.py -v`
  - 期待：6 passed

- [ ] **Step 7: `.github/workflows/test.yml` を作る**
  - push と pull_request のときに動かす。
  - `matrix.os: [ubuntu-latest, windows-latest, macos-latest]`、Python 3.12。
  - 手順：`git lfs install` → `pip install -e .[dev]` → `python -m pytest -v`。

- [ ] **Step 8: コミット**

```bash
git add pyproject.toml .gitignore peers_sync tests .github/workflows/test.yml
git commit -m "feat: プロジェクトの土台と GitRunner を追加"
```

---

### Task 2: 安全ルール（safety.py）

**Files:**
- Create: `peers_sync/safety.py`, `tests/test_safety.py`

**Interfaces:**
- Produces:
  - 定数
    - `LFS_SIZE_THRESHOLD = 50 * 1024**2`
    - `MAX_FILE_SIZE = 2 * 1024**3`
    - `NON_LFS_HARD_LIMIT = 100 * 1000**2`
    - `LFS_BATCH_WARN_BYTES = 1024**3`
    - `DELETE_CONFIRM_THRESHOLD = 20`
    - `VIDEO_EXTENSIONS: frozenset[str]`（`.mp4 .mov .avi .mkv .webm .m4v .wmv .flv .mts .m2ts`）
  - `def is_secret_path(path: str) -> bool`
  - `def needs_lfs(path: str, size: int) -> bool`
  - `def is_too_large(size: int) -> bool`

- [ ] **Step 1: 失敗するテストを書く**

```python
@pytest.mark.parametrize("p", [".env", "app/.env", ".env.local", "server.pem",
    "my.key", "id_rsa", ".ssh/id_ed25519", "cert.p12", "cert.pfx", "credentials.json", "A/.ENV"])
def test_secret_paths(p): assert is_secret_path(p)

@pytest.mark.parametrize("p", [".env.example", ".env.sample", ".env.template",
    "README.md", "keyboard.py", "notes/key.md", "environment.md"])
def test_not_secret_paths(p): assert not is_secret_path(p)

def test_needs_lfs():
    assert needs_lfs("動画/clip.MP4", 10)
    assert needs_lfs("data.bin", 50 * 1024**2 + 1)
    assert not needs_lfs("photo.png", 5 * 1024**2)
    assert not needs_lfs("data.bin", 50 * 1024**2)

def test_is_too_large():
    assert is_too_large(2 * 1024**3 + 1)
    assert not is_too_large(2 * 1024**3)
```

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`python -m pytest tests/test_safety.py -v`
  - 期待：ImportError で FAIL

- [ ] **Step 3: `safety.py` を書く**
  - 判定は、パスの最後の部分（ファイル名）を小文字にしてから `fnmatch` で行う。
  - 秘密ファイルとみなすパターン：`.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa`, `id_rsa.*`, `id_ed25519`, `id_ed25519.*`, `*.p12`, `*.pfx`, `credentials.json`
  - ただし `.env.example` / `.env.sample` / `.env.template` は秘密ファイルとみなさない。
  - 拡張子も小文字にしてから比べる。

- [ ] **Step 4: テストを実行して通ることを確かめる**
  - 実行：`python -m pytest tests/test_safety.py -v`
  - 期待：全部 PASS

- [ ] **Step 5: コミット**

```bash
git add peers_sync/safety.py tests/test_safety.py
git commit -m "feat: 秘密ファイル・大きいファイルの判定ルールを追加"
```

---

### Task 3: 設定の保存（config.py）

**Files:**
- Create: `peers_sync/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `Settings` のデータ型

    ```python
    @dataclass
    class Settings:
        base_dir: Path
        machine_name: str
        excluded: set[str]           # full_name の集合
        extra_repos: list[str]       # 自分以外のリポジトリの full_name
        repo_paths: dict[str, Path]  # full_name → 手元の場所（初期値の場所と違うときだけ）
        github_login: str | None
    ```

  - `def default_settings() -> Settings`
    - `base_dir = Path.home() / "GitHub"`
    - `machine_name = socket.gethostname()`
    - ほかは空、`github_login` は None
  - `def config_path() -> Path`
    - Windows：`%APPDATA%/peers-sync/settings.json`
    - Mac：`~/Library/Application Support/peers-sync/settings.json`
    - その他：`~/.config/peers-sync/settings.json`
  - `def load_settings(path: Path | None = None) -> Settings`
  - `def save_settings(s: Settings, path: Path | None = None) -> None`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    s = default_settings(); s.excluded = {"me/x"}; s.repo_paths = {"me/y": tmp_path / "y"}
    save_settings(s, p)
    assert load_settings(p) == s

def test_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "none.json") == default_settings()

def test_broken_file_is_backed_up_and_defaults_used(tmp_path):
    p = tmp_path / "s.json"; p.write_text("{broken", encoding="utf-8")
    assert load_settings(p) == default_settings()
    assert (tmp_path / "s.json.broken").exists()

def test_unicode_paths_roundtrip(tmp_path):
    p = tmp_path / "s.json"; s = default_settings(); s.base_dir = tmp_path / "作業"
    save_settings(s, p); assert load_settings(p).base_dir == tmp_path / "作業"
```

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`python -m pytest tests/test_config.py -v`
  - 期待：FAIL

- [ ] **Step 3: `config.py` を書く**
  - 保存は JSON（UTF-8、`ensure_ascii=False`）。
  - 保存先が壊れないように、まず `tmp` ファイルに書いてから `os.replace` で入れかえる。
  - 設定ファイルの中に項目が足りないときは、初期値で補う。

- [ ] **Step 4: テストを実行して通ることを確かめる**
  - 実行：`python -m pytest tests/test_config.py -v`
  - 期待：4 passed

- [ ] **Step 5: コミット**

```bash
git add peers_sync/config.py tests/test_config.py
git commit -m "feat: 設定の読み書きを追加"
```

---

### Task 4: ログインと GitHub API（credentials.py / github_api.py）

**Files:**
- Create: `peers_sync/credentials.py`, `peers_sync/github_api.py`, `tests/test_credentials.py`, `tests/test_github_api.py`

**Interfaces:**
- Consumes: `RemoteRepo`（Task 1）
- Produces:
  - `Runner = Callable[..., subprocess.CompletedProcess[str]]`（`subprocess.run` と同じ形）
  - `def get_stored_token(run: Runner = subprocess.run) -> tuple[str, str] | None`
    - 対話なしで動く。env に `GIT_TERMINAL_PROMPT=0`、`GCM_INTERACTIVE=never` を付ける。
    - 失敗したり password が空だったりしたら None を返す。
  - `def login(run: Runner = subprocess.run) -> tuple[str, str]`
    - 対話ありで `git credential fill` を呼ぶ。GCM がブラウザを開く。
    - 成功したら `git credential approve` で保存する。失敗したら `LoginError`。
  - `def logout(run: Runner = subprocess.run) -> None`
    - `git credential reject` を呼ぶ。
  - `class LoginError(Exception)`
  - `class GitHubError(Exception)`（属性：`status: int`）
  - `HttpGet = Callable[[str, dict[str, str]], tuple[int, Any, dict[str, str]]]`
  - `class GitHubClient(token: str, http_get: HttpGet | None = None)`
    - `.current_login() -> str`
    - `.list_owned_repos() -> list[RemoteRepo]`
    - `.get_repo(full_name: str) -> RemoteRepo`

- [ ] **Step 1: 失敗するテストを書く**

```python
# test_credentials.py — 偽の run を渡して、引数・入力・env を確かめる
def test_get_stored_token_parses_output():
    calls = []
    def fake(args, **kw):
        calls.append((args, kw))
        return subprocess.CompletedProcess(args, 0, "protocol=https\nhost=github.com\nusername=me\npassword=tok\n", "")
    assert get_stored_token(fake) == ("me", "tok")
    args, kw = calls[0]
    assert args[:3] == ["git", "credential", "fill"]
    assert kw["input"] == "protocol=https\nhost=github.com\n\n"
    assert kw["env"]["GCM_INTERACTIVE"] == "never" and kw["env"]["GIT_TERMINAL_PROMPT"] == "0"

def test_get_stored_token_none_on_failure():
    fake = lambda args, **kw: subprocess.CompletedProcess(args, 128, "", "fatal")
    assert get_stored_token(fake) is None

def test_login_approves_after_fill():
    seen = []
    def fake(args, **kw):
        seen.append(args[2])
        return subprocess.CompletedProcess(args, 0, "username=me\npassword=tok\n", "")
    assert login(fake) == ("me", "tok") and seen == ["fill", "approve"]

# test_github_api.py
def test_list_owned_repos_paginates_and_drops_archived():
    pages = {
      "https://api.github.com/user/repos?affiliation=owner&per_page=100":
        (200, [repo_json("me/a"), repo_json("me/old", archived=True)],
         {"Link": '<https://api.github.com/user/repos?page=2>; rel="next"'}),
      "https://api.github.com/user/repos?page=2": (200, [repo_json("me/b", private=True)], {}),
    }
    c = GitHubClient("tok", http_get=lambda url, h: pages[url])
    assert [r.full_name for r in c.list_owned_repos()] == ["me/a", "me/b"]

def test_sends_bearer_token():
    seen = {}
    def get(url, h): seen.update(h); return (200, {"login": "me"}, {})
    GitHubClient("tok", http_get=get).current_login()
    assert seen["Authorization"] == "Bearer tok"

def test_http_error_raises():
    c = GitHubClient("tok", http_get=lambda u, h: (401, {"message": "Bad"}, {}))
    with pytest.raises(GitHubError) as e: c.current_login()
    assert e.value.status == 401
```

  - `repo_json(full_name, private=False, archived=False)` はテスト用の小さな関数で、`full_name`, `name`, `clone_url`, `private`, `archived` を持つ辞書を返す。

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`python -m pytest tests/test_credentials.py tests/test_github_api.py -v`
  - 期待：FAIL

- [ ] **Step 3: 2 つのファイルを書く**
  - 標準の `http_get` は `urllib.request` を使う。ヘッダーは次の 3 つ。
    - `Accept: application/vnd.github+json`
    - `X-GitHub-Api-Version: 2022-11-28`
    - `User-Agent: peers-sync`
  - 次のページは、返ってきたヘッダーの `Link` の `rel="next"` をたどって読む。
  - `credential` の出力は `key=value` の行を辞書にして読む。

- [ ] **Step 4: テストを実行して通ることを確かめる**
  - 実行：`python -m pytest tests/test_credentials.py tests/test_github_api.py -v`
  - 期待：全部 PASS

- [ ] **Step 5: コミット**

```bash
git add peers_sync/credentials.py peers_sync/github_api.py tests/test_credentials.py tests/test_github_api.py
git commit -m "feat: GitHub ログインとリポジトリ一覧の取得を追加"
```

---

### Task 5: 手元の変更をセーブする（changes.py）

**Files:**
- Create: `peers_sync/changes.py`, `tests/test_changes.py`

**Interfaces:**
- Consumes: `GitRunner`（Task 1）、`safety.*`（Task 2）、`SyncContext`（Task 1）
- Produces:
  - `LocalChanges` のデータ型

    ```python
    @dataclass
    class LocalChanges:
        to_stage: list[str]          # 追加・変更（安全なものだけ）
        deleted: list[str]
        lfs_new: list[str]           # これから LFS に登録する新しいファイル
        lfs_bytes: int
        warnings: list[str]          # 日本語
    ```

  - `def scan_changes(git: GitRunner, repo: Path, lfs_available: bool) -> LocalChanges`
  - `def commit_local_changes(git: GitRunner, repo: Path, ctx: SyncContext, lfs_available: bool) -> tuple[int, list[str]]`
    - 戻り値は（コミットしたファイル数, 警告のリスト）。

- [ ] **Step 1: 失敗するテストを書く**
  - どのテストも、`make_remote` → `clone` で作った作業フォルダで行う。
  - `ctx = SyncContext("PC-A", "me", confirm=lambda q: True, now=lambda: datetime(2026, 11, 27, 14, 32))`

```python
def test_commits_new_and_modified_with_message(work, ctx):
    write(work / "a.md", "x"); write(work / "README.md", "changed")
    n, _ = commit_local_changes(GitRunner(work), work, ctx, True)
    assert n == 2
    assert git(work, "log", "-1", "--format=%s") == "[peers Sync] PC-A から同期 (2026-11-27 14:32) — 2 個のファイル"

def test_nothing_to_commit_returns_zero(work, ctx):
    assert commit_local_changes(GitRunner(work), work, ctx, True) == (0, [])

def test_secret_file_is_not_committed_and_warned(work, ctx):
    write(work / ".env", "TOKEN=1"); write(work / "ok.md", "x")
    n, warns = commit_local_changes(GitRunner(work), work, ctx, True)
    assert n == 1 and ".env" not in git(work, "ls-files") and any(".env" in w for w in warns)

def test_video_goes_to_lfs_by_filename_rule(work, ctx):
    write(work / "動画/clip 1.mp4", b"\x00" * 100)
    commit_local_changes(GitRunner(work), work, ctx, True)
    assert "clip" in (work / ".gitattributes").read_text(encoding="utf-8")
    assert "filter=lfs" in git(work, "check-attr", "filter", "--", "動画/clip 1.mp4")

def test_existing_tracked_video_is_not_moved_to_lfs(work, ctx):
    write(work / "old.mp4", b"\x00" * 10); git(work, "add", "old.mp4"); git(work, "commit", "-m", "old")
    write(work / "old.mp4", b"\x01" * 10)
    commit_local_changes(GitRunner(work), work, ctx, True)
    assert "lfs" not in git(work, "check-attr", "filter", "--", "old.mp4")

def test_lfs_needed_but_unavailable_skips_with_warning(work, ctx):
    write(work / "v.mp4", b"\x00")
    n, warns = commit_local_changes(GitRunner(work), work, ctx, lfs_available=False)
    assert n == 0 and any("Git LFS" in w for w in warns)

def test_many_deletions_ask_and_respect_no(work, ctx):
    for i in range(20): write(work / f"f{i}.txt", "x")
    git(work, "add", "."); git(work, "commit", "-m", "files")
    for i in range(20): (work / f"f{i}.txt").unlink()
    asked = []
    ctx.confirm = lambda q: asked.append(q) or False
    n, _ = commit_local_changes(GitRunner(work), work, ctx, True)
    assert asked and "20" in asked[0] and n == 0
    assert "f0.txt" in git(work, "ls-files")

def test_19_deletions_do_not_ask(work, ctx): ...   # confirm が呼ばれず、19 個がコミットされる

def test_large_lfs_batch_asks_before_commit(work, ctx, monkeypatch):
    monkeypatch.setattr("peers_sync.safety.LFS_BATCH_WARN_BYTES", 10)
    write(work / "v.mp4", b"\x00" * 11)
    ctx.confirm = lambda q: False
    n, warns = commit_local_changes(GitRunner(work), work, ctx, True)
    assert n == 0

def test_too_large_file_skipped(work, ctx, monkeypatch):
    monkeypatch.setattr("peers_sync.safety.MAX_FILE_SIZE", 5)
    write(work / "big.mp4", b"\x00" * 6)
    n, warns = commit_local_changes(GitRunner(work), work, ctx, True)
    assert n == 0 and any("2GB" in w for w in warns)

def test_tracked_non_lfs_file_over_100mb_skipped(work, ctx, monkeypatch): ...
    # NON_LFS_HARD_LIMIT を小さくして、すでに管理されている LFS ではないファイルを大きくする → スキップ＋警告

def test_japanese_and_space_filenames(work, ctx):
    write(work / "メモ/日本語 ノート.md", "こんにちは")
    n, _ = commit_local_changes(GitRunner(work), work, ctx, True)
    assert n == 1 and "日本語 ノート.md" in git(work, "-c", "core.quotepath=false", "ls-files")

def test_commit_without_git_identity(work, ctx, monkeypatch):
    git(work, "config", "--global", "--unset", "user.name"); git(work, "config", "--global", "--unset", "user.email")
    write(work / "a.md", "x")
    n, _ = commit_local_changes(GitRunner(work), work, ctx, True)
    assert n == 1 and git(work, "log", "-1", "--format=%ae") == "me@users.noreply.github.com"
```

  - 注意：`test_large_lfs_batch_asks_before_commit` などで `monkeypatch` が効くように、`changes.py` は値を `from safety import X` で取り込まない。`safety.X` の形で、使うときに毎回参照する。

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`python -m pytest tests/test_changes.py -v`
  - 期待：FAIL

- [ ] **Step 3: `changes.py` を書く**

  **scan_changes**
  - `git status --porcelain=v1 -z --untracked-files=all` を読む。
  - 状態の読み方
    - `R` / `C` の行は、パスが 2 つ続くので、2 つ目も読み飛ばす。
    - X か Y が `D` なら「削除」。
    - `??` なら「新しいファイル」。それ以外は「変更」。
  - 変更・新規のファイルは、次の順番で 1 つずつ振り分ける。
    1. 秘密ファイル → スキップして警告
    2. `is_too_large` → スキップして警告。文言に「2GB」を入れる。
    3. 新しいファイルで `needs_lfs`
       - `lfs_available` なら → `lfs_new` に入れる
       - そうでなければ → スキップして警告。文言に「Git LFS をインストールしてください」を入れる。
    4. すでに管理されているファイルで、LFS ではなく、`NON_LFS_HARD_LIMIT` を超える → スキップして警告
    5. それ以外 → `to_stage` に入れる

  **commit_local_changes**
  1. 削除が `DELETE_CONFIRM_THRESHOLD` 以上なら、`ctx.confirm(f"{repo.name} で {n} 個のファイルが消えています。消したままにしますか？")` で確認する。「いいえ」なら、削除はコミットしない。
  2. `lfs_bytes` が `LFS_BATCH_WARN_BYTES` 以上なら、無料枠についての確認を出す。「いいえ」なら、`lfs_new` は今回は送らない。
  3. `lfs_new` のファイルを 1 つずつ `git lfs track --filename -- <path>` で登録して、`.gitattributes` もステージする。
  4. `git add -A --pathspec-from-file=- --pathspec-file-nul` に、NUL 区切りのパス一覧を stdin で渡す。
  5. ステージされた数を `git diff --cached --name-only -z` で数える。0 なら何もしない。
  6. `git config user.email` が空なら、`extra_config` で一時的に名前とメールを入れる。
     - `user.name = ctx.github_login or ctx.machine_name`
     - `user.email = f"{login}@users.noreply.github.com"`（ログインしていなければ `peers-sync@localhost`）
  7. コミットする。

- [ ] **Step 4: テストを実行して通ることを確かめる**
  - 実行：`python -m pytest tests/test_changes.py -v`
  - 期待：全部 PASS

- [ ] **Step 5: コミット**

```bash
git add peers_sync/changes.py tests/test_changes.py
git commit -m "feat: 手元の変更を安全にセーブする処理を追加"
```

---

### Task 6: 1 つのリポジトリを同期する（sync.py と conflicts.py の自動解決部分）

**Files:**
- Create: `peers_sync/sync.py`, `peers_sync/conflicts.py`, `tests/test_sync.py`

**Interfaces:**
- Consumes: `commit_local_changes`（Task 5）、`GitRunner`, `RepoResult`, `Status`, `SyncContext`（Task 1）
- Produces:
  - `conflicts.py`
    - `ConflictFile` のデータ型

      ```python
      @dataclass
      class ConflictFile:
          path: str
          kind: Literal["text", "binary", "deleted"]
          mine: str | None      # テキストのときだけ中身
          theirs: str | None
      ```

    - `def conflicted_paths(git: GitRunner) -> list[str]`（`git diff --name-only --diff-filter=U -z`）
    - `def classify(git: GitRunner, path: str) -> ConflictFile`
    - `def copy_name(path: str, label: str, exists: Callable[[str], bool]) -> str`
    - `def keep_both(git: GitRunner, repo: Path, path: str, machine: str, keep: Literal["mine", "theirs"]) -> str`
    - `def auto_resolve(git: GitRunner, repo: Path, machine: str) -> list[ConflictFile]`
      - binary と deleted は解決する。残ったテキストの競合を返す。
  - `sync.py`
    - `def current_branch(git: GitRunner) -> str | None`
    - `def ahead_behind(git: GitRunner, branch: str) -> tuple[int, int] | None`
      - リモートにブランチがなければ None。
    - `def sync_repo(path: Path, clone_url: str, full_name: str, ctx: SyncContext, lfs_available: bool) -> RepoResult`

- [ ] **Step 1: 失敗するテストを書く**
  - 準備：`remote = make_remote(tmp)`、`a = clone(remote, tmp/"a")`、`b = clone(remote, tmp/"b")`
  - `ctx_a` と `ctx_b` は、`machine_name` がそれぞれ "PC-A" と "PC-B"。

```python
def test_clone_when_missing(tmp_path, ctx_a):
    r = sync_repo(tmp_path / "new", remote.as_uri(), "me/x", ctx_a, True)
    assert r.status is Status.CLONED and (tmp_path / "new/README.md").exists()

def test_clone_target_exists_not_repo(tmp_path, ctx_a):
    write(tmp_path / "x/mine.txt", "keep")
    r = sync_repo(tmp_path / "x", remote.as_uri(), "me/x", ctx_a, True)
    assert r.status is Status.ERROR and (tmp_path / "x/mine.txt").read_text() == "keep"

def test_a_to_b_roundtrip(ctx_a, ctx_b):
    write(a / "n.md", "A")
    assert sync_repo(a, ..., ctx_a, True).status is Status.PUSHED
    r = sync_repo(b, ..., ctx_b, True)
    assert r.status is Status.PULLED and r.pulled_commits == 1 and (b / "n.md").read_text() == "A"

def test_up_to_date(ctx_a): assert sync_repo(a, ..., ctx_a, True).status is Status.UP_TO_DATE

def test_both_changed_different_files_merges(ctx_a, ctx_b):
    write(a / "x.md", "A"); sync_repo(a, ...)
    write(b / "y.md", "B"); r = sync_repo(b, ...)
    assert r.status is Status.PUSHED_AND_PULLED and (b / "x.md").exists()
    assert git(b, "rev-list", "--count", "--merges", "HEAD") == "1"

def test_text_conflict_aborts_and_reports(ctx_a, ctx_b):
    write(a / "README.md", "A"); sync_repo(a, ...)
    write(b / "README.md", "B"); r = sync_repo(b, ...)
    assert r.status is Status.CONFLICT
    assert not (b / ".git/MERGE_HEAD").exists() and (b / "README.md").read_text() == "B"
    assert git(b, "status", "--porcelain") == ""

def test_binary_conflict_keeps_both_and_finishes(ctx_a, ctx_b):
    write(a / "img.png", b"\x89PNG\x00A"); sync_repo(a, ...); sync_repo(b, ...)
    write(a / "img.png", b"\x89PNG\x00AA"); sync_repo(a, ...)
    write(b / "img.png", b"\x89PNG\x00BB"); r = sync_repo(b, ...)
    assert r.status is Status.PUSHED_AND_PULLED
    assert (b / "img.png").read_bytes() == b"\x89PNG\x00AA"
    assert (b / "img (PC-B の版).png").read_bytes() == b"\x89PNG\x00BB"

def test_delete_vs_modify_keeps_modified(ctx_a, ctx_b):
    (a / "README.md").unlink(); sync_repo(a, ...)
    write(b / "README.md", "edited"); r = sync_repo(b, ...)
    assert r.status is Status.PUSHED_AND_PULLED and (b / "README.md").read_text() == "edited"

def test_existing_merge_in_progress_reports_conflict(ctx_b): ...  # MERGE_HEAD がある状態 → CONFLICT になり、何も変更しない

def test_detached_head_is_error(ctx_a): ...  # git checkout --detach → ERROR（日本語の説明あり）

def test_never_force_pushes(ctx_a, monkeypatch): ...  # GitRunner.run を包んで、どの呼び出しの引数にも "--force" や "reset" が入っていないことを確かめる

def test_push_rejected_is_error_not_force(ctx_a, ctx_b): ...
    # fetch の直後に、別のクローンから先にプッシュされたことにする（GitRunner をフックする）→ ERROR

def test_empty_remote_clone_then_first_push(tmp_path, ctx_a):
    empty = make_remote(tmp_path, "empty", initial=False)
    p = tmp_path / "e"
    assert sync_repo(p, empty.as_uri(), "me/e", ctx_a, True).status is Status.CLONED
    write(p / "first.md", "1")
    assert sync_repo(p, empty.as_uri(), "me/e", ctx_a, True).status is Status.PUSHED
    assert git(empty, "log", "--format=%s", "main").startswith("[peers Sync]")

def test_lfs_video_roundtrip(ctx_a, ctx_b):
    write(a / "v.mp4", b"\x00VIDEO"); sync_repo(a, ...); sync_repo(b, ...)
    assert (b / "v.mp4").read_bytes() == b"\x00VIDEO"
```

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`python -m pytest tests/test_sync.py -v`
  - 期待：FAIL

- [ ] **Step 3: `conflicts.py` の自動解決部分を書く**

  **classify**
  - 次のどれかに当てはまれば `binary` にする。
    - `git check-attr filter -- path` の結果が `lfs`
    - stage 2 か 3 の中身（`run_bytes("cat-file", "-p", ":2:path")`）の先頭 8000 バイトに NUL が入っている
    - 中身を UTF-8 として読めない
  - stage 2 か 3 のどちらかがない（片方で消された）ときは `deleted` にする。

  **keep_both**
  1. `git checkout --ours -- path`（または `--theirs`）で、残さないほうの版を取り出す。
  2. それを `copy_name` の名前にコピーする。名前に入れる出どころは、残さないほうが ours なら `machine`、theirs なら `"GitHub"`。
  3. 残すほうの版を `git checkout --<keep> -- path` で取り出す。
  4. 両方のファイルを `git add` する。
  5. コピーのパスを返す。
  - 画像・動画の自動解決では `keep="theirs"` を使う。GitHub の版を元の名前のまま残すため（設計書 5.4）。

  **deleted**
  - 中身が残っているほうを `git checkout --ours/--theirs` で取り出して、`git add` する。

- [ ] **Step 4: `sync.py` を書く**

  1. フォルダがない、または空 → `git clone <url> <path>` を実行して `CLONED` を返す。
  2. フォルダがあるが `.git` がない → `ERROR` を返す。説明は「同じ名前の別のフォルダがあります」。
  3. `.git/MERGE_HEAD` がある → `CONFLICT` を返す。
  4. `current_branch` が None（どのブランチも開いていない）→ `ERROR` を返す。
  5. `commit_local_changes` を呼ぶ。
  6. `git fetch origin` を実行する。
  7. `ahead_behind` を調べる。
     - behind が 1 以上なら `git merge --no-edit origin/<branch>` を実行する。
     - 失敗したら `auto_resolve` を呼ぶ。
       - テキストの競合が残っていたら → `git merge --abort` を実行して `CONFLICT` を返す。
       - 残っていなければ → `git commit --no-edit` で合体を完了する。
  8. もう一度 `ahead_behind` を調べる。次のどちらかなら `git push -u origin <branch>` を実行する。
     - ahead が 1 以上
     - リモートにブランチがなく、HEAD にコミットがある
  9. Status を決める。
     - 送った かつ 取り込んだ → `PUSHED_AND_PULLED`
     - 送っただけ → `PUSHED`
     - 取り込んだだけ → `PULLED`
     - どちらもなし → `UP_TO_DATE`
  - `GitError` が起きたら `ERROR` にする。説明は Task 8 の `explain_error` でつける。それまでは stderr の 1 行目を仮に入れておく。

- [ ] **Step 5: テストを実行して通ることを確かめる**
  - 実行：`python -m pytest tests/test_sync.py -v`
  - 期待：全部 PASS

- [ ] **Step 6: コミット**

```bash
git add peers_sync/sync.py peers_sync/conflicts.py tests/test_sync.py
git commit -m "feat: リポジトリ 1 つ分の同期（クローン・合体・送信）を追加"
```

---

### Task 7: テキストの競合をあとで解決する（conflicts.py の残り）

**Files:**
- Modify: `peers_sync/conflicts.py`
- Test: `tests/test_conflict_resolution.py`

**Interfaces:**
- Consumes: Task 6 の `keep_both`, `auto_resolve`, `current_branch`
- Produces:
  - `def start_resolution(repo: Path, ctx: SyncContext) -> list[ConflictFile]`
    - `fetch` と `merge` をやり直す。binary の競合は自動で解決する。テキストの競合だけを返す。
    - 競合がなければ合体を完了して `[]` を返す。
  - `def finish_resolution(repo: Path, choices: dict[str, Literal["mine", "theirs"]], ctx: SyncContext) -> RepoResult`
  - `def cancel_resolution(repo: Path) -> None`（`git merge --abort`）

- [ ] **Step 1: 失敗するテストを書く**
  - 準備は Task 6 のテキスト競合と同じ状態（a が "A" をプッシュ、b が "B" を変更したまま）。

```python
def test_start_returns_text_conflicts_with_both_contents(...):
    files = start_resolution(b, ctx_b)
    assert [(f.path, f.mine, f.theirs) for f in files] == [("README.md", "B", "A")]

def test_finish_mine_keeps_mine_and_saves_theirs_copy_and_pushes(...):
    start_resolution(b, ctx_b)
    r = finish_resolution(b, {"README.md": "mine"}, ctx_b)
    assert r.status is Status.PUSHED_AND_PULLED
    assert (b / "README.md").read_text() == "B"
    assert (b / "README (GitHub の版).md").read_text() == "A"
    assert git(remote, "show", "main:README.md") == "B"

def test_finish_theirs(...):  # README.md == "A"、"README (PC-B の版).md" の中身が "B"
def test_cancel_restores_clean_state(...):  # cancel → MERGE_HEAD がなく、README.md == "B"
def test_finish_requires_choice_for_every_file(...):  # 選んでいないファイルがある → ValueError。何も変えない
def test_copy_name_increments(): 
    assert copy_name("d/a.md", "PC", lambda p: p == "d/a (PC の版).md") == "d/a (PC の版 2).md"
    assert copy_name("noext", "PC", lambda p: False) == "noext (PC の版)"
```

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`python -m pytest tests/test_conflict_resolution.py -v`
  - 期待：FAIL

- [ ] **Step 3: 3 つの関数を書く**
  - `finish_resolution` は、ファイルごとに `keep_both(..., keep=choice)` を呼ぶ。
  - 選ばれなかったほうは、`keep_both` が出どころの名前を付けたコピーとして残す（Global Constraints を参照）。
  - 最後に `git commit --no-edit` → `git push` をする。

- [ ] **Step 4: テストを実行して通ることを確かめる**
  - 実行：`python -m pytest tests/test_conflict_resolution.py -v`
  - 期待：全部 PASS

- [ ] **Step 5: コミット**

```bash
git add peers_sync/conflicts.py tests/test_conflict_resolution.py
git commit -m "feat: テキストの競合をあとから選んで解決する機能を追加"
```

---

### Task 8: 全リポジトリをまとめて同期する＋日本語メッセージ（engine.py / messages.py）

**Files:**
- Create: `peers_sync/engine.py`, `peers_sync/messages.py`, `tests/test_engine.py`, `tests/test_messages.py`
- Modify: `peers_sync/sync.py`（エラーの説明を `explain_error` に差しかえる）

**Interfaces:**
- Consumes: `Settings`（Task 3）、`GitHubClient`（Task 4）、`sync_repo`（Task 6）
- Produces:
  - `def local_path_for(repo: RemoteRepo, settings: Settings) -> Path`
    - `repo_paths` に登録があればそれを使う。なければ `base_dir / repo.name`。
  - `def check_prerequisites() -> tuple[str | None, bool]`
    - 戻り値は（Git がないときの日本語メッセージ, LFS が使えるか）。
    - LFS が使えるときは `git lfs install --skip-repo` も実行する。
  - `class SyncAbort(Exception)`（日本語のメッセージを持つ）
  - `def sync_all(settings: Settings, api: GitHubClient, ctx: SyncContext, on_result: Callable[[RepoResult], None], lfs_available: bool) -> list[RepoResult]`
  - `def status_text(r: RepoResult) -> str`
  - `def explain_error(stderr: str) -> str`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_status_text():
    assert status_text(res(Status.PUSHED, committed=3)) == "✅ セーブして送信 (3)"
    assert status_text(res(Status.PULLED, pulled=5)) == "✅ 最新を取り込み (5)"
    assert status_text(res(Status.PUSHED_AND_PULLED, committed=1, pulled=2)) == "✅ 送信 (1)・取り込み (2)"
    assert status_text(res(Status.CLONED)) == "⬇️ 新しくダウンロード"
    assert status_text(res(Status.UP_TO_DATE)) == "➖ 変更なし"
    assert status_text(res(Status.CONFLICT)) == "⚠️ 競合あり → 確認する"
    assert status_text(res(Status.SKIPPED)) == "⏭ 同期しない設定"
    assert status_text(res(Status.ERROR, error="ネット")) == "❌ エラー: ネット"

@pytest.mark.parametrize("stderr,word", [
    ("fatal: Authentication failed for 'https://github.com/x'", "ログイン"),
    ("fatal: could not read Username for 'https://github.com'", "ログイン"),
    ("fatal: unable to access '...': Could not resolve host: github.com", "インターネット"),
    ("! [rejected] main -> main (fetch first)", "もう一度"),
    ("remote: error: File big.bin is 120.00 MB; this exceeds GitHub's file size limit of 100.00 MB", "100MB"),
    ("batch response: This repository is over its data quota.", "無料枠"),
    ("something odd", "くわしいログ"),
])
def test_explain_error(stderr, word): assert word in explain_error(stderr)

def test_sync_all_skips_excluded_and_continues_after_error(tmp_path, ctx_a):
    # FakeApi.list_owned_repos が file:// の RemoteRepo を 3 つ返す
    #   - "me/ok"
    #   - "me/broken"（clone_url が存在しないパス）
    #   - "me/skip"（settings.excluded に入れておく）
    results = sync_all(settings, FakeApi(...), ctx_a, on_result=seen.append, lfs_available=True)
    assert {r.full_name: r.status for r in results} == {
        "me/ok": Status.CLONED, "me/broken": Status.ERROR, "me/skip": Status.SKIPPED}
    assert len(seen) == 3

def test_sync_all_includes_extra_repos_once(...):  # extra_repos に入れたものは get_repo で取る。list の結果と重なっても 1 回だけ
def test_sync_all_list_failure_raises_syncabort(...):  # list_owned_repos が GitHubError(401) を出す → SyncAbort。メッセージに「ログイン」が入る
def test_local_path_for_uses_override(tmp_path): ...
```

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`python -m pytest tests/test_engine.py tests/test_messages.py -v`
  - 期待：FAIL

- [ ] **Step 3: 2 つのファイルを書き、`sync.py` の仮の説明を `explain_error(e.stderr)` に差しかえる**
  - `explain_error` は、stderr に含まれる言葉で判定する（小文字にしてから、部分一致で探す）。
  - 返す日本語の文章には、「何が起きたか」と「どうすればいいか」の両方を入れる。
  - どれにも当てはまらないときは、「Git でエラーが起きました。くわしいログを見てください」を返す。
  - `sync_all` は、`sync_repo` の外側を `except Exception` で囲む。1 つ失敗しても次のリポジトリに進むため。

- [ ] **Step 4: テストをすべて実行して通ることを確かめる**
  - 実行：`python -m pytest -v`
  - 期待：全部 PASS

- [ ] **Step 5: コミット**

```bash
git add peers_sync/engine.py peers_sync/messages.py peers_sync/sync.py tests/test_engine.py tests/test_messages.py
git commit -m "feat: 全リポジトリの一括同期と日本語メッセージを追加"
```

---

### Task 9: 画面（ui/）

**Files:**
- Create: `peers_sync/ui/__init__.py`, `peers_sync/ui/main_window.py`, `peers_sync/ui/settings_dialog.py`, `peers_sync/ui/conflict_dialog.py`, `peers_sync/__main__.py`, `run_peers_sync.py`, `tests/test_ui_smoke.py`

**Interfaces:**
- Consumes: `sync_all`, `check_prerequisites`, `status_text`（Task 8）、`get_stored_token` / `login` / `logout`（Task 4）、`load_settings` / `save_settings`（Task 3）、`start_resolution` / `finish_resolution` / `cancel_resolution`（Task 7）
- Produces:
  - `class MainWindow(tk.Tk)`
    - `__init__(self, settings: Settings, services: Services)`
    - `.on_sync_clicked()`
    - `.show_result(r: RepoResult)`
  - `Services` のデータ型。画面のテストで偽物に差しかえるためのまとめ役。

    ```python
    @dataclass
    class Services:
        sync_all: Callable[..., list[RepoResult]]
        get_token: Callable[[], tuple[str, str] | None]
        login: Callable[[], tuple[str, str]]
        logout: Callable[[], None]
        make_api: Callable[[str], GitHubClient]
        prerequisites: Callable[[], tuple[str | None, bool]]
    ```

  - `def main() -> None`（`__main__.py`。本物の Services を組み立てて `MainWindow(...).mainloop()`）

- [ ] **Step 1: 失敗する画面のテストを書く**
  - このテストは、画面を表示できない環境では `pytest.importorskip("tkinter")` と表示の確認でスキップする。
  - Linux の CI では `xvfb-run` で動かす。この作業用環境では、先に `apt-get install -y python3-tk` が必要。

```python
def test_sync_button_shows_one_line_per_repo(tk_root_ok, settings):
    results = [res("me/a", Status.PUSHED, committed=3), res("me/b", Status.CONFLICT)]
    def fake_sync_all(s, api, ctx, on_result, lfs_available):
        for r in results: on_result(r)
        return results
    w = MainWindow(settings, fake_services(sync_all=fake_sync_all))
    w.on_sync_clicked(); pump_until_idle(w)
    assert w.result_lines() == ["a  ✅ セーブして送信 (3)", "b  ⚠️ 競合あり → 確認する"]
    w.destroy()

def test_not_logged_in_shows_login_prompt(...):  # get_token が None → 「GitHub でログイン」の案内が出る。sync_all は呼ばれない
def test_missing_git_shows_install_message(...):  # prerequisites が ("Git が見つかりません…", False) → その文言が出て、ボタンが押せない
def test_confirm_from_worker_thread_is_answered_on_ui_thread(...):
    # 偽の sync_all が別スレッドで ctx.confirm("質問") を呼ぶ
    # → 画面スレッドで askyesno が呼ばれる（テストでは monkeypatch で True を返す）
    # → 別スレッド側に True が返る
```

- [ ] **Step 2: 実行して失敗することを確かめる**
  - 実行：`xvfb-run -a python -m pytest tests/test_ui_smoke.py -v`
  - 期待：FAIL

- [ ] **Step 3: 画面を書く**

  **全体のレイアウト**（設計書 5.1 のとおり）
  - 上：アプリ名、[設定]ボタン、[?]ボタン
  - 真ん中：大きな「🔄 ぜんぶ同期」ボタン
  - その下：「最後の同期」の時刻
  - その下：結果のリスト（`ttk.Treeview`）
  - いちばん下：折りたためる「くわしいログ」（`Text`）
    - 各行は `$ git <args>  — <explanation>` の形にする。

  **同期中の動き**
  - 同期は `threading.Thread` で動かす。
  - 結果は `queue.Queue` に入れ、`after(100, ...)` で画面側が取り出して表示する。
  - 同期中はボタンを押せないようにする。

  **確認ダイアログの出し方**
  - `ctx.confirm` は、別スレッドから画面スレッドへ質問を渡す。
  - `threading.Event` で答えを待ち、`messagebox.askyesno` で聞く。

  **競合の行**
  - CONFLICT の行をダブルクリックすると `ConflictDialog` が開く。
  - ダイアログの中身
    - ファイルごとに、左に「このパソコンの版」、右に「GitHub の版」を並べる（`Text` を 2 つ、読み取り専用）。
    - それぞれにラジオボタンを付ける。
    - [決定] → `finish_resolution`
    - [キャンセル] → `cancel_resolution`

  **設定画面（SettingsDialog）**
  - ログイン / ログアウト
  - 置き場所（`filedialog.askdirectory`）
  - 同期しないリポジトリのチェックリスト（リポジトリ一覧を取得して並べる）
  - このパソコンの名前
  - 自分以外のリポジトリの追加欄（`owner/name`）
  - すでにクローンしてあるリポジトリの場所の登録
  - [保存] → `save_settings`

  **起動時**
  1. `prerequisites()` で Git が入っているかを確かめる。
  2. `get_token()` で、ログインしているかを確かめる。
  3. ログインしていれば `make_api(token).current_login()` を `settings.github_login` に入れて保存する。

- [ ] **Step 4: テストを実行して通ることを確かめる**
  - 実行：`xvfb-run -a python -m pytest tests/test_ui_smoke.py -v`
  - 期待：全部 PASS
  - 手動の確認：`xvfb-run -a python -m peers_sync` で起動できること。その画面のスクリーンショットを撮って、5.1 のスケッチと見くらべる。

- [ ] **Step 5: コミット**

```bash
git add peers_sync/ui peers_sync/__main__.py run_peers_sync.py tests/test_ui_smoke.py
git commit -m "feat: 同期ボタン・設定・競合解決の画面を追加"
```

---

### Task 10: 配布ファイルと説明書

**Files:**
- Create: `.github/workflows/build.yml`, `README.md`
- Modify: `.github/workflows/test.yml`（ubuntu では `xvfb-run` を付けて pytest を実行する）

**Interfaces:**
- Consumes: `run_peers_sync.py`（Task 9）

- [ ] **Step 1: `build.yml` を書く**
  - 動くタイミング：`workflow_dispatch` と、`v*` のタグを push したとき。
  - `matrix.os: [windows-latest, macos-latest]`
  - 手順
    1. `pip install pyinstaller`
    2. `pyinstaller --noconfirm --windowed --name "peers Sync" run_peers_sync.py`
    3. Mac では `dist/peers Sync.app` を `ditto -c -k` で zip にする。
    4. `actions/upload-artifact` で、`peers-Sync-windows` / `peers-Sync-mac` という名前でアップロードする。
  - コード署名はしない（設計書 6 章）。

- [ ] **Step 2: `README.md`（日本語）を書く**
  - 書く内容
    1. このアプリでできること
    2. 最初に入れるもの
       - Windows：「Git for Windows」（Git LFS と Git Credential Manager が入っている）
       - Mac：Git、`git lfs`、`brew install --cask git-credential-manager`
    3. ダウンロードの場所（Actions の Artifacts）
    4. 署名なしのアプリを最初に開く方法
       - Mac：右クリック →「開く」
       - Windows：「詳細情報」→「実行」
    5. 使い方（ログイン → ぜんぶ同期）
    6. 競合が起きたときの見方
    7. LFS の無料枠の注意（10GB・月 10GB・1 ファイル 2GB）
    8. 送らないファイルのルール（秘密ファイル、2GB 超）
    9. Git の用語ミニ辞典（コミット / プッシュ / フェッチ / プル / マージ / 競合 / ブランチ / LFS）

- [ ] **Step 3: ワークフローの書き方を確かめる**
  - 実行：`python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in sys.argv[1:]]" .github/workflows/test.yml .github/workflows/build.yml`
  - 期待：エラーが出ない
  - PyYAML がなければ `pip install pyyaml` で入れる。
  - 実行：`pip install pyinstaller && pyinstaller --noconfirm --windowed --name "peers Sync" run_peers_sync.py`
  - 期待：`dist/peers Sync/` ができる（Linux 版で、ビルドが通ることだけを確かめる）

- [ ] **Step 4: コミット**

```bash
git add .github/workflows README.md
git commit -m "docs: 配布ワークフローと日本語の説明書を追加"
```

---

## 設計書との対応表（自己チェック）

| 設計書 | タスク |
|---|---|
| 2 ゴール 1（全リポジトリ、本人のもの、アーカイブ除外、追加リポジトリ） | 4, 8 |
| 2 ゴール 2（クローン・コミット・プッシュ・プル） | 5, 6 |
| 2 ゴール 3 / 5.5（LFS、新しいファイルだけ、2GB、1GB の確認） | 2, 5, 6 |
| 2 ゴール 4（Windows / Mac） | 1（CI）, 10 |
| 2 ゴール 5 / 5.1（日本語の結果・くわしいログ） | 1, 8, 9 |
| 安全装置（秘密ファイル、20 個以上の削除） | 2, 5 |
| やってはいけないこと（force / reset なし、競合で黙って捨てない、1 つの失敗で全体を止めない） | 6, 7, 8 |
| 5.2 マージ方式 / 5.3 コミットメッセージ | 5, 6 |
| 5.4 競合（自動合体 → テキストはあとで選ぶ、選ばれなかったほうもコピーで残す、画像・動画は両方残す） | 6, 7, 9 |
| 5.6 置き場所 / 5.7 設定 | 3, 8, 9 |
| 5.8 ログイン（ブラウザ、OS の安全な保管場所） | 4（GCM）, 9 |
| 6 章 署名なし配布 | 10 |
