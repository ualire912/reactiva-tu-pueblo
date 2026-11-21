"""
Prueba la lectura de carteles con IA.

Uso (desde la carpeta api):
    python probar_cartel.py pruebas/cartel1.jpg                 -> lee un cartel guardado en tu PC
    python probar_cartel.py https://.../cartel.jpg              -> lee un cartel desde una URL
    python probar_cartel.py pruebas/cartel1.jpg "texto del post" -> cartel + texto del post juntos
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
aqui = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, aqui)

import ia  # noqa: E402

if len(sys.argv) < 2:
    sys.exit("Uso: python probar_cartel.py <ruta-o-url-de-la-imagen> [texto del post]\n"
             "     python probar_cartel.py generar   (crea carteles de ejemplo, sin IA)")

if sys.argv[1] == "generar":
    from cartel import generar_cartel
    ejemplos = [
        {"title": "Taller de esparto para mayores", "category": "taller",
         "starts_at": "2026-12-05T10:00:00+00:00", "ends_at": "2026-12-05T12:00:00+00:00",
         "location_name": "Centro de Día", "municipio": "Tahal", "price": 3, "max_capacity": 15,
         "inscripcion_hasta": "2026-12-03T22:59:59+00:00"},
        {"title": "Ruta de las Minas", "category": "senderismo",
         "starts_at": "2026-11-29T08:30:00+00:00", "location_name": "Plaza del Ayuntamiento",
         "municipio": "Lucainena de las Torres", "price": 0, "max_capacity": 30, "is_accessible": True},
        {"title": "Fiesta de la Inmaculada", "category": "fiestas",
         "starts_at": "2026-12-08T17:00:00+00:00", "location_name": "Plaza de la Iglesia",
         "municipio": "Velefique", "price": 0},
    ]
    for k, ev in enumerate(ejemplos, 1):
        ruta = os.path.join(aqui, "pruebas", f"cartel_generado{k}.png")
        with open(ruta, "wb") as f:
            f.write(generar_cartel(ev))
        print("Creado:", ruta)
    sys.exit()

origen = sys.argv[1]
texto = sys.argv[2] if len(sys.argv) > 2 else None

if origen.startswith("http"):
    import httpx
    r = httpx.get(origen, timeout=15, follow_redirects=True)
    r.raise_for_status()
    imagen = r.content
else:
    with open(origen, "rb") as f:
        imagen = f.read()

print(f"Imagen: {len(imagen) // 1024} KB | texto: {texto!r}\n")
ev = ia.extraer_evento(texto, imagen)
for k, v in ev.items():
    print(f"  {k:18}: {v}")
print("\nCONSUMO:", ia.CONSUMO)
