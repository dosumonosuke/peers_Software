"""裏の処理の最中は、Python の後片付け（GC）を止めておく係。

後片付けが裏のスレッドで走ると、画面の部品がそのスレッドで壊されて、Mac でアプリが落ちることがある。
いくつかの裏の処理が重なっても大丈夫なように、止めた回数を数えて、全部終わったら画面のスレッドで片付ける。
"""
from __future__ import annotations

import gc
import threading

_lock = threading.Lock()
_count = 0


def pause() -> None:
    global _count
    with _lock:
        _count += 1
        gc.disable()


def resume() -> None:
    """画面のスレッドから呼ぶ。"""
    global _count
    with _lock:
        _count = max(0, _count - 1)
        if _count:
            return
        gc.enable()
    gc.collect()
