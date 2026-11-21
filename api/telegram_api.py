import os
from typing import Any

import httpx


def escape_html(text: str | None) -> str:
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_reply_markup(
    buttons: list[list[tuple[str, str]]] | None = None,
) -> dict[str, Any] | None:
    if not buttons:
        return None
    inline_keyboard = [
        [{"text": label, "callback_data": data} for label, data in row]
        for row in buttons
        if row
    ]
    if not inline_keyboard:
        return None
    return {"inline_keyboard": inline_keyboard}


async def answer_callback(callback_query_id: str, text: str | None = None) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not callback_query_id:
        return False
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception:
        return False


async def send_message(
    chat_id: int | str,
    text: str,
    buttons: list[list[tuple[str, str]]] | None = None,
    escape: bool = True,
) -> dict[str, Any] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    safe_text = escape_html(text) if escape else str(text)
    if len(safe_text) > 3900:
        safe_text = safe_text[:3897] + "..."

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": safe_text,
        "parse_mode": "HTML",
    }
    markup = _build_reply_markup(buttons)
    if markup:
        payload["reply_markup"] = markup

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result") if isinstance(data, dict) else None
            return None
    except Exception:
        return None


async def edit_message_reply_markup(
    chat_id: int | str,
    message_id: int,
    buttons: list[list[tuple[str, str]]] | None = None,
) -> bool:
    """Edita únicamente los botones inline de un mensaje ya enviado."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
    markup = _build_reply_markup(buttons) or {"inline_keyboard": []}
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": markup,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception:
        return False


async def send_photo(
    chat_id: int | str,
    photo_url: str,
    caption: str | None = None,
    buttons: list[list[tuple[str, str]]] | None = None,
    escape: bool = True,
) -> dict[str, Any] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not photo_url:
        return None
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo_url,
    }
    if caption:
        safe_cap = escape_html(caption) if escape else str(caption)
        if len(safe_cap) > 1000:
            safe_cap = safe_cap[:997] + "..."
        payload["caption"] = safe_cap
        payload["parse_mode"] = "HTML"

    markup = _build_reply_markup(buttons)
    if markup:
        payload["reply_markup"] = markup

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result") if isinstance(data, dict) else None
            return None
    except Exception:
        return None


async def send_document(
    chat_id: int | str,
    filename: str,
    content_bytes: bytes,
    caption: str | None = None,
) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    data: dict[str, Any] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:1000]
    files = {"document": (filename, content_bytes, "text/calendar; charset=utf-8")}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, data=data, files=files)
            return resp.status_code == 200
    except Exception:
        return False


async def send_audio(
    chat_id: int | str,
    audio_bytes: bytes,
    caption: str | None = None,
    filename: str = "respuesta.mp3",
) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not audio_bytes:
        return False
    url = f"https://api.telegram.org/bot{token}/sendAudio"
    data: dict[str, Any] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:1000]
    files = {"audio": (filename, audio_bytes, "audio/mpeg")}
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, data=data, files=files)
            return resp.status_code == 200
    except Exception:
        return False


async def get_file_bytes(file_id: str) -> bytes | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not file_id:
        return None
    get_file_url = f"https://api.telegram.org/bot{token}/getFile"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            meta_resp = await client.get(get_file_url, params={"file_id": file_id})
            if meta_resp.status_code != 200:
                return None
            meta_json = meta_resp.json()
            file_path = ((meta_json or {}).get("result") or {}).get("file_path")
            if not file_path:
                return None
            dl_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            file_resp = await client.get(dl_url)
            if file_resp.status_code == 200:
                return file_resp.content
            return None
    except Exception:
        return None