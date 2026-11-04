"""
ALIA - マップ起動モジュール

現時点(PC/CLI版)ではGoogle Mapsのルート検索をデフォルトブラウザで開く(open_map)。

以下の関数(build_google_maps_nav_url / build_apple_maps_url / geocode_place)は
iPhone対応フェーズで使う想定の関数。URLスキーム(maps://, google.navigation:)は
スマホ上のネイティブアプリでないと反応しないため、Windows CLIでは動作しない。
FastAPI化してiPhoneのショートカットアプリ等からこれらのURLを開く形に繋ぎ込む。
"""

import os
import webbrowser
import urllib.parse
import requests
from urllib.parse import quote

HOME_STATION = os.getenv("HOME_STATION", "新宿")


import re


def extract_nav_destination(text: str) -> str | None:
    """
    「秋川渓谷まで行きたい」のような発話全文から、目的地名だけを抜き出す。
    見つからなければ元のtextをそのまま返す(フォールバック)。
    """
    # 「ここから」「そこから」のような出発地の前置きは、目的地抽出の邪魔になるため先に除去
    text = re.sub(r'^(?:ここ|そこ|あそこ|今|現在地)から', '', text.strip())

    patterns = [
        r'(.{1,20}?)まで(?:案内|ナビ|行き方|道順|連れて行って)',
        r'(.{1,20}?)(?:に|へ)(?:行きたい|向かいたい|連れて行って|案内して)',
        r'(.{1,20}?)(?:に|へ)(?:行く|向かう)',
        r'(.{1,20}?)への経路',
        r'(.{1,20}?)までの道',
        r'(.{1,20}?)のナビ',
        r'(.{1,20}?)まで(?:行きたい|行く)',
    ]
    skip = {"そこ", "ここ", "あそこ", "どこか", "家", ""}
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            dest = m.group(1).strip()
            dest = re.sub(r'^(?:の|に|へ|で|を|から|は|が)', '', dest)
            if dest and dest not in skip:
                return dest
    return text.strip() or None


def find_nearest_station(lat: float, lon: float, radius_m: int = 1500) -> str | None:
    """
    緯度経度から最寄りの鉄道駅名を検索する(OpenStreetMap Overpass API使用、無料・APIキー不要)。
    見つからなければ None を返す。
    """
    query = f"""
    [out:json][timeout:10];
    node(around:{radius_m},{lat},{lon})[railway=station];
    out body;
    """
    try:
        res = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=10,
        )
        res.raise_for_status()
        elements = res.json().get("elements", [])
        if not elements:
            return None

        def _distance(el):
            dlat = el["lat"] - lat
            dlon = el["lon"] - lon
            return dlat * dlat + dlon * dlon  # 近似距離(比較用なので平方根不要)

        elements.sort(key=_distance)
        nearest = elements[0]
        tags = nearest.get("tags", {})
        return tags.get("name:ja") or tags.get("name") or None

    except Exception as e:
        print(f"[map_utils] 最寄り駅検索エラー: {type(e).__name__}: {e}")
        return None


def get_map_url(destination: str, origin: str = None) -> str:
    """
    Apple Mapsのユニバーサルリンクを組み立てる(副作用なし、URLを返すだけ)。
    iPhone側で「URLを開く」アクションに渡すことで、ネイティブのマップアプリが起動する。
    """
    origin = origin or HOME_STATION
    origin_q = quote(origin)
    dest_q = quote(destination)
    return f"http://maps.apple.com/?saddr={origin_q}&daddr={dest_q}&dirflg=d"


def open_map(destination: str, origin: str = None) -> str:
    """
    [PC/CLI用] Google Mapsでルート検索をブラウザで開く。
    destination: 目的地（駅名・住所・施設名など）
    origin: 出発地。指定がなければ自宅最寄り駅を使う。
    """
    if not destination:
        return "目的地が指定されていません。"

    origin = origin or HOME_STATION

    origin_q = urllib.parse.quote(origin)
    dest_q = urllib.parse.quote(destination)

    url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={origin_q}&destination={dest_q}&travelmode=transit"
    )

    try:
        webbrowser.open(url)
        return f"{origin}から{destination}までの経路をGoogle Mapsで開きました。"
    except Exception as e:
        return f"マップを開けませんでした: {type(e).__name__}: {e}"


# ============ iPhone対応フェーズ用（現時点では未使用） ============

def build_google_maps_nav_url(destination: str) -> str:
    """Android/GoogleマップアプリのナビをURLスキームで起動するためのURLを組み立てる"""
    dest_encoded = quote(destination)
    return f"google.navigation:q={dest_encoded}&avoid=tf"


def build_apple_maps_url(destination: str, from_lat: float = None, from_lon: float = None) -> str:
    """iPhoneのApple MapsアプリのナビをURLスキームで起動するためのURLを組み立てる"""
    dest_encoded = quote(destination)
    if from_lat and from_lon:
        return f"maps://?saddr={from_lat},{from_lon}&daddr={dest_encoded}&dirflg=d"
    return f"maps://?daddr={dest_encoded}&dirflg=d"


def geocode_place(place: str):
    """
    地名・施設名から緯度経度を取得する(OpenStreetMap Nominatim使用、無料・APIキー不要)。
    戻り値: (lat, lon) または (None, None)
    """
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="alia_ai_assistant")
        loc = geolocator.geocode(place, language="ja")
        if loc:
            return loc.latitude, loc.longitude
    except Exception as e:
        print(f"[map_utils] ジオコードエラー: {e}")
    return None, None