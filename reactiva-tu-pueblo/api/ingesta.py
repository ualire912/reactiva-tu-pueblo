"""
api/ingesta.py  ·  C1  ·  Ingesta de Facebook + endpoints de IA (VERSIÓN STUB 10:00)

Endpoints (sección 6):
  POST /ingesta/facebook        {source_url, page_url, text, image_url} -> {ingesta_id, estado}
  POST /ia/extraer         🔒   {text?, image_url?}                     -> EventoExtraido
  POST /eventos/{id}/chat-ia 🔒 {pregunta}                              -> {respuesta}
  GET  /ia/consumo                                                       -> {llamadas, tokens}

Funciones para B2:
  procesar_ingesta(ingesta_id)
  crear_evento_desde_ingesta(ingesta_id) -> dict
  completar_campo(ingesta_id, campo, respuesta) -> list[str]

En main.py (B1):  from ingesta import router as ingesta_router
                  app.include_router(ingesta_router)
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel

import ia

router = APIRouter(tags=["ia-ingesta"])


# ── Auth 🔒 ────────────────────────────────────────────────────
def _usuario(authorization: str | None) -> dict:
    """Valida 'Authorization: Bearer <token>' con db.get_user (de B1).
    Import perezoso: si db.py aún no existe, en modo stub deja pasar."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Falta el token")
    try:
        from db import get_user
    except ImportError:
        return {"id": "stub-user"}
    user = get_user(authorization.split(" ", 1)[1])
    if not user:
        raise HTTPException(401, "Token no válido")
    return user


# ── Modelos de entrada ─────────────────────────────────────────
class PostFacebook(BaseModel):
    source_url: str
    page_url: str | None = None
    text: str | None = None
    image_url: str | None = None


class PeticionExtraer(BaseModel):
    text: str | None = None
    image_url: str | None = None


class PreguntaChat(BaseModel):
    pregunta: str


# ── Endpoints ──────────────────────────────────────────────────
@router.post("/ingesta/facebook")
def ingesta_facebook(post: PostFacebook, bg: BackgroundTasks):
    ingesta_id = str(uuid.uuid4())  # stub: sin BD todavía
    bg.add_task(procesar_ingesta, ingesta_id)
    return {"ingesta_id": ingesta_id, "estado": "nueva"}


@router.post("/ia/extraer")
def ia_extraer(body: PeticionExtraer, authorization: str | None = Header(None)):
    _usuario(authorization)
    return ia.extraer_evento(body.text, None)


@router.post("/eventos/{event_id}/chat-ia")
def chat_ia(event_id: str, body: PreguntaChat, authorization: str | None = Header(None)):
    _usuario(authorization)
    return {"respuesta": ia.responder_chat({"id": event_id}, [], body.pregunta)}


@router.get("/ia/consumo")
def ia_consumo():
    return dict(ia.CONSUMO)


# ── Funciones para B2 ──────────────────────────────────────────
def procesar_ingesta(ingesta_id: str) -> None:
    print(f"[ingesta stub] procesar_ingesta({ingesta_id})")
    try:
        # Import perezoso: evita import circular (bot.py también importa ingesta)
        from bot import pedir_confirmacion_ingesta
        pedir_confirmacion_ingesta(ingesta_id)
    except ImportError:
        print("[ingesta stub] bot.py aún no disponible")


def crear_evento_desde_ingesta(ingesta_id: str) -> dict:
    e = ia.extraer_evento(None)
    return {
        "id": str(uuid.uuid4()),
        "title": e["title"],
        "category": e["category"],
        "starts_at": e["starts_at"],
        "status": "pendiente_moderacion",
        "origen": "facebook",
    }


def completar_campo(ingesta_id: str, campo: str, respuesta: str) -> list[str]:
    """Devuelve lo que aún falta (stub: ya no falta nada)."""
    return []
