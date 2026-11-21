"""B1 · POST /recomendaciones/enviar 🔒 moderador  {user_id?} -> {enviados: n}

Reglas primero (función SQL recomendar_eventos, gratis) y la IA solo redacta.
Solo moderador: si fuera público, cualquiera podría mandar mensajes a todos los vecinos.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from db import require_moderador, sb
from puentes import avisar, redactar_recomendacion

router = APIRouter(tags=["recomendaciones"])


class EnviarIn(BaseModel):
    user_id: str | None = None


@router.post("/recomendaciones/enviar")
def enviar_recomendaciones(body: EnviarIn | None = None, _mod=Depends(require_moderador)):
    q = sb.table("profiles").select("id, full_name")
    if body and body.user_id:
        q = q.eq("id", body.user_id)
    else:
        q = q.eq("telegram_linked", True).eq("notificaciones_activas", True)
    perfiles = q.execute().data

    enviados = 0
    for p in perfiles:
        try:
            uid = p["id"]
            recs = sb.rpc("recomendar_eventos", {"p_user": uid, "p_limite": 5}).execute().data or []

            ya = sb.table("notificaciones").select("event_id") \
                .eq("user_id", uid).eq("tipo", "recomendacion").execute().data
            ya_ids = {f["event_id"] for f in ya}
            recs = [r for r in recs if r["event_id"] not in ya_ids][:3]
            if not recs:
                continue

            nombre = (p.get("full_name") or "vecino/a").split()[0]
            texto = redactar_recomendacion(nombre, recs)

            botones = [[("✅ Apuntarme: " + r["title"][:20], f"insc:{r['event_id']}")] for r in recs]
            primero = recs[0]["event_id"]
            botones.append([("🚗 Llevo coche", f"coche:ofrece:{primero}"),
                            ("🙋 Busco coche", f"coche:busca:{primero}")])
            botones.append([("📅 Calendario", f"ics:{primero}")])

            if not avisar(uid, texto, botones):
                continue
            enviados += 1

            sb.table("notificaciones").upsert(
                [{"user_id": uid, "event_id": r["event_id"], "tipo": "recomendacion",
                  "canal": "telegram", "motivo": ", ".join(r.get("motivos") or [])}
                 for r in recs],
                on_conflict="user_id,event_id,tipo", ignore_duplicates=True,
            ).execute()
        except Exception as e:  # si uno falla, siguen los demás
            print(f"[recomendaciones] error con {p.get('id')}: {e}")

    return {"enviados": enviados}
