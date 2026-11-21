"""
api/ia.py  ·  C1  ·  Las 6 funciones de IA

Orden de proveedores en cada llamada (si uno falla o tarda, pasa al siguiente):
    1. freellmpool (principal; 15+ proveedores gratis, solo texto; FREELLMPOOL=0 lo apaga)
    2. Gemini con GEMINI_API_KEY
    3. Gemini con GEMINI_API_KEY_2   (clave de reserva, otra cuenta)
    4. NVIDIA NIM con NVIDIA_API_KEY (opcional, solo texto)
    5. Plan B sin IA (cada función devuelve algo útil)
Carteles (imágenes): solo Gemini, porque freellmpool y NVIDIA no ven imágenes.
Notas de voz: freellmpool las transcribe (Whisper) y se tratan como texto; si no
puede, Gemini escucha el audio directamente.

REAL ya:   extraer_evento, clasificar_spam
STUB aún:  entender_mensaje, redactar_recomendacion, responder_chat, revisar_confianza
           (devuelven el Plan B sin IA; se completan por la tarde)

Nunca lanza excepciones hacia fuera: si algo falla, Plan B.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:  # carga api/.env en local (en Render las variables vienen del panel)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

# ── Constantes del contrato ───────────────────────────────────
CATEGORIAS = ["senderismo", "deporte", "cultura", "gastronomia",
              "fiestas", "taller", "voluntariado", "otro"]
PUEBLOS = ["Lucainena de las Torres", "Velefique", "Senés", "Uleila del Campo", "Tahal"]
TZ = ZoneInfo("Europe/Madrid")
NO_LO_SE = "No lo sé, se lo pregunto al organizador"

# Campos que, si faltan, hay que preguntar al publicador
CAMPOS_CLAVE = ["title", "starts_at", "municipio", "location_name", "price", "max_capacity"]

CONSUMO = {"llamadas": 0, "tokens": 0}   # lo expone GET /ia/consumo
_CACHE: dict[str, str] = {}              # mismo prompt -> misma respuesta, sin pagar dos veces

TIMEOUT_S = 15
# GEMINI_MODEL admite varios separados por coma: se prueba el 1.º y, si falla, el siguiente
# (en el plan gratuito cada modelo tiene su propia cuota -> más margen)
GEMINI_MODELOS = [m.strip() for m in os.getenv("GEMINI_MODEL", "gemini-3.5-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
GEMINI_MODEL = GEMINI_MODELOS[0]
# NVIDIA_MODEL también admite varios separados por coma (si uno está retirado, prueba el siguiente)
NVIDIA_MODELOS = [m.strip() for m in os.getenv("NVIDIA_MODEL", "").split(",") if m.strip()]
DEBUG = os.getenv("IA_DEBUG") == "1"  # IA_DEBUG=1 en el .env -> imprime la respuesta cruda del modelo


# ══════════════════════════════════════════════════════════════
#  Motor común: _llamar()
# ══════════════════════════════════════════════════════════════
_clientes: dict[str, object] = {}
# Qué ajuste de "pensar" acepta cada modelo (se descubre la 1.ª vez y se recuerda).
# Pensar menos = respuesta más rápida y menos tokens.
_AJUSTE_OK: dict[str, int] = {}


def _ajustes_thinking(modelo: str) -> list:
    from google.genai import types
    if modelo.startswith("gemini-2"):
        return [types.ThinkingConfig(thinking_budget=0), None]
    return [types.ThinkingConfig(thinking_level="MINIMAL"),
            types.ThinkingConfig(thinking_level="LOW"), None]


def _cliente_gemini(clave: str):
    if clave not in _clientes:
        from google import genai
        from google.genai import types
        _clientes[clave] = genai.Client(
            api_key=clave, http_options=types.HttpOptions(timeout=TIMEOUT_S * 1000))
    return _clientes[clave]


def _gemini(clave: str, modelo: str, prompt: str, image_bytes, audio_bytes, como_json: bool) -> str:
    from google.genai import types
    partes: list = [prompt]
    if image_bytes:
        partes.append(types.Part.from_bytes(data=image_bytes, mime_type=_mime_imagen(image_bytes)))
    if audio_bytes:
        partes.append(types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"))

    ajustes = _ajustes_thinking(modelo)
    inicio = _AJUSTE_OK.get(modelo, 0)
    cliente = _cliente_gemini(clave)
    for i in range(inicio, len(ajustes)):
        kw = dict(temperature=0.1,
                  response_mime_type="application/json" if como_json else "text/plain")
        if ajustes[i] is not None:
            kw["thinking_config"] = ajustes[i]
        try:
            r = cliente.models.generate_content(
                model=modelo, contents=partes, config=types.GenerateContentConfig(**kw))
        except Exception as e:
            # 400 = este modelo no acepta ese ajuste -> probar el siguiente (es rápido)
            if "400" in str(e) and i < len(ajustes) - 1:
                continue
            raise
        _AJUSTE_OK[modelo] = i
        CONSUMO["llamadas"] += 1
        if r.usage_metadata:
            CONSUMO["tokens"] += r.usage_metadata.total_token_count or 0
        if not r.text:
            raise RuntimeError("Gemini devolvió respuesta vacía")
        return r.text
    raise RuntimeError("sin ajustes válidos")


def _nvidia(clave: str, modelo: str, prompt: str, como_json: bool) -> str:
    import httpx
    sistema = "/no_think\nNo expliques nada ni razones en voz alta."
    if como_json:
        sistema += " Responde SOLO con un objeto JSON válido."
    cuerpo = {"model": modelo, "temperature": 0.1, "max_tokens": 2048,
              "messages": [{"role": "system", "content": sistema},
                           {"role": "user", "content": prompt}]}
    if como_json:
        cuerpo["response_format"] = {"type": "json_object"}
    # Apagar el "razonamiento" en los modelos que lo tienen (nemotron, qwen...)
    cuerpo["chat_template_kwargs"] = {"enable_thinking": False, "thinking": False}
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    cab = {"Authorization": f"Bearer {clave}"}
    r = httpx.post(url, headers=cab, json=cuerpo, timeout=TIMEOUT_S + 10)
    if r.status_code in (400, 422):
        # este modelo no admite alguno de los extras -> sin ellos
        cuerpo.pop("response_format", None)
        cuerpo.pop("chat_template_kwargs", None)
        r = httpx.post(url, headers=cab, json=cuerpo, timeout=TIMEOUT_S + 10)
    r.raise_for_status()
    datos = r.json()
    CONSUMO["llamadas"] += 1
    CONSUMO["tokens"] += (datos.get("usage") or {}).get("total_tokens", 0)
    msg = datos["choices"][0]["message"]
    texto = msg.get("content") or ""
    if not texto.strip():  # algunos modelos dejan la respuesta en el razonamiento
        texto = msg.get("reasoning_content") or msg.get("reasoning") or ""
    if DEBUG:
        print(f"[ia] NVIDIA fin={datos['choices'][0].get('finish_reason')} tokens={(datos.get('usage') or {}).get('total_tokens')}")
    if not texto.strip():
        raise RuntimeError("NVIDIA devolvió respuesta vacía")
    return texto


_POOL = {"pool": None, "ok": True}


def _pool():
    """Crea el pool de freellmpool la primera vez. None si no está instalado o desactivado."""
    if not _POOL["ok"] or os.getenv("FREELLMPOOL", "1") == "0":
        return None
    if _POOL["pool"] is None:
        try:
            from freellmpool import Pool
            _POOL["pool"] = Pool.from_default_config()
        except Exception as e:
            print(f"[ia] freellmpool no disponible: {type(e).__name__}: {e}")
            _POOL["ok"] = False
            return None
    return _POOL["pool"]


def _con_limite(funcion, *args, segundos: int = TIMEOUT_S + 10):
    """Ejecuta funcion(*args) con un tiempo máximo (freellmpool no tiene timeout propio)."""
    import concurrent.futures
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(funcion, *args).result(timeout=segundos)
    finally:
        ex.shutdown(wait=False)


def _freellmpool(prompt: str, como_json: bool = False) -> str | None:
    pool = _pool()
    if not pool:
        return None
    if como_json:  # freellmpool no tiene modo JSON: se pide en el propio prompt
        prompt += "\n\nResponde SOLO con un objeto JSON válido, sin texto antes ni después."
    try:
        t0 = time.time()
        r = _con_limite(pool.ask, prompt)
        texto = getattr(r, "text", None) or ""
        if not texto.strip():
            return None
        CONSUMO["llamadas"] += 1  # (freellmpool no informa de los tokens)
        print(f"[ia] OK: freellmpool ({getattr(r, 'provider_id', '?')}) en {time.time() - t0:.1f} s")
        if DEBUG:
            print("[ia] RESPUESTA CRUDA:", texto[:800])
        return texto
    except Exception as e:
        print(f"[ia] freellmpool falló: {type(e).__name__}: {str(e)[:120]}")
        return None


def transcribir_reserva(audio_bytes: bytes) -> str | None:
    """Transcribe una nota de voz con freellmpool (Whisper gratis). None si no puede."""
    pool = _pool()
    if not pool or not audio_bytes:
        return None
    try:
        r = _con_limite(pool.transcribe, audio_bytes, "nota.ogg")
        texto = (getattr(r, "text", None) or "").strip()
        if texto:
            CONSUMO["llamadas"] += 1
            print("[ia] OK: transcripción de reserva con freellmpool")
        return texto or None
    except Exception as e:
        print(f"[ia] transcripción de reserva falló: {type(e).__name__}: {str(e)[:120]}")
        return None


def _llamar(prompt: str, image_bytes: bytes | None = None,
            audio_bytes: bytes | None = None, como_json: bool = True) -> str | None:
    """Prueba los proveedores en orden. Devuelve el texto o None si todos fallan."""
    h = hashlib.sha256(prompt.encode() + (image_bytes or b"") + (audio_bytes or b"")).hexdigest()
    if h in _CACHE:
        return _CACHE[h]

    # 1.º freellmpool (principal). Solo texto: carteles y audio van directos a Gemini.
    if not image_bytes and not audio_bytes:
        texto = _freellmpool(prompt, como_json)
        if texto and (not como_json or _json(texto) is not None):
            _CACHE[h] = texto
            return texto
        if texto:
            print("[ia] freellmpool no devolvió JSON válido -> reserva")

    # Reserva: Gemini. Hasta 2 vueltas: si todos los modelos están saturados (503) es un pico pasajero,
    # se espera 2 s y se reintenta una vez antes de pasar a NVIDIA / Plan B.
    # (si freellmpool ya falló arriba, no se vuelve a probar)
    for vuelta in range(2):
        saturado = False
        for modelo in GEMINI_MODELOS:
            for nombre in ("GEMINI_API_KEY", "GEMINI_API_KEY_2"):
                clave = os.getenv(nombre)
                if not clave:
                    continue
                try:
                    t0 = time.time()
                    texto = _gemini(clave, modelo, prompt, image_bytes, audio_bytes, como_json)
                    print(f"[ia] OK: {modelo} con {nombre} en {time.time() - t0:.1f} s")
                    if DEBUG:
                        print("[ia] RESPUESTA CRUDA:", texto[:800])
                    _CACHE[h] = texto
                    return texto
                except Exception as e:
                    err = str(e)
                    print(f"[ia] {modelo} con {nombre} falló: {type(e).__name__}: {err[:90]}")
                    if "503" in err or "UNAVAILABLE" in err:
                        saturado = True
                    # 429 = sin cuota en ESTA clave -> probar la otra clave.
                    # Otro error (saturado, no existe...) es del modelo -> siguiente modelo.
                    if "429" not in err:
                        break
        if not saturado or vuelta == 1:
            break
        print("[ia] Modelos saturados, reintento en 2 s...")
        time.sleep(2)

    if image_bytes:
        # NVIDIA y freellmpool no ven imágenes: con un cartel responderían "vacío" y se
        # descartaría mal. Mejor devolver None (se reintenta más tarde).
        print("[ia] Gemini no disponible para leer la imagen -> se reintentará")
        return None

    clave = os.getenv("NVIDIA_API_KEY")
    if clave and not audio_bytes:  # NVIDIA no entiende audio
        for modelo in NVIDIA_MODELOS:
            try:
                t0 = time.time()
                texto = _nvidia(clave, modelo, prompt, como_json)
                print(f"[ia] OK: NVIDIA {modelo} en {time.time() - t0:.1f} s")
                if DEBUG:
                    print("[ia] RESPUESTA CRUDA:", texto[:800])
                _CACHE[h] = texto
                return texto
            except Exception as e:
                print(f"[ia] NVIDIA {modelo} falló: {type(e).__name__}: {str(e)[:90]}")

    print("[ia] Ningún proveedor disponible -> Plan B sin IA")
    return None


# ── Utilidades ────────────────────────────────────────────────
def _mime_imagen(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _json(texto: str | None) -> dict | None:
    """Saca el objeto JSON de la respuesta aunque venga con ```json```, texto alrededor
    o el razonamiento <think>...</think> de algunos modelos."""
    if not texto:
        return None
    t = re.sub(r"<think>.*?</think>", "", texto, flags=re.S)
    if "<think>" in t:  # razonamiento sin cerrar: quedarse con lo de después
        t = t.split("</think>")[-1]
    t = t.replace("```json", "").replace("```", "").strip()
    try:
        d = json.loads(t)
        return d if isinstance(d, dict) else None
    except Exception:
        pass
    # Buscar todos los {...} equilibrados y quedarse con el último que se pueda leer
    candidatos, nivel, ini = [], 0, None
    for k, c in enumerate(t):
        if c == "{":
            if nivel == 0:
                ini = k
            nivel += 1
        elif c == "}" and nivel:
            nivel -= 1
            if nivel == 0:
                candidatos.append(t[ini:k + 1])
    for cand in reversed(candidatos):
        try:
            d = json.loads(cand)
            if isinstance(d, dict):
                return d
        except Exception:
            continue
    return None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower().strip()


def _pueblo(nombre) -> str | None:
    if not nombre or not isinstance(nombre, str):
        return None
    n = _norm(nombre)
    for p in PUEBLOS:
        if _norm(p) in n or n in _norm(p):
            return p
    return None


def _fecha(valor) -> str | None:
    """ISO -> ISO con offset de Madrid. None si no se puede leer."""
    if not valor or not isinstance(valor, str):
        return None
    try:
        d = datetime.fromisoformat(valor.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ)
    return d.astimezone(TZ).isoformat()


def _num(valor, entero=False):
    if valor is None or isinstance(valor, bool):
        return None
    try:
        v = float(str(valor).replace(",", ".").replace("€", "").strip())
    except ValueError:
        return None
    if v < 0:
        return None
    return int(v) if entero else (int(v) if v.is_integer() else v)


def _hoy_texto() -> str:
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    ahora = datetime.now(TZ)
    return f"{dias[ahora.weekday()]} {ahora.strftime('%Y-%m-%d %H:%M')} (offset {ahora.strftime('%z')[:3]}:{ahora.strftime('%z')[3:]})"


# ══════════════════════════════════════════════════════════════
#  1 · extraer_evento
# ══════════════════════════════════════════════════════════════
_PROMPT_EXTRAER = """Eres un extractor de datos de eventos de pueblos de Almería (España).
Hoy es {hoy}, zona horaria Europe/Madrid. Úsalo para fechas relativas ("este sábado", "mañana").
Si el texto empieza por "[Publicado el DD/MM/AAAA]", las fechas relativas ("el próximo sábado",
"mañana") y el año se calculan desde ESA fecha de publicación, no desde hoy.
Si no dice el año ni la fecha de publicación, usa la PRÓXIMA vez que llegue esa fecha.

Lee el texto (y el cartel si se adjunta) y devuelve SOLO un JSON con exactamente estas claves:
title (string corto), category, starts_at, ends_at, duration_minutes (entero), municipio,
location_name, price (número en euros), max_capacity (entero), min_age, max_age,
inscripcion_hasta, description (máx. 300 caracteres, en español), is_accessible (boolean),
confidence (0..1), missing (lista), es_evento (boolean), motivo_no_evento (string o null).

Reglas:
- NUNCA inventes. Si un dato no aparece, pon null y añade el nombre de la clave a "missing".
- starts_at y ends_at en ISO 8601 con el offset de Madrid, p. ej. 2026-11-29T09:30:00+01:00.
- starts_at necesita día Y hora exacta. Si falta la hora exacta ("por la mañana", "por la tarde",
  sin hora), pon starts_at = null y añade "starts_at" a missing. No supongas horas.
- inscripcion_hasta: fecha límite para apuntarse (ISO con offset) SOLO si el texto dice
  "inscripciones hasta…" o similar. Si no lo dice, null (y NO lo pongas en missing).
- price: 0 SOLO si dice gratis, gratuito o entrada libre. Si no se menciona, null.
- category: una de {categorias}.
- municipio: uno de {pueblos}, o null si no se sabe.
- is_accessible: true solo si lo dice expresamente; si no, false.
- confidence: lo seguro que estás de que es un evento real con datos correctos.
- es_evento: true SOLO si anuncia una actividad a la que la gente puede ir o apuntarse
  (ruta, fiesta, taller, concierto, mercadillo, reunión abierta...). false si es una noticia,
  felicitación, esquela o pésame, aviso de servicio (corte de agua/luz, obras), compraventa,
  objeto perdido, oferta de empleo, publicidad o una foto/recuerdo de algo que ya pasó.
  Si es_evento es false, explica por qué en motivo_no_evento (frase corta).

TEXTO:
\"\"\"{texto}\"\"\"
"""


def _evento_vacio(text: str | None) -> dict:
    """Plan B: sin IA. El moderador o el bot completan el resto."""
    primera = next((l.strip() for l in (text or "").splitlines()
                    if l.strip() and not l.strip().startswith("[Publicado el")), None)
    return {
        "title": re.split(r"(?<=[.!?])\s", primera)[0][:80] if primera else None,
        "category": "otro",
        "starts_at": None, "ends_at": None, "duration_minutes": None,
        "municipio": _pueblo_en_texto(text), "location_name": None, "price": None,
        "max_capacity": None, "min_age": None, "max_age": None, "inscripcion_hasta": None,
        "description": (text or "")[:300] or None, "is_accessible": False,
        "confidence": 0.0, "missing": [],
        "es_evento": None,          # None = no se sabe (sin IA): no se descarta
    }


def _pueblo_en_texto(text: str | None) -> str | None:
    n = _norm(text or "")
    return next((p for p in PUEBLOS if _norm(p) in n), None)


_RE_GRATIS = re.compile(r"gratis|gratuit|entrada libre|acceso libre|sin coste|\b0 ?(€|euros)")


def _validar_evento(d: dict, text: str | None, hay_imagen: bool = False) -> dict:
    """Limpia lo que devuelve el modelo y garantiza la forma de EventoExtraido."""
    ev = _evento_vacio(text)
    ev["confidence"] = 0.0
    if d:
        ev["title"] = (str(d.get("title")).strip()[:120] if d.get("title") else ev["title"])
        cat = _norm(str(d.get("category") or ""))
        ev["category"] = cat if cat in CATEGORIAS else "otro"
        ev["starts_at"] = _fecha(d.get("starts_at"))
        ev["ends_at"] = _fecha(d.get("ends_at"))
        ev["inscripcion_hasta"] = _fecha(d.get("inscripcion_hasta"))
        ev["duration_minutes"] = _num(d.get("duration_minutes"), entero=True)
        ev["municipio"] = _pueblo(d.get("municipio")) or ev["municipio"]
        ev["location_name"] = (str(d.get("location_name")).strip()[:200]
                               if d.get("location_name") else None)
        ev["price"] = _num(d.get("price"))
        ev["max_capacity"] = _num(d.get("max_capacity"), entero=True) or None
        ev["min_age"] = _num(d.get("min_age"), entero=True)
        ev["max_age"] = _num(d.get("max_age"), entero=True)
        if d.get("description"):
            ev["description"] = str(d["description"]).strip()[:500]
        ev["is_accessible"] = d.get("is_accessible") is True
        if isinstance(d.get("es_evento"), bool):
            ev["es_evento"] = d["es_evento"]
            if not d["es_evento"]:
                ev["motivo_no_evento"] = str(d.get("motivo_no_evento") or "no anuncia una actividad")[:200]
        try:
            ev["confidence"] = max(0.0, min(1.0, float(d.get("confidence") or 0)))
        except (TypeError, ValueError):
            ev["confidence"] = 0.5

    # Regla anti-invento: precio 0 solo si el texto dice gratis (si hay cartel, se confía en el modelo)
    if ev["price"] == 0 and not hay_imagen and not _RE_GRATIS.search(_norm(text or "")):
        ev["price"] = None

    # Fechas en el pasado:
    #  - si al llevarla al año correcto (el más cercano) cae en los últimos 6 meses ->
    #    el evento YA HA PASADO (post antiguo): no se publica
    #  - si cae en el futuro (el modelo puso mal el año) -> se corrige el año
    ayer = datetime.now(TZ) - timedelta(days=1)
    hace_6m = ayer - timedelta(days=180)

    def _ajustar(iso):
        d_ = datetime.fromisoformat(iso)
        if d_ >= ayer:
            return d_, False
        try:
            while d_ < hace_6m:
                d_ = d_.replace(year=d_.year + 1)
        except ValueError:  # 29 de febrero
            return None, False
        if d_ < ayer:
            return d_, True  # pasado reciente
        return (d_, False) if d_ < ayer + timedelta(days=366) else (None, False)

    if ev["starts_at"]:
        d_, pasado = _ajustar(ev["starts_at"])
        if pasado:
            ev["es_evento"] = False
            ev["motivo_no_evento"] = "el evento ya ha pasado"
            ev["starts_at"] = d_.isoformat()
        else:
            delta = (d_ - datetime.fromisoformat(ev["starts_at"])) if d_ else None
            ev["starts_at"] = d_.isoformat() if d_ else None
            for campo in ("ends_at", "inscripcion_hasta"):  # mover igual que el inicio
                if ev[campo]:
                    ev[campo] = (datetime.fromisoformat(ev[campo]) + delta).isoformat() if delta else None
    elif ev["inscripcion_hasta"]:
        d_, pasado = _ajustar(ev["inscripcion_hasta"])
        ev["inscripcion_hasta"] = None if pasado or not d_ else d_.isoformat()

    if ev["ends_at"] and ev["starts_at"] and ev["ends_at"] <= ev["starts_at"]:
        ev["ends_at"] = None
    # La BD da error si inscripcion_hasta > starts_at -> mejor null
    if ev["inscripcion_hasta"] and (
            not ev["starts_at"] or
            datetime.fromisoformat(ev["inscripcion_hasta"]) > datetime.fromisoformat(ev["starts_at"])):
        ev["inscripcion_hasta"] = None

    # missing se recalcula siempre en Python (el modelo a veces se olvida)
    ev["missing"] = [c for c in CAMPOS_CLAVE if ev[c] is None]
    return ev


def extraer_evento(text: str | None, image_bytes: bytes | None = None) -> dict:
    """Texto (+ cartel) -> EventoExtraido. Nunca falla."""
    try:
        if not (text or "").strip() and not image_bytes:
            return _validar_evento({}, text)
        prompt = _PROMPT_EXTRAER.format(
            hoy=_hoy_texto(), categorias=", ".join(CATEGORIAS),
            pueblos=", ".join(PUEBLOS), texto=(text or "(sin texto, mira el cartel)")[:4000])
        if image_bytes:
            prompt += ("\nSE ADJUNTA LA IMAGEN DEL CARTEL/POST: los datos principales (título, fecha, "
                       "hora, lugar, precio) suelen estar EN LA IMAGEN. Léela con atención y "
                       "combínala con el texto.\n")
        datos = _json(_llamar(prompt, image_bytes=image_bytes))
        return _validar_evento(datos or {}, text, hay_imagen=bool(image_bytes))
    except Exception as e:
        print(f"[ia] extraer_evento error: {e}")
        return _validar_evento({}, text)


# ══════════════════════════════════════════════════════════════
#  2 · clasificar_spam  (reglas primero, IA solo si hay duda)
# ══════════════════════════════════════════════════════════════
PALABRAS_SPAM = ["casino", "préstamo", "prestamo", "cripto", "bitcoin", "gana dinero",
                 "haz clic", "haz click", "apuestas", "inversión garantizada",
                 "inversion garantizada", "trabaja desde casa", "sorteo iphone", "forex"]
_RE_FECHA = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}|\d{1,2} de [a-z]+|lunes|martes|miercoles|jueves|viernes|sabado|domingo"
    r"|manana|hoy|\d{1,2}[:h]\d{2}|\d{1,2} ?h\b)", re.I)
_RE_LUGAR = re.compile(r"\b(plaza|calle|c/|iglesia|ayuntamiento|salon|pabellon|ermita|parque|"
                       r"centro|polideportivo|bar|casa de la cultura|camino|mirador)\b", re.I)


def reglas_spam(text: str | None, entidad: dict | None = None) -> dict | None:
    """Reglas gratis. Devuelve {"spam_score","motivo"} si es claro; None si hay duda.
    La usa también ingesta.py antes de gastar IA."""
    t = text or ""
    n = _norm(t)
    if entidad and entidad.get("auto_approve"):
        return {"spam_score": 0.0, "motivo": "entidad con aprobación automática"}
    for p in PALABRAS_SPAM:
        if _norm(p) in n:
            return {"spam_score": 0.95, "motivo": f"palabra prohibida: «{p}»"}
    if len(re.findall(r"https?://|www\.", t)) > 2:
        return {"spam_score": 0.9, "motivo": "demasiados enlaces"}
    letras = [c for c in t if c.isalpha()]
    if len(letras) > 30 and sum(c.isupper() for c in letras) / len(letras) > 0.8:
        return {"spam_score": 0.85, "motivo": "todo en mayúsculas"}
    tiene_fecha = bool(_RE_FECHA.search(n))
    tiene_lugar = bool(_RE_LUGAR.search(n)) or _pueblo_en_texto(t) is not None
    if tiene_fecha and tiene_lugar:
        return {"spam_score": 0.05, "motivo": "parece un evento normal (fecha y lugar)"}
    return None  # dudoso -> IA


_NO_EVENTO = [
    (r"descanse en paz|\bdep\b|fallec|pesame|condolencia|funeral|esquela", "esquela o pésame"),
    (r"corte de (agua|luz|suministro)|sin agua|sin luz|averia|obras en la calle", "aviso de servicio"),
    (r"\bse vende|\bvendo\b|\bse alquila|\balquilo\b|\bse traspasa", "compraventa o alquiler"),
    (r"\bperdid[oa]\b.*(perro|gato|llaves|cartera|movil)|se ha perdido|se busca (perro|gato)", "objeto o animal perdido"),
    (r"oferta de empleo|se busca (camarer|trabajador|personal)|buscamos personal", "oferta de empleo"),
]


def reglas_no_evento(text: str | None) -> str | None:
    """Posts legítimos que claramente NO son eventos (0 tokens). Devuelve el motivo o None."""
    n = _norm(text or "")
    for patron, motivo in _NO_EVENTO:
        if re.search(patron, n):
            return motivo
    return None


_PROMPT_SPAM = """¿Este post de Facebook es spam/estafa/publicidad ajena, o un evento o anuncio legítimo
de un pueblo de Almería? Devuelve SOLO JSON: {{"spam_score": número 0..1, "motivo": "frase corta en español"}}.
POST:
\"\"\"{texto}\"\"\"
"""


def clasificar_spam(text: str | None, entidad: dict | None = None) -> dict:
    """-> {"spam_score": 0..1, "motivo": str}. Nunca falla."""
    try:
        r = reglas_spam(text, entidad)
        if r is not None:
            return r
        datos = _json(_llamar(_PROMPT_SPAM.format(texto=(text or "")[:2000])))
        if datos and "spam_score" in datos:
            score = max(0.0, min(1.0, float(datos["spam_score"])))
            return {"spam_score": score, "motivo": str(datos.get("motivo") or "IA")[:200]}
    except Exception as e:
        print(f"[ia] clasificar_spam error: {e}")
    return {"spam_score": 0.5, "motivo": "dudoso: sin fecha ni lugar claros, revisar a mano"}


# ══════════════════════════════════════════════════════════════
#  3-6 · Pendientes para la tarde (ahora: Plan B sin IA)
# ══════════════════════════════════════════════════════════════
INTENCIONES = ["buscar", "proponer", "apuntarse", "pregunta", "otro"]
_DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
# Palabras que delatan cada categoría (las usan las reglas y el RAG)
SINONIMOS_CATEGORIA = {"senderismo": ["senderismo", "ruta", "andar", "caminar", "excursion", "sendero"],
                       "deporte": ["deporte", "deportiv", "bici", "futbol", "baloncesto", "padel", "yoga", "gimnasia", "petanca", "correr", "carrera", "torneo"],
                       "cultura": ["cultura", "cultural", "cine", "teatro", "concierto", "museo", "charla", "exposicion"],
                       "gastronomia": ["gastronomia", "gastronomic", "comida", "comer", "migas", "degustacion", "mercadillo"],
                       "fiestas": ["fiesta", "verbena", "feria", "baile"],
                       "taller": ["taller", "curso", "manualidad", "esparto"],
                       "voluntariado": ["voluntari", "ayudar", "solidari"]}


def _fecha_de_texto(n: str) -> str | None:
    """'hoy', 'mañana', 'el sábado', 'este finde' -> 'YYYY-MM-DD' (texto ya normalizado)."""
    hoy = datetime.now(TZ).date()
    if re.search(r"\bpasado manana\b", n):
        return (hoy + timedelta(days=2)).isoformat()
    if re.search(r"\bmanana\b", n) and not re.search(r"por la manana", n):
        return (hoy + timedelta(days=1)).isoformat()
    if re.search(r"\bhoy\b|esta tarde|esta noche", n):
        return hoy.isoformat()
    if re.search(r"fin de semana|finde", n):
        return (hoy + timedelta(days=(5 - hoy.weekday()) % 7)).isoformat()  # sábado
    for k, d in enumerate(_DIAS_SEMANA):
        if re.search(rf"\b{d}\b", n):
            return (hoy + timedelta(days=(k - hoy.weekday()) % 7)).isoformat()
    return None


def _reglas_mensaje(text: str) -> dict:
    n = _norm(text)
    filtros = {}
    p = _pueblo_en_texto(text)
    if p:
        filtros["municipio"] = p
    sinonimos = SINONIMOS_CATEGORIA
    for cat, palabras in sinonimos.items():
        if any(w in n for w in palabras):
            filtros["categoria"] = cat
            break
    f = _fecha_de_texto(n)
    if f:
        filtros["fecha"] = f

    if re.search(r"apunta(me|rme)|me apunto|inscribe|quiero ir", n):
        intencion = "apuntarse"
    elif re.search(r"propongo|proponer|quien se viene|quien se apunta|organiza|organizo|organizar|quedamos", n):
        intencion = "proponer"
    elif re.search(r"que hay|que .{0,25}hay\b|hay algo|que hacer|que planes|planes|agenda|eventos|actividades|busca|algo para", n):
        intencion = "buscar"
    elif filtros and len(n.split()) <= 6:  # "senderismo en Tahal" -> buscar
        intencion = "buscar"
    elif "?" in (text or ""):
        intencion = "pregunta"
    else:
        intencion = "otro"
    return {"intencion": intencion, "filtros": filtros, "texto": text or ""}


_PROMPT_MENSAJE = """Eres el bot de agenda de unos pueblos de Almería. Hoy es {hoy}.
{entrada}
Devuelve SOLO JSON:
{{"intencion": una de {intenciones},
  "filtros": {{"municipio": uno de {pueblos} o null,
              "categoria": una de {categorias} o null,
              "fecha": "YYYY-MM-DD" o null}},
  "texto": "{texto_instr}"}}
Intenciones: buscar = quiere ver planes/eventos; proponer = quiere organizar o proponer un plan;
apuntarse = quiere inscribirse; pregunta = pregunta algo concreto; otro = saludo u otra cosa.
No inventes filtros que no se digan.
"""


def _validar_mensaje(d: dict | None, texto_original: str) -> dict:
    d = d or {}
    f = d.get("filtros") or {}
    filtros = {}
    p = _pueblo(f.get("municipio"))
    if p:
        filtros["municipio"] = p
    c = _norm(str(f.get("categoria") or ""))
    if c in CATEGORIAS:
        filtros["categoria"] = c
    try:
        if f.get("fecha"):
            filtros["fecha"] = datetime.fromisoformat(str(f["fecha"])[:10]).date().isoformat()
    except ValueError:
        pass
    intencion = d.get("intencion") if d.get("intencion") in INTENCIONES else "otro"
    texto = str(d.get("texto") or texto_original or "").strip()[:1000]
    return {"intencion": intencion, "filtros": filtros, "texto": texto}


def entender_mensaje(text: str | None = None, audio_bytes: bytes | None = None) -> dict:
    """Frase libre o nota de voz -> {"intencion", "filtros", "texto"}.
    texto = lo que dijo la persona (con audio, la transcripción). Nunca falla."""
    try:
        if text and not audio_bytes:
            r = _reglas_mensaje(text)
            if r["intencion"] != "otro":
                return r  # reglas: 0 tokens
            if len(text.split()) <= 2:  # "hola", "gracias"...
                return r
            entrada, texto_instr = f'MENSAJE: """{text[:1000]}"""', "el mensaje tal cual"
        elif audio_bytes:
            # 1.º freellmpool: transcribe (Whisper) y se entiende como texto.
            # Si no puede, Gemini escucha el audio directamente.
            transcrito = transcribir_reserva(audio_bytes)
            if transcrito:
                return entender_mensaje(text=transcrito)
            entrada = ("Te paso una NOTA DE VOZ en español (acento andaluz, puede haber ruido). "
                       "Transcríbela y entiéndela.")
            texto_instr = "la transcripción literal de lo que dice"
        else:
            return {"intencion": "otro", "filtros": {}, "texto": ""}

        prompt = _PROMPT_MENSAJE.format(hoy=_hoy_texto(), entrada=entrada,
                                        intenciones=INTENCIONES, pueblos=PUEBLOS,
                                        categorias=CATEGORIAS, texto_instr=texto_instr)
        datos = _json(_llamar(prompt, audio_bytes=audio_bytes))
        if datos:
            return _validar_mensaje(datos, text or "")
    except Exception as e:
        print(f"[ia] entender_mensaje error: {e}")
    if text:
        return _reglas_mensaje(text)
    return {"intencion": "otro", "filtros": {}, "texto": ""}


_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_humana(iso) -> str:
    """'2026-12-05T10:00:00+00:00' -> 'el sábado 5 de diciembre a las 11:00' (hora de España)."""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TZ)
        return f"el {_DIAS_ES[d.weekday()]} {d.day} de {_MESES_ES[d.month - 1]} a las {d:%H:%M}"
    except Exception:
        return ""


def _plantilla_recomendacion(nombre: str, eventos: list[dict]) -> str:
    """Plan B sin IA: plantilla con los motivos que da la BD."""
    if not eventos:
        return f"Hola {nombre}, esta semana no hemos encontrado planes para ti."
    lineas = [f"Hola {nombre}, te pueden gustar estos planes:"]
    for e in eventos[:3]:
        motivos = ", ".join(e.get("motivos") or [])
        lineas.append(f"• {e.get('title')} en {e.get('municipio')} {fecha_humana(e.get('starts_at'))}"
                      + (f" ({motivos})" if motivos else "") + ".")
    return "\n".join(lineas)


_PROMPT_RECOMENDACION = """Escribe un mensaje de Telegram para {nombre}, vecino/a de un pueblo de Almería,
recomendándole estos planes. Tono cercano y sencillo (puede ser una persona mayor), en español.
- Entre 2 y 4 frases en total. Sin emojis, sin listas, sin saludos largos.
- Di POR QUÉ se lo recomendamos usando SOLO los motivos indicados.
- Usa SOLO estos datos (título, pueblo, fecha). No inventes precios, horas ni detalles.
- Termina invitándole a apuntarse con los botones.

PLANES:
{planes}
"""


def redactar_recomendacion(nombre: str, eventos: list[dict]) -> str:
    """eventos = filas de rpc('recomendar_eventos'): event_id, title, starts_at, municipio,
    puntos, motivos. Devuelve un texto corto con el porqué. Nunca falla."""
    try:
        nombre = (nombre or "").split(" ")[0] or "vecino"
        if not eventos:
            return _plantilla_recomendacion(nombre, eventos)
        planes = "\n".join(
            f"- {e.get('title')} | {e.get('municipio')} | {fecha_humana(e.get('starts_at'))}"
            f" | motivos: {', '.join(e.get('motivos') or []) or 'ninguno'}"
            for e in eventos[:3])
        texto = _llamar(_PROMPT_RECOMENDACION.format(nombre=nombre, planes=planes), como_json=False)
        texto = (texto or "").strip().strip('"')
        # Comprobación anti-invento: debe mencionar al menos un título recomendado
        if texto and any(_norm(str(e.get("title")))[:15] in _norm(texto) for e in eventos[:3]):
            return texto[:800]
    except Exception as e:
        print(f"[ia] redactar_recomendacion error: {e}")
    return _plantilla_recomendacion(nombre, eventos)


_PROMPT_CHAT = """Eres el asistente del chat de un evento de un pueblo de Almería.
Responde en español, en 1 a 3 frases cortas y amables, usando SOLO estos datos.
Si la respuesta no está en los datos, responde exactamente: "{no_lo_se}"
No inventes horarios, precios, material ni normas.

FICHA DEL EVENTO:
{ficha}

ÚLTIMOS MENSAJES DEL CHAT:
{hilo}

PREGUNTA: {pregunta}
"""

_CAMPOS_FICHA = ["title", "category", "starts_at", "ends_at", "municipio", "location_name",
                 "price", "max_capacity", "confirmed_count", "min_age", "max_age",
                 "is_accessible", "inscripcion_hasta", "description", "status"]


def responder_chat(evento: dict, hilo: list[dict], pregunta: str) -> str:
    """Respuesta SOLO con la ficha + hilo. Si no lo sabe -> NO_LO_SE. Nunca falla."""
    try:
        ficha = "\n".join(f"- {c}: {evento.get(c)}" for c in _CAMPOS_FICHA
                          if evento.get(c) not in (None, ""))
        mensajes = "\n".join(f"- [{m.get('tipo')}] {str(m.get('contenido'))[:300]}"
                             for m in (hilo or [])[-10:]) or "(sin mensajes)"
        texto = _llamar(_PROMPT_CHAT.format(no_lo_se=NO_LO_SE, ficha=ficha, hilo=mensajes,
                                            pregunta=(pregunta or "")[:500]), como_json=False)
        texto = (texto or "").strip().strip('"')
        return texto[:600] if texto else NO_LO_SE
    except Exception as e:
        print(f"[ia] responder_chat error: {e}")
        return NO_LO_SE


PALABRAS_ALERTA = ["estafa", "timo", "fraude", "spam", "mentira", "falso", "engaño", "engano",
                   "no existe", "no aparecio", "no vino nadie", "nos dejo tirados", "peligro",
                   "inseguro", "acoso", "robo", "cobraron", "no devolvieron"]

_PROMPT_CONFIANZA = """Eres el moderador de una agenda de eventos de pueblos de Almería.
Decide si este usuario puede publicar eventos directamente (sin revisión) a partir de su historial.
Aprueba solo si sus eventos parecen reales y locales, y las reseñas no muestran quejas graves,
spam ni engaños. Si hay pocos datos o dudas, no apruebes.

PERFIL: {perfil}
EVENTOS QUE HA ORGANIZADO:
{eventos}
RESEÑAS DE SUS EVENTOS:
{resenas}

Devuelve SOLO JSON: {{"aprobar": true|false, "motivo": "frase corta en español", "confianza": 0..1}}
"""


def revisar_confianza(perfil: dict, eventos: list[dict], resenas: list[dict]) -> dict:
    """-> {"aprobar": bool, "motivo": str, "confianza": 0..1}.
    B1: si aprobar es true pero confianza < 0.7 -> decide el moderador. Nunca falla."""
    try:
        eventos, resenas = eventos or [], resenas or []
        # Reglas primero (gratis)
        for r in resenas:
            n = _norm(str(r.get("comentario") or ""))
            alerta = next((p for p in PALABRAS_ALERTA if _norm(p) in n), None)
            if alerta:
                return {"aprobar": False, "confianza": 0.9,
                        "motivo": f"Hay una reseña con una señal de alerta («{alerta}»)"}
        if not eventos:
            return {"aprobar": False, "confianza": 0.9, "motivo": "No ha organizado eventos todavía"}

        ev_txt = "\n".join(f"- {e.get('title')} | {e.get('municipio') or e.get('municipio_id')} | "
                           f"{e.get('status')} | {str(e.get('description') or '')[:120]}"
                           for e in eventos[:10])
        re_txt = "\n".join(f"- {r.get('estrellas')}★ {str(r.get('comentario') or '')[:150]}"
                           for r in resenas[:15]) or "(sin comentarios)"
        p_txt = {k: (perfil or {}).get(k) for k in ("full_name", "rol", "reputation_score", "created_at")}
        datos = _json(_llamar(_PROMPT_CONFIANZA.format(perfil=p_txt, eventos=ev_txt, resenas=re_txt)))
        if datos and isinstance(datos.get("aprobar"), bool):
            try:
                conf = max(0.0, min(1.0, float(datos.get("confianza", 0.5))))
            except (TypeError, ValueError):
                conf = 0.5
            return {"aprobar": datos["aprobar"], "confianza": conf,
                    "motivo": str(datos.get("motivo") or "Revisión de la IA")[:200]}
    except Exception as e:
        print(f"[ia] revisar_confianza error: {e}")
    return {"aprobar": False, "confianza": 0.0,
            "motivo": "La IA no está disponible: revisión manual"}
