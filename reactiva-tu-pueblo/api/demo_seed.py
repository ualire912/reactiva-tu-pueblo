"""B1 · Datos de demo. Se ejecuta UNA vez en local (se puede repetir sin duplicar):

    cd api
    python demo_seed.py

Crea:
  - Cuentas: moderador, publicador (Asociación Cultural de Lucainena), Carmen (vecina
    que propone planes y acabará siendo Publicadora), Luis (70 años, Tahal, senderismo
    y talleres, prefiere audio) y 3 vecinos que ponen reseñas (Ana, Pedro, Rosa).
  - La entidad con facebook_page_url (la que usará C1 en el simulador).
  - 3 planes PASADOS de Carmen con inscritos. Los 2 primeros ya tienen reseñas
    (reputación 4). El 3.º está listo para la demo: pedir reseña -> Ana pone 5⭐ ->
    reputación 5 -> Carmen pasa a candidata -> /moderacion/promociones/revisar.

Necesita SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en api/.env.
Opcionales: DEMO_PASSWORD (contraseña de todas las cuentas) y DEMO_FACEBOOK_PAGE_URL.
"""
from dotenv import load_dotenv

load_dotenv()

import os
from datetime import datetime, timedelta, timezone

from db import sb

PASSWORD = os.environ.get("DEMO_PASSWORD") or "Reactiva-Demo-2026"
FB_PAGE = os.environ.get("DEMO_FACEBOOK_PAGE_URL") or "https://www.facebook.com/asociacionculturallucainena"
AHORA = datetime.now(timezone.utc)


def muni(nombre: str) -> int:
    return sb.table("municipios").select("id").eq("nombre", nombre).execute().data[0]["id"]


def cuenta(email: str, nombre: str, **campos) -> str:
    """Crea el usuario en Auth (o lo reutiliza) y actualiza su perfil. Devuelve su id."""
    filas = sb.table("profiles").select("id").eq("email", email).execute().data
    if filas:
        uid = filas[0]["id"]
    else:
        r = sb.auth.admin.create_user({
            "email": email, "password": PASSWORD, "email_confirm": True,
            "user_metadata": {"full_name": nombre},
        })
        uid = r.user.id
    sb.table("profiles").update({"full_name": nombre, **campos}).eq("id", uid).execute()
    print(f"  cuenta {email:28} -> {uid}")
    return uid


def evento_pasado(titulo, autor, municipio_id, dias_atras, categoria, inscritos):
    """Crea un plan pasado de 'autor' con inscritos confirmados que asistieron."""
    ya = sb.table("eventos").select("id").eq("title", titulo).eq("created_by", autor).execute().data
    if ya:
        return ya[0]["id"]
    inicio = (AHORA - timedelta(days=dias_atras)).replace(hour=9, minute=0, second=0, microsecond=0)
    ev = sb.table("eventos").insert({
        "title": titulo, "created_by": autor, "municipio_id": municipio_id,
        "category": categoria, "starts_at": inicio.isoformat(), "duration_minutes": 180,
        "location_name": "Plaza del pueblo", "description": "Plan de demo.",
        "status": "abierto",  # abierto para poder inscribir; luego se finaliza
        "origen": "web",
    }).execute().data[0]
    for uid in inscritos:
        sb.table("inscripciones").insert({"user_id": uid, "event_id": ev["id"]}).execute()
    sb.table("inscripciones").update({"asistio": True}).eq("event_id", ev["id"]).execute()
    sb.table("eventos").update({"status": "finalizado"}).eq("id", ev["id"]).execute()
    return ev["id"]


def resena(event_id, user_id, estrellas, comentario):
    sb.table("resenas").upsert(
        {"event_id": event_id, "user_id": user_id, "estrellas": estrellas, "comentario": comentario},
        on_conflict="event_id,user_id",
    ).execute()


def main():
    luc, tahal, senes = muni("Lucainena de las Torres"), muni("Tahal"), muni("Senés")

    print("Cuentas:")
    moderador = cuenta("moderador@reactiva.demo", "Moderación Mancomunidad", rol="moderador")
    publicador = cuenta("asociacion@reactiva.demo", "Asociación Cultural de Lucainena",
                        rol="publicador", municipio_id=luc)
    carmen = cuenta("carmen@reactiva.demo", "Carmen López", municipio_id=luc,
                    preferencias={"categorias": ["deporte", "senderismo"], "municipios": [str(luc)], "audio": False})
    luis = cuenta("luis@reactiva.demo", "Luis Martínez", municipio_id=tahal, edad=70,
                  preferencias={"categorias": ["senderismo", "taller"], "municipios": [str(tahal)], "audio": True})
    ana = cuenta("ana@reactiva.demo", "Ana Ruiz", municipio_id=luc)
    pedro = cuenta("pedro@reactiva.demo", "Pedro Gil", municipio_id=senes)
    rosa = cuenta("rosa@reactiva.demo", "Rosa Navarro", municipio_id=tahal)

    print("Entidad:")
    sb.table("entidades").upsert({
        "nombre": "Asociación Cultural de Lucainena", "owner_user_id": publicador,
        "municipio_id": luc, "facebook_page_url": FB_PAGE, "auto_approve": False,
    }, on_conflict="facebook_page_url").execute()
    print(f"  {FB_PAGE}")

    print("Historial de Carmen (camino a Publicadora):")
    e1 = evento_pasado("Bici por la Vía Verde (octubre)", carmen, luc, 20, "deporte", [ana, pedro, luis])
    e2 = evento_pasado("Paseo a la Torre de los Alcores", carmen, luc, 10, "senderismo", [rosa, luis, ana])
    e3 = evento_pasado("Ruta del Cerro al atardecer", carmen, luc, 1, "senderismo", [ana, pedro])
    resena(e1, ana, 5, "¡Genial!")
    resena(e1, pedro, 4, "Muy bien organizado")
    resena(e2, rosa, 5, "Repetiré")
    resena(e2, luis, 4, "Bonitas vistas")
    rep = sb.table("profiles").select("reputation_score, promocion_pendiente").eq("id", carmen).execute().data[0]
    print(f"  reputación de Carmen = {rep['reputation_score']} (candidata: {rep['promocion_pendiente']})")
    print(f"  Evento para la demo de reseñas: {e3}")
    print(f"    -> POST /resenas/pedir/{e3}  y que Ana (o Pedro) ponga 4-5 estrellas")

    print("\nListo. Contraseña de todas las cuentas:", "(la de DEMO_PASSWORD)" if os.environ.get("DEMO_PASSWORD") else PASSWORD)
    print("Moderador:", moderador)


if __name__ == "__main__":
    main()
