"""
ALIA - 電車情報モジュール
Yahoo!路線情報 (transit.yahoo.co.jp) の検索結果ページをスクレイピングして取得。
※ 公式APIではないため、Yahoo側のHTML構造変更で壊れる可能性がある点に注意。
"""

import re
import datetime
import requests
from bs4 import BeautifulSoup, Comment

# 住所（区）→ 最寄り駅の簡易マッピング。後で現在地連携時に精度を上げる想定。
ADDRESS_TO_STATION = {
    "新宿区": "新宿",
    "渋谷区": "渋谷",
    "千代田区": "秋葉原",
    "港区": "品川",
    "中央区": "東京",
    "台東区": "上野",
}

# 「次の電車」問い合わせ時に、隣駅への経路を代わりに検索するためのフォールバック
NEIGHBOR_STATION = {
    "新宿": "渋谷",
    "渋谷": "品川",
    "品川": "東京",
    "秋葉原": "東京",
    "上野": "東京",
    "池袋": "新宿",
}


def get_nearest_station(address: str) -> str | None:
    for keyword, station in ADDRESS_TO_STATION.items():
        if keyword in address:
            return station
    return None


def extract_destination(text: str) -> str | None:
    """発話テキストから目的地の駅名を抽出する"""
    cleaned = re.sub(r"\d{1,2}時\d{0,2}分?(?:まで|までに|に)?", "", text)
    cleaned = re.sub(r"\d{1,2}:\d{2}(?:まで|までに|に)?", "", cleaned)

    match = re.search(r"(\S+?)駅(?:まで|に|へ)", cleaned)
    if match:
        name = match.group(1)
        name = re.sub(r"[にへまで]+$", "", name)
        if name:
            return name

    match = re.search(r"(\S+?)駅(?:まで|に|へ)", text)
    if match:
        name = match.group(1)
        name = re.sub(r"[にへまで]+$", "", name)
        if name and not re.match(r"^\d+$", name):
            return name

    match = re.search(r"([^\d\s]+?)(?:に|へ)(?:行きたい|着きたい|行く|向かい|向かう)", cleaned)
    if match:
        return match.group(1).split()[-1]

    for m in re.finditer(r"(\S+?)まで", cleaned):
        candidate = m.group(1)
        if not re.search(r"\d", candidate) and len(candidate) >= 2:
            return candidate

    return None


def extract_arrive_time(text: str) -> str | None:
    """発話テキストから到着希望時刻を抽出する"""
    match = re.search(r"(\d{1,2})[時:](\d{2})(?:分)?.*?(?:着|に着|までに|まで)", text)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"

    match = re.search(r"(\d{1,2})時(?:まで|に着|までに|着きたい|までに着きたい)", text)
    if match:
        return f"{int(match.group(1)):02d}:00"

    return None


def get_next_trains(station: str) -> str:
    """指定駅から隣駅への次の電車情報を取得"""
    now = datetime.datetime.now()
    neighbor = NEIGHBOR_STATION.get(station, "東京")

    url = (
        f"https://transit.yahoo.co.jp/search/print"
        f"?from={station}&to={neighbor}&type=1"
        f"&y={now.year}&m={now.month:02d}&d={now.day:02d}"
        f"&hh={now.hour:02d}&m1={now.minute // 10}&m2={now.minute % 10}"
    )
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")

        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        summary = soup.find("div", class_="routeSummary")
        if not summary:
            return f"{station}駅の時刻表が取得できませんでした。"

        time_li = summary.find("li", class_="time")
        dep_span = time_li.find("span") if time_li else None
        dep_time = dep_span.get_text(strip=True).split("発")[0] if dep_span else None

        if not dep_time:
            return f"{station}駅の発車時刻が取得できませんでした。"

        h, m = map(int, dep_time.replace("発", "").split(":"))
        dep_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if dep_dt <= now:
            dep_dt += datetime.timedelta(days=1)
        diff = int((dep_dt - now).total_seconds() // 60)

        if diff == 0:
            time_label = "まもなく発車"
        elif diff >= 60:
            time_label = f"あと{diff // 60}時間{diff % 60}分"
        else:
            time_label = f"あと{diff}分"

        detail = soup.find("div", class_="routeDetail")
        line_name = ""
        if detail:
            line = detail.find("li", class_="transport")
            if line:
                div = line.find("div")
                if div:
                    line_name = div.get_text(strip=True)

        return (
            f"【次の電車】{station}駅\n"
            f"{time_label}（{dep_time}発）\n"
            f"{line_name}"
        )

    except Exception as e:
        return f"時刻表取得エラー: {e}"


def get_train_route(from_station: str, to_station: str, arrive_time: str = None) -> str:
    """出発駅→到着駅の経路検索"""
    now = datetime.datetime.now()

    if arrive_time:
        h, m = map(int, arrive_time.split(":"))
        url = (
            f"https://transit.yahoo.co.jp/search/print"
            f"?from={from_station}&to={to_station}"
            f"&y={now.year}&m={now.month:02d}&d={now.day:02d}"
            f"&hh={h:02d}&m1={m // 10}&m2={m % 10}"
            f"&type=4"
        )
    else:
        url = (
            f"https://transit.yahoo.co.jp/search/print"
            f"?from={from_station}&to={to_station}"
            f"&y={now.year}&m={now.month:02d}&d={now.day:02d}"
            f"&hh={now.hour:02d}&m1={now.minute // 10}&m2={now.minute % 10}"
            f"&type=1"
        )

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")

        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        summary = soup.find("div", class_="routeSummary")
        if not summary:
            return "経路が見つかりませんでした。"

        time_li = summary.find("li", class_="time")
        transfer_li = summary.find("li", class_="transfer")
        fare_li = summary.find("li", class_="fare")

        dep_span = time_li.find("span") if time_li else None
        arr_span = time_li.find("span", class_="mark") if time_li else None
        dep_time = dep_span.get_text(strip=True).split("発")[0] + "発" if dep_span else "不明"
        arr_time = arr_span.get_text(strip=True) if arr_span else "不明"

        for s in time_li.find_all("span"):
            s.decompose()
        duration = time_li.get_text(strip=True) if time_li else "不明"

        transfer = transfer_li.get_text(strip=True) if transfer_li else ""

        fare_mark = fare_li.find("span", class_="mark") if fare_li else None
        fare = fare_mark.get_text(strip=True) if fare_mark else "不明"

        detail = soup.find("div", class_="routeDetail")
        stations = []
        lines = []
        if detail:
            for station in detail.find_all("div", class_="station"):
                stations.append(station.get_text(strip=True))
            for line in detail.find_all("li", class_="transport"):
                div = line.find("div")
                if div:
                    lines.append(div.get_text(strip=True))

        route_steps = ""
        if stations and lines:
            for i, line in enumerate(lines):
                if i < len(stations):
                    route_steps += f"\n  {stations[i]} →[{line}]"
            if stations:
                route_steps += f"\n  {stations[-1]}"

        depart_in = ""
        dep_clean = dep_time.replace("発", "").strip()
        try:
            dh, dm = map(int, dep_clean.split(":"))
            dep_dt = now.replace(hour=dh, minute=dm, second=0, microsecond=0)
            if dep_dt <= now:
                dep_dt += datetime.timedelta(days=1)
            diff = int((dep_dt - now).total_seconds() // 60)
            if diff == 0:
                depart_in = "\nまもなく出発です！"
            elif diff >= 60:
                depart_in = f"\nあと{diff // 60}時間{diff % 60}分で出発（{dep_time}）"
            else:
                depart_in = f"\nあと{diff}分で出発（{dep_time}）"
        except Exception:
            pass

        return (
            f"【電車】{from_station}→{to_station} "
            f"出発:{dep_time} 到着:{arr_time} "
            f"所要時間:{duration} {transfer} 料金:{fare}"
            f"{depart_in}"
            f"{route_steps}"
        )
    except Exception as e:
        return f"経路検索エラー: {e}"