"""
ALIA - Google Calendar連携モジュール
リフレッシュトークン方式(.env)＋生のrequestsでAPIを叩くシンプル構成。

予定の確認(list_calendar_events)・変更(update_calendar_event)は、
アクセス可能な全カレンダーを横断して検索する。
予定の追加(add_calendar_event)は .env の GOOGLE_CALENDAR_ID で指定したカレンダーに追加する。

日時の解析（「明日」「午後3時」等の自然言語→具体的な日時への変換）はLLM側の
Function Callingに任せる。ここでは「もう解析済みの日時」を受け取ってAPIを叩くだけ。

事前準備: 初回のみ手動でOAuth認可フローを実行してリフレッシュトークンを取得し、
.env に GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN / GOOGLE_CALENDAR_ID を設定すること。
"""

import os
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary").strip()


def _get_access_token() -> str | None:
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN):
        return None

    res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": GOOGLE_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    token = res.json().get("access_token")
    if not token:
        print(f"[Calendar] トークン取得失敗: {res.text}")
    return token


def _parse_datetime(text: str) -> datetime.datetime:
    """'YYYY-MM-DD HH:MM' 形式の文字列をdatetimeにパースする"""
    return datetime.datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")


def _fmt_date(dt) -> str:
    return dt.strftime("%#m月%#d日") if os.name == "nt" else dt.strftime("%-m月%-d日")


def _get_calendar_list(access_token: str) -> list[dict]:
    """アクセス可能な全カレンダーの一覧(id, summary)を取得する"""
    res = requests.get(
        "https://www.googleapis.com/calendar/v3/users/me/calendarList",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if res.status_code != 200:
        print(f"[Calendar] カレンダー一覧取得エラー {res.status_code}: {res.text}")
        return [{"id": GOOGLE_CALENDAR_ID, "summary": "メイン"}]

    items = res.json().get("items", [])
    return [{"id": c["id"], "summary": c.get("summary", c["id"])} for c in items]


def _get_events_for_date_all_calendars(access_token: str, date: datetime.date) -> list[dict]:
    """全カレンダーを横断して、指定日の予定を取得する。各イベントに calendar_id / calendar_name を付与する。"""
    time_min = f"{date.strftime('%Y-%m-%d')}T00:00:00+09:00"
    time_max = f"{date.strftime('%Y-%m-%d')}T23:59:59+09:00"

    calendars = _get_calendar_list(access_token)
    all_events = []

    for cal in calendars:
        res = requests.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{requests.utils.quote(cal['id'])}/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeZone": "Asia/Tokyo",
            },
            timeout=10,
        )
        if res.status_code != 200:
            # 権限がないカレンダー等はスキップ
            continue

        for ev in res.json().get("items", []):
            ev["_calendar_id"] = cal["id"]
            ev["_calendar_name"] = cal["summary"]
            all_events.append(ev)

    def _sort_key(ev):
        start = ev.get("start", {})
        if "dateTime" in start:
            return datetime.datetime.fromisoformat(start["dateTime"]).replace(tzinfo=None)
        return datetime.datetime.combine(date, datetime.time.min)

    all_events.sort(key=_sort_key)
    return all_events


def add_calendar_event(summary: str, start_time: str, end_time: str = None) -> str:
    """
    予定を追加する(.envのGOOGLE_CALENDAR_IDで指定したカレンダーに追加)。
    start_time / end_time: 'YYYY-MM-DD HH:MM' 形式。end_timeなければ開始の1時間後。
    """
    access_token = _get_access_token()
    if not access_token:
        return "Google認証情報が未設定、またはトークン取得に失敗しました。"

    try:
        start_dt = _parse_datetime(start_time)
    except ValueError:
        return f"開始時刻の形式が正しくありません: {start_time}"

    if end_time:
        try:
            end_dt = _parse_datetime(end_time)
        except ValueError:
            return f"終了時刻の形式が正しくありません: {end_time}"
    else:
        end_dt = start_dt + datetime.timedelta(hours=1)

    event = {
        "summary": summary,
        "start": {"dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"), "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"), "timeZone": "Asia/Tokyo"},
    }

    res = requests.post(
        f"https://www.googleapis.com/calendar/v3/calendars/{GOOGLE_CALENDAR_ID}/events",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json=event,
        timeout=10,
    )

    if res.status_code == 200:
        date_str = _fmt_date(start_dt)
        return f"{date_str}{start_dt.strftime('%H時%M分')}から{summary}をカレンダーに追加しました。"

    return f"カレンダー追加エラー ({res.status_code}): {res.text[:200]}"


def list_calendar_events(date: str) -> str:
    """指定日の予定一覧を、全カレンダー横断で取得する（読み上げ用テキスト）。date: 'YYYY-MM-DD'"""
    access_token = _get_access_token()
    if not access_token:
        return "Google認証情報が未設定、またはトークン取得に失敗しました。"

    try:
        target_date = datetime.datetime.strptime(date.strip(), "%Y-%m-%d").date()
    except ValueError:
        return f"日付の形式が正しくありません: {date}"

    events = _get_events_for_date_all_calendars(access_token, target_date)
    date_str = _fmt_date(target_date)

    if not events:
        return f"{date_str}の予定はありません。"

    lines = [f"{date_str}の予定は{len(events)}件です。"]
    for ev in events:
        title = ev.get("summary", "無題")
        cal_name = ev.get("_calendar_name", "")
        start = ev.get("start", {})
        if "dateTime" in start:
            dt = datetime.datetime.fromisoformat(start["dateTime"])
            lines.append(f"・{dt.strftime('%H時%M分')} {title}（{cal_name}）")
        else:
            lines.append(f"・終日 {title}（{cal_name}）")

    return "\n".join(lines)


def update_calendar_event(summary_keyword: str, date: str, new_start_time: str) -> str:
    """
    指定日・キーワードで予定を全カレンダー横断で検索し、開始時刻を変更する（所要時間は維持）。
    date: 'YYYY-MM-DD', new_start_time: 'YYYY-MM-DD HH:MM'
    """
    access_token = _get_access_token()
    if not access_token:
        return "Google認証情報が未設定、またはトークン取得に失敗しました。"

    try:
        target_date = datetime.datetime.strptime(date.strip(), "%Y-%m-%d").date()
    except ValueError:
        return f"日付の形式が正しくありません: {date}"

    try:
        new_start_dt = _parse_datetime(new_start_time)
    except ValueError:
        return f"新しい開始時刻の形式が正しくありません: {new_start_time}"

    events = _get_events_for_date_all_calendars(access_token, target_date)
    matched = [ev for ev in events if summary_keyword in ev.get("summary", "")]

    if not matched:
        return f"{_fmt_date(target_date)}に「{summary_keyword}」を含む予定が見つかりませんでした。"
    if len(matched) > 1:
        titles = "、".join(f"{ev.get('summary','')}({ev.get('_calendar_name','')})" for ev in matched)
        return f"「{summary_keyword}」に一致する予定が複数見つかりました({titles})。もう少し具体的に指定してください。"

    event = matched[0]
    calendar_id = event["_calendar_id"]

    old_start = datetime.datetime.fromisoformat(event["start"]["dateTime"])
    old_end = datetime.datetime.fromisoformat(event["end"]["dateTime"])
    duration = old_end.replace(tzinfo=None) - old_start.replace(tzinfo=None)
    new_end_dt = new_start_dt + duration

    res = requests.patch(
        f"https://www.googleapis.com/calendar/v3/calendars/{requests.utils.quote(calendar_id)}/events/{event['id']}",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={
            "start": {"dateTime": new_start_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"), "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": new_end_dt.strftime("%Y-%m-%dT%H:%M:%S+09:00"), "timeZone": "Asia/Tokyo"},
        },
        timeout=10,
    )

    if res.status_code == 200:
        title = event.get("summary", "予定")
        return f"「{title}」の時間を{new_start_dt.strftime('%H時%M分')}〜{new_end_dt.strftime('%H時%M分')}に変更しました。"

    return f"カレンダー更新エラー ({res.status_code}): {res.text[:200]}"