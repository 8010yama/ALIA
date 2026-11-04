"""
ALIA - 共通コアロジック
ツール定義・ディスパッチ・Ollamaとの会話処理をまとめたモジュール。
CLI(main.py)とFastAPIサーバー(server.py)の両方から呼び出される。
"""

import os
import json
import datetime
import httpx
from dotenv import load_dotenv

from weather_utils import get_weather_info
from train_utils import get_next_trains, get_train_route
from notion_utils import write_to_notion
from map_utils import open_map, get_map_url, find_nearest_station
from calendar_utils import add_calendar_event, list_calendar_events, update_calendar_event

load_dotenv()

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "gemma4:12b"

HOME_LAT = float(os.getenv("HOME_LAT", "35.6895"))
HOME_LON = float(os.getenv("HOME_LON", "139.6917"))
HOME_STATION = os.getenv("HOME_STATION", "新宿")

_WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]


# ============ ツール定義（Ollamaのtools形式） ============

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "現在地（デフォルトの自宅地点）の本日の気温予報と降水情報を取得する。天気・雨・気温・傘について聞かれたら使う。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_train",
            "description": "指定した駅の次の電車の発車時刻を取得する。駅名の指定がなければ自宅最寄り駅を使う。",
            "parameters": {
                "type": "object",
                "properties": {
                    "station": {"type": "string", "description": "調べたい駅名。指定がなければ空文字でよい。"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_memo",
            "description": "ユーザーが「メモして」「覚えておいて」「書いておいて」などと言ったとき、指定された内容をNotionのメモページに追記する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "メモとして保存する内容のテキスト。"}
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "ユーザーが「TODOに追加して」「タスクに追加して」「やることリストに入れて」などと言ったとき、指定された内容をNotionのTODOページにチェックボックス付きで追記する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "TODOとして保存するタスクの内容。"}
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_train_route",
            "description": "出発駅から目的駅までの乗換案内・経路・所要時間・料金を取得する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_station": {"type": "string", "description": "目的地の駅名（必須）"},
                    "from_station": {"type": "string", "description": "出発駅名。指定がなければ自宅最寄り駅を使う。"},
                    "arrive_time": {"type": "string", "description": "到着希望時刻 (HH:MM形式)。指定がなければ空文字でよい。"},
                },
                "required": ["to_station"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_map",
            "description": "ユーザーが「地図を開いて」「ナビして」「車で行きたい」「運転でのルート教えて」のように、明示的に地図表示や車でのナビを求めたときだけ使う。単に「〜まで行きたい」「行き方を教えて」だけの場合は、電車のツール(get_train_route)を優先すること。",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string", "description": "目的地（駅名・住所・施設名など）。必須。"},
                    "origin": {"type": "string", "description": "出発地。指定がなければ空文字でよい（自宅最寄り駅が使われる）。"},
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_calendar_event",
            "description": "Google Calendarに新しい予定を追加する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "予定のタイトル。"},
                    "start_time": {"type": "string", "description": "開始日時。'YYYY-MM-DD HH:MM'形式（例: '2026-08-17 14:00'）。"},
                    "end_time": {"type": "string", "description": "終了日時。'YYYY-MM-DD HH:MM'形式。指定がなければ空文字でよい（開始の1時間後になる）。"},
                },
                "required": ["summary", "start_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "指定日のGoogle Calendarの予定一覧を取得する。「今日の予定」「明日の予定」などに使う。",
            "parameters": {
                "type": "object",
                "properties": {"date": {"type": "string", "description": "予定を確認したい日付。'YYYY-MM-DD'形式。"}},
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_calendar_event",
            "description": "既存の予定の開始時刻を変更する。予定はタイトルのキーワードと日付で特定する。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary_keyword": {"type": "string", "description": "変更したい予定のタイトルに含まれるキーワード。"},
                    "date": {"type": "string", "description": "予定が存在する日付。'YYYY-MM-DD'形式。"},
                    "new_start_time": {"type": "string", "description": "新しい開始日時。'YYYY-MM-DD HH:MM'形式。"},
                },
                "required": ["summary_keyword", "date", "new_start_time"],
            },
        },
    },
]


# ============ ツール実行のディスパッチ ============

def _resolve_default_station(context: dict) -> str:
    """
    駅名の指定がないとき、context(現在地lat/lon)があれば最寄り駅を検索し、
    無ければ.envのHOME_STATIONにフォールバックする。同一リクエスト内では結果をキャッシュする。
    """
    if not context:
        return HOME_STATION

    if "_nearest_station" in context:
        return context["_nearest_station"] or HOME_STATION

    lat = context.get("lat")
    lon = context.get("lon")
    if lat is not None and lon is not None:
        station = find_nearest_station(lat, lon)
        context["_nearest_station"] = station
        if station:
            return station

    context["_nearest_station"] = None
    return HOME_STATION


def dispatch_tool_call(name: str, arguments: dict, context: dict = None) -> str:
    """
    ツール名と引数を受け取り、対応するPython関数を実行して結果テキストを返す。
    context: 呼び出し元(CLI/サーバー)に副次情報を伝えるための辞書(例: map_url)。
    """
    try:
        if name == "get_weather":
            return get_weather_info(HOME_LAT, HOME_LON)

        elif name == "get_next_train":
            station = arguments.get("station") or _resolve_default_station(context)
            return get_next_trains(station)

        elif name == "add_memo":
            content = arguments.get("content")
            if not content:
                return "メモの内容が指定されていません。"
            return write_to_notion(content, memo_type="memo")

        elif name == "add_todo":
            content = arguments.get("content")
            if not content:
                return "TODOの内容が指定されていません。"
            return write_to_notion(content, memo_type="todo")

        elif name == "get_train_route":
            to_station = arguments.get("to_station")
            from_station = arguments.get("from_station") or _resolve_default_station(context)
            arrive_time = arguments.get("arrive_time") or None
            if not to_station:
                return "目的地の駅名が指定されていません。"
            return get_train_route(from_station, to_station, arrive_time)

        elif name == "open_map":
            destination = arguments.get("destination")
            origin = arguments.get("origin") or None
            if not destination:
                return "目的地が指定されていません。"
            url = get_map_url(destination, origin)
            if context is not None:
                context["map_url"] = url
            return f"{destination}までの地図を用意しました。"

        elif name == "add_calendar_event":
            summary = arguments.get("summary")
            start_time = arguments.get("start_time")
            end_time = arguments.get("end_time") or None
            if not summary or not start_time:
                return "予定のタイトルまたは開始時刻が指定されていません。"
            return add_calendar_event(summary, start_time, end_time)

        elif name == "list_calendar_events":
            date = arguments.get("date")
            if not date:
                return "日付が指定されていません。"
            return list_calendar_events(date)

        elif name == "update_calendar_event":
            summary_keyword = arguments.get("summary_keyword")
            date = arguments.get("date")
            new_start_time = arguments.get("new_start_time")
            if not (summary_keyword and date and new_start_time):
                return "予定の特定・変更に必要な情報が不足しています。"
            return update_calendar_event(summary_keyword, date, new_start_time)

        else:
            return f"未知のツールです: {name}"

    except Exception as e:
        return f"ツール実行エラー ({name}): {type(e).__name__}: {e}"


# ============ システムプロンプト ============

def _build_system_prompt() -> dict:
    """現在日時を埋め込んだシステムプロンプトを生成する（呼び出すたびに最新の日時になる）"""
    now = datetime.datetime.now()
    return {
        "role": "system",
        "content": (
            "あなたはALIAという名前のAIアシスタントです。"
            f"今日の日付は{now.strftime('%Y-%m-%d')}（{_WEEKDAYS_JA[now.weekday()]}曜日）、"
            f"現在時刻は{now.strftime('%H:%M')}です。"
            "「明日」「来週の月曜」などの相対的な日付表現は、この日付を基準に正しい YYYY-MM-DD 形式に変換してツールに渡してください。"
            "天気・電車について聞かれたら、必ず対応するツールを呼び出して最新情報を取得してから答えてください。"
            "「メモして」「覚えておいて」と言われたらadd_memoツールを、"
            "「TODOに追加して」「タスクに追加して」と言われたらadd_todoツールを呼び出してください。"
            "移動に関する質問への対応方針: 基本の移動手段は電車です。"
            "「〜まで行きたい」「〜への行き方」「〜に行くには」のような一般的な移動の相談は、"
            "特に車や地図を明示的に求めていない限り、get_train_routeまたはget_next_trainツールを使ってください。"
            "open_mapツールは、「ナビして」「地図で見せて」「車で行きたい」「運転で」のように、"
            "ユーザーが明示的に地図表示や車でのルートを求めたときだけ呼び出してください。"
            "「地図を開いて」「ナビして」「行き方教えて」と言われたらopen_mapツールを呼び出してください。"
            "open_mapツールを呼んだ後は「地図を用意しました」のように答え、"
            "「開きました」のように既に画面が開いたと断定する言い方はしないでください。"
            "「予定を追加して」「カレンダーに入れて」と言われたらadd_calendar_eventツールを、"
            "「今日の予定は？」「予定を教えて」と言われたらlist_calendar_eventsツールを、"
            "「予定の時間を変更して」と言われたらupdate_calendar_eventツールを呼び出してください。"
            "重要: 過去の会話で似た内容のメモやTODOを保存していたとしても、"
            "新しい依頼があるたびに必ず毎回ツールを実際に呼び出してください。"
            "ツールを呼ばずに「保存しました」「メモしたよ」と答えることは絶対に禁止です。"
            "ツールの結果がエラーや失敗を示している場合は、成功したかのように答えず、"
            "何が失敗したかを正直にユーザーに伝えてください。"
            "ツールの結果を元に、簡潔で自然な日本語の話し言葉で答えてください。"
            "長々とした説明よりも、要点を押さえた短い返答を優先してください。"
        ),
    }


def new_conversation() -> list:
    """新しい会話履歴(システムプロンプト付き)を生成する"""
    return [_build_system_prompt()]


# ============ Ollama呼び出し ============

def _call_ollama_once(messages: list, use_tools: bool) -> dict:
    """非ストリーミングで1回分のOllamaレスポンスを取得する"""
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
        "think": False,
    }
    if use_tools:
        payload["tools"] = TOOLS

    res = httpx.post(OLLAMA_URL, json=payload, timeout=30)
    res.raise_for_status()
    return res.json()


def _stream_ollama(messages: list, on_token=None) -> str:
    """
    ストリーミングでOllamaのレスポンスを取得する。
    on_token(str) が渡されていれば、トークンが来るたびに呼び出す(CLIでの逐次表示用)。
    戻り値: 全文
    """
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": True,
        "think": False,
    }

    full_response = ""
    with httpx.stream("POST", OLLAMA_URL, json=payload, timeout=None) as response:
        for line in response.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            content_piece = chunk.get("message", {}).get("content", "")
            if content_piece:
                full_response += content_piece
                if on_token:
                    on_token(content_piece)
            if chunk.get("done"):
                break

    return full_response


# ============ メイン処理 ============

def process_message(history: list, user_message: str, on_tool_event=None, on_token=None, context: dict = None) -> str:
    """
    ユーザーの発話を受け取り、ツール呼び出しを含めて処理し、最終回答のテキストを返す。
    historyはこの関数の中で直接更新される(in-place)。

    on_tool_event(name: str, args: dict, result: str): ツール実行時に呼ばれるコールバック(ログ表示用)
    on_token(token: str): 最終回答のストリーミング中、トークンごとに呼ばれるコールバック(CLI表示用)
    context: dict を渡すと、ツール実行時の副次情報(例: map_url)がin-placeで書き込まれる
    """
    history.append({"role": "user", "content": user_message})

    # ① まずツールが必要か判断させる
    first_result = _call_ollama_once(history, use_tools=True)
    message = first_result.get("message", {})
    tool_calls = message.get("tool_calls")

    if tool_calls:
        history.append(message)

        for call in tool_calls:
            func_name = call["function"]["name"]
            raw_args = call["function"].get("arguments", {})

            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = raw_args

            tool_result = dispatch_tool_call(func_name, args, context=context)

            if on_tool_event:
                on_tool_event(func_name, args, tool_result)

            history.append({"role": "tool", "content": tool_result})

        # ② ツール結果を踏まえた最終回答をストリーミングで生成
        final_text = _stream_ollama(history, on_token=on_token)
        history.append({"role": "assistant", "content": final_text})
        return final_text

    else:
        content = message.get("content", "")
        if on_token:
            on_token(content)
        history.append({"role": "assistant", "content": content})
        return content