"""音声：VOICEVOX への頼み方と、PC での再生・音声入力の扱い"""

import sys

import pytest

from alia import speech
from tests.conftest import FakeResponse


@pytest.fixture
def voicevox(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/audio_query"):
            return FakeResponse(json_data={"speedScale": 1.0, "prePhonemeLength": 0.0})
        return FakeResponse(content=b"WAVDATA")

    monkeypatch.setattr(speech.requests, "post", fake_post)
    return calls


def test_synthesize(voicevox, monkeypatch, test_config):
    monkeypatch.setattr(test_config, "VOICEVOX_VOLUME", 2.0)
    assert speech.synthesize("こんにちは") == b"WAVDATA"

    (query_url, query_kw), (synth_url, synth_kw) = voicevox
    assert query_url == "http://voicevox.test/audio_query"
    assert query_kw["params"] == {"text": "こんにちは", "speaker": 14}
    assert synth_kw["json"]["volumeScale"] == 2.0
    assert synth_kw["json"]["prePhonemeLength"] == 0.1  # 最初の音が切れないように
    assert synth_kw["json"]["speedScale"] == 1.0         # ほかの設定はそのまま


def test_speak_empty_text_does_nothing(voicevox):
    assert speech.speak("   ") is False
    assert voicevox == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows では実際に再生される")
def test_speak_outside_windows_does_not_crash(voicevox):
    assert speech.speak("こんにちは") is False


def test_speak_when_voicevox_is_down(monkeypatch):
    def down(url, **kwargs):
        raise speech.requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(speech.requests, "post", down)
    assert speech.speak("こんにちは") is False


def test_too_short_recording_is_silence(monkeypatch):
    monkeypatch.setattr(speech, "_get_whisper", lambda: pytest.fail("認識しなくてよい"))
    assert speech.transcribe([0.0] * 100) == ""
