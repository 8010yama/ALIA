"""
ALIA - 天気
気温: Open-Meteo (無料・APIキー不要)
直近の降水: Yahoo! YOLP気象情報API (要APIキー)
"""

import datetime

import requests

from alia import config


def _get_daily_forecast(lat: float, lon: float):
    """Open-Meteoから本日の平均気温と、これから雨が降り始める時刻を取得"""
    today = datetime.date.today().isoformat()
    now_hour = datetime.datetime.now().hour

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_mean"
        f"&hourly=precipitation"
        f"&timezone=auto"
        f"&start_date={today}&end_date={today}"
    )
    res = requests.get(url, timeout=5)
    res.raise_for_status()
    data = res.json()

    avg_temp = data["daily"]["temperature_2m_mean"][0]

    RAIN_THRESHOLD = 0.5
    rain_start = None
    for t, p in zip(data["hourly"]["time"], data["hourly"]["precipitation"]):
        hour = int(t[11:13])
        if p >= RAIN_THRESHOLD and hour > now_hour and rain_start is None:
            rain_start = t[11:16]

    return avg_temp, rain_start


def _get_current_rainfall(lat: float, lon: float) -> float:
    """Yahoo YOLPから現在の降水強度(mm/h)を取得"""
    if not config.YAHOO_APP_ID:
        raise RuntimeError("YAHOO_APP_ID が.envに設定されていません")

    url = (
        f"https://map.yahooapis.jp/weather/V1/place"
        f"?coordinates={lon},{lat}&output=json&appid={config.YAHOO_APP_ID}"
    )
    # デフォルトのUser-Agent(python-requests/x.x)だとYahoo側で弾かれることがあるため、
    # ブラウザ相当のヘッダーを明示的に付与する。
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }
    res = requests.get(url, headers=headers, timeout=5)
    res.raise_for_status()
    data = res.json()

    weather_list = data["Feature"][0]["Property"]["WeatherList"]["Weather"]
    observations = [w for w in weather_list if w["Type"] == "observation"]
    return observations[-1]["Rainfall"] if observations else 0.0


def get_weather_info(lat: float, lon: float) -> str:
    """気温予報＋降水情報をまとめてテキストで返す（LLMのツール実行結果として使う）"""
    lines = []
    avg_temp, rain_start = None, None

    try:
        avg_temp, rain_start = _get_daily_forecast(lat, lon)
        lines.append(f"本日の平均気温: {avg_temp}°C")
    except Exception as e:
        print(f"[weather] Open-Meteoエラー: {type(e).__name__}: {e}")
        lines.append("気温予報の取得に失敗しました。")

    try:
        current_rain = _get_current_rainfall(lat, lon)
        if current_rain > 0:
            if rain_start:
                lines.append(f"現在雨が降っており、{rain_start}以降も続く予報（{current_rain}mm/h）")
            else:
                lines.append(f"現在雨が降っているが、この後は止む予報（{current_rain}mm/h）")
        else:
            if rain_start:
                lines.append(f"雨の予報: {rain_start}頃から")
            else:
                lines.append("今日は雨の予報なし")
    except Exception as e:
        print(f"[weather] Yahoo降水エラー: {type(e).__name__}: {e}")
        lines.append(f"雨の予報: {rain_start}頃から" if rain_start else "今日は雨の予報なし")

    return "\n".join(lines)