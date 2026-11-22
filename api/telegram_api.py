import html
import os
from typing import Any

import httpx


def _get_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def escape_html(text: str | None) -> str:
    if not text:
        return ""
    return html.escape(str(text), quote=False)


def build_inline_keyboard(
    buttons: list[list[tuple[str, str]]] | None,
) -> dict[str, Any] | None:
    if not buttons:
        return None
    keyboard: list[list[dict[str, str]]] = []
    for row in buttons:
        row_items: list[dict[str, str]] = []
        for label, callback_data in row:
            row_items.append({"text": str(label), "callback_data": str(callback_data)})
        if row_items:
            keyboard.append(row_items)
    if not keyboard:
        return None
    return {"inline_keyboard": keyboard}


async def send_message(
    chat_id: int | str,
    text: str,
    buttons: list[list[tuple[str, str]]] | None = None,
    escape: bool = True,
) -> dict[str, Any] | None:
    token = _get_token()
    if not token:
        return None

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": escape_html(text) if escape else text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    reply_markup = build_inline_keyboard(buttons)
    if reply_markup:
        payload["reply_markup"] = reply_markup

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json().get("result")
    except Exception:
        pass
    return None


async def edit_message_reply_markup(
    chat_id: int | str,
    message_id: int,
    buttons: list[list[tuple[str, str]]] | None = None,
) -> bool:
    token = _get_token()
    if not token:
        return False

    reply_markup = build_inline_keyboard(buttons) or {"inline_keyboard": []}
    url = f"https://api.telegram.org/bot{token}/editMessageReplyMarkup"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": reply_markup,
                },
            )
            return resp.status_code == 200
    except Exception:
        return False


async def delete_message(chat_id: int | str, message_id: int) -> bool:
    token = _get_token()
    if not token:
        return False

    url = f"https://api.telegram.org/bot{token}/deleteMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json={"chat_id": chat_id, "message_id": message_id},
            )
            return resp.status_code == 200
    except Exception:
        return False


async def send_photo(
    chat_id: int | str,
    photo_url: str,
    caption: str = "",
    buttons: list[list[tuple[str, str]]] | None = None,
    escape: bool = True,
) -> dict[str, Any] | None:
    token = _get_token()
    if not token:
        return None

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": escape_html(caption) if escape else caption,
        "parse_mode": "HTML",
    }
    reply_markup = build_inline_keyboard(buttons)
    if reply_markup:
        payload["reply_markup"] = reply_markup

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json().get("result")
    except Exception:
        pass
    return None


async def send_audio(
    chat_id: int | str,
    audio_bytes: bytes,
    caption: str = "",
    filename: str = "respuesta.mp3",
) -> dict[str, Any] | None:
    token = _get_token()
    if not token or not audio_bytes:
        return None

    url = f"https://api.telegram.org/bot{token}/sendAudio"
    data: dict[str, Any] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    files = {"audio": (filename, audio_bytes, "audio/mpeg")}
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, data=data, files=files)
            if resp.status_code == 200:
                return resp.json().get("result")
    except Exception:
        pass
    return None


async def send_voice(
    chat_id: int | str,
    audio_bytes: bytes,
    caption: str = "",
) -> dict[str, Any] | None:
    """Nota de voz (no archivo de música): Telegram no la mete en la lista de reproducción
    con los audios antiguos del chat, así que al acabar no salta a otro audio ya escuchado.
    sendVoice admite MP3 (además de OGG/Opus y M4A)."""
    token = _get_token()
    if not token or not audio_bytes:
        return None
    url = f"https://api.telegram.org/bot{token}/sendVoice"
    data: dict[str, Any] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:1000]
    files = {"voice": ("reactiva-tu-pueblo.mp3", audio_bytes, "audio/mpeg")}
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, data=data, files=files)
            if resp.status_code == 200:
                return resp.json().get("result")
    except Exception:
        pass
    return None


async def send_document(
    chat_id: int | str,
    filename: str,
    doc_bytes: bytes,
    caption: str = "",
) -> dict[str, Any] | None:
    token = _get_token()
    if not token or not doc_bytes:
        return None

    url = f"https://api.telegram.org/bot{token}/sendDocument"
    data: dict[str, Any] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    files = {"document": (filename, doc_bytes, "text/calendar")}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, data=data, files=files)
            if resp.status_code == 200:
                return resp.json().get("result")
    except Exception:
        pass
    return None


async def answer_callback(callback_query_id: str, text: str = "") -> bool:
    token = _get_token()
    if not token:
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


async def delete_webhook() -> bool:
    """Quita el webhook para poder recibir mensajes por polling (bot_polling.py).
    No borra los mensajes pendientes."""
    token = _get_token()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"drop_pending_updates": False})
            return resp.status_code == 200
    except Exception:
        return False


async def get_file_bytes(file_id: str) -> bytes | None:
    token = _get_token()
    if not token or not file_id:
        return None

    get_file_url = f"https://api.telegram.org/bot{token}/getFile"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            meta_resp = await client.get(get_file_url, params={"file_id": file_id})
            if meta_resp.status_code != 200:
                return None
            file_path = (meta_resp.json().get("result") or {}).get("file_path")
            if not file_path:
                return None
            download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            file_resp = await client.get(download_url)
            if file_resp.status_code == 200:
                return file_resp.content
    except Exception:
        pass
    return None