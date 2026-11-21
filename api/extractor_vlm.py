"""
api/extractor_vlm.py · Extractor de eventos desde CARTELES con LLMs de visión GRATUITOS, en cascada.

Basado en el extractor de un compañero, adaptado a la app:
  - Sin dependencias nuevas: llama al protocolo OpenAI (chat.completions + image_url) con httpx.
  - Devuelve el MISMO formato que ia.extraer_evento (categorías, municipios y claves de la app),
    así ingesta.py lo usa sin cambiar nada más.
  - Si un proveedor falla (429, cuota, error, JSON roto) pasa al siguiente. Si el resultado sale
    con poca confianza, pide segunda opinión al siguiente y se queda con el mejor.
  - La IA propone; el sistema valida y marca (missing, dudas). Nunca decide publicar.
  - Cada llamada se apunta en api/pruebas/ia_llamadas.csv (proveedor, modelo, latencia, éxito...).

Claves (solo las que tengas; basta con una), en api/.env:
  GEMINI_API_KEY (aistudio.google.com) · NVIDIA_API_KEY (build.nvidia.com) ·
  MISTRAL_API_KEY (console.mistral.ai) · GROQ_API_KEY (console.groq.com) ·
  OPENROUTER_API_KEY (openrouter.ai)
Opcional: VLM_ORDEN="gemini,nvidia,mistral,groq,openrouter" y GEMINI_VLM_MODEL, NVIDIA_VLM_MODEL...

Los catálogos gratuitos cambian cada pocas semanas: compruébalos antes de fiarte de un modelo.
Uso (desde la carpeta api):
  python extractor_vlm.py cartel.jpg --municipio Senés [--fecha-pub 2026-11-20] [--texto "..."]
  python extractor_vlm.py --listar-modelos nvidia
  python extractor_vlm.py --healthcheck
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import httpx

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)
import ia  # noqa: E402  (categorías, pueblos, zona horaria; también carga api/.env)

UMBRAL_SEGUNDA_OPINION = 0.7
TIEMPO_MAX_S = 90           # por llamada
PAUSA_TRAS_429_S = 300      # proveedor sin cuota -> 5 min fuera
REGISTRO = os.path.join(AQUI, "pruebas", "ia_llamadas.csv")

# ── Proveedores (todos hablan el protocolo OpenAI) ────────────
PROVEEDORES: dict[str, dict] = {
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key": "GEMINI_API_KEY",
               "model": os.getenv("GEMINI_VLM_MODEL", ia.GEMINI_MODELOS[0]),
               "rpm": 10},
    "nvidia": {"base": "https://integrate.api.nvidia.com/v1", "key": "NVIDIA_API_KEY",
               # Comprobado con --listar-modelos/--healthcheck el 21/11/2026 (el llama-3.2-90b-vision
               # se retiró: 410 Gone; gemma-3-12b-it da 404)
               "model": os.getenv("NVIDIA_VLM_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b"),
               "rpm": 30},
    "mistral": {"base": "https://api.mistral.ai/v1", "key": "MISTRAL_API_KEY",
                "model": os.getenv("MISTRAL_VLM_MODEL", "mistral-small-latest"), "rpm": 20},
    "groq": {"base": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY",
             "model": os.getenv("GROQ_VLM_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
             "rpm": 20},
    "openrouter": {"base": "https://openrouter.ai/api/v1", "key": "OPENROUTER_API_KEY",
                   "model": os.getenv("OPENROUTER_VLM_MODEL", "openrouter/free"), "rpm": 10},
}
ORDEN = [p.strip() for p in os.getenv("VLM_ORDEN", "gemini,nvidia,mistral,groq,openrouter").split(",")
         if p.strip() in PROVEEDORES]
_bloqueado: dict[str, float] = {}      # proveedor -> hasta cuándo se salta
_ultimas: dict[str, list[float]] = {}  # limitador sencillo: llamadas del último minuto

PROMPT = """Eres un extractor de eventos para los pueblos de la Mancomunidad del interior de Almería
({pueblos}). Analiza el/los CARTEL(ES) adjuntos (y el texto del post si lo hay) y responde
SOLO con un objeto JSON válido (sin markdown, sin comentarios, sin texto fuera del JSON).

Primero transcribe TODO el texto legible del cartel en "texto_cartel". Después extrae:
{{
 "texto_cartel": str,
 "es_evento": bool,
 "motivo_no_evento": str|null,
 "title": str|null,
 "category": una de {categorias} | null,
 "starts_at": "YYYY-MM-DDTHH:MM:00"|null,
 "ends_at": str|null,
 "inscripcion_hasta": str|null,
 "duration_minutes": int|null,
 "location_name": str|null,
 "municipio": str|null,
 "min_age": int|null, "max_age": int|null,
 "price": number|null,
 "price_nota": str|null,
 "max_capacity": int|null,
 "is_accessible": bool|null,
 "organizador": str|null,
 "description": str|null,
 "anio_explicito": bool,
 "confianza": {{"title":0..1,"starts_at":0..1,"location_name":0..1,"price":0..1}},
 "evidencia": {{"starts_at": "frase literal", "location_name": "frase literal", "price": "frase literal"}},
 "missing": [str],
 "dudas": [str],
 "spam_score": 0..1, "spam_reason": str|null
}}
Reglas estrictas:
- NO inventes. Si algo no se ve, null y añádelo a "missing".
- Hora local Europe/Madrid. price 0 = gratis o entrada libre; donativo -> importe y price_nota.
- Si el cartel no muestra el año, deduce el próximo día coincidente a partir de la fecha de
  publicación y pon anio_explicito=false. Si falta el mes, añade una duda.
- description: máx. 400 caracteres, solo lo que consta. No copies teléfonos de particulares.
- Publicidad, apuestas, enlaces sospechosos o algo que no es de estos pueblos: sube spam_score.
- es_evento=false si no anuncia una actividad con fecha (esquela, aviso de servicio, venta...)."""


# ── Imágenes ──────────────────────────────────────────────────
def preparar_imagen(data: bytes, lado_max: int = 1600) -> tuple[bytes, str]:
    """Reduce y convierte a JPEG: menos tokens y evita límites de tamaño."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((lado_max, lado_max))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return buf.getvalue(), "image/jpeg"
    except Exception:  # imagen rara: se manda tal cual
        return data, ia._mime_imagen(data)


def a_data_url(data: bytes) -> str:
    b, mime = preparar_imagen(data)
    return f"data:{mime};base64," + base64.b64encode(b).decode()


def cargar_imagen(ruta: str) -> bytes:
    if ruta.startswith("http"):
        r = httpx.get(ruta, timeout=20, follow_redirects=True)
        r.raise_for_status()
        return r.content
    with open(ruta, "rb") as f:
        return f.read()


# ── Registro y limitador ──────────────────────────────────────
def registrar(prov: str, modelo: str, tarea: str, ok: bool, ms: int, tokens: int | None,
              error: str = "") -> None:
    """Una fila por llamada en ia_llamadas.csv. Nunca rompe el flujo."""
    try:
        os.makedirs(os.path.dirname(REGISTRO), exist_ok=True)
        nuevo = not os.path.exists(REGISTRO)
        with open(REGISTRO, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nuevo:
                w.writerow(["fecha", "proveedor", "modelo", "tarea", "ok", "latencia_ms", "tokens", "error"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), prov, modelo, tarea,
                        int(ok), ms, tokens or "", error[:160]])
    except OSError:
        pass
    ia.CONSUMO["llamadas"] += 1
    ia.CONSUMO["tokens"] += tokens or 0


def _esperar_turno(prov: str) -> None:
    """Respeta las llamadas por minuto del plan gratuito de cada proveedor."""
    ahora = time.time()
    ventana = [t for t in _ultimas.get(prov, []) if ahora - t < 60]
    limite = PROVEEDORES[prov]["rpm"]
    if len(ventana) >= limite:
        time.sleep(max(0.0, 60 - (ahora - ventana[0])) + 0.5)
    _ultimas[prov] = ventana + [time.time()]


# ── Llamada a un proveedor ────────────────────────────────────
def parsear_json(txt: str) -> dict:
    txt = re.sub(r"<think>.*?</think>", "", txt or "", flags=re.S).strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("sin JSON en la respuesta")
    return json.loads(txt[i:j + 1])


def llamar(prov: str, imagenes: list[bytes], texto: str, municipio: str | None,
           fecha_pub: str | None, tarea: str = "cartel") -> dict:
    cfg = PROVEEDORES[prov]
    _esperar_turno(prov)
    contenido: list[dict] = [{"type": "text", "text":
                              f"Municipio de la página: {municipio or 'desconocido'}\n"
                              f"Fecha de publicación: {fecha_pub or date.today().isoformat()}\n"
                              f"Texto del post:\n{(texto or '(sin texto)')[:3000]}"}]
    for data in imagenes:
        contenido.append({"type": "image_url", "image_url": {"url": a_data_url(data)}})
    prompt = PROMPT.format(pueblos=", ".join(ia.PUEBLOS), categorias=ia.CATEGORIAS)
    cuerpo = {"model": cfg["model"], "temperature": 0, "max_tokens": 3000,
              "messages": [{"role": "system", "content": prompt},
                           {"role": "user", "content": contenido}]}
    t0 = time.time()
    try:
        r = httpx.post(f"{cfg['base']}/chat/completions", json=cuerpo, timeout=TIEMPO_MAX_S,
                       headers={"Authorization": f"Bearer {os.environ[cfg['key']]}"})
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
        datos = r.json()
        salida = parsear_json(datos["choices"][0]["message"]["content"])
        registrar(prov, cfg["model"], tarea, True, int((time.time() - t0) * 1000),
                  (datos.get("usage") or {}).get("total_tokens"))
        return salida
    except Exception as e:
        registrar(prov, cfg["model"], tarea, False, int((time.time() - t0) * 1000), None, str(e))
        raise


def proveedores_disponibles(forzado: str | None = None) -> list[str]:
    ahora = time.time()
    lista = [forzado] if forzado else ORDEN
    return [p for p in lista if p in PROVEEDORES and os.getenv(PROVEEDORES[p]["key"])
            and _bloqueado.get(p, 0) < ahora]


# ── Validación: la IA propone, el sistema valida y marca ──────
def _iso_madrid(v) -> str | None:
    d = datetime.fromisoformat(str(v))
    if d.tzinfo is None:
        d = d.replace(tzinfo=ia.TZ)
    return d.isoformat()


def validar(d: dict, fecha_pub: str | None) -> dict:
    """-> mismo formato que ia.extraer_evento + 'dudas', 'texto_cartel', 'evidencia'."""
    if not isinstance(d, dict):
        raise ValueError("el JSON no es un objeto")
    dudas = [str(x) for x in (d.get("dudas") or [])]
    # El modelo a veces escribe "starts_at (hora exacta)" en missing pero pone una hora
    # inventada: se normaliza a "hora" y luego se borra esa hora (nunca inventar)
    missing = ["hora" if "hora" in ia._norm(str(x)) else str(x) for x in (d.get("missing") or [])]
    sin_hora = "hora" in missing
    try:
        ref = datetime.fromisoformat(str(fecha_pub)) if fecha_pub else datetime.now()
    except ValueError:
        ref = datetime.now()
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=ia.TZ)

    for campo in ("starts_at", "ends_at", "inscripcion_hasta"):
        if d.get(campo):
            try:
                d[campo] = _iso_madrid(d[campo])
            except ValueError:
                dudas.append(f"{campo} con formato inválido: {d[campo]!r}")
                d[campo] = None
    if d.get("starts_at") and sin_hora:  # se queda el día; la hora inventada fuera
        d["starts_at"] = datetime.fromisoformat(d["starts_at"]).replace(hour=0, minute=0).isoformat()
    if d.get("starts_at"):
        ini = datetime.fromisoformat(d["starts_at"])
        if ini < ref - timedelta(days=1):
            dudas.append("la fecha es anterior a la publicación: ¿falta el año?")
        if ini > ref + timedelta(days=366):
            dudas.append("la fecha está a más de un año vista")
        if d.get("ends_at") and datetime.fromisoformat(d["ends_at"]) < ini:
            dudas.append("ends_at anterior a starts_at")
            d["ends_at"] = None
        if ini.hour == 0 and ini.minute == 0 and "hora" not in missing:
            missing.append("hora")  # 00:00 casi siempre es "no pone la hora"
    elif "starts_at" not in missing:
        missing.append("starts_at")

    cat = ia._norm(str(d.get("category") or ""))
    equivalencias = {"deportiva": "deporte", "cultural": "cultura", "ludica": "otro"}
    cat = equivalencias.get(cat, cat)
    if cat and cat not in ia.CATEGORIAS:
        dudas.append(f"categoría fuera de catálogo: {cat}")
    d["category"] = cat if cat in ia.CATEGORIAS else "otro"
    if not isinstance(d.get("price"), (int, float)):
        d["price"] = None
        if "price" not in missing:
            missing.append("price")
    d["municipio"] = ia._pueblo(d.get("municipio"))
    if d.get("description"):  # sin teléfonos de particulares
        d["description"] = re.sub(r"\b(?:\+?34\s?)?[6-9]\d{2}[\s.]?\d{3}[\s.]?\d{3}\b", "[teléfono]",
                                  str(d["description"]))[:400]

    conf = d.get("confianza") or {}
    vals = [float(v) for v in conf.values() if isinstance(v, (int, float))]
    d["confidence"] = round(min(vals), 2) if vals else 0.0
    d["missing"], d["dudas"] = sorted(set(missing)), dudas
    if not isinstance(d.get("es_evento"), bool):
        d["es_evento"] = bool(d.get("title") and d.get("starts_at"))
    return d


def puntuacion(d: dict) -> float:
    return d["confidence"] - 0.1 * len(d["missing"]) - 0.1 * len(d["dudas"])


def extraer_cartel(imagenes: list[bytes], texto: str = "", municipio: str | None = None,
                   fecha_pub: str | None = None, proveedor: str | None = None) -> dict:
    """imagenes: lista de bytes (un carrusel = una llamada con todas).
    Cascada con segunda opinión si la confianza es baja. Lanza RuntimeError si nadie responde."""
    mejor, errores = None, []
    for prov in proveedores_disponibles(proveedor):
        try:
            d = validar(llamar(prov, imagenes, texto, municipio, fecha_pub), fecha_pub)
            d["_proveedor"], d["_modelo"] = prov, PROVEEDORES[prov]["model"]
        except Exception as e:
            msg = str(e)[:160]
            errores.append(f"{prov}: {msg}")
            if any(x in msg.lower() for x in ("429", "quota", "rate", "limit", "resource_exhausted")):
                _bloqueado[prov] = time.time() + PAUSA_TRAS_429_S
            continue
        if mejor is None or puntuacion(d) > puntuacion(mejor):
            mejor = d
        if mejor["confidence"] >= UMBRAL_SEGUNDA_OPINION and "starts_at" not in mejor["missing"]:
            break  # suficiente
    if mejor is None:
        raise RuntimeError("Ningún proveedor respondió: " + " | ".join(errores or ["sin claves configuradas"]))
    mejor["_errores_previos"] = errores
    return mejor


# ── Herramientas ──────────────────────────────────────────────
def listar_modelos(prov: str) -> None:
    cfg = PROVEEDORES[prov]
    r = httpx.get(f"{cfg['base']}/models", timeout=30,
                  headers={"Authorization": f"Bearer {os.environ[cfg['key']]}"})
    r.raise_for_status()
    ids = sorted(m.get("id", "") for m in r.json().get("data", []))
    pat = re.compile(r"vl|vision|visual|qwen|gemma|llama-3\.2|llama-4|pixtral|mistral-small|gemini|"
                     r"nemotron|paligemma|:free", re.I)
    for i in ids:
        print(("* " if pat.search(i) else "  ") + i)
    print(f"\n{len(ids)} modelos (* = posible modelo con visión; confírmalo en la documentación)")


def _imagen_de_prueba() -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (800, 500), "white")
    dib = ImageDraw.Draw(img)
    for k, linea in enumerate(["TALLER DE ESPARTO", "Sabado 12 de diciembre de 2026",
                               "17:00 h - Centro social de Tahal", "Gratis - 15 plazas"]):
        dib.text((60, 80 + k * 80), linea, fill="black", font_size=40)
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def healthcheck() -> None:
    """Prueba cada proveedor con una imagen de prueba: éxito, latencia y lo que entendió."""
    img = _imagen_de_prueba()
    for prov in ORDEN:
        cfg = PROVEEDORES[prov]
        if not os.getenv(cfg["key"]):
            print(f"  {prov:<11} SIN CLAVE ({cfg['key']})")
            continue
        t0 = time.time()
        try:
            d = validar(llamar(prov, [img], "", "Tahal", "2026-11-20", tarea="healthcheck"), "2026-11-20")
            print(f"  {prov:<11} OK   {time.time() - t0:5.1f} s · {cfg['model']} · «{d.get('title')}» "
                  f"{d.get('starts_at')} · conf={d['confidence']}")
        except Exception as e:
            print(f"  {prov:<11} FALLO {time.time() - t0:5.1f} s · {cfg['model']} · {str(e)[:110]}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Extractor de eventos desde carteles")
    ap.add_argument("imagenes", nargs="*", help="rutas o URLs de carteles (un evento por imagen)")
    ap.add_argument("--municipio")
    ap.add_argument("--fecha-pub", default=date.today().isoformat())
    ap.add_argument("--texto", default="")
    ap.add_argument("--proveedor", choices=list(PROVEEDORES))
    ap.add_argument("--listar-modelos", choices=list(PROVEEDORES))
    ap.add_argument("--healthcheck", action="store_true")
    a = ap.parse_args()
    if a.listar_modelos:
        return listar_modelos(a.listar_modelos)
    if a.healthcheck:
        return healthcheck()
    if not a.imagenes:
        ap.error("indica al menos una imagen (o --healthcheck / --listar-modelos)")
    for ruta in a.imagenes:
        try:
            d = extraer_cartel([cargar_imagen(ruta)], a.texto, a.municipio, a.fecha_pub, a.proveedor)
            d["municipio"] = d.get("municipio") or ia._pueblo(a.municipio)
            print(json.dumps({k: v for k, v in d.items() if k != "texto_cartel"},
                             ensure_ascii=False, indent=1))
        except Exception as e:
            print("ERROR", ruta, e, file=sys.stderr)


if __name__ == "__main__":
    main()
