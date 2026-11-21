"""
api/ingesta.py  ·  C1  ·  Ingesta de Facebook/Telegram + endpoints de IA

Endpoints (contrato sección 6):
  POST /ingesta/facebook           {source_url, page_url, text, image_url} -> {ingesta_id, estado}
  POST /ia/extraer            🔒   {text?, image_url?}                     -> EventoExtraido
  POST /eventos/{id}/chat-ia  🔒   {pregunta, parent_id?}                  -> {respuesta}
  GET  /ia/consumo                                                         -> {llamadas, tokens}

Funciones para B2 (bot.py):
  procesar_ingesta(ingesta_id)                        reglas + IA + spam -> pide confirmación
  crear_evento_desde_ingesta(ingesta_id) -> dict      fila de eventos creada
                                                      o {"error": str, "missing": [...]} si falta algo
  completar_campo(ingesta_id, campo, respuesta) -> list[str]   lo que AÚN falta

Regla de oro: reglas antes que IA, nunca inventar, nunca romper el servidor.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

import ia  # primero ia (carga el .env en local), luego db
from db import evento as db_evento
from db import perfil, require_user, sb

log = logging.getLogger("reactiva.ingesta")
router = APIRouter(tags=["ia-ingesta"])

UMBRAL_SPAM = 0.8
MAX_IMAGEN = 5 * 1024 * 1024  # 5 MB


# ══════════════════════════════════════════════════════════════
#  Utilidades
# ══════════════════════════════════════════════════════════════
def _una(tabla: str, columna: str, valor) -> dict | None:
    if valor is None:
        return None
    filas = sb.table(tabla).select("*").eq(columna, valor).limit(1).execute().data
    return filas[0] if filas else None


def _actualizar_ingesta(ingesta_id: str, **campos) -> None:
    sb.table("ingestas").update(campos).eq("id", ingesta_id).execute()


def _buscar_entidad(page_url: str | None) -> dict | None:
    """Busca la entidad por facebook_page_url, con y sin barra final."""
    if not page_url:
        return None
    url = page_url.strip()
    for variante in {url, url.rstrip("/"), url.rstrip("/") + "/"}:
        e = _una("entidades", "facebook_page_url", variante)
        if e:
            return e
    return None


def descargar_imagen(url: str | None) -> bytes | None:
    """Descarga el cartel (máx. 5 MB). None si no hay o falla."""
    if not url:
        return None
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        r.raise_for_status()
        if not r.headers.get("content-type", "image").startswith("image"):
            return None
        return r.content if len(r.content) <= MAX_IMAGEN else None
    except Exception as e:
        log.warning("No se pudo descargar la imagen %s: %s", url, e)
        return None


def _municipio_id(nombre: str | None) -> int | None:
    if not nombre:
        return None
    n = ia._norm(nombre)
    for m in sb.table("municipios").select("id, nombre").execute().data:
        if ia._norm(m["nombre"]) == n or ia._norm(m["nombre"]) in n:
            return m["id"]
    return None


# ══════════════════════════════════════════════════════════════
#  POST /ingesta/facebook
# ══════════════════════════════════════════════════════════════
class PostFacebook(BaseModel):
    source_url: str
    page_url: str | None = None
    text: str | None = None
    image_url: str | None = None


def registrar_post(source_url: str, page_url: str | None, text: str | None,
                   image_url: str | None, origen: str = "facebook",
                   user_id: str | None = None) -> tuple[dict, bool]:
    """Guarda el post en `ingestas`. Devuelve (fila, es_nueva).
    Regla 1 (gratis): misma source_url -> no se reprocesa."""
    existente = _una("ingestas", "source_url", source_url)
    if existente:
        return existente, False
    entidad = _buscar_entidad(page_url)
    fila = {
        "origen": origen,
        "source_url": source_url,
        "entidad_id": entidad["id"] if entidad else None,
        "user_id": user_id,
        "raw_text": (text or "")[:5000],
        "image_url": image_url,
        "estado": "nueva",
    }
    try:
        creada = sb.table("ingestas").insert(fila).execute().data[0]
        return creada, True
    except Exception:
        # Dos peticiones a la vez con la misma URL: la otra ganó
        existente = _una("ingestas", "source_url", source_url)
        if existente:
            return existente, False
        raise


@router.post("/ingesta/facebook")
def ingesta_facebook(post: PostFacebook, bg: BackgroundTasks):
    if not (post.text or "").strip() and not post.image_url:
        raise HTTPException(400, "El post no tiene texto ni imagen")
    fila, nueva = registrar_post(post.source_url, post.page_url, post.text, post.image_url)
    if nueva:
        bg.add_task(procesar_ingesta, fila["id"])  # el trabajo pesado, en segundo plano
    return {"ingesta_id": fila["id"], "estado": fila["estado"]}


# ══════════════════════════════════════════════════════════════
#  procesar_ingesta  (se ejecuta en segundo plano)
# ══════════════════════════════════════════════════════════════
def procesar_ingesta(ingesta_id: str) -> None:
    try:
        ing = _una("ingestas", "id", ingesta_id)
        if not ing or ing["estado"] not in ("nueva", "error"):
            return
        entidad = _una("entidades", "id", ing.get("entidad_id"))
        texto = ing.get("raw_text") or ""

        # 1. Reglas de spam (gratis)
        regla = ia.reglas_spam(texto, entidad)
        if regla and regla["spam_score"] >= UMBRAL_SPAM:
            _actualizar_ingesta(ingesta_id, estado="spam", spam_score=regla["spam_score"],
                                spam_reason=regla["motivo"])
            log.info("Ingesta %s -> spam por reglas (%s)", ingesta_id, regla["motivo"])
            return

        # 2. Extraer con IA (texto + cartel)
        imagen = descargar_imagen(ing.get("image_url"))
        extraido = ia.extraer_evento(texto, imagen)

        # 3. Spam con IA solo si las reglas dudaron
        spam = regla or ia.clasificar_spam(texto, entidad)
        if spam["spam_score"] >= UMBRAL_SPAM:
            _actualizar_ingesta(ingesta_id, estado="spam", spam_score=spam["spam_score"],
                                spam_reason=spam["motivo"], extracted=extraido)
            log.info("Ingesta %s -> spam por IA (%s)", ingesta_id, spam["motivo"])
            return

        # 4. Guardar lo extraído
        _actualizar_ingesta(ingesta_id, estado="extraida", extracted=extraido,
                            missing=extraido["missing"], spam_score=spam["spam_score"],
                            spam_reason=spam["motivo"])
        log.info("Ingesta %s -> extraida (falta: %s)", ingesta_id, extraido["missing"])

        # 5. Pedir confirmación por Telegram (B2). Si no hay nadie con Telegram,
        #    se queda en 'extraida' y la ve el moderador en el panel.
        if _pedir_confirmacion(ingesta_id):
            _actualizar_ingesta(ingesta_id, estado="esperando_confirmacion")
    except Exception as e:
        log.exception("Error procesando la ingesta %s", ingesta_id)
        try:
            _actualizar_ingesta(ingesta_id, estado="error", spam_reason=f"error: {str(e)[:200]}")
        except Exception:
            pass


def _pedir_confirmacion(ingesta_id: str) -> bool:
    try:
        from bot import pedir_confirmacion_ingesta  # import aquí: evita import circular
    except ImportError:
        log.info("bot.pedir_confirmacion_ingesta aún no existe -> se queda para el panel")
        return False
    try:
        return bool(pedir_confirmacion_ingesta(ingesta_id))
    except Exception:
        log.exception("pedir_confirmacion_ingesta falló")
        return False


# ══════════════════════════════════════════════════════════════
#  completar_campo  (el organizador contesta lo que faltaba)
# ══════════════════════════════════════════════════════════════
_FECHAS = ("starts_at", "ends_at", "inscripcion_hasta")


def interpretar_precio(respuesta: str):
    """'gratis' -> 0 · '5 euros' -> 5 · '3,50€' -> 3.5 · no se entiende -> None"""
    n = ia._norm(respuesta)
    if re.search(r"gratis|gratuit|libre|nada|no se paga|no cuesta|sin coste|^0\b", n):
        return 0
    m = re.search(r"(\d+(?:[.,]\d{1,2})?)", n)
    if m:
        v = float(m.group(1).replace(",", "."))
        return int(v) if v.is_integer() else v
    return None


_SIN_LIMITE = "sin_limite"


def interpretar_plazas(respuesta: str):
    """'30' -> 30 · 'sin límite' -> sin_limite (se guarda null) · no se entiende -> None"""
    n = ia._norm(respuesta)
    if re.search(r"sin limite|ilimitad|no hay limite|todas las que|las que quieran|no hay maximo", n):
        return _SIN_LIMITE
    m = re.search(r"\d+", n)
    return int(m.group(0)) if m and int(m.group(0)) > 0 else None


def completar_campo(ingesta_id: str, campo: str, respuesta: str) -> list[str]:
    """Guarda lo que contestó el organizador y devuelve lo que AÚN falta."""
    ing = _una("ingestas", "id", ingesta_id)
    if not ing:
        return []
    ex = dict(ing.get("extracted") or ia._evento_vacio(ing.get("raw_text")))
    missing = list(ing.get("missing") or ex.get("missing") or [])
    respuesta = (respuesta or "").strip()
    entendido = False

    if campo == "price":
        v = interpretar_precio(respuesta)
        if v is not None:
            ex["price"], entendido = v, True
    elif campo == "max_capacity":
        v = interpretar_plazas(respuesta)
        if v == _SIN_LIMITE:
            ex["max_capacity"], entendido = None, True   # null = sin límite
        elif v is not None:
            ex["max_capacity"], entendido = v, True
    elif campo == "municipio":
        p = ia._pueblo(respuesta)
        if p:
            ex["municipio"], entendido = p, True
    elif campo in ("title", "location_name", "description") and respuesta:
        ex[campo], entendido = respuesta[:200 if campo != "description" else 500], True
    elif campo in _FECHAS and respuesta:
        # Fechas: aquí sí hace falta IA. Se le da el post original + la respuesta.
        texto = (f"{ing.get('raw_text') or ''}\n\n"
                 f"DATO AÑADIDO POR EL ORGANIZADOR sobre {campo}: {respuesta}")
        nuevo = ia.extraer_evento(texto)
        if nuevo.get(campo):
            ex[campo], entendido = nuevo[campo], True
            if campo == "starts_at" and nuevo.get("ends_at") and not ex.get("ends_at"):
                ex["ends_at"] = nuevo["ends_at"]

    if entendido and campo in missing:
        missing.remove(campo)
    ex["missing"] = missing
    _actualizar_ingesta(ingesta_id, extracted=ex, missing=missing)
    return missing


# ══════════════════════════════════════════════════════════════
#  crear_evento_desde_ingesta  (el organizador pulsa "Sí")
# ══════════════════════════════════════════════════════════════
_CAMPOS_EVENTO = ["title", "category", "starts_at", "ends_at", "duration_minutes",
                  "location_name", "price", "max_capacity", "min_age", "max_age",
                  "description", "is_accessible", "inscripcion_hasta"]


def crear_evento_desde_ingesta(ingesta_id: str) -> dict:
    """Crea el evento. Devuelve la fila creada, o {"error", "missing"} si no se puede."""
    ing = _una("ingestas", "id", ingesta_id)
    if not ing:
        return {"error": "No existe esa ingesta", "missing": []}
    if ing.get("event_id"):  # ya creado (p. ej. doble clic en "Sí")
        return _una("eventos", "id", ing["event_id"]) or {"id": ing["event_id"]}
    if ing["estado"] == "spam":
        return {"error": "Marcado como spam", "missing": []}

    ex = ing.get("extracted") or {}
    entidad = _una("entidades", "id", ing.get("entidad_id"))

    # Imprescindibles: sin esto no se puede crear
    municipio_id = _municipio_id(ex.get("municipio")) or (entidad or {}).get("municipio_id")
    faltan = [c for c, v in (("title", ex.get("title")), ("starts_at", ex.get("starts_at")),
                             ("municipio", municipio_id)) if not v]
    if faltan:
        return {"error": "Faltan datos imprescindibles", "missing": faltan}

    # Quién lo publica y con qué estado
    autor_id = ing.get("user_id") or (entidad or {}).get("owner_user_id")
    rol = (perfil(autor_id) or {}).get("rol") if autor_id else None
    publica_directo = bool((entidad or {}).get("auto_approve")) or rol in ("publicador", "moderador")

    fila = {c: ex.get(c) for c in _CAMPOS_EVENTO if ex.get(c) is not None}
    if fila.get("category") not in ia.CATEGORIAS:
        fila["category"] = "otro"
    if fila.get("ends_at"):
        fila.pop("duration_minutes", None)  # la BD calcula uno a partir del otro
    fila.update({
        "municipio_id": municipio_id,
        "entidad_organizadora_id": ing.get("entidad_id"),
        "created_by": autor_id,
        "status": "abierto" if publica_directo else "pendiente_moderacion",
        "origen": ing.get("origen") or "facebook",
        "source_url": ing.get("source_url"),
        "poster_url": ing.get("image_url"),
        "ai_confidence": ex.get("confidence"),
        "ai_flags": {"missing": ing.get("missing") or ex.get("missing") or [],
                     "spam_score": ing.get("spam_score")},
    })
    if not fila.get("poster_url"):  # sin cartel original -> uno automático (sin IA)
        fila["poster_url"] = subir_cartel({**ex, **fila})
    fila = {k: v for k, v in fila.items() if v is not None}

    try:
        ev = sb.table("eventos").insert(fila).execute().data[0]
    except Exception as e:
        log.exception("No se pudo crear el evento de la ingesta %s", ingesta_id)
        return {"error": f"La base de datos no aceptó el evento: {str(e)[:200]}", "missing": []}

    _actualizar_ingesta(ingesta_id, estado="confirmada", event_id=ev["id"])
    log.info("Ingesta %s -> evento %s (%s)", ingesta_id, ev["id"], ev.get("status"))
    return ev


# ══════════════════════════════════════════════════════════════
#  POST /ia/extraer  🔒   (botón "Rellenar desde foto" de la app)
# ══════════════════════════════════════════════════════════════
class PeticionExtraer(BaseModel):
    text: str | None = None
    image_url: str | None = None


@router.post("/ia/extraer")
def ia_extraer(body: PeticionExtraer, user=Depends(require_user)):
    if not (body.text or "").strip() and not body.image_url:
        raise HTTPException(400, "Manda texto o image_url")
    return ia.extraer_evento(body.text, descargar_imagen(body.image_url))


# ══════════════════════════════════════════════════════════════
#  POST /eventos/{id}/chat-ia  🔒   (asistente del chat del evento)
# ══════════════════════════════════════════════════════════════
class PreguntaChat(BaseModel):
    pregunta: str
    parent_id: str | None = None  # id del mensaje-pregunta, si la app lo tiene


_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_bonita(iso: str | None) -> str | None:
    if not iso:
        return None
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ia.TZ)
    return f"el {_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]} a las {d:%H:%M}"


def faq_respuesta(ev: dict, pregunta: str) -> str | None:
    """Preguntas frecuentes respondidas SOLO con la ficha, sin IA (0 tokens).
    None = no es una pregunta frecuente -> que conteste la IA."""
    n = ia._norm(pregunta)
    partes, reconocida = [], False

    if re.search(r"\b(hora|cuando|que dia|empieza|fecha|termina|acaba|dura)", n):
        reconocida = True
        if ev.get("starts_at"):
            t = f"Empieza {_fecha_bonita(ev['starts_at'])}"
            if ev.get("ends_at"):
                fin = datetime.fromisoformat(ev["ends_at"].replace("Z", "+00:00")).astimezone(ia.TZ)
                t += f" y termina a las {fin:%H:%M}"
            partes.append(t + ".")
    if re.search(r"precio|cuesta|cuanto vale|cuanto es|gratis|pagar|euros|€|entrada", n):
        reconocida = True
        if ev.get("price") is not None:
            partes.append("Es gratis." if float(ev["price"]) == 0 else f"Cuesta {ev['price']} €.")
    if re.search(r"donde|lugar|sitio|ubicacion|direccion|punto de encuentro|quedamos", n):
        reconocida = True
        if ev.get("location_name"):
            pueblo = f" ({ev['municipio']})" if ev.get("municipio") else ""
            partes.append(f"Es en {ev['location_name']}{pueblo}.")
    if re.search(r"plaza|quedan|aforo|completo|hay sitio|cuantas personas", n):
        reconocida = True
        if ev.get("max_capacity"):
            libres = max(0, int(ev["max_capacity"]) - int(ev.get("confirmed_count") or 0))
            partes.append(f"Quedan {libres} plazas de {ev['max_capacity']}." if libres
                          else "Está completo, pero puedes apuntarte a la lista de espera.")
        elif "max_capacity" in ev and ev.get("max_capacity") is None:
            partes.append("No hay límite de plazas.")
    if re.search(r"accesib|silla de ruedas|movilidad reducida", n):
        reconocida = True
        if ev.get("is_accessible"):
            partes.append("Sí, es accesible.")
    if re.search(r"edad|ninos|menores|anos|mayores de", n):
        reconocida = True
        if ev.get("min_age") or ev.get("max_age"):
            partes.append(f"Edad: {ev.get('min_age') or 0}–{ev.get('max_age') or 'sin máximo'} años.")
    if re.search(r"apuntar|inscri|plazo|hasta cuando", n):
        reconocida = True
        if ev.get("inscripcion_hasta"):
            partes.append(f"Te puedes apuntar hasta {_fecha_bonita(ev['inscripcion_hasta'])}.")

    if partes:
        return " ".join(partes)
    if reconocida:  # pregunta frecuente pero la ficha no tiene el dato: no se inventa
        return ia.NO_LO_SE
    return None


@router.post("/eventos/{event_id}/chat-ia")
def chat_ia(event_id: str, body: PreguntaChat, user=Depends(require_user)):
    ev = db_evento(event_id)
    if not ev:
        raise HTTPException(404, "Evento no encontrado")
    pregunta = (body.pregunta or "").strip()[:1000]
    if not pregunta:
        raise HTTPException(400, "Pregunta vacía")

    respuesta = faq_respuesta(ev, pregunta)  # reglas primero (0 tokens)
    if respuesta is None:
        hilo = (sb.table("mensajes_evento").select("tipo, contenido, created_at")
                .eq("event_id", event_id).order("created_at", desc=True).limit(10)
                .execute().data)[::-1]
        respuesta = ia.responder_chat(ev, hilo, pregunta)

    fila = {"event_id": event_id, "user_id": None, "tipo": "asistente_ia",
            "contenido": respuesta[:2000]}
    if body.parent_id:
        fila["parent_id"] = body.parent_id
    try:
        sb.table("mensajes_evento").insert(fila).execute()
    except Exception:
        log.exception("No se pudo guardar la respuesta del asistente")
    return {"respuesta": respuesta}


# ══════════════════════════════════════════════════════════════
#  Cartel automático (sin IA)  ·  POST /ia/cartel 🔒
# ══════════════════════════════════════════════════════════════
def subir_cartel(evento: dict, foto: bytes | None = None) -> str | None:
    """Genera el cartel (con la foto dada, la del pueblo o de color), lo sube al bucket
    público 'posters' y devuelve su URL (o None)."""
    try:
        import uuid as _uuid
        from cartel import generar_cartel
        img = generar_cartel(evento, foto)
        es_jpg = img[:2] == b"\xff\xd8"
        ruta = f"generados/{_uuid.uuid4()}.{'jpg' if es_jpg else 'png'}"
        sb.storage.from_("posters").upload(
            ruta, img, {"content-type": "image/jpeg" if es_jpg else "image/png"})
        return sb.storage.from_("posters").get_public_url(ruta).rstrip("?")
    except Exception:
        log.exception("No se pudo generar/subir el cartel")
        return None


class PeticionCartel(BaseModel):
    event_id: str | None = None      # evento ya creado -> se le pone el cartel
    title: str | None = None         # o los datos sueltos (pantalla Proponer, antes de guardar)
    category: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    location_name: str | None = None
    municipio: str | None = None
    price: float | None = None
    max_capacity: int | None = None
    is_accessible: bool | None = None
    inscripcion_hasta: str | None = None
    foto_url: str | None = None      # foto propia del organizador (opcional)


@router.post("/ia/cartel")
def ia_cartel(body: PeticionCartel, user=Depends(require_user)):
    if body.event_id:
        ev = db_evento(body.event_id)
        if not ev:
            raise HTTPException(404, "Evento no encontrado")
        es_moderador = (perfil(user.id) or {}).get("rol") == "moderador"
        if ev.get("created_by") != user.id and not es_moderador:
            raise HTTPException(403, "Solo el autor o el moderador pueden cambiar el cartel")
        url = subir_cartel(ev, descargar_imagen(body.foto_url))
        if not url:
            raise HTTPException(503, "No se pudo generar el cartel")
        sb.table("eventos").update({"poster_url": url}).eq("id", body.event_id).execute()
        return {"poster_url": url}
    if not body.title:
        raise HTTPException(400, "Manda event_id o al menos title")
    url = subir_cartel(body.model_dump(exclude_none=True, exclude={"foto_url"}),
                       descargar_imagen(body.foto_url))
    if not url:
        raise HTTPException(503, "No se pudo generar el cartel")
    return {"poster_url": url}


# ══════════════════════════════════════════════════════════════
#  GET /ia/consumo
# ══════════════════════════════════════════════════════════════
@router.get("/ia/consumo")
def ia_consumo():
    return dict(ia.CONSUMO)
