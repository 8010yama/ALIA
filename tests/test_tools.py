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
NOW = datetime.datetime(2026, 11, 4, 9, 55)


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


def test_next_train(fake_fetch):
    calls = fake_fetch(ROUTE_HTML)
    text = train.get_next_trains("新宿", now=NOW)
    assert text == "【次の電車】新宿駅\nあと10分（10:05発）\nJR中央線快速"
    assert calls[0]["to"] == "渋谷"


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

def _forecast(precipitation):
    today = datetime.date.today().isoformat()
    return {
        "daily": {"temperature_2m_mean": [18.5]},
        "hourly": {
            "time": [f"{today}T{h:02d}:00" for h in range(24)],
            "precipitation": precipitation,
        },
    }


def test_weather_rain_later(fake_requests, monkeypatch, test_config):
    monkeypatch.setattr(test_config, "YAHOO_APP_ID", "yahoo-id")
    rain = [0.0] * 24
    rain[23] = 1.2  # 23 時から雨（今が何時でも「これから」になる）

    def handler(method, url, kw):
        if "open-meteo" in url:
            return FakeResponse(json_data=_forecast(rain))
        return FakeResponse(json_data={"Feature": [{"Property": {"WeatherList": {"Weather": [
            {"Type": "observation", "Rainfall": 0.0},
            {"Type": "forecast", "Rainfall": 3.0},
        ]}}}]})

    fake_requests(weather, handler)
    text = weather.get_weather_info(35.0, 139.0)
    assert "本日の平均気温: 18.5°C" in text
    assert "雨の予報: 23:00頃から" in text


def test_weather_raining_now(fake_requests, monkeypatch, test_config):
    monkeypatch.setattr(test_config, "YAHOO_APP_ID", "yahoo-id")

    def handler(method, url, kw):
        if "open-meteo" in url:
            return FakeResponse(json_data=_forecast([0.0] * 24))
        return FakeResponse(json_data={"Feature": [{"Property": {"WeatherList": {"Weather": [
            {"Type": "observation", "Rainfall": 2.5},
        ]}}}]})

    fake_requests(weather, handler)
    assert "現在雨が降っているが、この後は止む予報（2.5mm/h）" in weather.get_weather_info(35.0, 139.0)


def test_weather_without_yahoo_key_still_answers(fake_requests):
    fake_requests(weather, lambda m, url, kw: FakeResponse(json_data=_forecast([0.0] * 24)))
    text = weather.get_weather_info(35.0, 139.0)
    assert "本日の平均気温: 18.5°C" in text
    assert "今日は雨の予報なし" in text


def test_weather_all_down(fake_requests):
    fake_requests(weather, lambda m, url, kw: FakeResponse(status_code=500))
    assert "気温予報の取得に失敗しました" in weather.get_weather_info(35.0, 139.0)


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
