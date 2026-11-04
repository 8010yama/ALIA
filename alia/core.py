"""
ALIA - 中心の処理

LLM（Ollama）に会話とツールの一覧を渡し、LLM が選んだツールを実行して、その結果をもとに
最後の返事を作らせる。PC のコマンド版（cli.py）とスマホ用のサーバー（server.py）の両方から使う。

    ユーザーの発話 → LLM「天気のツールを、place=大阪 で呼びたい」
                  → dispatch_tool_call で実行 → 結果を LLM に返す → LLM が話し言葉で返事
"""

import datetime
import json

import httpx

from alia import config
from alia.tools.calendar import add_calendar_event, list_calendar_events, update_calendar_event
from alia.tools.maps import find_nearest_station, geocode_place, get_map_url
from alia.tools.notion import write_to_notion
from alia.tools.train import get_next_trains, get_train_route
from alia.tools.weather import get_weather_info

_WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]

# 1回の発話で、ツールを続けて呼べる回数の上限（LLM が同じツールを呼び続けて止まらないのを防ぐ）
MAX_TOOL_ROUNDS = 4


# ============ ツール定義（Ollamaのtools形式） ============

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "天気・最高/最低気温・降水確率・雨の時間帯を取得する（今日から6日先まで）。天気・雨・気温・傘について聞かれたら使う。場所の指定がなければ現在地（なければ自宅）の天気になる。",
            "parameters": {
                "type": "object",
                "properties": {
                    "place": {"type": "string", "description": "「大阪」「箱根」のように場所を指定されたときの地名。指定がなければ空文字でよい。"},
                    "date": {"type": "string", "description": "調べたい日付。'YYYY-MM-DD'形式。「明日」「土曜日」などは日付に直して渡す。今日なら空文字でよい。"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_train",
            "description": "駅から出る次の電車の発車時刻と、あと何分かを取得する。駅名の指定がなければ現在地の最寄り駅（なければ自宅の最寄り駅）。行き先や方面の指定がなければ、いつもの行き先（通勤・通学先）方面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "station": {"type": "string", "description": "乗る駅の名前。指定がなければ空文字でよい。"},
                    "to_station": {"type": "string", "description": "行き先・方面の駅名（「渋谷方面」なら渋谷）。指定がなければ空文字でよい。"},
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

def _resolve_default_station(context: dict | None) -> str:
    """
    駅名の指定がないときの出発駅。スマホの現在地（context の lat/lon）があればその最寄り駅、
    なければ .env の HOME_STATION。同じリクエストの中では検索結果を使い回す。
    """
    if not context:
        return config.HOME_STATION

    if "_nearest_station" not in context:
        lat, lon = context.get("lat"), context.get("lon")
        context["_nearest_station"] = find_nearest_station(lat, lon) if lat is not None and lon is not None else None

    return context["_nearest_station"] or config.HOME_STATION


def _weather_location(place: str, context: dict | None) -> tuple[float, float, str]:
    """天気を調べる場所：指定された地名 → スマホの現在地 → 自宅 の順に決める"""
    if place:
        lat, lon = geocode_place(place)
        if lat is not None:
            return lat, lon, place
    if context and context.get("lat") is not None and context.get("lon") is not None:
        return context["lat"], context["lon"], "現在地"
    return config.HOME_LAT, config.HOME_LON, "自宅"


def dispatch_tool_call(name: str, arguments: dict, context: dict = None) -> str:
    """
    ツール名と引数を受け取り、対応するPython関数を実行して結果テキストを返す。
    context: 呼び出し元(CLI/サーバー)に副次情報を伝えるための辞書(例: map_url)。
    """
    try:
        if name == "get_weather":
            place = (arguments.get("place") or "").strip()
            date_text = (arguments.get("date") or "").strip()
            date = None
            if date_text:
                try:
                    date = datetime.datetime.strptime(date_text, "%Y-%m-%d").date()
                except ValueError:
                    return f"日付の形式が正しくありません（YYYY-MM-DD で指定）: {date_text}"
            lat, lon, label = _weather_location(place, context)
            note = f"（「{place}」が見つからなかったので{label}の天気）" if place and label != place else ""
            return f"場所: {label}{note}\n{get_weather_info(lat, lon, date)}"

        elif name == "get_next_train":
            station = arguments.get("station") or _resolve_default_station(context)
            to_station = arguments.get("to_station") or config.COMMUTE_STATION or None
            return get_next_trains(station, to_station)

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
            "open_mapツールを呼んだ後は「地図を用意しました」のように答え、"
            "「開きました」のように既に画面が開いたと断定する言い方はしないでください。"
            "「予定を追加して」「カレンダーに入れて」と言われたらadd_calendar_eventツールを、"
            "「今日の予定は？」「予定を教えて」と言われたらlist_calendar_eventsツールを、"
            "「予定の時間を変更して」と言われたらupdate_calendar_eventツールを呼び出してください。"
            "ツールの結果を見てから、続けて別のツールを呼んでもかまいません"
            "（例: 予定の一覧を確認してから、空いている時間に予定を追加する）。"
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


def _refresh_system_prompt(history: list) -> None:
    """
    システムプロンプトの日時を今に更新する。
    サーバーは何日も動き続けるので、起動した日の日付のままだと「明日」の解釈がずれる。
    """
    if history and history[0].get("role") == "system":
        history[0] = _build_system_prompt()
    else:
        history.insert(0, _build_system_prompt())


def _trim_history(history: list, max_messages: int | None = None) -> None:
    """
    会話履歴が長くなりすぎないよう、古いものから捨てる（システムプロンプトは残す）。
    ツールの呼び出しとその結果が途中で切れないよう、残す履歴は必ずユーザーの発言から始める。
    """
    max_messages = config.MAX_HISTORY_MESSAGES if max_messages is None else max_messages
    body = history[1:]
    if len(body) <= max_messages:
        return
    body = body[-max_messages:]
    while body and body[0].get("role") != "user":
        body.pop(0)
    history[1:] = body


# ============ Ollama呼び出し ============

def _call_ollama_once(messages: list, use_tools: bool) -> dict:
    """非ストリーミングで1回分のOllamaレスポンスを取得する"""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
    }
    if use_tools:
        payload["tools"] = TOOLS

    res = httpx.post(config.OLLAMA_URL, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()


def _stream_ollama(messages: list, on_token=None) -> str:
    """
    ストリーミングでOllamaのレスポンスを取得する。
    on_token(str) が渡されていれば、トークンが来るたびに呼び出す(CLIでの逐次表示用)。
    戻り値: 全文
    """
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "think": False,
    }

    full_response = ""
    with httpx.stream("POST", config.OLLAMA_URL, json=payload, timeout=120) as response:
        response.raise_for_status()
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


def ask_once(prompt: str) -> str:
    """会話履歴もツールも使わずに、1回だけ LLM に聞く（ナビのときの一言づくりなど）"""
    result = _call_ollama_once([{"role": "user", "content": prompt}], use_tools=False)
    return result.get("message", {}).get("content", "").strip()


# ============ メイン処理 ============

def _run_tool_calls(history: list, tool_calls: list, on_tool_event, context) -> None:
    """LLM が頼んできたツールを順に実行し、結果を会話履歴に足す"""
    for call in tool_calls:
        func = call.get("function", {})
        func_name = func.get("name", "")
        raw_args = func.get("arguments", {})

        # LLM によっては引数が JSON の文字列で来る
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                raw_args = {}
        args = raw_args if isinstance(raw_args, dict) else {}

        tool_result = dispatch_tool_call(func_name, args, context=context)
        if on_tool_event:
            on_tool_event(func_name, args, tool_result)
        history.append({"role": "tool", "tool_name": func_name, "content": tool_result})


def process_message(history: list, user_message: str, on_tool_event=None, on_token=None, context: dict = None) -> str:
    """
    ユーザーの発話を受け取り、ツール呼び出しを含めて処理し、最終回答のテキストを返す。
    historyはこの関数の中で直接更新される(in-place)。

    ツールの結果を見て、さらに別のツールを呼ぶこともできる（「明日の予定を見て、空いている時間に
    ジムを入れて」なら、予定の一覧 → 予定の追加）。ただし MAX_TOOL_ROUNDS 回まで。

    on_tool_event(name: str, args: dict, result: str): ツール実行時に呼ばれるコールバック(ログ表示用)
    on_token(token: str): 最終回答のトークンごとに呼ばれるコールバック(CLI表示用)
    context: dict を渡すと、ツール実行時の副次情報(例: map_url)がin-placeで書き込まれる
    """
    _refresh_system_prompt(history)
    history.append({"role": "user", "content": user_message})
    _trim_history(history)

    for _ in range(MAX_TOOL_ROUNDS):
        message = _call_ollama_once(history, use_tools=True).get("message", {})
        tool_calls = message.get("tool_calls")

        if not tool_calls:
            # ツールが要らなくなったら、それが最後の返事
            content = message.get("content", "")
            if on_token:
                on_token(content)
            history.append({"role": "assistant", "content": content})
            return content

        history.append(message)
        _run_tool_calls(history, tool_calls, on_tool_event, context)

    # 上限まで呼んでもツールを頼み続けるときは、ツールなしで今わかっていることから答えさせる
    final_text = _stream_ollama(history, on_token=on_token)
    history.append({"role": "assistant", "content": final_text})
    return final_text
