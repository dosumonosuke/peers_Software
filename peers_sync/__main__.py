"""アプリの入口。`python -m peers_sync` で起動する。"""
from __future__ import annotations


def main() -> None:
    from . import credentials, installer
    from .config import load_settings
    from .engine import check_prerequisites, ensure_tool_path, sync_all
    from .github_api import GitHubClient
    from .ui.main_window import MainWindow, Services

    ensure_tool_path()
    services = Services(
        sync_all=sync_all,
        get_token=credentials.get_stored_token,
        login=credentials.login,
        logout=credentials.logout,
        make_api=GitHubClient,
        prerequisites=check_prerequisites,
        missing_tools=installer.missing_tools,
        install=installer.install_missing,
    )
    MainWindow(load_settings(), services).mainloop()


if __name__ == "__main__":
    main()
