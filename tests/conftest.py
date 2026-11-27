import subprocess
import pytest


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory, monkeypatch):
    """テストごとに、本物の Git 設定を使わない一時的な設定に切りかえる。"""
    cfg = tmp_path_factory.mktemp("gitcfg") / "gitconfig"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key, value in [
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("init.defaultBranch", "main"),
        ("protocol.file.allow", "always"),
        ("core.autocrlf", "false"),
    ]:
        subprocess.run(["git", "config", "--global", key, value], check=True)
    subprocess.run(["git", "lfs", "install", "--skip-repo"], check=True, capture_output=True)
    return cfg
