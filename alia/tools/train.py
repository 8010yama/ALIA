"""
ALIA - 電車（次の電車・乗換案内）

Yahoo!路線情報 (transit.yahoo.co.jp) の印刷用ページをスクレイピングして取得する。
公式 API ではないため、Yahoo 側の HTML が変わると壊れる可能性がある。

駅名・到着時刻の読み取りは LLM のツール呼び出しに任せているので、ここでは
「決まった駅名と時刻で検索して、結果を文章にする」ことだけを担当する。
HTML の読み取り（parse_route_summary）はネットに出ずにテストできるよう分けてある。
"""

import datetime

import requests
from bs4 import BeautifulSoup, Comment

SEARCH_URL = "https://transit.yahoo.co.jp/search/print"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# 「次の電車」は、行き先までの経路を検索して最初の電車を出す。
# 行き先がわからないとき（.env の COMMUTE_STATION もないとき）は、隣の駅で代用する
NEIGHBOR_STATION = {
    "新宿": "渋谷",
    "渋谷": "品川",
    "品川": "東京",
    "秋葉原": "東京",
    "上野": "東京",
    "池袋": "新宿",
}

# type: 1 = 出発時刻で検索、4 = 到着時刻で検索
_DEPART, _ARRIVE = 1, 4


def _search_params(from_station: str, to_station: str, when: datetime.datetime, search_type: int) -> dict:
    return {
        "from": from_station,
        "to": to_station,
        "y": when.year,
        "m": f"{when.month:02d}",
        "d": f"{when.day:02d}",
        "hh": f"{when.hour:02d}",
        "m1": when.minute // 10,
        "m2": when.minute % 10,
        "type": search_type,
    }


def _fetch(params: dict) -> BeautifulSoup:
    res = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=5)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    return soup


def minutes_until(hhmm: str, now: datetime.datetime) -> int:
    """'HH:MM' の時刻まであと何分か（過ぎていれば翌日のその時刻とみなす）"""
    h, m = map(int, hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return int((target - now).total_seconds() // 60)


def format_wait(minutes: int) -> str:
    if minutes == 0:
        return "まもなく"
    if minutes >= 60:
        return f"あと{minutes // 60}時間{minutes % 60}分"
    return f"あと{minutes}分"


def parse_route_summary(soup: BeautifulSoup) -> dict | None:
    """
    検索結果のページから、最初の経路の要点を取り出す。
    戻り値: {"depart", "arrive", "duration", "transfer", "fare", "stations", "lines"}
    経路が見つからなければ None。
    """
    summary = soup.find("div", class_="routeSummary")
    if not summary:
        return None

    time_li = summary.find("li", class_="time")
    transfer_li = summary.find("li", class_="transfer")
    fare_li = summary.find("li", class_="fare")

    depart = arrive = None
    duration = ""
    if time_li:
        dep_span = time_li.find("span")
        arr_span = time_li.find("span", class_="mark")
        if dep_span:
            depart = dep_span.get_text(strip=True).split("発")[0].strip()
        if arr_span:
            arrive = arr_span.get_text(strip=True).replace("着", "").strip()
        for s in time_li.find_all("span"):
            s.decompose()
        duration = time_li.get_text(strip=True)

    fare_mark = fare_li.find("span", class_="mark") if fare_li else None

    stations, lines = [], []
    detail = soup.find("div", class_="routeDetail")
    if detail:
        stations = [st.get_text(strip=True) for st in detail.find_all("div", class_="station")]
        for li in detail.find_all("li", class_="transport"):
            div = li.find("div")
            if div:
                lines.append(div.get_text(strip=True))

    return {
        "depart": depart,
        "arrive": arrive,
        "duration": duration,
        "transfer": transfer_li.get_text(strip=True) if transfer_li else "",
        "fare": fare_mark.get_text(strip=True) if fare_mark else "",
        "stations": stations,
        "lines": lines,
    }


def get_next_trains(station: str, to_station: str | None = None, now: datetime.datetime | None = None) -> str:
    """
    station から to_station 方面へ出る、次の電車の情報。
    to_station がなければ、隣の駅（NEIGHBOR_STATION、なければ東京）方面で代用する。
    """
    now = now or datetime.datetime.now()
    if to_station and to_station == station:
        return f"{station}駅から{station}駅への電車は調べられません。行き先を変えてください。"
    direction = to_station or NEIGHBOR_STATION.get(station, "東京")
    if direction == station:
        direction = "新宿"

    try:
        route = parse_route_summary(_fetch(_search_params(station, direction, now, _DEPART)))
    except Exception as e:
        return f"時刻表の取得でエラーが起きました: {type(e).__name__}: {e}"

    if not route or not route["depart"]:
        return f"{station}駅の時刻表が取得できませんでした。"

    wait = format_wait(minutes_until(route["depart"], now))
    line = route["lines"][0] if route["lines"] else ""
    text = f"【次の電車】{station}駅（{direction}方面）\n{wait}（{route['depart']}発）\n{line}".rstrip()
    if to_station and route["arrive"]:
        text += f"\n{to_station}着 {route['arrive']}"
    return text


def get_train_route(from_station: str, to_station: str, arrive_time: str | None = None,
                    now: datetime.datetime | None = None) -> str:
    """出発駅から到着駅までの経路。arrive_time（'HH:MM'）があれば、その時刻に着く経路を探す"""
    now = now or datetime.datetime.now()

    if arrive_time:
        try:
            h, m = map(int, arrive_time.split(":"))
            when = now.replace(hour=h, minute=m, second=0, microsecond=0)
        except ValueError:
            return f"到着時刻の形式が正しくありません（HH:MM で指定）: {arrive_time}"
        params = _search_params(from_station, to_station, when, _ARRIVE)
    else:
        params = _search_params(from_station, to_station, now, _DEPART)

    try:
        route = parse_route_summary(_fetch(params))
    except Exception as e:
        return f"経路検索でエラーが起きました: {type(e).__name__}: {e}"

    if not route:
        return "経路が見つかりませんでした。"

    text = (
        f"【電車】{from_station}→{to_station} "
        f"出発:{route['depart'] or '不明'}発 到着:{route['arrive'] or '不明'}着 "
        f"所要時間:{route['duration'] or '不明'} {route['transfer']} 料金:{route['fare'] or '不明'}"
    )

    if route["depart"]:
        try:
            wait = minutes_until(route["depart"], now)
            text += "\nまもなく出発です！" if wait == 0 else f"\n{format_wait(wait)}で出発（{route['depart']}発）"
        except ValueError:
            pass

    stations, lines = route["stations"], route["lines"]
    if stations and lines:
        for i, line in enumerate(lines):
            if i < len(stations):
                text += f"\n  {stations[i]} →[{line}]"
        text += f"\n  {stations[-1]}"

    return text
