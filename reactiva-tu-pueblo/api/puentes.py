"""B1 · Puente hacia B2 (bot) y C1 (ia) con PLAN B.

Mis routers llaman a estas funciones y no a bot/ia directamente. Así:
  - Si B2 o C1 aún no han subido su código, el servidor arranca igual (stub).
  - Si Telegram o Gemini fallan, se devuelve algo útil sin IA (lo pide el reto).
En cuanto bot.py e ia.py tengan las funciones del contrato, se usan solas.
"""
import logging

log = logging.getLogger("reactiva")

# ---------- B2: bot.avisar_usuario ----------
try:
    from bot import avisar_usuario as _avisar_usuario  # contrato B2
except Exception as e:  # bot.py vacío, o falta TELEGRAM_BOT_TOKEN...
    log.warning("bot.avisar_usuario no disponible (%s): uso stub con print", e)
    _avisar_usuario = None

# ---------- C1: ia.redactar_recomendacion / ia.revisar_confianza ----------
try:
    import ia as _ia
except Exception as e:
    log.warning("ia.py no disponible (%s): uso textos sin IA", e)
    _ia = None


def avisar(user_id: str, texto: str, buttons=None) -> bool:
    """Envía por Telegram. Nunca lanza excepción: devuelve True/False."""
    if _avisar_usuario is None:
        print(f"[STUB avisar_usuario] {user_id}: {texto} | botones={buttons}")
        return True  # para poder probar el flujo antes de que B2 termine
    try:
        return bool(_avisar_usuario(user_id, texto, buttons))
    except Exception as e:
        log.error("avisar_usuario falló para %s: %s", user_id, e)
        return False


def redactar_recomendacion(nombre: str, eventos: list[dict]) -> str:
    """Texto amable con el porqué. Si la IA falla: motivos de la BD tal cual."""
    if _ia is not None and hasattr(_ia, "redactar_recomendacion"):
        try:
            texto = _ia.redactar_recomendacion(nombre, eventos)
            if texto:
                return texto
        except Exception as e:
            log.error("ia.redactar_recomendacion falló: %s", e)
    # Plan B sin IA
    from ics import fecha_es
    lineas = [f"Hola {nombre} 👋, te proponemos estos planes:"]
    for ev in eventos:
        motivos = ", ".join(ev.get("motivos") or [])
        linea = f"• {ev['title']} ({ev.get('municipio') or ''}), el {fecha_es(ev['starts_at'])}"
        if motivos:
            linea += f". Porque {motivos}"
        lineas.append(linea + ".")
    return "\n".join(lineas)


def revisar_confianza(perfil: dict, eventos: list[dict], resenas: list[dict]) -> dict:
    """{'aprobar': bool, 'motivo': str}. Plan B: la BD ya filtró por reglas."""
    if _ia is not None and hasattr(_ia, "revisar_confianza"):
        try:
            r = _ia.revisar_confianza(perfil, eventos, resenas)
            if isinstance(r, dict) and "aprobar" in r:
                return {"aprobar": bool(r["aprobar"]), "motivo": str(r.get("motivo", ""))}
        except Exception as e:
            log.error("ia.revisar_confianza falló: %s", e)
    return {"aprobar": True, "motivo": "Cumple las reglas de reputación (revisión sin IA)"}
