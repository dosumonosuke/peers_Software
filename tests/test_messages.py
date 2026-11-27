from pathlib import Path

import pytest

from peers_sync.messages import explain_error, status_text
from peers_sync.models import RepoResult, Status


def res(status, committed=0, pulled=0, error=None):
    return RepoResult("me/a", Path("a"), status, committed, pulled, error=error)


def test_status_text():
    assert status_text(res(Status.PUSHED, committed=3)) == "✅ セーブして送信 (3)"
    assert status_text(res(Status.PULLED, pulled=5)) == "✅ 最新を取り込み (5)"
    assert status_text(res(Status.PUSHED_AND_PULLED, committed=1, pulled=2)) == "✅ 送信 (1)・取り込み (2)"
    assert status_text(res(Status.CLONED)) == "⬇️ 新しくダウンロード"
    assert status_text(res(Status.UP_TO_DATE)) == "➖ 変更なし"
    assert status_text(res(Status.CONFLICT)) == "⚠️ 競合あり → 確認する"
    assert status_text(res(Status.SKIPPED)) == "⏭ 同期しない設定"
    assert status_text(res(Status.ERROR, error="ネット")) == "❌ エラー: ネット"


@pytest.mark.parametrize("stderr,word", [
    ("fatal: Authentication failed for 'https://github.com/x'", "ログイン"),
    ("fatal: could not read Username for 'https://github.com'", "ログイン"),
    ("fatal: unable to access '...': Could not resolve host: github.com", "インターネット"),
    ("! [rejected] main -> main (fetch first)", "もう一度"),
    ("remote: error: File big.bin is 120.00 MB; this exceeds GitHub's file size limit of 100.00 MB", "100MB"),
    ("batch response: This repository is over its data quota.", "無料枠"),
    ("something odd", "くわしいログ"),
])
def test_explain_error(stderr, word):
    assert word in explain_error(stderr)
