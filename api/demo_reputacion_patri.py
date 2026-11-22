"""Datos de demostración: Patri (usuaria) con buena reputación (3,5 puntos) sin llegar a
Publicadora, y un plan suyo listo para reseñarse en la demo -> con una reseña de 4-5★ sube a
4 puntos y pasa a Publicadora (el bot se lo dice).

    python demo_reputacion_patri.py           # crea los datos
    python demo_reputacion_patri.py limpiar   # borra todo lo que creó (source_url demo-reputacion)

Respeta las reglas de la BD: los planes se crean en el futuro, se apunta la gente y luego se
les pone su fecha real (ya celebrados). Las reseñas son de usuarios que estaban apuntados.
"""
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()
from db import sb  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
MARCA = "https://prueba.local/demo-reputacion/"
TZ = ZoneInfo("Europe/Madrid")


def perfil(nombre: str) -> dict:
    filas = sb.table("profiles").select("id,full_name,rol").eq("full_name", nombre).execute().data
    assert filas, f"No existe el perfil {nombre}"
    return filas[0]


def municipio(nombre: str) -> int:
    return sb.table("municipios").select("id").eq("nombre", nombre).execute().data[0]["id"]


PLANES = [  # título, categoría, pueblo, lugar, fecha (2026), precio, plazas, descripción, reseñas
    ("Taller de cestería de esparto", "taller", "Senés", "Salón Cultural de Senés", "2026-09-26 11:00", 5, 15,
     "Aprende a trenzar esparto como se ha hecho siempre en la sierra y llévate tu propia pieza.",
     [("Irene", 5, "Muy bien explicado, me llevé mi primera cesta."), ("Carlos", 4, None)]),
    ("Ruta senderista por la Sierra de los Filabres", "senderismo", "Velefique", "Plaza de la Iglesia de Velefique",
     "2026-10-04 09:00", 0, 25, "Ruta circular de 9 km con vistas a la sierra. Dificultad media.",
     [("Irene", 5, "Paisajes preciosos y buen ritmo."), ("Patacrima", 4, None), ("Lucía", 5, None)]),
    ("Clase de cocina: migas tradicionales", "gastronomia", "Tahal", "Salón Cultural de Tahal", "2026-10-11 12:00",
     3, 20, "Cocinamos juntos unas migas como las de toda la vida y comemos después.",
     [("Carlos", 5, "¡Qué ricas! Repetiría."), ("Irene", 4, None)]),
    ("Tarde de juegos de mesa con los mayores", "cultura", "Lucainena de las Torres", "Hogar del Pensionista",
     "2026-10-18 17:00", 0, 30, "Dominó, parchís y cartas: una tarde para compartir entre generaciones.",
     [("Lucía", 5, "Muy bonito ver a pequeños y mayores juntos."), ("Carlos", 4, None)]),
    ("Taller de fotografía de paisaje", "taller", "Uleila del Campo", "La Placeta", "2026-10-25 16:30", 0, 12,
     "Salida fotográfica por el pueblo con consejos para sacar partido al móvil.",
     [("Irene", 4, "Aprendí trucos muy útiles."), ("Patacrima", 5, None)]),
    ("Mercadillo de trueque", "fiestas", "Senés", "Plaza Mayor de Senés", "2026-11-08 10:00", 0, None,
     "Trae lo que ya no uses y cámbialo por algo que te haga falta.",
     [("Carlos", 4, None), ("Lucía", 4, "Buena idea, había de todo.")]),
    ("Charla: huertos ecológicos en casa", "cultura", "Senés", "Salón Cultural de Senés", "2026-11-15 18:00", 0, 40,
     "Cómo empezar un pequeño huerto ecológico en el patio o la terraza.",
     [("Irene", 5, "Muy práctica, ya tengo mis tomateras."), ("Carlos", 5, None)]),
]
# Plan de la demo: empieza en unos minutos y dura 1 hora; al terminar se puede reseñar
DEMO = ("Taller de jabón artesanal con aceite de oliva", "taller", "Senés", "Salón Cultural de Senés", 0, 15,
        "Elabora tu propio jabón natural con aceite de oliva de la zona. Materiales incluidos.",
        ["Lucía", "Irene", "Carlos"])


def crear() -> None:
    patri = perfil("Patricia")
    assert patri["rol"] == "usuario", "Patri ya no es usuaria"
    futuro = datetime.now(timezone.utc) + timedelta(days=2)
    creados = []
    for i, (titulo, cat, pueblo, lugar, fecha, precio, plazas, desc, resenas) in enumerate(PLANES):
        ev = sb.table("eventos").insert({
            "title": titulo, "category": cat, "municipio_id": municipio(pueblo), "location_name": lugar,
            "starts_at": (futuro + timedelta(hours=i)).isoformat(), "duration_minutes": 120,
            "price": precio, "max_capacity": plazas, "description": desc, "created_by": patri["id"],
            "status": "abierto", "origen": "web", "source_url": f"{MARCA}{i}",
        }).execute().data[0]
        for nombre, _, _ in resenas:  # apuntados (antes de que sea pasado)
            sb.table("inscripciones").insert({"user_id": perfil(nombre)["id"], "event_id": ev["id"],
                                              "status": "confirmada"}).execute()
        inicio = datetime.fromisoformat(fecha).replace(tzinfo=TZ)
        sb.table("eventos").update({"starts_at": inicio.isoformat(), "ends_at": (inicio + timedelta(hours=2)).isoformat(),
                                    "status": "abierto"}).eq("id", ev["id"]).execute()
        for nombre, estrellas, comentario in resenas:
            uid = perfil(nombre)["id"]
            sb.table("inscripciones").update({"asistio": True}).eq("event_id", ev["id"]).eq("user_id", uid).execute()
            sb.table("resenas").insert({"event_id": ev["id"], "user_id": uid, "estrellas": estrellas,
                                        "comentario": comentario,
                                        "created_at": (inicio + timedelta(days=1)).isoformat()}).execute()
            # que el bot no les vuelva a pedir reseña de este plan
            sb.table("notificaciones").upsert({"user_id": uid, "event_id": ev["id"], "tipo": "resena",
                                               "canal": "telegram", "motivo": "demo"},
                                              on_conflict="user_id,event_id,tipo").execute()
        creados.append(titulo)
        print("  ✓", titulo, "·", fecha, "·", len(resenas), "reseñas")

    titulo, cat, pueblo, lugar, precio, plazas, desc, apuntados = DEMO
    inicio = datetime.now(timezone.utc) + timedelta(minutes=5)  # luego se pasa a ayer 18:00 (ya celebrado)
    ev = sb.table("eventos").insert({
        "title": titulo, "category": cat, "municipio_id": municipio(pueblo), "location_name": lugar,
        "starts_at": inicio.isoformat(), "ends_at": (inicio + timedelta(hours=1)).isoformat(),
        "price": precio, "max_capacity": plazas, "description": desc, "created_by": patri["id"],
        "status": "abierto", "origen": "web", "source_url": f"{MARCA}demo",
    }).execute().data[0]
    for nombre in apuntados:
        sb.table("inscripciones").insert({"user_id": perfil(nombre)["id"], "event_id": ev["id"],
                                          "status": "confirmada"}).execute()
    # Ya celebrado: ayer de 18:00 a 19:00 -> los apuntados pueden reseñarlo desde ya
    ayer = (datetime.now(TZ) - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    sb.table("eventos").update({"starts_at": ayer.isoformat(), "ends_at": (ayer + timedelta(hours=1)).isoformat(),
                                "status": "abierto"}).eq("id", ev["id"]).execute()
    print(f"  ✓ PLAN DE LA DEMO: «{titulo}» · {ayer:%d/%m} 18:00-19:00 (ya celebrado, sin reseñas) · "
          f"apuntados: {', '.join(apuntados)}")

    import reputacion
    total = reputacion.recalcular(patri["id"], avisar_si_sube=False)
    p = sb.table("profiles").select("rol,reputation_score").eq("id", patri["id"]).execute().data[0]
    print(f"\nPatri: {total} puntos · rol: {p['rol']}  (con una reseña de 4-5★ del plan de la demo -> 4 -> publicadora)")


def limpiar() -> None:
    evs = sb.table("eventos").select("id,title").like("source_url", f"{MARCA}%").execute().data
    ids = [e["id"] for e in evs]
    if ids:
        for t in ("notificaciones", "resenas", "inscripciones", "mensajes_evento"):
            sb.table(t).delete().in_("event_id", ids).execute()
        sb.table("eventos").delete().in_("id", ids).execute()
    import reputacion
    reputacion.recalcular(perfil("Patricia")["id"], avisar_si_sube=False)
    print(f"Borrados {len(ids)} planes de demostración de Patri")


if __name__ == "__main__":
    limpiar() if "limpiar" in sys.argv else crear()
