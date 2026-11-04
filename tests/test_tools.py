"""ツール：地図、電車、天気、Notion、Google カレンダー（外部の API はすべて偽物）"""

import datetime

import pytest
from bs4 import BeautifulSoup

from alia.tools import calendar, maps, notion, train, weather
from tests.conftest import FakeResponse

# ================= 地図 =================


@pytest.mark.parametrize("text,expected", [
    ("秋川渓谷まで行きたい", "秋川渓谷"),
    ("ここから東京タワーに行きたい", "東京タワー"),
    ("今から浅草へ連れて行って", "浅草"),
    ("箱根に向かう", "箱根"),
    ("横浜駅への経路", "横浜駅"),
    ("鎌倉までの道", "鎌倉"),
    ("江ノ島のナビ", "江ノ島"),
    ("渋谷まで案内して", "渋谷"),
    ("東京スカイツリー", "東京スカイツリー"),  # 言い方がわからなければ全文を目的地に
    ("どこか", None),
    ("", None),
])
def test_extract_nav_destination(text, expected):
    assert maps.extract_nav_destination(text) == expected


def test_map_url_is_encoded_and_defaults_to_home():
    assert maps.get_map_url("東京駅") == "http://maps.apple.com/?saddr=%E6%96%B0%E5%AE%BF&daddr=%E6%9D%B1%E4%BA%AC%E9%A7%85&dirflg=d"
    assert "saddr=35.1%2C139.2" in maps.get_map_url("東京駅", "35.1,139.2")
    assert maps.build_google_maps_nav_url("東京駅") == "google.navigation:q=%E6%9D%B1%E4%BA%AC%E9%A7%85&avoid=tf"


def test_nearest_station_picks_closest_and_drops_eki(fake_requests):
    fake_requests(maps, lambda m, url, kw: FakeResponse(json_data={"elements": [
        {"lat": 35.70, "lon": 139.70, "tags": {"name": "遠い駅"}},
        {"lat": 35.6896, "lon": 139.7006, "tags": {"name": "新宿駅", "name:ja": "新宿駅"}},
    ]}))
    assert maps.find_nearest_station(35.6895, 139.7005) == "新宿"


def test_nearest_station_none(fake_requests):
    fake_requests(maps, lambda m, url, kw: FakeResponse(json_data={"elements": []}))
    assert maps.find_nearest_station(0.0, 0.0) is None


def test_nearest_station_network_error(fake_requests):
    fake_requests(maps, lambda m, url, kw: FakeResponse(status_code=504))
    assert maps.find_nearest_station(35.0, 139.0) is None


# ================= 電車 =================

# Yahoo!路線情報の印刷用ページのうち、読み取りに使う部分
ROUTE_HTML = """
<html><body>
<!-- コメントは読み飛ばす -->
<div class="routeSummary"><ul>
  <li class="time"><span>10:05発</span><span class="mark">10:32着</span>27分</li>
  <li class="transfer">乗換：1回</li>
  <li class="fare"><span class="mark">380円</span></li>
</ul></div>
<div class="routeDetail">
  <div class="station">三鷹</div>
  <li class="transport"><div>JR中央線快速</div></li>
  <div class="station">新宿</div>
  <li class="transport"><div>JR山手線</div></li>
  <div class="station">渋谷</div>
</div>
</body></html>
"""
NOW = datetime.datetime(2026, 11, 4, 9, 55)  # 水曜日


@pytest.fixture
def fake_fetch(monkeypatch):
    calls = []

    def install(html):
        def fetch(params):
            calls.append(params)
            return BeautifulSoup(html, "html.parser")

        monkeypatch.setattr(train, "_fetch", fetch)
        return calls

    return install


def test_parse_route_summary():
    route = train.parse_route_summary(BeautifulSoup(ROUTE_HTML, "html.parser"))
    assert route["depart"] == "10:05"
    assert route["arrive"] == "10:32"
    assert route["duration"] == "27分"
    assert route["transfer"] == "乗換：1回"
    assert route["fare"] == "380円"
    assert route["stations"] == ["三鷹", "新宿", "渋谷"]
    assert route["lines"] == ["JR中央線快速", "JR山手線"]


def test_parse_route_summary_without_result():
    assert train.parse_route_summary(BeautifulSoup("<html></html>", "html.parser")) is None


def test_train_route(fake_fetch):
    calls = fake_fetch(ROUTE_HTML)
    text = train.get_train_route("三鷹", "渋谷", now=NOW)
    assert "【電車】三鷹→渋谷" in text
    assert "出発:10:05発 到着:10:32着" in text
    assert "料金:380円" in text
    assert "あと10分で出発" in text
    assert "新宿 →[JR山手線]" in text
    assert calls[0]["type"] == 1 and calls[0]["hh"] == "09"


def test_train_route_by_arrival_time(fake_fetch):
    calls = fake_fetch(ROUTE_HTML)
    train.get_train_route("三鷹", "渋谷", arrive_time="10:30", now=NOW)
    assert calls[0]["type"] == 4
    assert (calls[0]["hh"], calls[0]["m1"], calls[0]["m2"]) == ("10", 3, 0)


def test_train_route_bad_arrival_time(fake_fetch):
    fake_fetch(ROUTE_HTML)
    assert "形式が正しくありません" in train.get_train_route("三鷹", "渋谷", arrive_time="朝", now=NOW)


def test_next_train_without_direction(fake_fetch):
    calls = fake_fetch(ROUTE_HTML)
    text = train.get_next_trains("新宿", now=NOW)
    assert text == "【次の電車】新宿駅（渋谷方面）\nあと10分（10:05発）\nJR中央線快速"
    assert calls[0]["to"] == "渋谷"  # 方面がわからなければ隣の駅で代用


def test_next_train_with_direction(fake_fetch):
    calls = fake_fetch(ROUTE_HTML)
    text = train.get_next_trains("三鷹", "渋谷", now=NOW)
    assert text.startswith("【次の電車】三鷹駅（渋谷方面）\nあと10分（10:05発）")
    assert text.endswith("渋谷着 10:32")
    assert (calls[0]["from"], calls[0]["to"]) == ("三鷹", "渋谷")


def test_next_train_same_station(fake_fetch):
    calls = fake_fetch(ROUTE_HTML)
    assert "行き先を変えてください" in train.get_next_trains("渋谷", "渋谷", now=NOW)
    assert calls == []


def test_next_train_not_found(fake_fetch):
    fake_fetch("<html></html>")
    assert "取得できませんでした" in train.get_next_trains("新宿", now=NOW)


def test_network_error_is_reported(monkeypatch):
    def broken(params):
        raise ConnectionError("down")

    monkeypatch.setattr(train, "_fetch", broken)
    assert "エラー" in train.get_train_route("三鷹", "渋谷", now=NOW)
    assert "エラー" in train.get_next_trains("新宿", now=NOW)


@pytest.mark.parametrize("hhmm,now,expected", [
    ("10:05", datetime.datetime(2026, 1, 1, 9, 55), 10),
    ("09:55", datetime.datetime(2026, 1, 1, 9, 55), 24 * 60),  # ちょうど今は、次の日の同じ時刻
    ("00:10", datetime.datetime(2026, 1, 1, 23, 50), 20),      # 日付をまたぐ
])
def test_minutes_until(hhmm, now, expected):
    assert train.minutes_until(hhmm, now) == expected


@pytest.mark.parametrize("minutes,expected", [(0, "まもなく"), (5, "あと5分"), (75, "あと1時間15分")])
def test_format_wait(minutes, expected):
    assert train.format_wait(minutes) == expected


# ================= 天気 =================

def _forecast(day="2026-11-04", rain=None, code=3, prob=40):
    precipitation = [0.0] * 24
    for hour, mm in (rain or {}).items():
        precipitation[hour] = mm
    return {
        "daily": {"time": [day], "weather_code": [code], "temperature_2m_max": [22.1],
                  "temperature_2m_min": [13.0], "precipitation_probability_max": [prob]},
        "hourly": {"time": [f"{day}T{h:02d}:00" for h in range(24)], "precipitation": precipitation},
    }


def _yahoo(rainfall):
    return {"Feature": [{"Property": {"WeatherList": {"Weather": [
        {"Type": "observation", "Rainfall": rainfall}, {"Type": "forecast", "Rainfall": 9.9}]}}}]}


@pytest.fixture
def weather_api(fake_requests, monkeypatch, test_config):
    """forecast と今の雨（rainfall。None なら Yahoo は使えない）を決めて、偽の API を入れる"""
    def install(forecast, rainfall=None):
        if rainfall is not None:
            monkeypatch.setattr(test_config, "YAHOO_APP_ID", "yahoo-id")

        def handler(method, url, kw):
            if "open-meteo" in url:
                return forecast if isinstance(forecast, FakeResponse) else FakeResponse(json_data=forecast)
            return FakeResponse(json_data=_yahoo(rainfall))

        return fake_requests(weather, handler)

    return install


def test_weather_today(weather_api):
    calls = weather_api(_forecast(rain={8: 2.0, 15: 1.2}), rainfall=0.0)
    text = weather.get_weather_info(35.0, 139.0, now=NOW)
    assert text.splitlines() == [
        "今日（11月4日・水）の天気: くもり",
        "気温: 最高22.1°C / 最低13.0°C",
        "降水確率: 40%",
        "雨の予報: 15:00頃から",  # 8 時の雨はもう過ぎている
    ]
    assert calls.calls[0][2]["params"]["start_date"] == "2026-11-04"


def test_weather_tomorrow(weather_api):
    calls = weather_api(_forecast(day="2026-11-05", rain={8: 2.0}, code=61, prob=80), rainfall=5.0)
    text = weather.get_weather_info(35.0, 139.0, datetime.date(2026, 11, 5), now=NOW)
    assert text.splitlines() == [
        "明日（11月5日・木）の天気: 小雨",
        "気温: 最高22.1°C / 最低13.0°C",
        "降水確率: 80%",
        "雨の予報: 08:00頃から",  # 明日は朝からの雨も伝える
    ]
    assert calls.calls[0][2]["params"]["end_date"] == "2026-11-05"
    assert len(calls.calls) == 1  # 明日の天気では「今の雨」は調べない


def test_weather_later_day_label(weather_api):
    weather_api(_forecast(day="2026-11-08", code=0))
    assert weather.get_weather_info(35.0, 139.0, datetime.date(2026, 11, 8), now=NOW).startswith("11月8日（日）の天気: 快晴")


@pytest.mark.parametrize("date,expected", [
    (datetime.date(2026, 11, 3), "過去の天気"),
    (datetime.date(2026, 11, 11), "6日先まで"),
])
def test_weather_out_of_range(weather_api, date, expected):
    calls = weather_api(_forecast())
    assert expected in weather.get_weather_info(35.0, 139.0, date, now=NOW)
    assert calls.calls == []


def test_weather_raining_now_then_stops(weather_api):
    weather_api(_forecast(), rainfall=2.5)
    assert weather.get_weather_info(35.0, 139.0, now=NOW).endswith("今雨が降っているが（2.5mm/h）、この後は止む予報")


def test_weather_raining_now_and_later(weather_api):
    weather_api(_forecast(rain={12: 3.0}), rainfall=2.5)
    assert weather.get_weather_info(35.0, 139.0, now=NOW).endswith("今雨が降っていて（2.5mm/h）、この後も降る予報")


def test_weather_without_yahoo_key_still_answers(weather_api):
    weather_api(_forecast())
    text = weather.get_weather_info(35.0, 139.0, now=NOW)
    assert "最高22.1°C" in text
    assert text.endswith("この後は雨の予報なし")


def test_weather_forecast_down(weather_api):
    weather_api(FakeResponse(status_code=500))
    assert "予報の取得に失敗しました" in weather.get_weather_info(35.0, 139.0, now=NOW)


def test_weather_code_names():
    assert weather.describe_weather_code(95) == "雷雨"
    assert weather.describe_weather_code(42) == "不明"
    assert weather.describe_weather_code(None) == "不明"


# ================= Notion =================

def test_notion_without_token(fake_requests):
    fake = fake_requests(notion, lambda m, url, kw: pytest.fail("送ってはいけない"))
    assert "NOTION_TOKEN" in notion.write_to_notion("x")
    assert fake.calls == []


@pytest.fixture
def notion_ready(monkeypatch, test_config):
    monkeypatch.setattr(test_config, "NOTION_TOKEN", "secret")
    monkeypatch.setattr(test_config, "NOTION_MEMO_PAGE_ID", "memo-page")
    monkeypatch.setattr(test_config, "NOTION_TODO_PAGE_ID", "todo-page")


@pytest.mark.parametrize("memo_type,page,block_type,label", [
    ("memo", "memo-page", "bulleted_list_item", "メモ"),
    ("todo", "todo-page", "to_do", "TODO"),
])
def test_notion_writes_block(fake_requests, notion_ready, memo_type, page, block_type, label):
    fake = fake_requests(notion, lambda m, url, kw: FakeResponse(json_data={}))
    result = notion.write_to_notion("卵を買う", memo_type=memo_type)
    method, url, kw = fake.calls[0]
    assert (method, url) == ("PATCH", f"https://api.notion.com/v1/blocks/{page}/children")
    assert kw["json"]["children"][0]["type"] == block_type
    assert kw["headers"]["Authorization"] == "Bearer secret"
    assert result == f"{label}として「卵を買う」をNotionに保存しました。"


def test_notion_error_is_reported(fake_requests, notion_ready):
    fake_requests(notion, lambda m, url, kw: FakeResponse(status_code=401, text="unauthorized"))
    assert "Notion書き込みエラー (401)" in notion.write_to_notion("x")


# ================= Google カレンダー =================

def test_calendar_without_credentials(fake_requests):
    fake = fake_requests(calendar, lambda m, url, kw: pytest.fail("送ってはいけない"))
    assert "認証情報が未設定" in calendar.add_calendar_event("会議", "2026-11-05 10:00")
    assert "認証情報が未設定" in calendar.list_calendar_events("2026-11-05")
    assert fake.calls == []


@pytest.fixture
def google(monkeypatch, test_config, fake_requests):
    """トークン・カレンダー一覧・予定を返す偽の Google カレンダー"""
    monkeypatch.setattr(test_config, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(test_config, "GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(test_config, "GOOGLE_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr(test_config, "GOOGLE_CALENDAR_ID", "team@group.calendar.google.com")

    state = {"events": {"primary": [], "work@example.com": []}, "patch_status": 200}

    def handler(method, url, kw):
        if "oauth2.googleapis.com/token" in url:
            return FakeResponse(json_data={"access_token": "token"})
        if url.endswith("/users/me/calendarList"):
            return FakeResponse(json_data={"items": [
                {"id": "primary", "summary": "自分"},
                {"id": "work@example.com", "summary": "研究室"},
            ]})
        if method == "GET" and url.endswith("/events"):
            cal_id = "work@example.com" if "work%40example.com" in url else "primary"
            return FakeResponse(json_data={"items": state["events"][cal_id]})
        if method == "POST" and url.endswith("/events"):
            return FakeResponse(json_data={"id": "new"})
        if method == "PATCH":
            return FakeResponse(status_code=state["patch_status"], json_data={})
        return FakeResponse(status_code=404, text="not found")

    state["requests"] = fake_requests(calendar, handler)
    return state


def _event(summary, start, end, event_id="e1"):
    return {"id": event_id, "summary": summary,
            "start": {"dateTime": f"2026-11-05T{start}:00+09:00"},
            "end": {"dateTime": f"2026-11-05T{end}:00+09:00"}}


def test_add_event(google):
    result = calendar.add_calendar_event("ゼミ", "2026-11-05 14:00")
    method, url, kw = google["requests"].calls[-1]
    assert url.endswith("/calendars/team%40group.calendar.google.com/events")  # ID の @ をエスケープ
    assert kw["json"]["start"]["dateTime"] == "2026-11-05T14:00:00+09:00"
    assert kw["json"]["end"]["dateTime"] == "2026-11-05T15:00:00+09:00"  # 終わりがなければ1時間後
    assert result.endswith("14時00分からゼミをカレンダーに追加しました。")


def test_add_event_bad_time(google):
    assert "形式が正しくありません" in calendar.add_calendar_event("ゼミ", "明日の昼")


def test_list_events_across_calendars(google):
    google["events"]["primary"] = [_event("歯医者", "15:00", "16:00"),
                                   {"summary": "誕生日", "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}}]
    google["events"]["work@example.com"] = [_event("ゼミ", "10:00", "11:30")]

    lines = calendar.list_calendar_events("2026-11-05").splitlines()

    assert lines[0].endswith("の予定は3件です。")
    assert lines[1:] == ["・終日 誕生日（自分）", "・10時00分 ゼミ（研究室）", "・15時00分 歯医者（自分）"]


def test_list_no_events(google):
    assert calendar.list_calendar_events("2026-11-05").endswith("の予定はありません。")


def test_update_event_keeps_duration(google):
    google["events"]["work@example.com"] = [_event("ゼミ", "10:00", "11:30", event_id="abc")]
    result = calendar.update_calendar_event("ゼミ", "2026-11-05", "2026-11-05 13:00")
    method, url, kw = google["requests"].calls[-1]
    assert method == "PATCH" and url.endswith("/calendars/work%40example.com/events/abc")
    assert kw["json"]["end"]["dateTime"] == "2026-11-05T14:30:00+09:00"
    assert result == "「ゼミ」の時間を13時00分〜14時30分に変更しました。"


def test_update_event_ambiguous(google):
    google["events"]["primary"] = [_event("ゼミ準備", "09:00", "10:00", "a"), _event("ゼミ", "10:00", "11:00", "b")]
    assert "複数見つかりました" in calendar.update_calendar_event("ゼミ", "2026-11-05", "2026-11-05 13:00")


def test_update_event_ignores_all_day(google):
    google["events"]["primary"] = [{"id": "x", "summary": "ゼミ合宿", "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}}]
    assert "見つかりませんでした" in calendar.update_calendar_event("ゼミ", "2026-11-05", "2026-11-05 13:00")


def test_update_event_api_error(google):
    google["events"]["primary"] = [_event("ゼミ", "10:00", "11:00")]
    google["patch_status"] = 403
    assert "カレンダー更新エラー (403)" in calendar.update_calendar_event("ゼミ", "2026-11-05", "2026-11-05 13:00")
