"""B1 · POST /moderacion/promociones/revisar -> {promovidos: [...], a_revisar: [...]}

La BD ya marca candidatos (v_candidatos_publicador) por reglas.
Aquí solo se pasa el filtro de confianza (IA) y se promueve.
"""
from fastapi import APIRouter

from db import perfil, sb
from puentes import avisar, revisar_confianza

router = APIRouter(tags=["moderacion"])


@router.post("/moderacion/promociones/revisar")
def revisar_promociones():
    candidatos = sb.table("v_candidatos_publicador").select("*").execute().data
    promovidos, a_revisar = [], []

    for c in candidatos:
        uid = c["id"]
        try:
            p = perfil(uid) or {}
            eventos = sb.table("eventos") \
                .select("id, title, category, starts_at, status, municipio_id") \
                .eq("created_by", uid).execute().data
            ids = [e["id"] for e in eventos]
            resenas = sb.table("resenas").select("event_id, estrellas, comentario") \
                .in_("event_id", ids).execute().data if ids else []

            # Solo datos útiles para la IA (sin email, teléfono ni dirección: ahorra tokens y privacidad)
            resumen = {k: p.get(k) for k in ("full_name", "rol", "reputation_score", "created_at")}
            r = revisar_confianza(resumen, eventos, resenas)
            item = {"user_id": uid, "full_name": c.get("full_name"), "motivo": r["motivo"]}

            if r["aprobar"]:
                sb.table("profiles").update({"rol": "publicador", "promocion_pendiente": False}) \
                    .eq("id", uid).execute()
                avisar(uid, "🎉 Ya eres Publicador/a: tus planes se publican sin esperar.")
                promovidos.append(item)
            else:
                a_revisar.append(item)
        except Exception as e:
            print(f"[promociones] error con {uid}: {e}")
            a_revisar.append({"user_id": uid, "full_name": c.get("full_name"),
                              "motivo": f"Error al revisar: {e}"})

    return {"promovidos": promovidos, "a_revisar": a_revisar}
