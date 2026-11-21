"""B1 · Puente hacia B2 (bot) y C1 (ia) con PLAN B y LÍMITE DE TIEMPO.

Mis routers llaman a estas funciones y no a bot/ia directamente. Así:
  - Si B2 o C1 aún no han subido su código, el servidor arranca igual.
  - Si Telegram o Gemini fallan o tardan demasiado, NO se queda esperando:
    se corta a los X segundos y se sigue con un plan B sin IA.
  - Ninguna función de aquí lanza excepciones.
"""
import asyncio
import inspect
import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("reactiva")

LIMITE_TELEGRAM = 10  # segundos máximos por envío
LIMITE_IA = 15        # segundos máximos por llamada a la IA

_pool = ThreadPoolExecutor(max_workers=8)


def _ejecutar(fn, *args):
    """fn puede ser async (bot.avisar_usuario lo es): entonces se espera de verdad en este
    hilo, con su propio bucle. Antes se devolvía la corrutina sin ejecutar: bool() daba True,
    se contaba como 'enviado' y el mensaje NUNCA salía."""
    r = fn(*args)
    if inspect.isawaitable(r):
        r = asyncio.run(r)
    return r


def _con_limite(segundos: int, fn, *args):
    """Ejecuta fn(*args) y, si tarda más de 'segundos', lanza TimeoutError."""
    return _pool.submit(_ejecutar, fn, *args).result(timeout=segundos)


# ---------- B2: bot.avisar_usuario ----------
try:
    from bot import avisar_usuario as _avisar_usuario  # contrato B2
except Exception as e:  # bot.py vacío, o falta TELEGRAM_BOT_TOKEN...
    log.warning("bot.avisar_usuario no disponible (%s): los avisos solo se imprimen", e)
    _avisar_usuario = None

# ---------- C1: ia.redactar_recomendacion / ia.revisar_confianza ----------
try:
    import ia as _ia
except Exception as e:
    log.warning("ia.py no disponible (%s): uso plan B sin IA", e)
    _ia = None


def avisar_moderadores(event_id: str) -> int:
    """Manda una propuesta pendiente a los moderadores con Telegram. -> nº avisados (0 si falla)."""
    try:
        from bot import avisar_moderadores as _avisar_moderadores
        return int(_con_limite(LIMITE_TELEGRAM * 3, _avisar_moderadores, event_id) or 0)
    except Exception as e:
        log.error("avisar_moderadores falló para %s: %r", event_id, e)
        return 0


def quiere_aviso(user_id: str, tipo: str | None) -> bool:
    """Ajustes de la web (preferencias.avisos): la persona puede apagar cada tipo de aviso.
    Tipos: 'recordatorio', 'recomendacion', 'cambio', 'resena'. Sin ajuste -> sí."""
    if not tipo:
        return True
    try:
        from db import sb
        fila = (sb.table("profiles").select("preferencias").eq("id", user_id).limit(1)
                .execute().data or [{}])[0]
        return (fila.get("preferencias") or {}).get("avisos", {}).get(tipo, True) is not False
    except Exception:
        return True


def avisar(user_id: str, texto: str, buttons=None, tipo: str | None = None) -> bool:
    """Envía por Telegram. True solo si se envió de verdad.
    tipo: si la persona lo ha desactivado en Ajustes, no se envía (devuelve False)."""
    if not quiere_aviso(user_id, tipo):
        return False
    if _avisar_usuario is None:
        # Sin bot: se imprime pero NO cuenta como enviado (no ensucia 'notificaciones')
        print(f"[SIN BOT, no enviado] {user_id}: {texto} | botones={buttons}")
        return False
    try:
        return bool(_con_limite(LIMITE_TELEGRAM, _avisar_usuario, user_id, texto, buttons))
    except Exception as e:
        log.error("avisar_usuario falló o tardó demasiado para %s: %r", user_id, e)
        return False


def redactar_recomendacion(nombre: str, eventos: list[dict]) -> str:
    """Texto amable con el porqué. Si la IA falla o tarda: motivos de la BD tal cual."""
    if _ia is not None and hasattr(_ia, "redactar_recomendacion"):
        try:
            texto = _con_limite(LIMITE_IA, _ia.redactar_recomendacion, nombre, eventos)
            if texto:
                return texto
        except Exception as e:
            log.error("ia.redactar_recomendacion falló o tardó demasiado: %r", e)
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


CONFIANZA_MINIMA = 0.7  # por debajo, aunque la IA diga que sí, lo decide el moderador


def revisar_confianza(perfil: dict, eventos: list[dict], resenas: list[dict]) -> dict:
    """{'aprobar': bool, 'motivo': str, 'con_ia': bool}

    - La IA aprueba con seguridad -> aprobar True (promoción automática).
    - La IA duda (aprobar False, o 'confianza' < 0.7 si la envía) -> moderador.
    - La IA falla, tarda o responde algo raro -> moderador. NUNCA se aprueba solo.
    """
    if _ia is not None and hasattr(_ia, "revisar_confianza"):
        try:
            r = _con_limite(LIMITE_IA, _ia.revisar_confianza, perfil, eventos, resenas)
            if isinstance(r, dict) and isinstance(r.get("aprobar"), bool):
                motivo = str(r.get("motivo") or "")
                conf = r.get("confianza")  # opcional, 0..1
                if r["aprobar"] and isinstance(conf, (int, float)) and conf < CONFIANZA_MINIMA:
                    return {"aprobar": False, "con_ia": True,
                            "motivo": f"Dudoso (confianza {conf:.2f}): {motivo}"}
                return {"aprobar": r["aprobar"], "motivo": motivo, "con_ia": True}
        except Exception as e:
            log.error("ia.revisar_confianza falló o tardó demasiado: %r", e)
    return {"aprobar": False, "con_ia": False,
            "motivo": "La IA no está disponible: cumple las reglas de reputación, decide el moderador"}
