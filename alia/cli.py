"""
ALIA - PC 用（コマンドプロンプトで会話する）

起動:
    python -m alia.cli            # キーボードで話しかける
    python -m alia.cli --voice    # マイクで話しかける（Enter で録音開始、もう一度 Enter で終了）
    python -m alia.cli --mute     # 返事を読み上げない

終了: 「exit」か「quit」と入力、または Ctrl+C
地図を開くよう頼むと、ブラウザで経路の地図を開く。返事は VOICEVOX で読み上げる（Windows）。
"""

import argparse
import importlib.util
import webbrowser

from alia import config
from alia.core import new_conversation, process_message
from alia.speech import speak

EXIT_WORDS = {"exit", "quit", "終了", "おわり"}


def _on_tool_event(name: str, args: dict, result: str):
    print(f"  [ツール: {name}({args})]")
    print(f"  [結果: {result}]")


def _make_on_token():
    """最初の文字が来たときに 'ALIA  > ' を出してから、返事を少しずつ表示する"""
    state = {"started": False}

    def on_token(token: str):
        if not state["started"]:
            print("ALIA  > ", end="", flush=True)
            state["started"] = True
        print(token, end="", flush=True)

    return on_token


def _read_text() -> str:
    return input("あなた > ").strip()


def _read_voice() -> str:
    from alia.speech import listen

    input("（Enter で録音開始）")
    text = listen()
    print(f"あなた > {text}" if text else "（聞き取れませんでした）")
    return text


def main():
    parser = argparse.ArgumentParser(description="ALIA を PC で使う")
    parser.add_argument("--voice", action="store_true", help="マイクで話しかける")
    parser.add_argument("--mute", action="store_true", help="返事を読み上げない")
    args = parser.parse_args()

    print(f"=== ALIA（{config.OLLAMA_MODEL}）===")
    print("終了するには exit と入力するか Ctrl+C を押してください。\n")

    if args.voice:
        if not all(importlib.util.find_spec(m) for m in ("faster_whisper", "sounddevice")):
            print("音声入力に必要なパッケージがありません。次で入れてください:\n"
                  "    pip install -r requirements-voice.txt")
            return

    read = _read_voice if args.voice else _read_text
    history = new_conversation()

    while True:
        try:
            user_input = read()
        except (KeyboardInterrupt, EOFError):
            print("\n終了します。")
            break

        if not user_input:
            continue
        if user_input.lower() in EXIT_WORDS:
            print("終了します。")
            break

        context = {}
        try:
            reply = process_message(
                history,
                user_input,
                on_tool_event=_on_tool_event,
                on_token=_make_on_token(),
                context=context,
            )
        except Exception as e:
            print(f"\n[エラー] LLM に聞けませんでした（Ollama が動いているか確認してください）: {type(e).__name__}: {e}\n")
            continue
        print()

        if "map_url" in context:
            webbrowser.open(context["map_url"])
        if not args.mute:
            speak(reply)
        print()


if __name__ == "__main__":
    main()
