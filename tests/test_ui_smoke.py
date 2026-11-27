import threading
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from peers_sync.config import default_settings  # noqa: E402
from peers_sync.credentials import LoginError  # noqa: E402
from peers_sync.engine import SyncAbort  # noqa: E402
from peers_sync.gitrunner import CommandLog  # noqa: E402
from peers_sync.models import RepoResult, Status  # noqa: E402


@pytest.fixture
def tk_ok():
    try:
        root = tk.Tk()
        root.destroy()
    except tk.TclError:
        pytest.skip("画面（ディスプレイ）がない環境")


@pytest.fixture
def settings(tmp_path):
    s = default_settings()
    s.base_dir = tmp_path / "GitHub"
    s.github_login = "me"
    return s


def res(full_name, status, committed=0):
    return RepoResult(full_name, Path(full_name.split("/")[1]), status, committed)


class FakeApi:
    def current_login(self):
        return "me"

    def list_owned_repos(self):
        return []


def fake_services(**over):
    from peers_sync.ui.main_window import Services
    base = dict(
        sync_all=lambda *a, **k: [],
        get_token=lambda: ("me", "tok"),
        login=lambda: ("me", "tok"),
        logout=lambda: None,
        make_api=lambda token: FakeApi(),
        prerequisites=lambda: (None, True),
    )
    base.update(over)
    return Services(**base)


def make_window(settings, tmp_path, **over):
    from peers_sync.ui.main_window import MainWindow
    return MainWindow(settings, fake_services(**over), settings_path=tmp_path / "s.json")


def pump_until_idle(w, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        w.update()
        if not w.busy:
            w.update()
            return
        time.sleep(0.02)
    raise AssertionError("同期が終わらなかった")


def test_sync_button_shows_one_line_per_repo(tk_ok, settings, tmp_path):
    results = [res("me/a", Status.PUSHED, committed=3), res("me/b", Status.CONFLICT)]

    def fake_sync_all(s, api, ctx, on_result, lfs_available):
        for r in results:
            on_result(r)
        return results

    w = make_window(settings, tmp_path, sync_all=fake_sync_all)
    w.on_sync_clicked()
    pump_until_idle(w)
    assert w.result_lines() == ["a  ✅ セーブして送信 (3)", "b  ⚠️ 競合あり → 確認する"]
    assert "最後の同期" in w.last_sync_text()
    w.destroy()


def test_not_logged_in_shows_login_prompt(tk_ok, settings, tmp_path):
    called = []

    def bad_login():
        raise LoginError("GitHub へのログインができませんでした。")

    w = make_window(settings, tmp_path, get_token=lambda: None, login=bad_login,
                    sync_all=lambda *a, **k: called.append(1) or [])
    assert "ログイン" in w.main_button_text()
    w.on_sync_clicked()
    pump_until_idle(w)
    assert not called and "ログインができませんでした" in w.message_text()
    w.destroy()


def test_login_then_button_becomes_sync(tk_ok, settings, tmp_path):
    state = {"token": None}

    def do_login():
        state["token"] = ("me", "tok")
        return state["token"]

    w = make_window(settings, tmp_path, get_token=lambda: state["token"], login=do_login)
    w.on_sync_clicked()
    pump_until_idle(w)
    assert "ぜんぶ同期" in w.main_button_text()
    w.destroy()


def test_missing_git_shows_install_message(tk_ok, settings, tmp_path):
    w = make_window(settings, tmp_path, prerequisites=lambda: ("Git が見つかりません。", False))
    assert "Git が見つかりません" in w.message_text()
    assert str(w.main_button["state"]) == "disabled"
    w.destroy()


def test_confirm_from_worker_thread_is_answered_on_ui_thread(tk_ok, settings, tmp_path):
    answers, asked_on = [], []

    def fake_sync_all(s, api, ctx, on_result, lfs_available):
        answers.append(ctx.confirm("質問"))
        return []

    w = make_window(settings, tmp_path, sync_all=fake_sync_all)

    def ask(q):
        asked_on.append((q, threading.current_thread() is threading.main_thread()))
        return True

    w.ask_yes_no = ask
    w.on_sync_clicked()
    pump_until_idle(w)
    assert answers == [True] and asked_on == [("質問", True)]
    w.destroy()


def test_sync_abort_message_is_shown_and_button_reenabled(tk_ok, settings, tmp_path):
    def fake_sync_all(*a, **k):
        raise SyncAbort("GitHub のログインが切れています。")

    w = make_window(settings, tmp_path, sync_all=fake_sync_all)
    w.on_sync_clicked()
    pump_until_idle(w)
    assert "ログインが切れています" in w.message_text()
    assert str(w.main_button["state"]) == "normal"
    w.destroy()


def test_detail_log_lists_git_commands(tk_ok, settings, tmp_path):
    def fake_sync_all(s, api, ctx, on_result, lfs_available):
        ctx.log.append(CommandLog(Path("."), ["push", "origin", "main"], "セーブしたものを GitHub に送る", 0, "", ""))
        return []

    w = make_window(settings, tmp_path, sync_all=fake_sync_all)
    w.on_sync_clicked()
    pump_until_idle(w)
    assert "$ git push origin main  — セーブしたものを GitHub に送る" in w.log_text()
    w.destroy()


def test_gc_runs_only_on_ui_thread_while_worker_runs(tk_ok, settings, tmp_path):
    """裏の処理の最中に Python の後片付け（GC）が走ると、Mac で画面の部品が壊れて落ちる。"""
    import gc
    seen = []

    def fake_sync_all(*a, **k):
        seen.append(gc.isenabled())
        return []

    w = make_window(settings, tmp_path, sync_all=fake_sync_all)
    w.on_sync_clicked()
    pump_until_idle(w)
    assert seen == [False] and gc.isenabled()
    w.destroy()


def test_settings_dialog_loads_repo_list_without_touching_tk_from_thread(tk_ok, settings, tmp_path, monkeypatch):
    from peers_sync.models import RemoteRepo
    from peers_sync.ui.settings_dialog import SettingsDialog

    class Api(FakeApi):
        def list_owned_repos(self):
            return [RemoteRepo("me/a", "a", "u", False, False)]

    w = make_window(settings, tmp_path, make_api=lambda t: Api())
    d = SettingsDialog(w)
    orig_after = d.after
    import threading as th

    def guarded_after(*a, **k):
        assert th.current_thread() is th.main_thread(), "Tk を裏のスレッドから触った"
        return orig_after(*a, **k)

    monkeypatch.setattr(d, "after", guarded_after)
    end = time.time() + 5
    while "me/a" not in d.repo_vars and time.time() < end:
        w.update()
        time.sleep(0.02)
    assert "me/a" in d.repo_vars
    w.destroy()


def test_conflict_dialog_runs_git_off_ui_thread_and_passes_lfs_flag(tk_ok, settings, tmp_path):
    import threading as th
    from peers_sync.conflicts import ConflictFile
    from peers_sync.models import SyncContext
    from peers_sync.ui.conflict_dialog import ConflictDialog

    seen = {}
    done = []

    def start(path, ctx, lfs_available):
        seen["start_main"] = th.current_thread() is th.main_thread()
        seen["lfs"] = lfs_available
        return [ConflictFile("README.md", "text", "B", "A")]

    def finish(path, choices, ctx, full_name=None):
        seen["finish_main"] = th.current_thread() is th.main_thread()
        seen["choices"] = choices
        return res(full_name, Status.PUSHED_AND_PULLED)

    w = make_window(settings, tmp_path)
    ctx = SyncContext("PC", "me", confirm=lambda q: True)
    d = ConflictDialog(w, res("me/r", Status.CONFLICT), ctx,
                       on_done=lambda r: done.append((r.status, th.current_thread() is th.main_thread())),
                       lfs_available=False, start=start, finish=finish, cancel=lambda p: None)
    end = time.time() + 5
    while not d.choices and time.time() < end:
        w.update(); time.sleep(0.02)
    assert seen == {"start_main": False, "lfs": False}
    d.on_decide()
    while not done and time.time() < end:
        w.update(); time.sleep(0.02)
    assert seen["finish_main"] is False and seen["choices"] == {"README.md": "mine"}
    assert done == [(Status.PUSHED_AND_PULLED, True)]
    w.destroy()
