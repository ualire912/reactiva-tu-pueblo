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

import asyncio
import concurrent.futures
import inspect
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


def _copiar_a_storage(url: str) -> str | None:
    """Descarga una imagen y la guarda en el bucket 'posters'. Devuelve la URL pública o None."""
    img = descargar_imagen(url)
    if not img:
        return None
    try:
        import uuid as _uuid
        es_png = img[:8] == b"\x89PNG\r\n\x1a\n"
        ruta = f"facebook/{_uuid.uuid4()}.{'png' if es_png else 'jpg'}"
        sb.storage.from_("posters").upload(ruta, img, {"content-type": "image/png" if es_png else "image/jpeg"})
        return sb.storage.from_("posters").get_public_url(ruta).rstrip("?")
    except Exception:
        log.exception("No se pudo copiar la imagen a Storage")
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
    if nueva or fila.get("estado") == "error":  # nuevo, o reintento de uno que falló
        bg.add_task(procesar_ingesta, fila["id"])  # el trabajo pesado, en segundo plano
    return {"ingesta_id": fila["id"], "estado": fila["estado"]}


# ══════════════════════════════════════════════════════════════
#  procesar_ingesta  (se ejecuta en segundo plano)
# ══════════════════════════════════════════════════════════════
_OTROS_SITIOS = re.compile(
    r"\b(almeria capital|en almeria\b|nijar|tabernas|sorbas|gergal|roquetas|el ejido|vicar|huercal|"
    r"carboneras|mojacar|garrucha|macael|olula del rio|purchena|tijola|seron|baza|guadix|"
    r"granada|madrid|sevilla|malaga|murcia|valencia|barcelona|cordoba|jaen|cadiz|huelva|"
    r"aguadulce|adra|berja|cuevas del almanzora|huercal-overa|pulpi|benahadux|rioja|gador|"
    r"almeria|universidad|ual)\b")


def fuera_de_la_mancomunidad(texto: str, extraido: dict) -> bool:
    """¿Propone un plan de otro sitio? Solo si nombra otro pueblo/ciudad y NINGUNO de los
    nuestros (si no dice el pueblo, se le pregunta; no se descarta)."""
    if extraido.get("municipio") or ia._pueblo_en_texto(texto):
        return False
    return bool(_OTROS_SITIOS.search(ia._norm(texto or "")))


def _extraer_con_cartel(texto: str, imagen: bytes, entidad: dict | None) -> dict | None:
    """Cartel -> extractor_vlm (Gemini, NVIDIA, Mistral, Groq, OpenRouter en cascada).
    None si no hay proveedores o todos fallan (entonces se usa ia.extraer_evento)."""
    try:
        import extractor_vlm
    except Exception:
        return None
    m = re.search(r"\[Publicado el (\d{2})/(\d{2})/(\d{4})\]", texto or "")
    fecha_pub = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
    muni = None
    if entidad and entidad.get("municipio_id"):
        fila = _una("municipios", "id", entidad["municipio_id"])
        muni = (fila or {}).get("nombre")
    try:
        d = extractor_vlm.extraer_cartel([imagen], texto, muni, fecha_pub)
    except Exception as e:
        log.info("extractor_vlm sin respuesta (%s) -> extractor de siempre", str(e)[:120])
        return None
    if not d.get("municipio") and muni:
        d["municipio"] = muni
    return d


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

        # 1b. ¿Claramente no es un evento? (esquela, corte de agua, se vende...) -> fuera, gratis
        no_evento = ia.reglas_no_evento(texto) if not ing.get("image_url") else None
        if no_evento:
            _actualizar_ingesta(ingesta_id, estado="descartada",
                                spam_score=0, spam_reason=f"no es un evento: {no_evento}")
            log.info("Ingesta %s -> descartada por reglas (%s)", ingesta_id, no_evento)
            return

        # 2. Extraer con IA (texto + cartel). Con cartel: cascada de modelos de visión
        #    gratuitos (extractor_vlm); si ninguno responde, el extractor de siempre.
        imagen = descargar_imagen(ing.get("image_url"))
        texto_ia = texto
        if ing.get("origen") == "telegram":
            # Un vecino PROPONE un plan: aunque le falten datos (el día, el precio...) es un
            # evento; lo que falte se le pregunta. Solo no lo es una venta, un aviso, etc.
            texto_ia = ("(PROPUESTA DE UN VECINO que quiere organizar un plan. Si es un plan o "
                        "actividad, es_evento=true aunque falten datos: pon null y añádelos a "
                        "missing. es_evento=false solo si es una venta, un aviso, publicidad o "
                        "no propone ningún plan.)\n" + texto)
        extraido = _extraer_con_cartel(texto_ia, imagen, entidad) if imagen else None
        if extraido is None:
            extraido = ia.extraer_evento(texto_ia, imagen)

        # 2a. Había cartel pero la IA no pudo leerlo (Gemini caído/saturado) -> se reintenta luego
        if imagen and extraido.get("confidence", 0) == 0 and extraido.get("es_evento") is None:
            _actualizar_ingesta(ingesta_id, estado="error",
                                spam_reason="no se pudo leer el cartel (IA no disponible); se reintentará")
            log.info("Ingesta %s -> error temporal: cartel sin leer, se reintentará", ingesta_id)
            return

        # 2a-bis. Propuesta por Telegram de un plan de OTRO sitio (no de la Mancomunidad)
        if ing.get("origen") == "telegram" and fuera_de_la_mancomunidad(texto, extraido):
            _actualizar_ingesta(ingesta_id, estado="descartada", extracted=extraido, spam_score=0,
                                spam_reason="fuera de la Mancomunidad: no es de estos pueblos")
            log.info("Ingesta %s -> descartada (fuera de la Mancomunidad)", ingesta_id)
            return

        # 2b. La IA dice que no es un evento -> descartada (visible en el panel, sin molestar a nadie)
        if extraido.get("es_evento") is False:
            _actualizar_ingesta(ingesta_id, estado="descartada", extracted=extraido, spam_score=0,
                                spam_reason=f"no es un evento: {extraido.get('motivo_no_evento')}")
            log.info("Ingesta %s -> descartada por IA (%s)", ingesta_id, extraido.get("motivo_no_evento"))
            return

        # 3. Spam con IA solo si las reglas dudaron (y si hay texto: un cartel solo ya lo ha leído la IA)
        if regla is None and len(texto.strip()) < 30 and imagen:
            regla = {"spam_score": 0.05, "motivo": "solo cartel (revisado al extraer)"}
        if ing.get("origen") == "telegram" and texto.strip():
            # Propuesta de un vecino: la IA la revisa SIEMPRE (ofensivo, odio, violencia...),
            # aunque las reglas la den por buena por tener fecha y lugar
            spam = ia.clasificar_spam(texto, entidad, solo_ia=True)
        else:
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
        r = pedir_confirmacion_ingesta(ingesta_id)
        if inspect.isawaitable(r):
            # Es async (manda mensajes por Telegram). Antes se llamaba sin await: la ingesta
            # se marcaba "esperando_confirmacion" pero el mensaje NUNCA se enviaba.
            try:
                asyncio.get_running_loop()
                hay_bucle = True
            except RuntimeError:
                hay_bucle = False
            if hay_bucle:  # dentro del servidor: se ejecuta en un hilo con su propio bucle
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    r = ex.submit(asyncio.run, r).result(timeout=60)
            else:
                r = asyncio.run(r)
        return bool(r)
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
    if re.search(r"\ba la?s?\b|\bhoras?\b|\d{1,2}:\d{2}|\bmanana\b|\btarde\b|\bnoche\b|\bplazas?\b|"
                 r"\bpersonas\b|\bdia\b", n) and not re.search(r"€|euro", n):
        return None  # "a las 10 de la mañana" o "20 plazas" no son un precio
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
            ex["_sin_limite"] = True  # ya contestado: no se vuelve a preguntar
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
        conocido = ""
        if campo == "starts_at" and ex.get("starts_at"):
            # Ya se sabía el día (p. ej. del cartel) y solo falta la hora: se le recuerda a la IA
            try:
                d = datetime.fromisoformat(str(ex["starts_at"])).astimezone(ia.TZ)
                conocido = (f"\nEL DÍA YA SE SABE: {ia._DIAS_ES[d.weekday()]} {d.day} de "
                            f"{ia._MESES_ES[d.month - 1]} de {d.year}. Si el organizador solo "
                            "dice la hora, es ese día.")
            except ValueError:
                pass
        texto = (f"{ing.get('raw_text') or ''}{conocido}\n\n"
                 f"DATO AÑADIDO POR EL ORGANIZADOR sobre {campo}: {respuesta}")
        nuevo = ia.extraer_evento(texto)
        if nuevo.get(campo):
            ex[campo], entendido = nuevo[campo], True
            if campo == "starts_at" and nuevo.get("ends_at") and not ex.get("ends_at"):
                ex["ends_at"] = nuevo["ends_at"]
            if campo == "starts_at":
                missing = [m for m in missing if m != "hora"]

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


def crear_evento_desde_ingesta(ingesta_id: str, publicar: bool | None = None,
                               motivo: str | None = None) -> dict:
    """Crea el evento. Devuelve la fila creada, o {"error", "missing"} si no se puede.
    publicar=None: decide la entidad/rol (auto_approve, publicador, moderador).
    publicar=False: siempre a revisión del moderador (p. ej. la IA no está segura).
    publicar=True: se publica (fuente verificada o publicador con buena reputación)."""
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

    # ¿Lo dudoso? (incompleto, con dudas o la IA poco segura)
    faltan_datos = [c for c in (ing.get("missing") or ex.get("missing") or [])
                    if c not in ("max_capacity", "inscripcion_hasta", "min_age", "max_age")]
    dudoso = bool(faltan_datos or ex.get("dudas") or float(ex.get("confidence") or 0) < 0.7)
    if ing.get("origen") == "telegram":
        # El propio organizador ha completado y confirmado los datos en el cuestionario:
        # la poca seguridad de la IA con su primer mensaje ya no cuenta, ni lo que el
        # extractor de carteles marca y no es importante ("organizador", "inscripcion_hasta"...)
        dudoso = any(c in ("title", "starts_at", "hora", "municipio", "location_name", "price")
                     for c in (ing.get("missing") or []))

    # Quién lo publica y con qué estado:
    #   entidad con auto_approve, moderador o PUBLICADOR (4 puntos de reputación) -> se publica
    #   usuario normal -> siempre pasa por el moderador
    #   (en los dos casos la IA ya ha revisado el mensaje: spam, ofensivo, no es un evento...)
    autor_id = ing.get("user_id") or (entidad or {}).get("owner_user_id")
    rol = (perfil(autor_id) or {}).get("rol") if autor_id else None
    motivo_revision = motivo  # por qué lo revisa un moderador (si no se publica)
    if publicar is not None:  # quien llama ya ha decidido (agente, fuente verificada...)
        publica_directo = publicar
    elif (entidad or {}).get("auto_approve") or rol in ("moderador", "publicador"):
        publica_directo = True
    else:
        publica_directo, motivo_revision = False, "lo propone un usuario: lo revisa un moderador"

    fila = {c: ex.get(c) for c in _CAMPOS_EVENTO if ex.get(c) is not None}
    if fila.get("category") not in ia.CATEGORIAS:
        fila["category"] = "otro"
    if fila.get("ends_at"):
        fila.pop("duration_minutes", None)  # la BD calcula uno a partir del otro
    # Sin publicar: incompleto o dudoso -> borrador_ia; completo -> pendiente_moderacion
    estado = "abierto" if publica_directo else ("borrador_ia" if dudoso else "pendiente_moderacion")
    fila.update({
        "municipio_id": municipio_id,
        "entidad_organizadora_id": ing.get("entidad_id"),
        "created_by": autor_id,
        "status": estado,
        "origen": ing.get("origen") or "facebook",
        "source_url": ing.get("source_url"),
        "poster_url": ing.get("image_url"),
        "ai_confidence": ex.get("confidence"),
        "ai_flags": {"missing": ing.get("missing") or ex.get("missing") or [],
                     "dudas": ex.get("dudas") or [],
                     "proveedor": ex.get("_proveedor"),
                     "revision": None if publica_directo else (motivo_revision or "la IA tiene dudas"),
                     "spam_score": ing.get("spam_score")},
    })
    if fila.get("poster_url") and re.search(r"fbcdn|facebook\.com|fbsbx", fila["poster_url"]):
        # Las URLs de imágenes de Facebook caducan: se copia el cartel a nuestro Storage
        fila["poster_url"] = _copiar_a_storage(fila["poster_url"]) or fila["poster_url"]
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
    if ev.get("status") in ("pendiente_moderacion", "borrador_ia") and ing.get("origen") == "telegram":
        try:  # los moderadores lo reciben por Telegram con botones Aprobar / Rechazar
            from puentes import avisar_moderadores
            avisar_moderadores(ev["id"])
        except Exception as e:
            log.warning("No se pudo avisar a los moderadores: %r", e)
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
        if not evento.get("municipio") and evento.get("municipio_id"):
            # evento ya guardado: trae el id del pueblo, el cartel necesita el nombre (y su foto)
            m = _una("municipios", "id", evento["municipio_id"])
            evento = {**evento, "municipio": (m or {}).get("nombre")}
        img = generar_cartel(evento, foto)
        es_jpg = img[:2] == b"\xff\xd8"
        ruta = f"generados/{_uuid.uuid4()}.{'jpg' if es_jpg else 'png'}"
        sb.storage.from_("posters").upload(
            ruta, img, {"content-type": "image/jpeg" if es_jpg else "image/png"})
        return sb.storage.from_("posters").get_public_url(ruta).rstrip("?")
    except Exception:
        log.exception("No se pudo generar/subir el cartel")
        return None


def regenerar_cartel(event_id: str) -> str | None:
    """Cartel nuevo (diseño actual, con la foto del pueblo) para un evento ya creado:
    lo sube, lo guarda en poster_url y devuelve la URL. Lo usa el moderador desde Telegram."""
    ev = db_evento(event_id)
    if not ev:
        return None
    url = subir_cartel(ev)
    if url:
        sb.table("eventos").update({"poster_url": url}).eq("id", event_id).execute()
    return url


def cartel_de(event_id: str) -> str | None:
    """El cartel del evento; si no tiene, se genera uno (y se guarda)."""
    ev = db_evento(event_id)
    if not ev:
        return None
    return ev.get("poster_url") or regenerar_cartel(event_id)


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


# El asistente de la agenda (rag.py) cuelga de este mismo router: así main.py no cambia
try:
    from rag import router as _rag_router
    router.include_router(_rag_router)
except Exception as _e:  # si falta algo (p. ej. aún no se ejecutó rag.sql), el resto sigue
    log.warning("Asistente de la agenda (rag) no disponible: %s", _e)
