import pytest

from peers_sync.gitrunner import GitError, GitRunner, explain_command


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


def test_run_bytes_returns_raw_bytes(tmp_path):
    g = GitRunner(tmp_path)
    g.run("init")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01")
    sha = g.run("hash-object", "-w", "b.bin").stdout.strip()
    assert g.run_bytes("cat-file", "-p", sha) == b"\x00\x01"


def test_explain_command():
    assert "ダウンロード" in explain_command(["clone", "u", "d"])
    assert "セーブ" in explain_command(["commit", "-m", "x"])
    assert "送" in explain_command(["push", "origin", "main"])
    assert "確認" in explain_command(["fetch", "origin"])
    assert "合体" in explain_command(["merge", "origin/main"])
    assert explain_command(["unknown-sub"]) != ""
