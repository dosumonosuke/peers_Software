"""画面に出す日本語の文章。"""
from __future__ import annotations

from .models import RepoResult, Status


def status_text(r: RepoResult) -> str:
    s = r.status
    if s is Status.PUSHED:
        return f"✅ セーブして送信 ({r.committed_files})"
    if s is Status.PULLED:
        return f"✅ 最新を取り込み ({r.pulled_commits})"
    if s is Status.PUSHED_AND_PULLED:
        return f"✅ 送信 ({r.committed_files})・取り込み ({r.pulled_commits})"
    if s is Status.CLONED:
        return "⬇️ 新しくダウンロード"
    if s is Status.UP_TO_DATE:
        return "➖ 変更なし"
    if s is Status.CONFLICT:
        return "⚠️ 競合あり → 確認する"
    if s is Status.SKIPPED:
        return "⏭ 同期しない設定"
    return f"❌ エラー: {r.error or '原因がわかりませんでした'}"


LFS_MISSING = ("Git LFS が見つかりません。動画などの大きいファイルは送れません。"
               "https://git-lfs.com からインストールして、このアプリを開き直してください。")

# (stderr に含まれる言葉, 日本語の説明)。上から順に探す。
_RULES = [
    (("authentication failed", "could not read username", "invalid username or password",
      "permission denied (publickey)", "returned error: 403", "returned error: 401"),
     "GitHub のログインが切れているか、権限がありません。設定から、もう一度ログインしてください。"),
    (("unable to unlink", "unable to create", "permission denied", "device or resource busy",
      "could not open", "cannot open"),
     "ファイルがほかのアプリで開かれているか、書きこめません。そのアプリを閉じてから、もう一度同期してください。"),
    (("could not resolve host", "unable to access", "connection timed out", "failed to connect",
      "network is unreachable"),
     "GitHub につながりません。インターネットの接続を確かめてから、もう一度同期してください。"),
    (("over its data quota", "exceeded its lfs", "bandwidth limit"),
     "Git LFS の無料枠（容量 10GB・月 10GB）を使い切りました。GitHub の設定で枠を増やすか、大きいファイルを減らしてください。"),
    (("exceeds github's file size limit", "large files detected"),
     "100MB を超えるファイルがあり、GitHub が受け取りませんでした。そのファイルを外してから、もう一度同期してください。"),
    (("[rejected]", "fetch first", "non-fast-forward", "failed to push some refs"),
     "同期している間に、ほかのパソコンから GitHub が更新されました。もう一度「ぜんぶ同期」を押してください。"),
    (("repository not found", "does not appear to be a git repository"),
     "GitHub にこのリポジトリが見つかりません。名前が変わったか、消された可能性があります。"),
    (("would be overwritten by merge", "untracked working tree files would be overwritten"),
     "送らなかったファイル（パスワードっぽいファイルなど）と、GitHub 側の変更がぶつかっています。"
     "そのファイルを別の場所に移してから、もう一度同期してください。"),
    (("no space left on device",),
     "パソコンの空き容量が足りません。いらないファイルを消してから、もう一度同期してください。"),
]


def explain_error(stderr: str) -> str:
    low = stderr.lower()
    for words, text in _RULES:
        if any(w in low for w in words):
            return text
    return "Git でエラーが起きました。くわしいログを見てください。"
