"""
ALIA - 音声合成モジュール(VOICEVOX)
テキストを音声化してその場で再生する。

VOICEVOXエンジンが http://localhost:50021 で起動している必要がある。
再生にはWindows標準のwinsoundを使うため、追加ライブラリのインストールは不要。
"""

import os
import tempfile
import winsound
import requests
from dotenv import load_dotenv

load_dotenv()

VOICEVOX_URL = "http://localhost:50021"

# 冥鳴ひまり(ノーマル) をデフォルト話者に。.envで上書き可能。
DEFAULT_SPEAKER_ID = int(os.getenv("VOICEVOX_SPEAKER_ID", "14"))
# ちび式じい(ノーマル)。将来的に切り替えたい場合の予備。
CHIBI_SPEAKER_ID = 42


def speak(text: str, speaker_id: int = None) -> bool:
    """
    テキストを音声合成し、その場で再生する(再生完了まで待機=ブロッキング)。
    speaker_id省略時はDEFAULT_SPEAKER_ID(冥鳴ひまり)を使用。
    戻り値: 成功したかどうか
    """
    if not text.strip():
        return False

    speaker_id = speaker_id if speaker_id is not None else DEFAULT_SPEAKER_ID

    try:
        # ① 音声クエリ生成
        query_res = requests.post(
            f"{VOICEVOX_URL}/audio_query",
            params={"text": text, "speaker": speaker_id},
            timeout=10,
        )
        query_res.raise_for_status()
        query_json = query_res.json()

        # ② 音声合成
        synth_res = requests.post(
            f"{VOICEVOX_URL}/synthesis",
            params={"speaker": speaker_id},
            json=query_json,
            timeout=30,
        )
        synth_res.raise_for_status()

        # ③ 一時ファイルに保存して再生
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(synth_res.content)
            temp_path = f.name

        winsound.PlaySound(temp_path, winsound.SND_FILENAME)
        os.remove(temp_path)
        return True

    except requests.exceptions.ConnectionError:
        print("[voice] VOICEVOXエンジンに接続できません。起動しているか確認してください。")
        return False
    except Exception as e:
        print(f"[voice] 音声合成エラー: {type(e).__name__}: {e}")
        return False