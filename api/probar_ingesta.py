"""
Prueba la ingesta contra la base de datos REAL, sin arrancar el servidor.

Uso (desde la carpeta api):
    python probar_ingesta.py            -> mete los 6 posts de prueba y los procesa
    python probar_ingesta.py crear      -> además crea el evento del taller (queda pendiente_moderacion)
    python probar_ingesta.py limpiar    -> borra las ingestas y eventos de prueba
    python probar_ingesta.py ver        -> muestra las últimas 15 ingestas (p. ej. las de Facebook)
    python probar_ingesta.py limpiar-facebook -> borra las ingestas leídas de Facebook (para repetir la demo)

Los posts de prueba tienen source_url "https://www.facebook.com/prueba/posts/..."
para poder borrarlos luego sin tocar nada más.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
aqui = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, aqui)

import ia  # noqa: E402  (carga el .env)
import ingesta  # noqa: E402
from db import sb  # noqa: E402

PATRON = "https://www.facebook.com/prueba/posts/%"
modo = sys.argv[1] if len(sys.argv) > 1 else ""

if modo == "ver":
    filas = (sb.table("ingestas").select("created_at, estado, source_url, spam_reason, extracted, missing")
             .order("created_at", desc=True).limit(15).execute().data)
    if not filas:
        print("No hay ingestas. ¿Has lanzado el lector y está el servidor encendido?")
    for f in filas:
        ex = f.get("extracted") or {}
        print("=" * 70)
        print(f"{f['estado']:<22} {f['source_url'][:80]}")
        if ex.get("title"):
            print(f"   título : {ex.get('title')} | {ex.get('starts_at')} | {ex.get('location_name')}")
        if f.get("missing"):
            print(f"   falta  : {f['missing']}")
        if f.get("spam_reason") and f["estado"] in ("spam", "descartada", "error"):
            print(f"   motivo : {f['spam_reason']}")
    sys.exit()

if modo == "limpiar-facebook":
    # Solo las ingestas que no se han convertido en evento (las confirmadas se quedan)
    ing = (sb.table("ingestas").delete().like("source_url", "https://www.facebook.com/permalink.php%")
           .is_("event_id", "null").execute().data)
    print(f"Borradas {len(ing)} ingestas de Facebook (sin evento). Ya puedes volver a leer la página.")
    sys.exit()

if modo == "limpiar":
    ev = sb.table("eventos").delete().like("source_url", PATRON).execute().data
    ing = sb.table("ingestas").delete().like("source_url", PATRON).execute().data
    print(f"Borrados: {len(ev)} eventos y {len(ing)} ingestas de prueba")
    sys.exit()

with open(os.path.join(aqui, "pruebas", "posts.json"), encoding="utf-8") as f:
    posts = json.load(f)

ids = {}
for p in posts:
    fila, nueva = ingesta.registrar_post(p["source_url"], p["page_url"], p["text"], p["image_url"])
    print(f"\n=== {p['nombre']} -> ingesta {fila['id'][:8]}… ({'nueva' if nueva else 'ya existía'})")
    if nueva:
        ingesta.procesar_ingesta(fila["id"])  # en el servidor esto va en segundo plano
    fila = sb.table("ingestas").select("*").eq("id", fila["id"]).execute().data[0]
    ids[p["nombre"]] = fila["id"]
    print("   estado     :", fila["estado"])
    print("   spam       :", fila.get("spam_score"), "-", fila.get("spam_reason"))
    ex = fila.get("extracted") or {}
    print("   título     :", ex.get("title"), "|", ex.get("starts_at"), "|", ex.get("municipio"))
    print("   falta      :", fila.get("missing"))

if modo == "crear":
    print("\n=== Crear evento desde la ingesta del taller")
    print(ingesta.crear_evento_desde_ingesta(ids["taller"]))
    print("\n=== Intentar crear la petanca (le falta la hora): debe dar error")
    print(ingesta.crear_evento_desde_ingesta(ids["incompleto"]))
    print("\n=== Completar la petanca: precio y hora")
    print("   falta ahora:", ingesta.completar_campo(ids["incompleto"], "price", "gratis"))
    print("   falta ahora:", ingesta.completar_campo(ids["incompleto"], "starts_at", "a las 10 de la mañana"))

print("\nCONSUMO:", ia.CONSUMO)
