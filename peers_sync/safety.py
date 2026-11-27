"""送ってはいけないファイル・大きいファイルを見分けるルール。数値はすべてここにまとめる。"""
from __future__ import annotations

from fnmatch import fnmatch

LFS_SIZE_THRESHOLD = 50 * 1024**2        # これより大きいファイルは LFS へ
MAX_FILE_SIZE = 2 * 1024**3              # これより大きいファイルは GitHub に送れない
NON_LFS_HARD_LIMIT = 100 * 1000**2       # LFS なしで GitHub に送れる上限
LFS_BATCH_WARN_BYTES = 1024**3           # 1 回の同期で送る LFS がこれ以上なら確認
DELETE_CONFIRM_THRESHOLD = 20            # これ以上のファイルが消えていたら確認

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".wmv", ".flv", ".mts", ".m2ts",
})

_SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_rsa.*", "id_ed25519",
    "id_ed25519.*", "*.p12", "*.pfx", "credentials.json",
)
_SECRET_ALLOWED = {".env.example", ".env.sample", ".env.template"}


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _ext(path: str) -> str:
    name = _basename(path)
    return name[name.rfind("."):] if "." in name else ""


def is_secret_path(path: str) -> bool:
    name = _basename(path)
    if name in _SECRET_ALLOWED:
        return False
    return any(fnmatch(name, pat) for pat in _SECRET_PATTERNS)


def is_secret_file(path: str, abs_path) -> bool:
    """中身も見て判定する。Keynote のファイル（.key）は zip なので、鍵ファイルではない。"""
    if not is_secret_path(path):
        return False
    if _basename(path).endswith(".key"):
        try:
            with open(abs_path, "rb") as f:
                if f.read(4) == b"PK\x03\x04":
                    return False
        except OSError:
            pass
    return True


def needs_lfs(path: str, size: int) -> bool:
    return _ext(path) in VIDEO_EXTENSIONS or size > LFS_SIZE_THRESHOLD


def is_too_large(size: int) -> bool:
    return size > MAX_FILE_SIZE
