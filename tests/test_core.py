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


# ---------------- 出発駅の決め方 ----------------

def test_station_defaults_to_home_without_location(monkeypatch):
    stations = []
    monkeypatch.setattr(core, "get_next_trains", lambda st: stations.append(st) or "ok")
    core.dispatch_tool_call("get_next_train", {}, context={})
    assert stations == ["新宿"]


def test_station_uses_phone_location_and_caches_lookup(monkeypatch):
    lookups, routes = [], []
    monkeypatch.setattr(core, "find_nearest_station", lambda lat, lon: lookups.append((lat, lon)) or "三鷹")
    monkeypatch.setattr(core, "get_train_route", lambda frm, to, at: routes.append((frm, to, at)) or "ok")

    context = {"lat": 35.7, "lon": 139.56}
    core.dispatch_tool_call("get_train_route", {"to_station": "東京"}, context=context)
    core.dispatch_tool_call("get_train_route", {"to_station": "品川", "arrive_time": "10:00"}, context=context)

    assert routes == [("三鷹", "東京", None), ("三鷹", "品川", "10:00")]
    assert lookups == [(35.7, 139.56)]  # 同じリクエストの中では1回だけ調べる


def test_station_falls_back_to_home_when_lookup_fails(monkeypatch):
    stations = []
    monkeypatch.setattr(core, "find_nearest_station", lambda lat, lon: None)
    monkeypatch.setattr(core, "get_next_trains", lambda st: stations.append(st) or "ok")
    core.dispatch_tool_call("get_next_train", {}, context={"lat": 1.0, "lon": 2.0})
    assert stations == ["新宿"]


# ---------------- 天気の場所の決め方 ----------------

@pytest.fixture
def weather_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "get_weather_info", lambda lat, lon: calls.append((lat, lon)) or "晴れ")
    return calls


def test_weather_for_named_place(monkeypatch, weather_calls):
    monkeypatch.setattr(core, "geocode_place", lambda place: (34.69, 135.50))
    result = core.dispatch_tool_call("get_weather", {"place": "大阪"}, context={"lat": 35.7, "lon": 139.7})
    assert weather_calls == [(34.69, 135.50)]
    assert result.startswith("大阪の天気")


def test_weather_uses_phone_location(weather_calls):
    result = core.dispatch_tool_call("get_weather", {}, context={"lat": 35.7, "lon": 139.7})
    assert weather_calls == [(35.7, 139.7)]
    assert result.startswith("現在地の天気")


def test_weather_defaults_to_home(weather_calls):
    core.dispatch_tool_call("get_weather", {})
    assert weather_calls == [(35.0, 139.0)]


def test_weather_unknown_place_says_so(monkeypatch, weather_calls):
    monkeypatch.setattr(core, "geocode_place", lambda place: (None, None))
    result = core.dispatch_tool_call("get_weather", {"place": "存在しない町"})
    assert weather_calls == [(35.0, 139.0)]
    assert "見つからなかった" in result


# ---------------- LLM との会話の流れ（process_message） ----------------

class FakeOllama:
    """1回目（ツールを選ぶ）と2回目（返事を作る）の LLM の答えを決めておく"""

    def __init__(self, first_message, final_text="了解です。"):
        self.first_message = first_message
        self.final_text = final_text
        self.first_calls = []
        self.stream_calls = []

    def call_once(self, messages, use_tools):
        self.first_calls.append(([dict(m) for m in messages], use_tools))
        return {"message": self.first_message}

    def stream(self, messages, on_token=None):
        self.stream_calls.append([dict(m) for m in messages])
        if on_token:
            on_token(self.final_text)
        return self.final_text


@pytest.fixture
def ollama(monkeypatch):
    def install(first_message, final_text="了解です。"):
        fake = FakeOllama(first_message, final_text)
        monkeypatch.setattr(core, "_call_ollama_once", fake.call_once)
        monkeypatch.setattr(core, "_stream_ollama", fake.stream)
        return fake

    return install


def test_reply_without_tools(ollama):
    fake = ollama({"role": "assistant", "content": "こんにちは！"})
    history = core.new_conversation()
    assert core.process_message(history, "やあ") == "こんにちは！"
    assert [m["role"] for m in history] == ["system", "user", "assistant"]
    assert fake.first_calls[0][1] is True  # ツールの一覧を渡している
    assert fake.stream_calls == []


def test_reply_with_tool_call(monkeypatch, ollama):
    executed = []
    monkeypatch.setattr(core, "write_to_notion", lambda content, memo_type: executed.append(content) or "保存しました")
    fake = ollama(
        {"role": "assistant", "content": "", "tool_calls": [
            # LLM によっては引数が JSON の文字列で来る
            {"function": {"name": "add_memo", "arguments": json.dumps({"content": "卵を買う"})}},
        ]},
        final_text="メモしました。",
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
    # 返事を作るときには、ツールの結果まで LLM に渡している
    assert fake.stream_calls[0][-1]["content"] == "保存しました"


def test_tool_call_with_missing_arguments(ollama):
    ollama({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "add_todo", "arguments": None}}]})
    history = core.new_conversation()
    core.process_message(history, "TODO に追加して")
    assert "指定されていません" in history[3]["content"]


def test_system_prompt_date_is_refreshed(ollama):
    """サーバーを何日も動かしても、LLM に伝える「今日」がずれない"""
    ollama({"role": "assistant", "content": "はい"})
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
