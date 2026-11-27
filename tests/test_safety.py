import pytest

from peers_sync.safety import is_secret_path, is_too_large, needs_lfs


@pytest.mark.parametrize("p", [".env", "app/.env", ".env.local", "server.pem",
    "my.key", "id_rsa", ".ssh/id_ed25519", "cert.p12", "cert.pfx", "credentials.json", "A/.ENV",
    "win\\dir\\.env"])
def test_secret_paths(p):
    assert is_secret_path(p)


@pytest.mark.parametrize("p", [".env.example", ".env.sample", ".env.template",
    "README.md", "keyboard.py", "notes/key.md", "environment.md"])
def test_not_secret_paths(p):
    assert not is_secret_path(p)


def test_needs_lfs():
    assert needs_lfs("動画/clip.MP4", 10)
    assert needs_lfs("data.bin", 50 * 1024**2 + 1)
    assert not needs_lfs("photo.png", 5 * 1024**2)
    assert not needs_lfs("data.bin", 50 * 1024**2)


def test_is_too_large():
    assert is_too_large(2 * 1024**3 + 1)
    assert not is_too_large(2 * 1024**3)
