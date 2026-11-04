"""
ALIA - 音声（合成と入力）

- synthesize: VOICEVOX でテキストを wav にする（サーバーは iPhone にこの wav を返す）
- speak: synthesize した音声を PC のスピーカーで再生する（Windows）
- listen: PC のマイクで録音し、faster-whisper で文字にする（音声入力を使うときだけ）

VOICEVOX のエンジンが config.VOICEVOX_URL（標準は http://localhost:50021）で動いている必要がある。
音声入力に使う sounddevice・faster-whisper は重いので、使うときにだけ読み込む
（requirements-voice.txt で別に入れる）。
"""

import os
import sys
import tempfile

import requests

from alia import config


def synthesize(text: str, speaker_id: int | None = None) -> bytes:
    """テキストを音声にして wav のバイト列を返す。失敗したら例外を投げる"""
    speaker = config.VOICEVOX_SPEAKER_ID if speaker_id is None else speaker_id

    query_res = requests.post(
        f"{config.VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": speaker},
        timeout=10,
    )
    query_res.raise_for_status()
    query = query_res.json()
    query["volumeScale"] = config.VOICEVOX_VOLUME
    query["prePhonemeLength"] = max(query.get("prePhonemeLength", 0.1), 0.1)  # 最初の音が切れないように

    synth_res = requests.post(
        f"{config.VOICEVOX_URL}/synthesis",
        params={"speaker": speaker},
        json=query,
        timeout=30,
    )
    synth_res.raise_for_status()
    return synth_res.content


def speak(text: str) -> bool:
    """テキストを読み上げる（再生が終わるまで待つ）。成功したら True"""
    if not text.strip():
        return False
    try:
        audio = synthesize(text)
    except requests.exceptions.ConnectionError:
        print("[音声] VOICEVOX につながりません。起動しているか確認してください。")
        return False
    except Exception as e:
        print(f"[音声] 音声合成でエラー: {type(e).__name__}: {e}")
        return False

    if sys.platform != "win32":
        print("[音声] 再生は Windows でのみ対応しています（文字の返答は表示済み）。")
        return False

    import winsound

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio)
        path = f.name
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME)
    finally:
        os.remove(path)
    return True


# ============ 音声入力（PC のマイク） ============

SAMPLE_RATE = 16000
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        print(f"[音声入力] 音声認識モデル（{config.WHISPER_MODEL}）を読み込んでいます...")
        _whisper_model = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE, compute_type="int8")
    return _whisper_model


def record_until_enter():
    """Enter を押すまで録音して、音声（float32 の配列）を返す"""
    import numpy as np
    import sounddevice as sd

    chunks = []

    def on_audio(indata, frames, time, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=on_audio):
        input("● 録音中... 話し終わったら Enter ")

    if not chunks:
        return np.zeros(0, dtype="float32")
    return np.concatenate(chunks).flatten()


def transcribe(audio) -> str:
    """音声を日本語の文字にする"""
    if len(audio) < SAMPLE_RATE * 0.3:  # 0.3 秒未満は何も話していないとみなす
        return ""
    segments, _ = _get_whisper().transcribe(audio, language="ja", vad_filter=True)
    return "".join(seg.text for seg in segments).strip()


def listen() -> str:
    """Enter で録音を始め、もう一度 Enter で止めて、文字にして返す"""
    return transcribe(record_until_enter())
