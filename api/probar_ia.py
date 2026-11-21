"""
Prueba rápida de ia.py con los posts de pruebas/posts.json.

Uso (desde la carpeta api):
    python probar_ia.py            -> prueba los 6 posts
    python probar_ia.py sin-ia     -> simula que no hay claves (Plan B)
    python probar_ia.py nvidia     -> simula que Gemini no funciona (solo NVIDIA)
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")  # emojis y tildes en la terminal de Windows
aqui = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, aqui)

import ia  # noqa: E402

modo = sys.argv[1] if len(sys.argv) > 1 else ""
if modo == "sin-ia":
    for k in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "NVIDIA_API_KEY"):
        os.environ.pop(k, None)
    print(">>> MODO SIN IA (Plan B)\n")
elif modo == "nvidia":
    for k in ("GEMINI_API_KEY", "GEMINI_API_KEY_2"):
        os.environ.pop(k, None)
    print(">>> SOLO NVIDIA (simula que Gemini no funciona)\n")
else:
    print("Claves cargadas:",
          {k: bool(os.getenv(k)) for k in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "NVIDIA_API_KEY")},
          "| modelos:", ia.GEMINI_MODELOS, "\n")

with open(os.path.join(aqui, "pruebas", "posts.json"), encoding="utf-8") as f:
    posts = json.load(f)

for p in posts:
    print("=" * 70)
    print("POST:", p["nombre"])
    spam = ia.clasificar_spam(p["text"])
    print("  spam:", spam)
    if spam["spam_score"] >= 0.8:
        print("  -> SPAM: no se extrae (0 llamadas de extracción)")
        continue
    ev = ia.extraer_evento(p["text"])
    for k in ("title", "category", "starts_at", "municipio", "location_name",
              "price", "max_capacity", "inscripcion_hasta", "confidence", "missing"):
        print(f"  {k:15}: {ev[k]}")

print("=" * 70)
print("CONSUMO TOTAL:", ia.CONSUMO)
