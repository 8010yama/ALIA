"""中心の処理：ツールの振り分け、LLM との会話の流れ、会話履歴の扱い"""

import datetime
import json

import pytest

from alia import core


# ---------------- ツールの振り分け（dispatch_tool_call） ----------------

def test_unknown_tool():
    assert "未知のツール" in core.dispatch_tool_call("fly_to_the_moon", {})


@pytest.mark.parametrize("name", ["add_memo", "add_todo"])
def test_memo_without_content_is_not_saved(monkeypatch, name):
    monkeypatch.setattr(core, "write_to_notion", lambda *a, **k: pytest.fail("保存してはいけない"))
    assert "指定されていません" in core.dispatch_tool_call(name, {"content": ""})


def test_memo_and_todo_go_to_the_right_page(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "write_to_notion", lambda content, memo_type: calls.append((content, memo_type)) or "ok")
    core.dispatch_tool_call("add_memo", {"content": "牛乳を買う"})
    core.dispatch_tool_call("add_todo", {"content": "レポート"})
    assert calls == [("牛乳を買う", "memo"), ("レポート", "todo")]


def test_tool_error_is_reported_not_raised(monkeypatch):
    def broken(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(core, "get_next_trains", broken)
    result = core.dispatch_tool_call("get_next_train", {"station": "渋谷"})
    assert "ツール実行エラー" in result and "network down" in result


def test_open_map_puts_url_in_context():
    context = {}
    result = core.dispatch_tool_call("open_map", {"destination": "横浜駅"}, context=context)
    assert "横浜駅" in result
    assert context["map_url"].startswith("http://maps.apple.com/")


# ---------------- 出発駅・方面の決め方 ----------------

@pytest.fixture
def next_train_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "get_next_trains", lambda st, to=None: calls.append((st, to)) or "ok")
    return calls


def test_station_defaults_to_home_without_location(next_train_calls):
    core.dispatch_tool_call("get_next_train", {}, context={})
    assert next_train_calls == [("新宿", None)]


def test_next_train_uses_commute_station(monkeypatch, next_train_calls, test_config):
    """方面を言わなければ、いつもの行き先（.env の COMMUTE_STATION）方面"""
    monkeypatch.setattr(test_config, "COMMUTE_STATION", "御茶ノ水")
    core.dispatch_tool_call("get_next_train", {})
    core.dispatch_tool_call("get_next_train", {"to_station": "立川"})
    assert next_train_calls == [("新宿", "御茶ノ水"), ("新宿", "立川")]


def test_station_uses_phone_location_and_caches_lookup(monkeypatch):
    lookups, routes = [], []
    monkeypatch.setattr(core, "find_nearest_station", lambda lat, lon: lookups.append((lat, lon)) or "三鷹")
    monkeypatch.setattr(core, "get_train_route", lambda frm, to, at: routes.append((frm, to, at)) or "ok")

    context = {"lat": 35.7, "lon": 139.56}
    core.dispatch_tool_call("get_train_route", {"to_station": "東京"}, context=context)
    core.dispatch_tool_call("get_train_route", {"to_station": "品川", "arrive_time": "10:00"}, context=context)

    assert routes == [("三鷹", "東京", None), ("三鷹", "品川", "10:00")]
    assert lookups == [(35.7, 139.56)]  # 同じリクエストの中では1回だけ調べる


def test_station_falls_back_to_home_when_lookup_fails(monkeypatch, next_train_calls):
    monkeypatch.setattr(core, "find_nearest_station", lambda lat, lon: None)
    core.dispatch_tool_call("get_next_train", {}, context={"lat": 1.0, "lon": 2.0})
    assert next_train_calls == [("新宿", None)]


# ---------------- 天気の場所と日付の決め方 ----------------

@pytest.fixture
def weather_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "get_weather_info", lambda lat, lon, date=None: calls.append((lat, lon, date)) or "晴れ")
    return calls


def test_weather_for_named_place(monkeypatch, weather_calls):
    monkeypatch.setattr(core, "geocode_place", lambda place: (34.69, 135.50))
    result = core.dispatch_tool_call("get_weather", {"place": "大阪"}, context={"lat": 35.7, "lon": 139.7})
    assert weather_calls == [(34.69, 135.50, None)]
    assert result.startswith("場所: 大阪")


def test_weather_uses_phone_location(weather_calls):
    result = core.dispatch_tool_call("get_weather", {}, context={"lat": 35.7, "lon": 139.7})
    assert weather_calls == [(35.7, 139.7, None)]
    assert result.startswith("場所: 現在地")


def test_weather_defaults_to_home(weather_calls):
    core.dispatch_tool_call("get_weather", {})
    assert weather_calls == [(35.0, 139.0, None)]


def test_weather_for_a_date(weather_calls):
    core.dispatch_tool_call("get_weather", {"date": "2026-11-05"})
    assert weather_calls == [(35.0, 139.0, datetime.date(2026, 11, 5))]


def test_weather_bad_date(weather_calls):
    assert "形式が正しくありません" in core.dispatch_tool_call("get_weather", {"date": "明日"})
    assert weather_calls == []


def test_weather_unknown_place_says_so(monkeypatch, weather_calls):
    monkeypatch.setattr(core, "geocode_place", lambda place: (None, None))
    result = core.dispatch_tool_call("get_weather", {"place": "存在しない町"})
    assert weather_calls == [(35.0, 139.0, None)]
    assert "見つからなかった" in result


# ---------------- LLM との会話の流れ（process_message） ----------------

class FakeOllama:
    """LLM の答えを、呼ばれた順に決めておく（ツールを選ぶ → 返事、またはツールを何回か → 返事）"""

    def __init__(self, messages, final_text="（ツールなしの返事）"):
        self.messages = list(messages)
        self.final_text = final_text
        self.calls = []
        self.stream_calls = []

    def call_once(self, messages, use_tools):
        self.calls.append(([dict(m) for m in messages], use_tools))
        return {"message": self.messages.pop(0)}

    def stream(self, messages, on_token=None):
        self.stream_calls.append([dict(m) for m in messages])
        if on_token:
            on_token(self.final_text)
        return self.final_text


@pytest.fixture
def ollama(monkeypatch):
    def install(*messages, final_text="（ツールなしの返事）"):
        fake = FakeOllama(messages, final_text)
        monkeypatch.setattr(core, "_call_ollama_once", fake.call_once)
        monkeypatch.setattr(core, "_stream_ollama", fake.stream)
        return fake

    return install


def _tool_call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def _answer(text):
    return {"role": "assistant", "content": text}


def test_reply_without_tools(ollama):
    fake = ollama(_answer("こんにちは！"))
    history = core.new_conversation()
    assert core.process_message(history, "やあ") == "こんにちは！"
    assert [m["role"] for m in history] == ["system", "user", "assistant"]
    assert fake.calls[0][1] is True  # ツールの一覧を渡している
    assert fake.stream_calls == []


def test_reply_with_tool_call(monkeypatch, ollama):
    executed = []
    monkeypatch.setattr(core, "write_to_notion", lambda content, memo_type: executed.append(content) or "保存しました")
    fake = ollama(
        # LLM によっては引数が JSON の文字列で来る
        _tool_call("add_memo", json.dumps({"content": "卵を買う"})),
        _answer("メモしました。"),
    )
    events, tokens = [], []
    history = core.new_conversation()

    reply = core.process_message(
        history, "卵を買うってメモして",
        on_tool_event=lambda *e: events.append(e), on_token=tokens.append,
    )

    assert reply == "メモしました。"
    assert executed == ["卵を買う"]
    assert events == [("add_memo", {"content": "卵を買う"}, "保存しました")]
    assert tokens == ["メモしました。"]
    assert [m["role"] for m in history] == ["system", "user", "assistant", "tool", "assistant"]
    assert history[3] == {"role": "tool", "tool_name": "add_memo", "content": "保存しました"}
    # 2回目には、ツールの結果まで LLM に渡している
    assert fake.calls[1][0][-1]["content"] == "保存しました"


def test_tools_can_be_chained(monkeypatch, ollama):
    """予定を見てから、空いている時間に予定を入れる"""
    steps = []
    monkeypatch.setattr(core, "list_calendar_events", lambda date: steps.append(("list", date)) or "10時 ゼミ")
    monkeypatch.setattr(core, "add_calendar_event",
                        lambda summary, start, end: steps.append(("add", summary, start)) or "追加しました")
    fake = ollama(
        _tool_call("list_calendar_events", {"date": "2026-11-05"}),
        _tool_call("add_calendar_event", {"summary": "ジム", "start_time": "2026-11-05 13:00"}),
        _answer("ゼミのあと、13時にジムを入れました。"),
    )
    history = core.new_conversation()

    reply = core.process_message(history, "明日の空いてる時間にジムを入れて")

    assert steps == [("list", "2026-11-05"), ("add", "ジム", "2026-11-05 13:00")]
    assert reply == "ゼミのあと、13時にジムを入れました。"
    assert [m["role"] for m in history] == ["system", "user", "assistant", "tool", "assistant", "tool", "assistant"]
    assert fake.calls[1][0][-1]["content"] == "10時 ゼミ"  # 1つ目の結果を見てから2つ目を選んでいる


def test_tool_rounds_are_limited(monkeypatch, ollama):
    """LLM がツールを呼び続けても止まる。最後はツールなしで答えさせる"""
    monkeypatch.setattr(core, "get_next_trains", lambda st, to=None: "あと5分")
    fake = ollama(*[_tool_call("get_next_train", {}) for _ in range(core.MAX_TOOL_ROUNDS)],
                  final_text="あと5分で出ます。")
    history = core.new_conversation()

    assert core.process_message(history, "次の電車は？") == "あと5分で出ます。"
    assert len(fake.calls) == core.MAX_TOOL_ROUNDS
    assert len(fake.stream_calls) == 1
    assert history[-1] == {"role": "assistant", "content": "あと5分で出ます。"}


def test_tool_call_with_missing_arguments(ollama):
    ollama({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "add_todo", "arguments": None}}]},
           _answer("内容を教えてください。"))
    history = core.new_conversation()
    core.process_message(history, "TODO に追加して")
    assert "指定されていません" in history[3]["content"]


def test_system_prompt_date_is_refreshed(ollama):
    """サーバーを何日も動かしても、LLM に伝える「今日」がずれない"""
    ollama(_answer("はい"))
    history = core.new_conversation()
    history[0]["content"] = "今日の日付は2000-01-01です。"

    core.process_message(history, "明日の予定は？")

    today = datetime.date.today().strftime("%Y-%m-%d")
    assert today in history[0]["content"]
    assert "2000-01-01" not in history[0]["content"]


def test_history_is_trimmed_from_a_user_message():
    history = core.new_conversation()
    for i in range(10):
        history += [
            {"role": "user", "content": f"u{i}"},
            {"role": "assistant", "content": "", "tool_calls": []},
            {"role": "tool", "content": "r"},
            {"role": "assistant", "content": f"a{i}"},
        ]

    core._trim_history(history, max_messages=6)

    assert history[0]["role"] == "system"
    assert len(history) - 1 <= 6
    assert history[1] == {"role": "user", "content": "u9"}  # ツールの結果だけが残る、ということがない
    assert history[-1] == {"role": "assistant", "content": "a9"}


def test_short_history_is_untouched():
    history = core.new_conversation() + [{"role": "user", "content": "hi"}]
    before = list(history)
    core._trim_history(history, max_messages=30)
    assert history == before


def test_warm_up_loads_the_model(monkeypatch, test_config):
    sent = []
    monkeypatch.setattr(test_config, "OLLAMA_KEEP_ALIVE", "45m")

    class Res:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(core.httpx, "post", lambda url, json, timeout: sent.append((json, timeout)) or Res())
    assert core.warm_up() is True
    payload, timeout = sent[0]
    assert payload == {"model": "test-model", "messages": [], "keep_alive": "45m"}
    assert timeout >= 300  # 読み込みは時間がかかるので長く待つ


def test_warm_up_failure_is_reported_not_raised(monkeypatch):
    def down(*args, **kwargs):
        raise core.httpx.ConnectError("refused")

    monkeypatch.setattr(core.httpx, "post", down)
    assert core.warm_up() is False


def test_requests_keep_the_model_loaded(monkeypatch, test_config):
    sent = []
    monkeypatch.setattr(test_config, "OLLAMA_KEEP_ALIVE", "-1")
    monkeypatch.setattr(test_config, "OLLAMA_TIMEOUT", 99.0)

    class Res:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "はい"}}

    monkeypatch.setattr(core.httpx, "post", lambda url, json, timeout: sent.append((json, timeout)) or Res())
    core._call_ollama_once([{"role": "user", "content": "x"}], use_tools=True)
    assert sent[0][0]["keep_alive"] == "-1"
    assert sent[0][1] == 99.0
