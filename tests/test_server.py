"""スマホから呼ぶサーバー：認証、返す形（今のショートカットが動き続けること）、エラーのとき"""

import pytest
from fastapi.testclient import TestClient

from alia import server

KEY = {"x-api-key": "test-key"}
WAV = b"RIFF....WAVEfmt fake"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "conversation_history", server.new_conversation())
    monkeypatch.setattr(server, "synthesize", lambda text: WAV)
    return TestClient(server.app)


@pytest.fixture
def llm(monkeypatch):
    """process_message の代わり。受け取った発話と context を記録する"""
    calls = []

    def install(reply="はい。", map_url=None, error=None):
        def fake_process_message(history, text, context=None, **kwargs):
            calls.append((text, dict(context or {})))
            if error:
                raise error
            if map_url is not None:
                context["map_url"] = map_url
            return reply

        monkeypatch.setattr(server, "process_message", fake_process_message)
        return calls

    return install


def test_health_needs_no_key(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/chat_text", "/chat_voice", "/alia", "/navigate"])
@pytest.mark.parametrize("headers", [{}, {"x-api-key": "wrong"}])
def test_key_is_required(client, llm, path, headers):
    llm()
    res = client.post(path, json={"text": "こんにちは", "lat": 35.0, "lon": 139.0}, headers=headers)
    assert res.status_code in (401, 422)


def test_server_refuses_everything_when_key_is_not_configured(client, llm, monkeypatch):
    llm()
    monkeypatch.setattr(server.config, "API_KEY", "")
    assert client.post("/chat_text", json={"text": "x"}, headers={"x-api-key": ""}).status_code == 401


def test_chat_text_passes_location(client, llm):
    calls = llm(reply="晴れです。")
    res = client.post("/chat_text", json={"text": "天気は？", "lat": 35.7, "lon": 139.7}, headers=KEY)
    assert res.status_code == 200
    assert res.json() == {"text": "晴れです。"}
    assert calls == [("天気は？", {"lat": 35.7, "lon": 139.7})]


@pytest.mark.parametrize("path", ["/chat_voice", "/alia"])
def test_voice_returns_wav(client, llm, path):
    llm()
    res = client.post(path, json={"text": "こんにちは"}, headers=KEY)
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    assert res.content == WAV
    assert "x-maps-url" not in res.headers


@pytest.mark.parametrize("path", ["/alia", "/chat_text"])
def test_json_without_content_type_is_accepted(client, llm, path):
    """iPhone のショートカットが Content-Type を付けずに JSON を送ってきても読める（実際に起きた 422 エラー）"""
    calls = llm()
    body = '{"lon":139.7199371255114,"text":"ハロー","lat":35.74516051569766}'.encode()
    res = client.post(path, content=body, headers={**KEY, "content-type": "text/plain"})
    assert res.status_code == 200
    assert calls == [("ハロー", {"lat": 35.74516051569766, "lon": 139.7199371255114})]


def test_lat_lon_as_strings_are_accepted(client, llm):
    calls = llm()
    res = client.post("/chat_text", json={"text": "やあ", "lat": "35.7", "lon": "139.7"}, headers=KEY)
    assert res.status_code == 200
    assert calls[0][1] == {"lat": 35.7, "lon": 139.7}


@pytest.mark.parametrize("body,status", [
    ("not json", 400),
    ("[1, 2]", 400),
    ('{"lat": 35.7}', 422),         # text がない
    ('{"text": "x", "lat": "北"}', 422),
])
def test_chat_bad_bodies(client, llm, body, status):
    llm()
    res = client.post("/alia", content=body.encode(), headers={**KEY, "content-type": "application/json"})
    assert res.status_code == status


def test_voice_returns_map_url_header(client, llm):
    url = "http://maps.apple.com/?saddr=a&daddr=b&dirflg=d"
    llm(reply="地図を用意しました。", map_url=url)
    res = client.post("/alia", json={"text": "横浜までナビして"}, headers=KEY)
    assert res.headers["X-Maps-Url"] == url  # 今のショートカットが読むヘッダー
    assert res.headers["X-Map-URL"] == url   # 6月版のヘッダー名


def test_llm_down_still_answers(client, llm, monkeypatch):
    spoken = []
    monkeypatch.setattr(server, "synthesize", lambda text: spoken.append(text) or WAV)
    llm(error=ConnectionError("ollama is down"))
    res = client.post("/chat_voice", json={"text": "こんにちは"}, headers=KEY)
    assert res.status_code == 200
    assert "Ollama" in spoken[0]


def test_voice_synthesis_failure_is_500(client, llm, monkeypatch):
    llm()

    def broken(text):
        raise ConnectionError("voicevox is down")

    monkeypatch.setattr(server, "synthesize", broken)
    assert client.post("/chat_voice", json={"text": "x"}, headers=KEY).status_code == 500


# ---------------- /navigate ----------------

@pytest.fixture
def nav(monkeypatch):
    monkeypatch.setattr(server, "geocode_place", lambda place: (35.44, 139.64))
    monkeypatch.setattr(server, "get_weather_info", lambda lat, lon: "本日の平均気温: 20°C")
    prompts = []
    monkeypatch.setattr(server, "ask_once", lambda prompt: prompts.append(prompt) or "横浜は晴れ、いってらっしゃい！")
    return prompts


def test_navigate(client, nav):
    res = client.post("/navigate", json={"text": "横浜まで行きたい", "lat": 35.7, "lon": 139.7}, headers=KEY)
    assert res.status_code == 200
    body = res.json()
    assert body["url"] == "http://maps.apple.com/?saddr=35.7%2C139.7&daddr=%E6%A8%AA%E6%B5%9C&dirflg=d"
    assert body["google_url"].startswith("google.navigation:q=")
    assert body["text"] == "横浜は晴れ、いってらっしゃい！"
    assert "本日の平均気温: 20°C" in nav[0]


def test_navigate_accepts_text_plain_body(client, nav):
    """ショートカットが Content-Type を JSON にしないで送ってきても読める"""
    res = client.post(
        "/navigate",
        content='{"text": "鎌倉に行きたい", "lat": 35.7, "lon": 139.7}'.encode(),
        headers={**KEY, "content-type": "text/plain"},
    )
    assert res.status_code == 200
    assert "daddr=%E9%8E%8C%E5%80%89" in res.json()["url"]


def test_navigate_when_llm_is_down(client, nav, monkeypatch):
    def broken(prompt):
        raise ConnectionError("down")

    monkeypatch.setattr(server, "ask_once", broken)
    res = client.post("/navigate", json={"text": "横浜まで行きたい", "lat": 35.7, "lon": 139.7}, headers=KEY)
    assert res.status_code == 200
    assert "横浜への経路を用意しました" in res.json()["text"]


@pytest.mark.parametrize("body,status", [
    ("not json", 400),
    ('{"text": "横浜まで行きたい"}', 400),         # 位置がない
    ('{"text": "", "lat": 1, "lon": 2}', 400),     # 発話がない
    ('{"text": "どこか", "lat": 1, "lon": 2}', 400),  # 目的地がない
])
def test_navigate_bad_requests(client, nav, body, status):
    res = client.post("/navigate", content=body.encode(), headers={**KEY, "content-type": "application/json"})
    assert res.status_code == status


def test_startup_message_suggests_fixing_the_url():
    text = server.startup_message("https://abc-123.ngrok-free.app")
    assert "https://abc-123.ngrok-free.app/alia" in text
    assert "NGROK_DOMAIN=abc-123.ngrok-free.app" in text


def test_startup_message_with_fixed_domain():
    text = server.startup_message("https://alia.ngrok-free.app", "alia.ngrok-free.app")
    assert "https://alia.ngrok-free.app/alia" in text
    assert "NGROK_DOMAIN" not in text
