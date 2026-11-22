"""Varios idiomas: traducción de textos de la web y del bot con la IA de la app.

POST /traducir  {idioma, textos: [str]}  -> {traducciones: [str]}   (sin sesión)
  La web manda los textos que hay en pantalla y los sustituye. Cada texto se traduce
  una sola vez (caché en memoria): las etiquetas de la web se repiten mucho.
  Si la IA no responde, se devuelven los textos tal cual (la web sigue funcionando).

traducir(textos, idioma) -> list[str]   la usa también el bot (api/bot.py).
"""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["idiomas"])

IDIOMAS = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "ro": "Romanian",
    "ar": "Arabic",
}
MAX_TEXTOS = 150
MAX_LARGO = 600
_CACHE: dict[tuple[str, str], str] = {}
_LOCK = threading.Lock()

_PROMPT = """Translate each Spanish text of this JSON list into {idioma}.
Context: a community events app for small villages in Almería (Spain).
Rules: keep the same order and the same number of items; keep emojis, numbers, dates,
prices, URLs, HTML tags and «quotes» as they are; do NOT translate the names of the
villages (Senés, Tahal, Velefique, Uleila del Campo, Lucainena de las Torres), people or
places; short UI labels stay short. If a text is already in {idioma}, copy it.
Answer ONLY with JSON: {{"t": ["...", "..."]}}

TEXTS:
{textos}"""


def traducir(textos: list[str], idioma: str) -> list[str]:
    """Traduce del español al idioma pedido. Nunca falla: si no puede, devuelve los originales."""
    if idioma not in IDIOMAS or not textos:
        return list(textos)
    salida = list(textos)
    pendientes = [(i, t) for i, t in enumerate(textos) if t and t.strip() and (idioma, t) not in _CACHE]
    for i, t in enumerate(textos):
        if (idioma, t) in _CACHE:
            salida[i] = _CACHE[(idioma, t)]
    for inicio in range(0, len(pendientes), 40):  # de 40 en 40: respuestas cortas y fiables
        lote = pendientes[inicio:inicio + 40]
        try:
            import ia
            bruto = ia._llamar(_PROMPT.format(idioma=IDIOMAS[idioma],
                                              textos=json.dumps([t for _, t in lote], ensure_ascii=False)),
                               como_json=True)
            datos = ia._json(bruto) or {}
            trad = datos.get("t") if isinstance(datos, dict) else None
            if not isinstance(trad, list) or len(trad) != len(lote):
                continue
            with _LOCK:
                if len(_CACHE) > 20000:
                    _CACHE.clear()
                for (i, original), nuevo in zip(lote, trad):
                    nuevo = str(nuevo or "").strip() or original
                    _CACHE[(idioma, original)] = nuevo
                    salida[i] = nuevo
        except Exception as e:
            print(f"[idiomas] no se pudo traducir a {idioma}: {e!r}")
    return salida


class PeticionTraducir(BaseModel):
    idioma: str
    textos: list[str]


@router.post("/traducir")
def traducir_textos(body: PeticionTraducir):
    if body.idioma not in IDIOMAS:
        raise HTTPException(400, "Idioma no disponible")
    textos = [str(t)[:MAX_LARGO] for t in body.textos[:MAX_TEXTOS]]
    return {"traducciones": traducir(textos, body.idioma)}
