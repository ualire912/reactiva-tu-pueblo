"""
Genera eventos de PRUEBA (sin IA, 0 tokens) para probar el asistente con historial:
eventos que se repiten cada año (romerías, fiestas patronales...) de 2023 a 2026
y eventos variados de todas las categorías en los 5 pueblos.

Uso (desde la carpeta api):
    python generar_eventos_prueba.py          -> crea ~20 por categoría (≈160)
    python generar_eventos_prueba.py 50       -> ~50 por categoría (lo que sugirió el profesor)
    python generar_eventos_prueba.py limpiar  -> borra todos los eventos de prueba

Todos llevan source_url "https://prueba.local/sintetico/..." para poder borrarlos.
OJO: aparecen en la agenda de la app. Bórralos antes de la demo si no los queréis ver.
"""
import random
import sys
import uuid
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")
import ia  # noqa: E402  (carga el .env)
from db import sb  # noqa: E402

PREFIJO = "https://prueba.local/sintetico/"
random.seed(2026)

if len(sys.argv) > 1 and sys.argv[1] == "limpiar":
    borrados = sb.table("eventos").delete().like("source_url", PREFIJO + "%").execute().data
    print(f"Borrados {len(borrados)} eventos de prueba")
    sys.exit()

por_categoria = int(sys.argv[1]) if len(sys.argv) > 1 else 20
municipios = {m["nombre"]: m["id"] for m in sb.table("municipios").select("id, nombre").execute().data}
PUEBLOS = list(municipios)

# Eventos que se repiten cada año: (título, categoría, pueblo, mes, día, hora, lugar, descripción)
ANUALES = [
    ("Romería de la Virgen del Rosario", "fiestas", "Tahal", 10, 5, 10, "Ermita",
     "Romería tradicional con procesión, misa rociera y comida de convivencia."),
    ("Fiestas patronales de San Roque", "fiestas", "Velefique", 8, 16, 21, "Plaza de la Iglesia",
     "Verbena, orquesta, juegos infantiles y castillo de fuegos artificiales."),
    ("Fiesta de la Inmaculada", "fiestas", "Velefique", 12, 8, 18, "Plaza de la Iglesia",
     "Chocolate con migas, música y castillo de fuegos."),
    ("Feria de Lucainena", "fiestas", "Lucainena de las Torres", 9, 12, 20, "Plaza del Ayuntamiento",
     "Feria con atracciones, casetas y concurso de paellas."),
    ("Ruta de las Minas", "senderismo", "Lucainena de las Torres", 11, 29, 9, "Plaza del Ayuntamiento",
     "Ruta guiada de 12 km por los antiguos cargaderos de mineral. Traer agua y calzado cómodo."),
    ("Subida al Calar de Velefique", "senderismo", "Velefique", 4, 20, 8, "Fuente del pueblo",
     "Subida exigente con vistas a la Sierra de los Filabres."),
    ("Migas solidarias", "gastronomia", "Tahal", 1, 18, 13, "Plaza Mayor",
     "Migas para todo el pueblo; lo recaudado va a la asociación de mayores."),
    ("Mercadillo navideño de productores", "gastronomia", "Senés", 12, 13, 10, "Plaza",
     "Miel, aceite, almendras y artesanía de la comarca."),
    ("Matanza tradicional", "gastronomia", "Uleila del Campo", 2, 10, 9, "Salón social",
     "Jornada de matanza con degustación de productos típicos."),
    ("Semana cultural", "cultura", "Senés", 7, 20, 20, "Casa de la Cultura",
     "Teatro, cine al aire libre, exposiciones y charlas durante toda la semana."),
    ("Concurso de cuentos de la Mancomunidad", "cultura", "Uleila del Campo", 4, 23, 18, "Biblioteca",
     "Entrega de premios del concurso de relatos por el Día del Libro."),
    ("Carrera popular del Pantano", "deporte", "Tahal", 5, 3, 10, "Polideportivo",
     "Carrera de 5 y 10 km con categorías infantiles."),
    ("Torneo de petanca de verano", "deporte", "Uleila del Campo", 7, 11, 19, "Pista municipal",
     "Torneo por parejas abierto a todas las edades."),
    ("Limpieza del río", "voluntariado", "Senés", 3, 22, 10, "Puente viejo",
     "Jornada de voluntariado para limpiar el cauce. Se ponen guantes y bolsas."),
]

# Plantillas para eventos variados
VARIADOS = {
    "senderismo": ["Ruta de las fuentes", "Paseo al atardecer", "Ruta botánica por la sierra",
                   "Ruta de los cortijos", "Senderismo nocturno con luna llena"],
    "deporte": ["Ruta en bici por la Vía Verde", "Torneo de fútbol sala", "Clase de yoga al aire libre",
                "Gimnasia para mayores", "Partido de pádel solidario"],
    "cultura": ["Cine de verano en invierno", "Concierto de la banda municipal", "Teatro infantil",
                "Charla sobre la historia minera", "Exposición de fotografía antigua"],
    "gastronomia": ["Degustación de aceite", "Concurso de tortillas", "Taller de dulces de Navidad",
                    "Gachas de la abuela", "Jornada de la almendra"],
    "fiestas": ["Verbena de verano", "Fiesta de Halloween infantil", "Cabalgata de Reyes",
                "Fiesta de fin de curso", "Carnaval"],
    "taller": ["Taller de esparto", "Taller de móvil para mayores", "Taller de cerámica",
               "Taller de huerto ecológico", "Taller de primeros auxilios"],
    "voluntariado": ["Reforestación en la sierra", "Recogida de alimentos", "Acompañamiento a mayores",
                     "Arreglo del parque infantil", "Banco de libros"],
    "otro": ["Reunión vecinal", "Feria de asociaciones", "Mercado de segunda mano",
             "Jornada de puertas abiertas del ayuntamiento", "Intercambio de semillas"],
}
LUGARES = ["Plaza del Ayuntamiento", "Casa de la Cultura", "Polideportivo", "Salón social",
           "Plaza de la Iglesia", "Centro de Día", "Biblioteca", "Parque municipal"]


def fecha_madrid(anio, mes, dia, hora):
    return datetime(anio, mes, dia, hora, 0, tzinfo=ia.TZ)


filas = []
hoy = datetime.now(ia.TZ)
for (titulo, cat, pueblo, mes, dia, hora, lugar, desc) in ANUALES:
    for anio in (2023, 2024, 2025, 2026):
        inicio = fecha_madrid(anio, mes, dia, hora)
        if inicio > hoy + timedelta(days=200):
            continue
        filas.append({"title": titulo, "category": cat, "municipio_id": municipios[pueblo],
                      "starts_at": inicio.isoformat(), "duration_minutes": 180,
                      "location_name": lugar, "description": desc,
                      "price": 0, "max_capacity": None})

for cat, titulos in VARIADOS.items():
    ya = sum(1 for f in filas if f["category"] == cat)
    for _ in range(max(0, por_categoria - ya)):
        inicio = hoy + timedelta(days=random.randint(-700, 90), hours=random.randint(-6, 6))
        inicio = inicio.replace(minute=0, second=0, microsecond=0, hour=random.choice([10, 11, 12, 17, 18, 19]))
        pueblo = random.choice(PUEBLOS)
        filas.append({"title": random.choice(titulos), "category": cat,
                      "municipio_id": municipios[pueblo], "starts_at": inicio.isoformat(),
                      "duration_minutes": random.choice([60, 90, 120, 180]),
                      "location_name": random.choice(LUGARES),
                      "description": f"{random.choice(titulos)} en {pueblo}. Actividad de prueba.",
                      "price": random.choice([0, 0, 0, 2, 3, 5, 10]),
                      "max_capacity": random.choice([None, 15, 20, 30, 50])})

for f in filas:
    f.update({"status": "abierto",  # la BD lo corrige sola (finalizado si ya pasó)
              "origen": "moderador", "source_url": PREFIJO + str(uuid.uuid4())})
    if f["max_capacity"] is None:
        f.pop("max_capacity")

creados = 0
for i in range(0, len(filas), 100):
    creados += len(sb.table("eventos").insert(filas[i:i + 100]).execute().data)
print(f"Creados {creados} eventos de prueba ({len(ANUALES)} recurrentes de 2023 a 2026 + variados).")
print("Ahora indexa:  python probar_rag.py indexar")
