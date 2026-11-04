"""
ALIA - FastAPIサーバー(iPhone対応版)

音声認識(STT)はiPhone側(ショートカットアプリの「テキストを書き起こす」)で行い、
このサーバーはテキストを受け取ってgemma4:12b(+ツール呼び出し)で処理し、
VOICEVOXで音声合成した結果(wavファイル)を返す。

起動方法:
    python server.py

起動すると自動的にngrokトンネルが張られ、外部からアクセス可能なURLが表示される。
.env に以下を設定しておくこと:
    API_KEY=好きな文字列（iPhone側と共有する簡易認証キー）
    NGROK_TOKEN=ngrokのauthtoken
"""

import os
import json
import io
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from dotenv import load_dotenv
import ngrok
import uvicorn
import requests

from assistant_core import new_conversation, process_message, OLLAMA_URL, MODEL_NAME
from map_utils import get_map_url, geocode_place, extract_nav_destination
from weather_utils import get_weather_info

load_dotenv()

app = FastAPI()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422エラー時に、実際に送られてきたリクエストの中身をコンソールに出力する(デバッグ用)"""
    body = await request.body()
    print(f"[422エラー] path={request.url.path}")
    print(f"[422エラー] headers={dict(request.headers)}")
    print(f"[422エラー] body={body}")
    print(f"[422エラー] detail={exc.errors()}")

    # exc.errors()の中にbytes型が混ざっているとJSON化できないため、安全な形に変換する
    safe_errors = []
    for err in exc.errors():
        safe_err = dict(err)
        if isinstance(safe_err.get("input"), bytes):
            safe_err["input"] = safe_err["input"].decode("utf-8", errors="replace")
        safe_errors.append(safe_err)

    return JSONResponse(status_code=422, content={"detail": safe_errors})

API_KEY = os.getenv("API_KEY")
NGROK_TOKEN = os.getenv("NGROK_TOKEN")
NGROK_DOMAIN = os.getenv("NGROK_DOMAIN")  # 例: alia-yamato.ngrok-free.app

VOICEVOX_URL = "http://localhost:50021"
VOICEVOX_SPEAKER_ID = int(os.getenv("VOICEVOX_SPEAKER_ID", "14"))

# サーバーは常時起動想定なので、会話履歴はプロセス内でセッション単位に持つ。
# 今回はシンプルにグローバル1セッションのみ(iPhone1台からの利用を想定)。
conversation_history = new_conversation()


class TextPayload(BaseModel):
    text: str
    lat: float | None = None
    lon: float | None = None


def phrase_destination_weather(destination: str, weather_text: str) -> str:
    """目的地の天気情報を、ナビ開始時に話しかける一言に自然言語化する（ツール呼び出し不要の軽量な単発呼び出し）"""
    prompt = (
        f"目的地「{destination}」の天気情報:\n{weather_text}\n\n"
        "これから目的地に向かう人に向けて、この天気情報を踏まえた自然な日本語の話し言葉で、"
        "1〜2文の短い一言を作ってください。前置きや説明は不要で、その一言だけを出力してください。"
    )
    try:
        res = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
            },
            timeout=15,
        )
        res.raise_for_status()
        return res.json()["message"]["content"].strip()
    except Exception as e:
        print(f"[navigate] 天気の文章化に失敗: {type(e).__name__}: {e}")
        return f"{destination}への経路を用意しました。{weather_text}"


def synthesize_voice(text: str) -> bytes:
    """VOICEVOXでテキストを音声化し、wavのバイト列を返す"""
    query_res = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": VOICEVOX_SPEAKER_ID},
        timeout=10,
    )
    query_res.raise_for_status()

    synth_res = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        params={"speaker": VOICEVOX_SPEAKER_ID},
        json=query_res.json(),
        timeout=30,
    )
    synth_res.raise_for_status()
    return synth_res.content


def _verify_api_key(x_api_key: str):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="認証エラー")


@app.post("/chat_text")
async def chat_text(payload: TextPayload, x_api_key: str = Header(...)):
    """
    テキストを受け取り、テキストで返す(音声合成なし・デバッグ/軽量用)。
    """
    _verify_api_key(x_api_key)

    print(f"[受信] {payload.text}")
    context = {}
    if payload.lat is not None and payload.lon is not None:
        context["lat"] = payload.lat
        context["lon"] = payload.lon
    reply = process_message(conversation_history, payload.text, context=context)
    print(f"[返答] {reply}")

    return {"text": reply}


@app.post("/chat_voice")
async def chat_voice(payload: TextPayload, x_api_key: str = Header(...)):
    """
    テキストを受け取り、gemma4:12bでの回答をVOICEVOXで音声化してwavで返す。
    ツールがマップを呼んだ場合、地図URLをレスポンスヘッダー X-Maps-Url に載せる。
    iPhoneのショートカットは、このヘッダーがあれば「URLを開く」でマップアプリを起動できる。
    """
    _verify_api_key(x_api_key)

    print(f"[受信] {payload.text} (lat={payload.lat}, lon={payload.lon})")
    context = {}
    if payload.lat is not None and payload.lon is not None:
        context["lat"] = payload.lat
        context["lon"] = payload.lon
    reply = process_message(conversation_history, payload.text, context=context)
    print(f"[返答] {reply}")

    try:
        audio_bytes = synthesize_voice(reply)
    except Exception as e:
        print(f"[音声合成エラー] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="音声合成に失敗しました")

    headers = {}
    if "map_url" in context:
        headers["X-Maps-Url"] = context["map_url"]

    return Response(content=audio_bytes, media_type="audio/wav", headers=headers)


@app.post("/navigate")
async def navigate(request: Request, x_api_key: str = Header(...)):
    """
    iPhoneの現在地(lat/lon)と発話全文(text)を受け取り、
      1. textから目的地名を抽出
      2. 現在地(lat/lon)を出発地として経路URL(Apple Maps)を生成
      3. 目的地をジオコーディングして天気を取得
      4. gemma4:12bで天気情報を自然な一言に整形
    をまとめて行い、JSONで返す({"url": 地図URL, "text": 天気コメント})。
    iPhoneのショートカットは "url" キーの値を「URLを開く」アクションに渡せばよい。
    """
    _verify_api_key(x_api_key)

    raw_body = await request.body()
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[navigate] JSONパース失敗: {e} / body={raw_body}")
        raise HTTPException(status_code=400, detail="リクエストボディをJSONとして解釈できませんでした")

    text = data.get("text")
    lat = data.get("lat")
    lon = data.get("lon")

    if not text or lat is None or lon is None:
        raise HTTPException(status_code=400, detail="text/lat/lonが不足しています")

    destination = extract_nav_destination(text)
    if not destination:
        raise HTTPException(status_code=400, detail="目的地を認識できませんでした")

    origin_coords = f"{lat},{lon}"
    url = get_map_url(destination, origin_coords)
    print(f"[navigate] {destination} <- {text} (origin={origin_coords})")
    print(f"[navigate] url={url}")

    dest_lat, dest_lon = geocode_place(destination)
    if dest_lat is not None:
        weather_text = get_weather_info(dest_lat, dest_lon)
    else:
        weather_text = "目的地周辺の天気情報は取得できませんでした。"

    reply = phrase_destination_weather(destination, weather_text)
    print(f"[navigate] {reply}")

    return {"url": url, "text": reply}


@app.post("/alia")
async def alia(payload: TextPayload, x_api_key: str = Header(...)):
    """
    /chat_voice と同じ処理。以前のショートカットのURL(.../alia)をそのまま使えるようにするための
    エイリアスエンドポイント。
    """
    return await chat_voice(payload, x_api_key)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    if NGROK_DOMAIN:
        listener = ngrok.forward(8000, authtoken=NGROK_TOKEN, domain=NGROK_DOMAIN)
    else:
        listener = ngrok.forward(8000, authtoken=NGROK_TOKEN)
    print(f"外部URL: {listener.url()}")
    uvicorn.run(app, host="0.0.0.0", port=8000)