"""
ALIA - 地図・場所

- get_map_url: 経路の地図 URL（Apple マップ）を作る。iPhone ではショートカットの「URL を開く」で、
  PC ではブラウザで開く
- build_google_maps_nav_url: Android の Google マップでナビを始める URL
- geocode_place: 地名・施設名 → 緯度経度（OpenStreetMap Nominatim、無料・キー不要）
- find_nearest_station: 緯度経度 → 最寄り駅（OpenStreetMap Overpass API、無料・キー不要）
- extract_nav_destination: 「秋川渓谷まで行きたい」のような発話から目的地だけを取り出す
  （/navigate は LLM を通さずに速く返すため、ここだけ正規表現で読み取る）
"""

import re
from urllib.parse import quote

import requests

from alia import config

_NAV_PATTERNS = [
    r"(.{1,20}?)まで(?:案内|ナビ|行き方|道順|連れて行って|連れてって)",
    r"(.{1,20}?)(?:に|へ)(?:行きたい|向かいたい|連れて行って|連れてって|案内して)",
    r"(.{1,20}?)(?:に|へ)(?:行く|向かう)",
    r"(.{1,20}?)への経路",
    r"(.{1,20}?)までの道",
    r"(.{1,20}?)のナビ",
    r"(.{1,20}?)まで(?:行きたい|行く)",
]
_NOT_A_PLACE = {"そこ", "ここ", "あそこ", "どこか", "家", ""}


def extract_nav_destination(text: str) -> str | None:
    """
    「〇〇まで行きたい」「〇〇までナビして」のような言い方から、目的地の名前だけを取り出す。
    行き先を言っていない発話（「ハロー」「明日の天気は？」など）は None。
    iPhone のショートカットは毎回 /navigate も呼ぶので、ここで行き先がないものを地図にしてはいけない。
    """
    # 「ここから」「今から」のような出発地の前置きは、目的地の読み取りの邪魔になるので先に消す
    text = re.sub(r"^(?:ここ|そこ|あそこ|今|現在地)から", "", text.strip())
    for pattern in _NAV_PATTERNS:
        m = re.search(pattern, text)
        if m:
            dest = re.sub(r"^(?:の|に|へ|で|を|から|は|が)", "", m.group(1).strip())
            if dest not in _NOT_A_PLACE:
                return dest
    return None


def get_map_url(destination: str, origin: str | None = None) -> str:
    """
    Apple マップの経路 URL（ユニバーサルリンク）を作る。origin は地名か 'lat,lon'。
    指定がなければ自宅の最寄り駅から。
    """
    origin = origin or config.HOME_STATION
    return f"http://maps.apple.com/?saddr={quote(origin)}&daddr={quote(destination)}&dirflg=d"


def build_google_maps_nav_url(destination: str) -> str:
    """Android の Google マップでナビを始める URL スキーム"""
    return f"google.navigation:q={quote(destination)}&avoid=tf"


def geocode_place(place: str) -> tuple[float | None, float | None]:
    """地名・施設名から (緯度, 経度) を返す。見つからなければ (None, None)"""
    try:
        from geopy.geocoders import Nominatim

        loc = Nominatim(user_agent="alia_ai_assistant").geocode(place, language="ja")
        if loc:
            return loc.latitude, loc.longitude
    except Exception as e:
        print(f"[maps] 場所の検索でエラー: {type(e).__name__}: {e}")
    return None, None


def find_nearest_station(lat: float, lon: float, radius_m: int = 1500) -> str | None:
    """緯度経度から一番近い駅の名前を返す。見つからなければ None"""
    query = f"""
    [out:json][timeout:10];
    node(around:{radius_m},{lat},{lon})[railway=station];
    out body;
    """
    try:
        res = requests.post("https://overpass-api.de/api/interpreter", data={"data": query}, timeout=10)
        res.raise_for_status()
        elements = res.json().get("elements", [])
    except Exception as e:
        print(f"[maps] 最寄り駅の検索でエラー: {type(e).__name__}: {e}")
        return None

    if not elements:
        return None
    # 比べるだけなので、平方根を取らない近似の距離で十分
    nearest = min(elements, key=lambda el: (el["lat"] - lat) ** 2 + (el["lon"] - lon) ** 2)
    tags = nearest.get("tags", {})
    name = tags.get("name:ja") or tags.get("name")
    # OSM の駅名は「新宿駅」のように「駅」が付いていることがあるので、乗換案内に渡せる形にそろえる
    if name and name.endswith("駅") and len(name) > 1:
        name = name[:-1]
    return name or None
