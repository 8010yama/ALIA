"""
ALIA - CLI版
assistant_core.py の共通ロジックを使って、コマンドプロンプト上で会話する。

起動方法:
    python main.py

終了方法:
    「exit」または「quit」と入力、もしくは Ctrl+C
"""

import webbrowser

from assistant_core import new_conversation, process_message
from voice_utils import speak

conversation_history = new_conversation()


def _on_tool_event(name: str, args: dict, result: str):
    print(f"[ツール呼び出し: {name}({args})]")
    print(f"[ツール結果: {result}]")


def _make_on_token():
    """最初のトークンが来たタイミングで'ALIA  > 'プレフィックスを出す"""
    state = {"prefix_printed": False}

    def on_token(token: str):
        if not state["prefix_printed"]:
            print("ALIA  > ", end="", flush=True)
            state["prefix_printed"] = True
        print(token, end="", flush=True)

    return on_token


def main():
    print("=== ALIA CLI (gemma4:12b / Function Calling対応) ===")
    print("終了するには 'exit' または 'quit' と入力してください。\n")

    while True:
        try:
            user_input = input("あなた > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n終了します。")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            print("終了します。")
            break

        context = {}
        final_text = process_message(
            conversation_history,
            user_input,
            on_tool_event=_on_tool_event,
            on_token=_make_on_token(),
            context=context,
        )
        print()
        if "map_url" in context:
            webbrowser.open(context["map_url"])
        speak(final_text)
        print()


if __name__ == "__main__":
    main()