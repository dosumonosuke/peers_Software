"""アプリ全体で使うデータの形。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .gitrunner import CommandLog


@dataclass(frozen=True)
class RemoteRepo:
    full_name: str      # "dosumonosuke/muscle_app"
    name: str           # "muscle_app"
    clone_url: str      # https://... または file://...（テスト用）
    private: bool
    archived: bool


class Status(Enum):
    CLONED = "cloned"
    PUSHED = "pushed"
    PULLED = "pulled"
    PUSHED_AND_PULLED = "pushed_and_pulled"
    UP_TO_DATE = "up_to_date"
    CONFLICT = "conflict"
    SKIPPED = "skipped"
    ERROR = "error"


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
