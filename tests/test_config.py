from peers_sync.config import default_settings, load_settings, save_settings


def test_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    s = default_settings()
    s.excluded = {"me/x"}
    s.repo_paths = {"me/y": tmp_path / "y"}
    s.extra_repos = ["other/z"]
    s.github_login = "me"
    save_settings(s, p)
    assert load_settings(p) == s


def test_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "none.json") == default_settings()


def test_broken_file_is_backed_up_and_defaults_used(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{broken", encoding="utf-8")
    assert load_settings(p) == default_settings()
    assert (tmp_path / "s.json.broken").exists()


def test_unicode_paths_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    s = default_settings()
    s.base_dir = tmp_path / "作業"
    save_settings(s, p)
    assert load_settings(p).base_dir == tmp_path / "作業"


def test_missing_keys_filled_with_defaults(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"machine_name": "PC-X"}', encoding="utf-8")
    s = load_settings(p)
    assert s.machine_name == "PC-X" and s.base_dir == default_settings().base_dir
