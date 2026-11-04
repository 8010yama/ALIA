"""
テストの共通部品。

テストはネットにも Ollama にも VOICEVOX にもつながない。外に出る部分（requests・LLM の呼び出し）は
すべて偽物に差し替える。.env がなくても動くように、設定値もテストの中で決める。
"""

import json

import pytest

from alia import config


class FakeResponse:
    """requests / httpx のレスポンスの代わり"""

    def __init__(self, status_code=200, json_data=None, text=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.text = text if text is not None else (json.dumps(json_data, ensure_ascii=False) if json_data is not None else "")
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("JSON ではありません")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RecordingRequests:
    """
    requests モジュールの代わり。呼ばれた URL と引数を記録し、
    handler(method, url, kwargs) が返した FakeResponse を返す。
    """

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def _call(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.handler(method, url, kwargs)

    def get(self, url, **kwargs):
        return self._call("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._call("POST", url, **kwargs)

    def patch(self, url, **kwargs):
        return self._call("PATCH", url, **kwargs)


@pytest.fixture(autouse=True)
def test_config(monkeypatch):
    """どのテストも、同じ決まった設定で動かす（手元の .env の影響を受けないように）"""
    values = {
        "API_KEY": "test-key",
        "HOME_LAT": 35.0,
        "HOME_LON": 139.0,
        "HOME_STATION": "新宿",
        "COMMUTE_STATION": "",
        "OLLAMA_MODEL": "test-model",
        "MAX_HISTORY_MESSAGES": 30,
        "VOICEVOX_URL": "http://voicevox.test",
        "VOICEVOX_SPEAKER_ID": 14,
        "VOICEVOX_VOLUME": 1.0,
        "YAHOO_APP_ID": "",
        "NOTION_TOKEN": "",
        "NOTION_MEMO_PAGE_ID": "",
        "NOTION_TODO_PAGE_ID": "",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "GOOGLE_REFRESH_TOKEN": "",
        "GOOGLE_CALENDAR_ID": "primary",
    }
    for name, value in values.items():
        monkeypatch.setattr(config, name, value)
    return config


@pytest.fixture
def fake_requests(monkeypatch):
    """fake_requests(module, handler) で、そのモジュールの requests を偽物にする"""

    def install(module, handler):
        fake = RecordingRequests(handler)
        monkeypatch.setattr(module, "requests", fake)
        return fake

    return install
