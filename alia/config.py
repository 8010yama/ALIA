"""
ALIA - 設定

.env の読み込みをここ1か所にまとめる。ほかのモジュールは os.getenv を直接呼ばず、
このモジュールの値を使う（読み込む順番によって設定が効かない、ということをなくすため）。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent

# 起動したフォルダの .env → プロジェクトのフォルダの .env の順に読む（先に読んだほうが優先）
load_dotenv(Path.cwd() / ".env")
load_dotenv(PROJECT_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ---- LLM（Ollama） ----
OLLAMA_URL = _get("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = _get("OLLAMA_MODEL", "gemma4:12b")
# 会話履歴として LLM に渡すメッセージの最大数（システムプロンプトを除く）。
# サーバーを動かし続けても履歴が膨らみ続けないようにする。
MAX_HISTORY_MESSAGES = int(_get("MAX_HISTORY_MESSAGES", "30"))

# ---- 自宅（位置情報がないときの既定値） ----
HOME_LAT = float(_get("HOME_LAT", "35.6895"))
HOME_LON = float(_get("HOME_LON", "139.6917"))
HOME_STATION = _get("HOME_STATION", "新宿")
# 「次の電車は？」と方面を言わなかったときの行き先（通勤・通学先の駅）。空なら隣の駅で代用する
COMMUTE_STATION = _get("COMMUTE_STATION")

# ---- サーバー（iPhone から使う） ----
API_KEY = _get("API_KEY")
NGROK_TOKEN = _get("NGROK_TOKEN")
NGROK_DOMAIN = _get("NGROK_DOMAIN")  # 例: alia-yamato.ngrok-free.app（固定ドメインを使うとき）
SERVER_PORT = int(_get("SERVER_PORT", "8000"))

# ---- 音声合成（VOICEVOX） ----
VOICEVOX_URL = _get("VOICEVOX_URL", "http://localhost:50021")
VOICEVOX_SPEAKER_ID = int(_get("VOICEVOX_SPEAKER_ID", "14"))  # 14 = 冥鳴ひまり（ノーマル）
VOICEVOX_VOLUME = float(_get("VOICEVOX_VOLUME", "1.0"))  # スマホで聞こえにくければ 2.0 など

# ---- 音声入力（PC のマイク、faster-whisper） ----
WHISPER_MODEL = _get("WHISPER_MODEL", "medium")
WHISPER_DEVICE = _get("WHISPER_DEVICE", "cpu")  # GPU で動かすなら cuda（CUDA のライブラリが必要）

# ---- 外部サービス ----
YAHOO_APP_ID = _get("YAHOO_APP_ID")

NOTION_TOKEN = _get("NOTION_TOKEN")
NOTION_MEMO_PAGE_ID = _get("NOTION_MEMO_PAGE_ID")
NOTION_TODO_PAGE_ID = _get("NOTION_TODO_PAGE_ID")

GOOGLE_CLIENT_ID = _get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _get("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = _get("GOOGLE_REFRESH_TOKEN")
GOOGLE_CALENDAR_ID = _get("GOOGLE_CALENDAR_ID", "primary")
