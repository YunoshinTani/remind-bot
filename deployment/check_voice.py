"""音声用依存の読み込みと暗号化を確認する。Discordへの接続・投稿はしない。"""
from __future__ import annotations

from importlib.metadata import version


def check() -> None:
    import nacl.secret
    import nacl.utils
    from discord.voice_client import has_nacl
    from discord.voice_state import has_dave

    if not has_nacl or not has_dave:
        raise RuntimeError("Discordから音声用ライブラリを読み込めませんでした。")
    box = nacl.secret.Aead(nacl.utils.random(nacl.secret.Aead.KEY_SIZE))
    if box.decrypt(box.encrypt(b"voice dependency check")) != b"voice dependency check":
        raise RuntimeError("PyNaClの暗号化・復号を確認できませんでした。")
    print(f"VOICE OK: PyNaCl {version('PyNaCl')} / davey {version('davey')}")
    print("追加ライブラリを読み込めました。Discordへの接続・投稿は行っていません。")


if __name__ == "__main__":
    check()
