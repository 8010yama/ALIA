"""
ALIA - Googleカレンダー用リフレッシュトークン取得スクリプト（初回のみ実行）

事前準備: Google Cloud ConsoleでOAuthクライアントID(デスクトップアプリ)を発行し、
credentials.json をプロジェクトのフォルダ（このスクリプトの1つ上）に置いておくこと。
credentials.json は .gitignore で Git に入らないようにしてある。

実行方法（プロジェクトのフォルダで）:
    python scripts/get_refresh_token.py

実行するとブラウザが開き、Googleログイン・権限許可を求められる。
許可すると、ターミナルに CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN が表示されるので、
それらを .env にコピーしてください。

このスクリプトはセットアップのときだけ使う。ALIA を動かすときには要らない。
"""

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = "credentials.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        client_info = json.load(f)["installed"]

    print("\n=== 以下を .env にコピーしてください ===\n")
    print(f"GOOGLE_CLIENT_ID={client_info['client_id']}")
    print(f"GOOGLE_CLIENT_SECRET={client_info['client_secret']}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("GOOGLE_CALENDAR_ID=primary")
    print("\n==========================================\n")


if __name__ == "__main__":
    main()