"""
ALIA - サーバー（iPhone から使う）

iPhone のショートカットで話した言葉を文字にして送ると、LLM（＋ツール）で返事を作り、
VOICEVOX で音声にした wav を返す。ngrok で外から届く URL を作るので、家の外からも使える。

起動:
    python -m alia.server              # ngrok の URL を作って起動
    python -m alia.server --no-ngrok   # 同じ Wi-Fi の中や PC で試すとき

.env に API_KEY（ショートカットと同じ文字列）と NGROK_TOKEN を設定しておく。

エンドポイント（どれもヘッダー x-api-key が必要。/health だけ不要）
    POST /chat_voice  {"text", "lat"?, "lon"?} → 返事の wav。地図を開くときはヘッダー X-Maps-Url に URL
    POST /alia        /chat_voice と同じ（前のショートカットの URL のまま使えるように）
    POST /chat_text   {"text", "lat"?, "lon"?} → {"text": 返事}（音声なし。動作確認用）
    POST /navigate    {"text", "lat", "lon"}   → {"url": Apple マップ, "google_url": Google マップ, "text": 目的地の天気の一言}
    GET  /health      → {"status": "ok"}
"""

import argparse
import json
import threading

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError

from alia import config
from alia.core import ask_once, new_conversation, process_message
from alia.speech import synthesize
from alia.tools.maps import build_google_maps_nav_url, extract_nav_destination, geocode_place, get_map_url
from alia.tools.weather import get_weather_info

app = FastAPI(title="ALIA")

# iPhone 1台から使う前提なので、会話の履歴はサーバーに1つだけ持つ。
# リクエストが重なっても履歴が混ざらないよう、ロックで1つずつ処理する。
conversation_history = new_conversation()
_conversation_lock = threading.Lock()


class TextPayload(BaseModel):
    text: str
    lat: float | None = None
    lon: float | None = None


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 エラーのとき、送られてきた中身をコンソールに出す（ショートカットの設定ミスを探すため）"""
    body = await request.body()
    print(f"[422エラー] path={request.url.path} body={body!r}")
    print(f"[422エラー] detail={exc.errors()}")

    safe_errors = []
    for err in exc.errors():
        safe_err = dict(err)
        if isinstance(safe_err.get("input"), bytes):
            safe_err["input"] = safe_err["input"].decode("utf-8", errors="replace")
        safe_err.pop("ctx", None)
        safe_errors.append(safe_err)
    return JSONResponse(status_code=422, content={"detail": safe_errors})


def _verify_api_key(x_api_key: str) -> None:
    if not config.API_KEY or x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="認証エラー")


async def _read_json(request: Request) -> dict:
    """
    本文を JSON として読む。iPhone のショートカットは Content-Type を付けずに送ってくることがあり、
    FastAPI の標準の読み方だと 422 エラーになるので、ヘッダーに関係なく自分で読む。
    """
    raw_body = await request.body()
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[{request.url.path}] JSON として読めませんでした: {e} / body={raw_body!r}")
        raise HTTPException(status_code=400, detail="リクエストボディをJSONとして解釈できませんでした")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="リクエストボディはJSONのオブジェクトにしてください")
    return data


async def _read_payload(request: Request) -> TextPayload:
    data = await _read_json(request)
    try:
        return TextPayload.model_validate(data)
    except ValidationError as e:
        print(f"[{request.url.path}] 中身が足りないか、形が違います: {data} / {e.errors()}")
        raise HTTPException(status_code=422, detail="text（と、あれば lat・lon）を送ってください")


def _reply(payload: TextPayload) -> tuple[str, dict]:
    """発話を LLM に渡して返事を作る。戻り値: (返事, context)。context には地図の URL などが入る"""
    print(f"[受信] {payload.text} (lat={payload.lat}, lon={payload.lon})")
    context = {}
    if payload.lat is not None and payload.lon is not None:
        context["lat"], context["lon"] = payload.lat, payload.lon

    try:
        with _conversation_lock:
            reply = process_message(conversation_history, payload.text, context=context)
    except Exception as e:
        print(f"[LLMエラー] {type(e).__name__}: {e}")
        reply = "ごめんなさい、今は考えることができません。PC で Ollama が動いているか確認してください。"

    print(f"[返答] {reply}")
    return reply, context


def _text_response(payload: TextPayload) -> dict:
    reply, context = _reply(payload)
    result = {"text": reply}
    if "map_url" in context:
        result["map_url"] = context["map_url"]
    return result


def _voice_response(payload: TextPayload) -> Response:
    reply, context = _reply(payload)

    try:
        audio = synthesize(reply)
    except Exception as e:
        print(f"[音声合成エラー] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="音声合成に失敗しました")

    headers = {}
    if "map_url" in context:
        # 今のショートカットは X-Maps-Url を見る。6月版の X-Map-URL も付けておく
        headers["X-Maps-Url"] = context["map_url"]
        headers["X-Map-URL"] = context["map_url"]
    return Response(content=audio, media_type="audio/wav", headers=headers)


# LLM と音声合成は時間がかかるので、サーバーが止まらないよう別のスレッドで動かす
@app.post("/chat_text")
async def chat_text(request: Request, x_api_key: str = Header(...)):
    _verify_api_key(x_api_key)
    return await run_in_threadpool(_text_response, await _read_payload(request))


@app.post("/chat_voice")
async def chat_voice(request: Request, x_api_key: str = Header(...)):
    _verify_api_key(x_api_key)
    return await run_in_threadpool(_voice_response, await _read_payload(request))


@app.post("/alia")
async def alia(request: Request, x_api_key: str = Header(...)):
    return await chat_voice(request, x_api_key)


def _destination_weather_comment(destination: str, weather_text: str) -> str:
    """目的地の天気を、出発する人への1〜2文の一言にする"""
    prompt = (
        f"目的地「{destination}」の天気情報:\n{weather_text}\n\n"
        "これから目的地に向かう人に向けて、この天気情報を踏まえた自然な日本語の話し言葉で、"
        "1〜2文の短い一言を作ってください。前置きや説明は不要で、その一言だけを出力してください。"
    )
    try:
        return ask_once(prompt) or f"{destination}への経路を用意しました。"
    except Exception as e:
        print(f"[navigate] 天気の一言づくりに失敗: {type(e).__name__}: {e}")
        return f"{destination}への経路を用意しました。{weather_text}"


def _navigate(text: str, lat: float, lon: float) -> dict:
    destination = extract_nav_destination(text)
    if not destination:
        raise HTTPException(status_code=400, detail="目的地を認識できませんでした")

    url = get_map_url(destination, f"{lat},{lon}")
    print(f"[navigate] {destination} <- {text} url={url}")

    dest_lat, dest_lon = geocode_place(destination)
    if dest_lat is not None:
        weather_text = get_weather_info(dest_lat, dest_lon)
    else:
        weather_text = "目的地周辺の天気情報は取得できませんでした。"

    return {
        "url": url,
        "google_url": build_google_maps_nav_url(destination),
        "text": _destination_weather_comment(destination, weather_text),
    }


@app.post("/navigate")
async def navigate(request: Request, x_api_key: str = Header(...)):
    """
    現在地と発話の全文から、目的地までの地図の URL と、目的地の天気の一言を返す。
    """
    _verify_api_key(x_api_key)
    data = await _read_json(request)

    text, lat, lon = data.get("text"), data.get("lat"), data.get("lon")
    if not text or lat is None or lon is None:
        raise HTTPException(status_code=400, detail="text/lat/lonが不足しています")

    return await run_in_threadpool(_navigate, text, lat, lon)


@app.get("/health")
def health():
    return {"status": "ok"}


def startup_message(public_url: str, fixed_domain: str = "") -> str:
    """起動したときに出す案内（iPhone のショートカットに入れる URL と、URL を固定する方法）"""
    lines = [
        f"外部URL: {public_url}",
        f"iPhone のショートカットに入れる URL: {public_url}/alia",
    ]
    if not fixed_domain:
        domain = public_url.split("://", 1)[-1].rstrip("/")
        lines += [
            "",
            "[ヒント] この URL を固定すると、ショートカットを書き換えずに済みます。.env に次の1行を足してください:",
            f"    NGROK_DOMAIN={domain}",
        ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ALIA のサーバー（iPhone から使う）")
    parser.add_argument("--no-ngrok", action="store_true", help="ngrok を使わない（同じ Wi-Fi の中や PC で試すとき）")
    parser.add_argument("--port", type=int, default=config.SERVER_PORT)
    args = parser.parse_args()

    if not config.API_KEY:
        print("[警告] API_KEY が .env にありません。このままだと、どのリクエストも認証エラーになります。")

    if not args.no_ngrok:
        import ngrok

        options = {"authtoken": config.NGROK_TOKEN}
        if config.NGROK_DOMAIN:
            options["domain"] = config.NGROK_DOMAIN
        listener = ngrok.forward(args.port, **options)
        print(startup_message(listener.url(), config.NGROK_DOMAIN))

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
