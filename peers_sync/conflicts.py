"""競合（2 台のパソコンで同じファイルを別々に変えた状態）の扱い。

いちばん大事なルール：どちらの版も消さない。残さないほうは、名前を変えたコピーとして残す。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .gitrunner import GitRunner

Side = Literal["mine", "theirs"]


@dataclass
class ConflictFile:
    path: str
    kind: Literal["text", "binary", "deleted"]
    mine: str | None = None      # テキストのときだけ中身
    theirs: str | None = None


def conflicted_paths(git: GitRunner) -> list[str]:
    out = git.run("diff", "--name-only", "--diff-filter=U", "-z").stdout
    return [p for p in out.split("\0") if p]


def _stages(git: GitRunner, path: str) -> set[int]:
    out = git.run("ls-files", "-u", "-z", "--", ":(literal)" + path).stdout
    stages = set()
    for entry in out.split("\0"):
        if "\t" in entry:
            meta = entry.split("\t", 1)[0].split()
            stages.add(int(meta[2]))
    return stages


def is_lfs(git: GitRunner, path: str) -> bool:
    return git.run("check-attr", "filter", "--", path, check=False).stdout.strip().endswith(": lfs")


def _decode_text(data: bytes) -> str | None:
    if b"\0" in data[:8000]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def classify(git: GitRunner, path: str) -> ConflictFile:
    stages = _stages(git, path)
    if 2 not in stages or 3 not in stages:
        return ConflictFile(path, "deleted")
    if is_lfs(git, path):
        return ConflictFile(path, "binary")
    mine = _decode_text(git.run_bytes("cat-file", "-p", f":2:{path}"))
    theirs = _decode_text(git.run_bytes("cat-file", "-p", f":3:{path}"))
    if mine is None or theirs is None:
        return ConflictFile(path, "binary")
    return ConflictFile(path, "text", mine, theirs)


def merge_in_progress(git: GitRunner) -> bool:
    """合体の途中かどうか（.git がファイルの worktree などにも対応）。"""
    p = git.run("rev-parse", "--git-path", "MERGE_HEAD", check=False).stdout.strip()
    return bool(p) and (git.cwd / p).exists()


_BAD_CHARS = '\\/:*?"<>|'


def safe_label(label: str) -> str:
    """パソコン名に、ファイル名に使えない記号があれば _ に変える。"""
    out = "".join("_" if c in _BAD_CHARS else c for c in label).strip()
    return out or "PC"


def copy_name(path: str, label: str, exists: Callable[[str], bool],
              template: str = "{label} の版") -> str:
    tag = template.format(label=safe_label(label))
    directory, _, name = path.rpartition("/")
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    prefix = f"{directory}/" if directory else ""
    n = 1
    while True:
        suffix = f" ({tag})" if n == 1 else f" ({tag} {n})"
        candidate = f"{prefix}{stem}{suffix}{ext}"
        if not exists(candidate):
            return candidate
        n += 1


def keep_both(git: GitRunner, repo: Path, path: str, machine: str, keep: Side) -> str:
    """`keep` の版を元の名前で残し、もう片方を名前を変えたコピーとして残す。コピーのパスを返す。"""
    repo = Path(repo)
    other = "theirs" if keep == "mine" else "mine"
    flag = {"mine": "--ours", "theirs": "--theirs"}
    label = machine if other == "mine" else "GitHub"
    lfs = is_lfs(git, path)

    git.run("checkout", flag[other], "--", path)
    data = (repo / path).read_bytes()
    copy = copy_name(path, label, lambda p: (repo / p).exists())
    (repo / copy).write_bytes(data)
    git.run("checkout", flag[keep], "--", path)

    to_add = [path, copy]
    if lfs:
        git.run("lfs", "track", "--filename", "--", copy)
        to_add.append(".gitattributes")
    git.run("add", "--", *to_add)
    return copy


_MARKERS = ("<<<<<<<", "=======", ">>>>>>>", "|||||||")


def _normalize_markers(text: str) -> str:
    return "\n".join(line[:7] if line.startswith(_MARKERS) else line for line in text.splitlines())


def preserve_manual_edits(git: GitRunner, repo: Path) -> list[str]:
    """合体の途中でユーザーが手で直したファイルを「(手で直した版)」のコピーとして残す。コピーのパスを返す。"""
    repo = Path(repo)
    saved = []
    for path in conflicted_paths(git):
        f = repo / path
        if not f.is_file():
            continue
        current = f.read_bytes()
        git.run("checkout", "-m", "--", path)  # Git が最初に書いた「競合の印つき」の状態を作り直す
        regenerated = f.read_bytes()
        f.write_bytes(current)  # ユーザーの版に戻す
        if current == regenerated:
            continue
        try:
            same = _normalize_markers(current.decode("utf-8")) == _normalize_markers(regenerated.decode("utf-8"))
        except UnicodeDecodeError:
            same = False
        if same:
            continue
        copy = copy_name(path, "", lambda p: (repo / p).exists(), template="手で直した版")
        (repo / copy).write_bytes(current)
        saved.append(copy)
    return saved


def _resolve_deleted(git: GitRunner, path: str) -> None:
    """片方で消され、片方で変えられたファイル。変えたほう（中身があるほう）を残す。"""
    stages = _stages(git, path)
    if 2 in stages:
        git.run("checkout", "--ours", "--", path)
    elif 3 in stages:
        git.run("checkout", "--theirs", "--", path)
    git.run("add", "--", path)


def _merge_gitattributes(git: GitRunner, repo: Path, cf: ConflictFile) -> None:
    """.gitattributes は「両方の行を全部残す」で合体する（LFS のルールを消さないため）。"""
    lines: list[str] = []
    for text in (cf.mine or "", cf.theirs or ""):
        for line in text.splitlines():
            if line not in lines:
                lines.append(line)
    (Path(repo) / cf.path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    git.run("add", "--", cf.path)


def auto_resolve(git: GitRunner, repo: Path, machine: str,
                 notes: list[str] | None = None) -> list[ConflictFile]:
    """画像・動画などと、消された/変えられたファイルの競合を自動で解決する。残ったテキストの競合を返す。"""
    remaining = []
    for path in conflicted_paths(git):
        cf = classify(git, path)
        if cf.kind == "deleted":
            _resolve_deleted(git, path)
        elif cf.kind == "binary":
            copy = keep_both(git, repo, path, machine, keep="theirs")
            if notes is not None:
                notes.append(f"「{path}」はほかのパソコンでも変えられていました。GitHub の版を元の名前で、"
                             f"このパソコンの版を「{copy}」として、両方残しました。")
        elif path.rsplit("/", 1)[-1] == ".gitattributes":
            _merge_gitattributes(git, repo, cf)
        else:
            remaining.append(cf)
    return remaining


# ---- テキストの競合を、あとからユーザーに選んでもらって解決する ----

def start_resolution(repo: Path, ctx, lfs_available: bool = True) -> list[ConflictFile]:
    """合体をやり直す。画像などは自動で解決し、ユーザーに選んでほしいテキストの競合だけを返す。"""
    from .changes import commit_local_changes, identity_config
    from .sync import ahead_behind, current_branch, unrelated_flag

    repo = Path(repo)
    git = GitRunner(repo, log=ctx.log)
    if merge_in_progress(git):
        preserve_manual_edits(git, repo)
    else:
        commit_local_changes(git, repo, ctx, lfs_available)
        branch = current_branch(git)
        if branch is None:
            raise ValueError("どのブランチも開かれていません。")
        git.run("fetch", "origin")
        counts = ahead_behind(git, branch)
        if not counts or counts[1] == 0:
            return []
        merger = GitRunner(repo, log=git.log, extra_config=identity_config(git, ctx))
        if merger.run("merge", "--no-edit", *unrelated_flag(git, branch), f"origin/{branch}", check=False).returncode == 0:
            return []
    remaining = auto_resolve(git, repo, ctx.machine_name)
    if not remaining:
        GitRunner(repo, log=git.log, extra_config=identity_config(git, ctx)).run("commit", "--no-edit", "-q")
    return remaining


def finish_resolution(repo: Path, choices: dict[str, Side], ctx,
                      full_name: str | None = None):
    """ユーザーの選んだ版で合体を完了して、GitHub に送る。"""
    from .changes import identity_config
    from .gitrunner import GitError
    from .models import RepoResult, Status
    from .sync import _explain, ahead_behind, current_branch

    repo = Path(repo)
    git = GitRunner(repo, log=ctx.log)
    result = RepoResult(full_name or repo.name, repo, Status.PUSHED_AND_PULLED)
    pending = conflicted_paths(git)
    missing = [p for p in pending if p not in choices]
    if missing:
        raise ValueError(f"まだ選んでいないファイルがあります: {', '.join(missing)}")
    try:
        if merge_in_progress(git):
            result.pulled_commits = int(git.run("rev-list", "--count", "HEAD..MERGE_HEAD").stdout.strip() or 0)
            for path in pending:
                keep_both(git, repo, path, ctx.machine_name, keep=choices[path])
            GitRunner(repo, log=git.log, extra_config=identity_config(git, ctx)).run("commit", "--no-edit", "-q")
        branch = current_branch(git)
        counts = ahead_behind(git, branch) if branch else None
        if branch and (counts is None or counts[0] > 0):
            git.run("push", "-u", "origin", branch)
        elif not result.pulled_commits:
            result.status = Status.UP_TO_DATE
    except GitError as e:
        result.status = Status.ERROR
        result.error = _explain(e.stderr)
    return result


def cancel_resolution(repo: Path) -> None:
    repo = Path(repo)
    git = GitRunner(repo)
    if merge_in_progress(git):
        preserve_manual_edits(git, repo)
        git.run("merge", "--abort")
