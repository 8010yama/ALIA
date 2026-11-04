"""
ALIA - 天気

- 予報: Open-Meteo（無料・キー不要）。今日から MAX_DAYS 日先まで
- 今の雨: Yahoo! YOLP 気象情報 API（キーが必要。今日の天気のときだけ使う）
"""

import datetime

import requests

from alia import config

MAX_DAYS = 7          # 何日先まで答えるか（Open-Meteo は 16 日先まで出せるが、遠いほど当たらない）
RAIN_MM = 0.5         # 1時間にこれ以上降る予報なら「雨」とみなす [mm]
_WEEKDAYS_JA = "月火水木金土日"

# WMO の天気コード → 日本語（https://open-meteo.com/en/docs の Weather variable documentation）
_WEATHER_CODES = {
    0: "快晴", 1: "晴れ", 2: "晴れ時々くもり", 3: "くもり",
    45: "霧", 48: "霧",
    51: "弱い霧雨", 53: "霧雨", 55: "強い霧雨", 56: "着氷性の霧雨", 57: "着氷性の霧雨",
    61: "小雨", 63: "雨", 65: "大雨", 66: "着氷性の雨", 67: "着氷性の雨",
    71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪",
    80: "にわか雨", 81: "強いにわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "強いにわか雪",
    95: "雷雨", 96: "ひょうを伴う雷雨", 99: "ひょうを伴う激しい雷雨",
}


def describe_weather_code(code) -> str:
    return _WEATHER_CODES.get(int(code), "不明") if code is not None else "不明"


def _date_label(date: datetime.date, today: datetime.date) -> str:
    names = {0: "今日", 1: "明日", 2: "明後日"}
    prefix = names.get((date - today).days, "")
    return f"{prefix}（{date.month}月{date.day}日・{_WEEKDAYS_JA[date.weekday()]}）" if prefix else \
        f"{date.month}月{date.day}日（{_WEEKDAYS_JA[date.weekday()]}）"


def _get_forecast(lat: float, lon: float, date: datetime.date) -> dict:
    """Open-Meteo から、その日の予報を取ってくる"""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "hourly": "precipitation",
        "timezone": "auto",
        "start_date": date.isoformat(),
        "end_date": date.isoformat(),
    }
    res = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=5)
    res.raise_for_status()
    return res.json()


def _rain_hours(forecast: dict, after_hour: int = -1) -> list[str]:
    """雨が降る予報の時刻（'HH:MM'）の一覧。after_hour より後だけ"""
    hourly = forecast.get("hourly", {})
    return [t[11:16] for t, p in zip(hourly.get("time", []), hourly.get("precipitation", []))
            if p is not None and p >= RAIN_MM and int(t[11:13]) > after_hour]


def _get_current_rainfall(lat: float, lon: float) -> float:
    """Yahoo YOLP から、今の降水の強さ [mm/h] を取ってくる"""
    if not config.YAHOO_APP_ID:
        raise RuntimeError("YAHOO_APP_ID が .env に設定されていません")

    # requests の標準の User-Agent だと Yahoo に弾かれることがあるので、ブラウザと同じにする
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
    }
    res = requests.get(
        "https://map.yahooapis.jp/weather/V1/place",
        params={"coordinates": f"{lon},{lat}", "output": "json", "appid": config.YAHOO_APP_ID},
        headers=headers,
        timeout=5,
    )
    res.raise_for_status()
    weather_list = res.json()["Feature"][0]["Property"]["WeatherList"]["Weather"]
    observations = [w for w in weather_list if w["Type"] == "observation"]
    return observations[-1]["Rainfall"] if observations else 0.0


def get_weather_info(lat: float, lon: float, date: datetime.date | None = None,
                     now: datetime.datetime | None = None) -> str:
    """
    その日の天気・最高/最低気温・降水確率・雨の時間帯を、LLM に渡す文章にする。
    date を省略すると今日。今日のときは「これから」の雨と、今降っているかも調べる。
    """
    now = now or datetime.datetime.now()
    today = now.date()
    date = date or today

    days_ahead = (date - today).days
    if days_ahead < 0:
        return "過去の天気は調べられません。"
    if days_ahead >= MAX_DAYS:
        return f"天気予報は{MAX_DAYS - 1}日先までしか調べられません。"

    is_today = days_ahead == 0
    lines = [f"{_date_label(date, today)}の天気"]
    rain = []

    try:
        forecast = _get_forecast(lat, lon, date)
        daily = forecast["daily"]
        lines[0] += f": {describe_weather_code(daily['weather_code'][0])}"
        lines.append(f"気温: 最高{daily['temperature_2m_max'][0]}°C / 最低{daily['temperature_2m_min'][0]}°C")
        if daily.get("precipitation_probability_max", [None])[0] is not None:
            lines.append(f"降水確率: {daily['precipitation_probability_max'][0]}%")
        rain = _rain_hours(forecast, after_hour=now.hour if is_today else -1)
    except Exception as e:
        print(f"[weather] Open-Meteo でエラー: {type(e).__name__}: {e}")
        lines.append("予報の取得に失敗しました。")

    rain_text = f"雨の予報: {rain[0]}頃から" if rain else ("この後は雨の予報なし" if is_today else "雨の予報なし")

    if is_today:
        try:
            current = _get_current_rainfall(lat, lon)
        except Exception as e:
            print(f"[weather] Yahoo の降水でエラー: {type(e).__name__}: {e}")
            current = None
        if current:
            rain_text = (f"今雨が降っていて（{current}mm/h）、この後も降る予報" if rain
                         else f"今雨が降っているが（{current}mm/h）、この後は止む予報")

    lines.append(rain_text)
    return "\n".join(lines)
