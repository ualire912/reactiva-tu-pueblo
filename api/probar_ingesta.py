"""
Prueba la ingesta contra la base de datos REAL, sin arrancar el servidor.

Uso (desde la carpeta api):
    python probar_ingesta.py            -> mete los 6 posts de prueba y los procesa
    python probar_ingesta.py crear      -> además crea el evento del taller (queda pendiente_moderacion)
    python probar_ingesta.py limpiar    -> borra las ingestas y eventos de prueba

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
