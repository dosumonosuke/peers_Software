import subprocess

import pytest

from peers_sync.credentials import LoginError, get_stored_token, login, logout


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


def test_get_stored_token_none_when_password_empty():
    fake = lambda args, **kw: subprocess.CompletedProcess(args, 0, "username=me\n", "")
    assert get_stored_token(fake) is None


def test_login_approves_after_fill():
    seen = []

    def fake(args, **kw):
        seen.append(args[2])
        return subprocess.CompletedProcess(args, 0, "username=me\npassword=tok\n", "")

    assert login(fake) == ("me", "tok") and seen == ["fill", "approve"]


def test_login_failure_raises():
    fake = lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "cancelled")
    with pytest.raises(LoginError):
        login(fake)


def test_logout_rejects():
    seen = []

    def fake(args, **kw):
        seen.append((args[2], kw["input"]))
        return subprocess.CompletedProcess(args, 0, "", "")

    logout(fake)
    assert seen == [("reject", "protocol=https\nhost=github.com\n\n")]
