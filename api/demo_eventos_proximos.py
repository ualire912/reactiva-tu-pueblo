"""
Eventos de DEMOSTRACIÓN para estos días (no hay eventos futuros publicados en las webs).
Todos llevan source_url "https://prueba.local/demo/..." para poder borrarlos de golpe:

    python demo_eventos_proximos.py           -> los crea (y marca la asistencia de la demo)
    python demo_eventos_proximos.py limpiar   -> los borra todos

Además (para probar recordatorios y recomendaciones en Telegram) al usuario indicado con
DEMO_USUARIO (por defecto, Lucía):
  - lo marca como que FUE al «Mercadillo de otoño de productores» (pasado),
  - lo apunta al taller de mañana (le llega el recordatorio del día antes).
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")
import ia  # noqa: E402  (carga el .env)
from db import sb  # noqa: E402

PREFIJO = "https://prueba.local/demo/"
USUARIO = os.getenv("DEMO_USUARIO", "22fc44e9-7e7b-4aaf-809b-3aa011963a0e")
NOTA = " (Evento de demostración.)"

if len(sys.argv) > 1 and sys.argv[1] == "limpiar":
    ids = [e["id"] for e in sb.table("eventos").select("id").like("source_url", PREFIJO + "%").execute().data]
    if ids:
        sb.table("notificaciones").delete().in_("event_id", ids).execute()
        sb.table("inscripciones").delete().in_("event_id", ids).execute()
        sb.table("eventos").delete().in_("id", ids).execute()
    print(f"Borrados {len(ids)} eventos de demostración")
    sys.exit()

M = {m["nombre"]: m["id"] for m in sb.table("municipios").select("id,nombre").execute().data}
hoy = datetime.now(ia.TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def dia(n: int, hora: int, minuto: int = 0) -> datetime:
    return (hoy + timedelta(days=n)).replace(hour=hora, minute=minuto)


def proximo(dia_semana: int, semanas: int, hora: int, minuto: int = 0) -> datetime:
    """dia_semana: 0=lunes ... 6=domingo; semanas=0 -> esta semana o la próxima."""
    n = (dia_semana - hoy.weekday()) % 7 + 7 * semanas
    return dia(n or 7, hora, minuto)


# (título, categoría, pueblo, inicio, duración h, lugar, precio, plazas, descripción)
PROXIMOS = [
    ("Taller de esparto para principiantes", "taller", "Tahal", dia(1, 17, 30), 2,
     "Centro social", 3, 15, "Aprende a trenzar esparto con artesanas del pueblo. Material incluido."),
    ("Cine en el salón social", "cultura", "Velefique", proximo(4, 0, 19), 2,
     "Salón social", 0, 60, "Proyección de una película familiar con palomitas."),
    ("Ruta por la Vía Verde al atardecer", "senderismo", "Lucainena de las Torres", proximo(5, 0, 16), 3,
     "Antigua estación", 0, 30, "Ruta suave de 8 km por el antiguo trazado del tren minero."),
    ("Taller de belenes", "taller", "Uleila del Campo", proximo(5, 1, 11), 2,
     "Casa de la Cultura", 0, 20, "Montamos entre todos el belén del pueblo."),
    ("Mercadillo navideño de productores", "gastronomia", "Senés", proximo(6, 2, 10), 5,
     "Plaza Mayor", 0, None, "Miel, aceite, almendras, dulces y artesanía de la comarca."),
    ("Concierto de villancicos", "cultura", "Lucainena de las Torres", proximo(5, 3, 20), 2,
     "Iglesia", 0, None, "La banda y el coro del pueblo cantan villancicos."),
    ("Mercadillo de artesanía y segunda mano", "otro", "Tahal", proximo(6, 3, 10), 4,
     "Plaza del Ayuntamiento", 0, None, "Puestos de artesanía y objetos de segunda mano."),
]
PASADO = ("Mercadillo de otoño de productores", "gastronomia", "Senés", dia(-7, 10), 4,
          "Plaza Mayor", 0, None, "Productos de temporada de los agricultores de la zona.")


def crear(t, cat, pueblo, inicio, horas, lugar, precio, plazas, desc, inicio_real=None) -> str:
    fila = {"title": t, "category": cat, "municipio_id": M[pueblo], "starts_at": inicio.isoformat(),
            "ends_at": (inicio + timedelta(hours=horas)).isoformat(), "location_name": lugar,
            "price": precio, "max_capacity": plazas, "description": desc + NOTA,
            "status": "abierto", "origen": "moderador", "source_url": PREFIJO + str(uuid.uuid4())}
    return sb.table("eventos").insert({k: v for k, v in fila.items() if v is not None}).execute().data[0]["id"]


ya = sb.table("eventos").select("id").like("source_url", PREFIJO + "%").limit(1).execute().data
if ya:
    sys.exit("Ya hay eventos de demostración. Bórralos antes con: python demo_eventos_proximos.py limpiar")

ids = {}
for e in PROXIMOS:
    ids[e[0]] = crear(*e)
    print(f"  + {e[3]:%a %d/%m %H:%M} · {e[2]} · {e[0]}")

# Asistencia a un evento PASADO: la BD no deja apuntarse a algo que ya ha empezado, así que se
# crea con fecha futura, se apunta y se le pone su fecha real (ya celebrado)
t, cat, pueblo, inicio, horas, lugar, precio, plazas, desc = PASADO
pasado_id = crear(t, cat, pueblo, dia(2, 10), horas, lugar, precio, plazas, desc)
sb.table("inscripciones").insert({"user_id": USUARIO, "event_id": pasado_id}).execute()
sb.table("eventos").update({"starts_at": inicio.isoformat(),
                            "ends_at": (inicio + timedelta(hours=horas)).isoformat(),
                            "status": "finalizado"}).eq("id", pasado_id).execute()
sb.table("inscripciones").update({"asistio": True}).eq("event_id", pasado_id).eq("user_id", USUARIO).execute()
print(f"  ✓ asistencia: {inicio:%d/%m} «{t}» (ya celebrado)")

# Apuntado al taller de mañana -> le llegará el recordatorio del día antes
sb.table("inscripciones").insert({"user_id": USUARIO,
                                  "event_id": ids["Taller de esparto para principiantes"]}).execute()
print("  ✓ apuntado al «Taller de esparto para principiantes» de mañana")
print(f"{len(PROXIMOS) + 1} eventos de demostración creados")
