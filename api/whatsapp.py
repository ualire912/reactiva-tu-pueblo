"""WhatsApp (Cloud API de Meta, modo de prueba: sin verificar la empresa) -> el MISMO bot.

Cómo encaja (sin duplicar la lógica del bot):
  · Entrada: Meta llama a POST /whatsapp/webhook. Cada mensaje se traduce al formato de un
    "update" de Telegram (texto, nota de voz, foto o pulsación de botón) y se pasa a
    bot.procesar_update. El bot no sabe por qué canal le llega.
  · Salida: cada chat de WhatsApp tiene un identificador propio (WA_BASE + nº de teléfono)
    guardado en profiles.telegram_chat_id. telegram_api.py, al enviar, mira si el chat es de
    WhatsApp y entonces lo manda por aquí (texto, botones, fotos, audios, documentos).
  · Botones: hasta 3 -> botones de respuesta de WhatsApp; más -> una lista desplegable.

Variables de entorno (api/.env y Render):
  WHATSAPP_TOKEN            token de acceso (el temporal dura 24 h; el de usuario del sistema no caduca)
  WHATSAPP_PHONE_NUMBER_ID  "Identificador del número de teléfono" del panel de WhatsApp
  WHATSAPP_VERIFY_TOKEN     una palabra que eliges tú y pegas también en Meta al configurar el webhook
  WHATSAPP_APP_SECRET       (opcional) secreto de la app: si está, se comprueba la firma de Meta
"""
from __future__ import annotations

import hashlib
import hmac
import html
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

log = logging.getLogger("reactiva")
router = APIRouter(tags=["whatsapp"])

GRAPH = "https://graph.facebook.com/v21.0"
WA_BASE = 9_000_000_000_000_000_000  # chat_id = WA_BASE + teléfono (cabe en bigint; Telegram usa < 1e13)


# ── Identificadores ───────────────────────────────────────────
def es_whatsapp(chat_id) -> bool:
    try:
        return int(chat_id) >= WA_BASE
    except (TypeError, ValueError):
        return False


def chat_id_de(telefono: str) -> int:
    return WA_BASE + int(re.sub(r"\D", "", telefono))


def telefono_de(chat_id) -> str:
    return str(int(chat_id) - WA_BASE)


def _token() -> str:
    return os.environ.get("WHATSAPP_TOKEN", "").strip()


def _phone_id() -> str:
    return os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()


def configurado() -> bool:
    return bool(_token() and _phone_id())


# ── Formato: el bot escribe en HTML de Telegram; WhatsApp usa *negrita* y _cursiva_ ──
def a_whatsapp(texto: str, escapado: bool = True) -> str:
    t = texto or ""
    if not escapado:  # el bot mandó HTML
        t = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r"\2: \1", t, flags=re.S)
        t = re.sub(r"</?(b|strong)>", "*", t)
        t = re.sub(r"</?(i|em)>", "_", t)
        t = re.sub(r"</?(u|s|code|pre|span|tg-spoiler)[^>]*>", "", t)
        t = re.sub(r"<br\s*/?>", "\n", t)
        t = re.sub(r"<[^>]+>", "", t)
        t = html.unescape(t)
        t = re.sub(r"\*\s*\*", "", t)  # negritas vacías
    return t.strip()[:4096]


def _corto(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", str(t)).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


# ── Envío ─────────────────────────────────────────────────────
async def _post(payload: dict) -> dict | None:
    if not configurado():
        log.warning("WhatsApp sin configurar (WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID)")
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{GRAPH}/{_phone_id()}/messages",
                             headers={"Authorization": f"Bearer {_token()}"},
                             json={"messaging_product": "whatsapp", **payload})
        if r.status_code >= 400:
            log.warning("WhatsApp respondió %s: %s", r.status_code, r.text[:300])
            return None
        return r.json()
    except httpx.HTTPError as e:
        log.warning("WhatsApp no disponible: %r", e)
        return None


def _interactivo(texto: str, botones: list[list[tuple[str, str]]]) -> dict:
    planos = [(str(t), str(d)) for fila in botones for t, d in fila][:10]
    cuerpo = (texto or "Elige una opción 👇").strip()  # conserva los saltos de línea
    cuerpo = cuerpo if len(cuerpo) <= 1024 else cuerpo[:1023] + "…"
    if len(planos) <= 3:
        return {"type": "interactive", "interactive": {
            "type": "button", "body": {"text": cuerpo},
            "action": {"buttons": [{"type": "reply", "reply": {"id": d[:256], "title": _corto(t, 20)}}
                                   for t, d in planos]}}}
    return {"type": "interactive", "interactive": {
        "type": "list", "body": {"text": cuerpo},
        "action": {"button": "Ver opciones",
                   "sections": [{"title": "Opciones",
                                 "rows": [{"id": d[:200], "title": _corto(t, 24),
                                           **({"description": _corto(t, 72)} if len(t) > 24 else {})}
                                          for t, d in planos]}]}}}


async def enviar_texto(chat_id, texto: str, botones=None, escapado: bool = True) -> dict | None:
    to = telefono_de(chat_id)
    cuerpo = a_whatsapp(texto, escapado)
    if botones:
        if len(cuerpo) > 1024:  # el texto de un mensaje con botones es más corto
            await _post({"to": to, "type": "text", "text": {"body": cuerpo, "preview_url": False}})
            return await _post({"to": to, **_interactivo("👇", botones)})
        return await _post({"to": to, **_interactivo(cuerpo, botones)})
    if not cuerpo:
        return None
    return await _post({"to": to, "type": "text", "text": {"body": cuerpo, "preview_url": True}})


async def enviar_foto(chat_id, url: str, caption: str = "", botones=None, escapado: bool = True) -> dict | None:
    to = telefono_de(chat_id)
    pie = a_whatsapp(caption, escapado)
    if botones and len(pie) <= 1024:
        # Foto como cabecera del mensaje con botones: todo en uno
        inter = _interactivo(pie, botones)
        inter["interactive"]["header"] = {"type": "image", "image": {"link": url}}
        r = await _post({"to": to, **inter})
        if r:
            return r
    r = await _post({"to": to, "type": "image", "image": {"link": url, "caption": pie[:1024]}})
    if r and botones:
        await _post({"to": to, **_interactivo("👇", botones)})
    return r


async def _subir_medio(datos: bytes, mime: str, nombre: str) -> str | None:
    if not configurado():
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{GRAPH}/{_phone_id()}/media",
                             headers={"Authorization": f"Bearer {_token()}"},
                             data={"messaging_product": "whatsapp", "type": mime},
                             files={"file": (nombre, datos, mime)})
        return r.json().get("id") if r.status_code < 400 else None
    except httpx.HTTPError:
        return None


async def enviar_audio(chat_id, audio: bytes, caption: str = "") -> dict | None:
    media = await _subir_medio(audio, "audio/mpeg", "respuesta.mp3")
    if not media:
        return None
    r = await _post({"to": telefono_de(chat_id), "type": "audio", "audio": {"id": media}})
    if r and caption:
        await enviar_texto(chat_id, caption)
    return r


async def enviar_documento(chat_id, nombre: str, datos: bytes, caption: str = "") -> dict | None:
    mime = "text/calendar" if nombre.endswith(".ics") else "application/octet-stream"
    media = await _subir_medio(datos, mime, nombre)
    if not media:
        return None
    return await _post({"to": telefono_de(chat_id), "type": "document",
                        "document": {"id": media, "filename": nombre, "caption": caption[:1024]}})


async def descargar_medio(media_id: str) -> bytes | None:
    """Nota de voz o foto que manda el vecino."""
    if not _token():
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            h = {"Authorization": f"Bearer {_token()}"}
            meta = await c.get(f"{GRAPH}/{media_id}", headers=h)
            url = meta.json().get("url") if meta.status_code < 400 else None
            if not url:
                return None
            r = await c.get(url, headers=h)
            return r.content if r.status_code < 400 else None
    except httpx.HTTPError:
        return None


# ── Entrada: mensaje de WhatsApp -> update de Telegram -> bot ──
def a_update(msg: dict, nombre: str | None) -> dict | None:
    chat = chat_id_de(msg["from"])
    de = {"id": chat, "first_name": nombre or "Vecino/a", "language_code": "es"}
    base = {"chat": {"id": chat}, "from": de, "message_id": 0}
    tipo = msg.get("type")
    if tipo == "text":
        return {"message": {**base, "text": msg["text"]["body"]}}
    if tipo == "interactive":
        inter = msg["interactive"]
        dato = (inter.get("button_reply") or inter.get("list_reply") or {}).get("id")
        if dato:
            return {"callback_query": {"id": "", "data": dato, "from": de,
                                       "message": {"chat": {"id": chat}, "message_id": 0}}}
    if tipo == "button":  # respuesta a una plantilla
        return {"message": {**base, "text": msg["button"].get("text") or ""}}
    if tipo == "audio":
        return {"message": {**base, "voice": {"file_id": "wa:" + msg["audio"]["id"]}}}
    if tipo == "image":
        return {"message": {**base, "photo": [{"file_id": "wa:" + msg["image"]["id"]}],
                            "caption": msg["image"].get("caption") or ""}}
    if tipo == "location":
        loc = msg["location"]
        texto = f"Estoy en {loc.get('name') or loc.get('address') or ''} ({loc['latitude']}, {loc['longitude']})"
        return {"message": {**base, "text": texto.strip()}}
    return None


def _firma_valida(cuerpo: bytes, firma: str | None) -> bool:
    secreto = os.environ.get("WHATSAPP_APP_SECRET", "").strip()
    if not secreto:
        return True  # sin secreto configurado no se comprueba (modo de prueba)
    esperada = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma or "")


async def procesar_payload(payload: dict) -> int:
    """Procesa lo que manda Meta. -> nº de mensajes pasados al bot."""
    import bot
    n = 0
    for entrada in payload.get("entry") or []:
        for cambio in entrada.get("changes") or []:
            valor = cambio.get("value") or {}
            nombres = {c.get("wa_id"): (c.get("profile") or {}).get("name")
                       for c in valor.get("contacts") or []}
            for msg in valor.get("messages") or []:
                upd = a_update(msg, nombres.get(msg.get("from")))
                if not upd:
                    await enviar_texto(chat_id_de(msg["from"]),
                                       "Puedo leer texto, notas de voz, fotos de carteles y botones 🙂")
                    continue
                try:
                    await _marcar_leido(msg.get("id"))
                    await bot.procesar_update(upd)
                    n += 1
                except Exception:
                    log.exception("WhatsApp: error con un mensaje")
    return n


async def _marcar_leido(message_id: str | None) -> None:
    if message_id:
        await _post({"status": "read", "message_id": message_id})


@router.get("/whatsapp/webhook")
def verificar(request: Request):
    """Meta comprueba el webhook al configurarlo (hub.challenge)."""
    q = request.query_params
    esperado = os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()
    if q.get("hub.mode") == "subscribe" and esperado and q.get("hub.verify_token") == esperado:
        return PlainTextResponse(q.get("hub.challenge") or "")
    raise HTTPException(403, "Token de verificación incorrecto")


@router.post("/whatsapp/webhook")
async def recibir(request: Request, bg: BackgroundTasks) -> dict:
    cuerpo = await request.body()
    if not _firma_valida(cuerpo, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "Firma no válida")
    payload = await request.json()
    bg.add_task(procesar_payload, payload)  # Meta necesita un 200 rápido
    return {"ok": True}
