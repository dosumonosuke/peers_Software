"""設定（置き場所・同期しないリポジトリなど）を JSON ファイルに保存する。"""
from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    base_dir: Path
    machine_name: str
    excluded: set[str] = field(default_factory=set)          # full_name の集合
    extra_repos: list[str] = field(default_factory=list)     # 自分以外のリポジトリ
    repo_paths: dict[str, Path] = field(default_factory=dict)  # full_name → 手元の場所
    github_login: str | None = None


def default_settings() -> Settings:
    return Settings(base_dir=Path.home() / "GitHub", machine_name=socket.gethostname())


def config_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "peers-sync" / "settings.json"


def _to_json(s: Settings) -> dict:
    return {
        "base_dir": str(s.base_dir),
        "machine_name": s.machine_name,
        "excluded": sorted(s.excluded),
        "extra_repos": list(s.extra_repos),
        "repo_paths": {k: str(v) for k, v in s.repo_paths.items()},
        "github_login": s.github_login,
    }


def _from_json(d: dict) -> Settings:
    s = default_settings()
    if "base_dir" in d:
        s.base_dir = Path(d["base_dir"])
    s.machine_name = d.get("machine_name") or s.machine_name
    s.excluded = set(d.get("excluded", []))
    s.extra_repos = list(d.get("extra_repos", []))
    s.repo_paths = {k: Path(v) for k, v in d.get("repo_paths", {}).items()}
    s.github_login = d.get("github_login")
    return s


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_path()
    if not path.exists():
        return default_settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings must be an object")
        return _from_json(data)
    except (ValueError, TypeError, AttributeError):
        os.replace(path, path.with_name(path.name + ".broken"))
        return default_settings()


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_to_json(s), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
