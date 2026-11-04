"""
ALIA - Notion連携モジュール
メモ: 指定ページに段落ブロックとして追記
TODO: 指定ページにチェックボックス付き(to_do)ブロックとして追記

内容の抽出（何をメモ/TODOとして書くか）はgemma4のFunction Callingに任せるため、
ここでは「Notionへの書き込み実行」のみを担当するシンプルな構成にしている。
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_MEMO_PAGE_ID = os.getenv("NOTION_MEMO_PAGE_ID")
NOTION_TODO_PAGE_ID = os.getenv("NOTION_TODO_PAGE_ID")

NOTION_VERSION = "2022-06-28"


def write_to_notion(content: str, memo_type: str = "memo") -> str:
    """
    Notionページにブロックを追記する。
    memo_type: "memo" (段落として追記) または "todo" (チェックボックス付きで追記)
    戻り値: 結果を表す短いテキスト（LLMへのツール実行結果として渡す）
    """
    if not NOTION_TOKEN:
        return "NOTION_TOKENが.envに設定されていません。"

    page_id = NOTION_TODO_PAGE_ID if memo_type == "todo" else NOTION_MEMO_PAGE_ID
    if not page_id:
        label = "NOTION_TODO_PAGE_ID" if memo_type == "todo" else "NOTION_MEMO_PAGE_ID"
        return f"{label}が.envに設定されていません。"

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }

    if memo_type == "todo":
        block = {
            "object": "block",
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "text": {"content": content}}],
                "checked": False,
            },
        }
    else:
        block = {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": content}}]
            },
        }

    try:
        res = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=headers,
            json={"children": [block]},
            timeout=10,
        )
        if res.status_code == 200:
            label = "TODO" if memo_type == "todo" else "メモ"
            return f"{label}として「{content}」をNotionに保存しました。"

        return f"Notion書き込みエラー ({res.status_code}): {res.text[:200]}"

    except Exception as e:
        return f"Notion書き込み中に例外が発生しました: {type(e).__name__}: {e}"