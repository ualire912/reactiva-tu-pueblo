"""Alta de una asociación / ayuntamiento / negocio por Telegram (Reto 1 · diapositiva «Alta»).

  1. 🙋 La persona pide ser publicadora de su entidad: «quiero dar de alta mi asociación»,
     /alta o el botón «🏢 Dar de alta mi asociación».
  2. ⚙️ Su cuenta de Telegram ya está verificada (Telegram identifica el número), así que
     queda unida a la solicitud.
  3. 🛡️ El moderador la aprueba o la rechaza desde Telegram (botones).
  4. 🙋 Ha conectado su fuente: la página de Facebook (o su web) que dio al pedir el alta.
  5. ⚙️ El agente empieza a vigilar esa página. Cuando publique un plan, el bot le pregunta a
     ella «¿La subimos a la agenda?» y lo que falta (pasos 4-6 del caso completo).

Sin cambios en la base de datos: la solicitud se guarda en entidades.redes_sociales["alta"]
({estado, solicitante, fecha...}); al aprobarla, owner_user_id = quien la pidió.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from db import sb

# chat -> {"paso": "nombre" | "pueblo" | "pagina", "nombre": ..., "municipio_id": ...}
ESPERANDO_ALTA: dict[int, dict[str, Any]] = {}

RE_ALTA = re.compile(
    r"\b(dar(me)? de alta|alta (de |a )?(mi |una |nuestra )?(asociacion|entidad|organizacion|pagina|"
    r"negocio|club|empresa|bar|ayuntamiento)|registrar (mi |una |nuestra )?(asociacion|entidad|"
    r"organizacion|pagina|negocio)|conectar (mi |nuestra )?(pagina|facebook)|vincular (mi |nuestra )?"
    r"(pagina|facebook)|quiero publicar (como|en nombre de|por) (una |la |mi )?(asociacion|entidad|"
    r"ayuntamiento|club|negocio)|soy (el |la )?(responsable|presidente|presidenta|secretario|"
    r"secretaria) de (una |la )?(asociacion|club|hermandad|cofradia|pena))\b")

_RE_CANCELAR = re.compile(r"^(cancelar|cancela|nada|dejalo|olvidalo|salir|no quiero)\b")
_RE_SIN_PAGINA = re.compile(r"\b(no (tengo|tenemos)|ninguna|sin pagina|no hay)\b")


def _norm(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()


def _municipios() -> list[dict]:
    return sb.table("municipios").select("id,nombre").order("nombre").execute().data or []


def _url_limpia(u: str) -> str:
    """Igual que el agente: la misma página escrita de formas distintas se reconoce."""
    try:
        from scraper.agente_eventos import _url_limpia as limpia  # type: ignore
    except Exception:
        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scraper"))
            from agente_eventos import _url_limpia as limpia  # type: ignore
        except Exception:
            return (u or "").strip().rstrip("/")
    return limpia(u)


def _alta_de(ent: dict) -> dict:
    return ((ent.get("redes_sociales") or {}).get("alta") or {}) if ent else {}


def _guardar_alta(ent: dict, alta: dict) -> None:
    redes = dict(ent.get("redes_sociales") or {})
    redes["alta"] = alta
    sb.table("entidades").update({"redes_sociales": redes}).eq("id", ent["id"]).execute()
    ent["redes_sociales"] = redes


def entidades_de(user_id: str) -> list[dict]:
    """Entidades de las que la persona es responsable."""
    return sb.table("entidades").select("*").eq("owner_user_id", user_id).execute().data or []


# ── Conversación ──────────────────────────────────────────────
async def empezar(chat_id: int, perfil: dict | None) -> None:
    import bot
    if perfil:
        ya = entidades_de(perfil["id"])
        if ya:
            nombres = ", ".join(f"«{e['nombre']}»" for e in ya)
            await bot.send_message(
                chat_id, f"✅ Ya eres responsable de {nombres}. Cuando publiquéis un plan en "
                         "vuestra página te lo preguntaré por aquí. Si quieres dar de alta otra "
                         "entidad, dime su nombre (o escribe «cancelar»).")
            ESPERANDO_ALTA[chat_id] = {"paso": "nombre"}
            return
    ESPERANDO_ALTA[chat_id] = {"paso": "nombre"}
    await bot.send_message(
        chat_id,
        "🏢 <b>Dar de alta tu asociación, ayuntamiento o negocio</b>\n\n"
        "Así, cuando publiquéis un plan en vuestro Facebook <b>como siempre</b>, lo veré y te "
        "preguntaré por aquí si lo subimos a la agenda. No hace falta publicar dos veces.\n\n"
        "Son 3 preguntas. La primera: <b>¿cómo se llama?</b>\n"
        "<i>(Puedes escribir «cancelar» en cualquier momento)</i>",
        escape=False)


async def continuar(chat_id: int, perfil: dict | None, dicho: str) -> None:
    """Respuesta de texto (o audio ya transcrito) mientras se hace el alta."""
    import bot
    estado = ESPERANDO_ALTA.get(chat_id)
    if not estado:
        return
    dicho = (dicho or "").strip()
    if _RE_CANCELAR.search(_norm(dicho)):
        ESPERANDO_ALTA.pop(chat_id, None)
        await bot.send_message(chat_id, "👌 Vale, lo dejamos. Cuando quieras, escribe /alta.")
        return
    if estado["paso"] == "nombre":
        if len(dicho) < 3:
            await bot.send_message(chat_id, "¿Cómo se llama la asociación, ayuntamiento o negocio?")
            return
        estado["nombre"] = dicho[:120]
        estado["paso"] = "pueblo"
        await preguntar_pueblo(chat_id, estado["nombre"])
        return
    if estado["paso"] == "pueblo":  # lo ha escrito en vez de pulsar un botón
        n = _norm(dicho)
        for m in _municipios():
            if _norm(m["nombre"]) in n or n in _norm(m["nombre"]):
                await elegir_pueblo(chat_id, perfil, m["id"])
                return
        await preguntar_pueblo(chat_id, estado.get("nombre") or "")
        return
    if estado["paso"] == "pagina":
        url = None
        m = re.search(r"(https?://\S+|(www\.|m\.)?facebook\.com/\S+)", dicho)
        if m:
            url = m.group(1).rstrip(".,;)")
            if not url.startswith("http"):
                url = "https://" + url
        elif not _RE_SIN_PAGINA.search(_norm(dicho)):
            await bot.send_message(
                chat_id, "No encuentro la dirección 🤔 Pega el enlace de vuestra página de Facebook "
                         "(empieza por https://www.facebook.com/…) o de vuestra web. Si no tenéis, "
                         "escribe «no tengo».")
            return
        ESPERANDO_ALTA.pop(chat_id, None)
        await solicitar(chat_id, perfil, estado["nombre"], estado["municipio_id"], url)


async def preguntar_pueblo(chat_id: int, nombre: str) -> None:
    import bot
    botones = [[(f"🏘️ {m['nombre']}", f"alta_pueblo:{m['id']}")] for m in _municipios()]
    await bot.send_message(chat_id, f"📍 ¿De qué pueblo es «{nombre}»?", buttons=botones)


async def elegir_pueblo(chat_id: int, perfil: dict | None, municipio_id: int) -> None:
    import bot
    estado = ESPERANDO_ALTA.get(chat_id)
    if not estado or not estado.get("nombre"):
        await empezar(chat_id, perfil)
        return
    estado["municipio_id"] = int(municipio_id)
    estado["paso"] = "pagina"
    await bot.send_message(
        chat_id,
        "🔗 Última pregunta: <b>pega la dirección de vuestra página de Facebook</b> (o de vuestra "
        "web). La revisaré cada día buscando planes nuevos.\n\nSi no tenéis, escribe «no tengo»: "
        "podrás proponer los planes directamente aquí.", escape=False)


async def solicitar(chat_id: int, perfil: dict | None, nombre: str, municipio_id: int,
                    url: str | None) -> dict | None:
    """Guarda la solicitud y avisa a los moderadores. Devuelve la entidad."""
    import bot
    if not perfil:
        perfil = await bot._asegurar_perfil_telegram(chat_id)
    if not perfil:
        await bot.send_message(chat_id, "⚠️ No he podido identificar tu cuenta. Prueba con /start.")
        return None
    es_facebook = bool(url and "facebook.com" in url)
    url_fb = _url_limpia(url) if es_facebook else None

    # ¿Ya existe esa entidad? (por su página, o por el nombre en el mismo pueblo)
    ent = None
    if url_fb:
        for e in sb.table("entidades").select("*").not_.is_("facebook_page_url", "null").execute().data or []:
            if _url_limpia(e["facebook_page_url"]) == url_fb:
                ent = e
                break
    if not ent:
        filas = sb.table("entidades").select("*").ilike("nombre", nombre).eq("municipio_id", municipio_id) \
            .limit(1).execute().data
        ent = filas[0] if filas else None
    if ent and ent.get("owner_user_id") and ent["owner_user_id"] != perfil["id"]:
        await bot.send_message(
            chat_id, f"⚠️ «{ent['nombre']}» ya tiene una persona responsable. Si crees que es un "
                     "error, pulsa «🙋 Hablar con una persona».",
            buttons=[[("🙋 Hablar con una persona", "cmd:persona")]])
        return None
    if not ent:
        fila = {"nombre": nombre, "municipio_id": municipio_id, "auto_approve": False,
                "redes_sociales": {} if es_facebook or not url else {"web": url}}
        if url_fb:
            fila["facebook_page_url"] = url_fb
        ent = sb.table("entidades").insert(fila).execute().data[0]
        creada = True
    else:
        creada = False
        if url_fb and not ent.get("facebook_page_url"):
            sb.table("entidades").update({"facebook_page_url": url_fb}).eq("id", ent["id"]).execute()
            ent["facebook_page_url"] = url_fb
    _guardar_alta(ent, {"estado": "pendiente", "solicitante": perfil["id"],
                        "nombre_pedido": nombre, "fuente": url, "creada_en_alta": creada,
                        "fecha": datetime.now(timezone.utc).isoformat()})

    avisados = await avisar_moderadores(ent, perfil)
    await bot.send_message(
        chat_id,
        f"📨 ¡Hecho! He enviado la solicitud de <b>{bot.escape_html(ent['nombre'])}</b> al moderador.\n\n"
        + (f"🔗 Página: {bot.escape_html(url)}\n" if url else "")
        + "Te avisaré por aquí en cuanto la revise. Después, cuando publiquéis un plan, te "
          "preguntaré si lo subimos a la agenda 👍"
        + ("" if avisados else "\n\n<i>(Ahora mismo no hay moderadores con Telegram: la verán en el panel.)</i>"),
        escape=False)
    return ent


# ── Moderador ─────────────────────────────────────────────────
def _tarjeta_alta(ent: dict, perfil: dict) -> tuple[str, list]:
    import bot
    pueblo = next((m["nombre"] for m in _municipios() if m["id"] == ent.get("municipio_id")), "?")
    alta = _alta_de(ent)
    texto = (f"🏢 <b>Solicitud de alta de una entidad</b>\n\n"
             f"<b>{bot.escape_html(ent['nombre'])}</b> · {bot.escape_html(pueblo)}\n"
             f"🙋 La pide: {bot.escape_html((perfil or {}).get('full_name') or 'un usuario de Telegram')}\n"
             + (f"🔗 {bot.escape_html(alta.get('fuente'))}\n" if alta.get("fuente") else "🔗 Sin página\n")
             + "\nComprueba que representa a esa entidad. Si la apruebas, será su responsable: "
               "le preguntaré a esa persona por los planes que publiquen.")
    botones = [[("✅ Aprobar", f"alta_ok:{ent['id']}"), ("🚫 Rechazar", f"alta_no:{ent['id']}")]]
    return texto, botones


async def avisar_moderadores_a(chat_id: int, ent: dict, perfil: dict) -> bool:
    import bot
    texto, botones = _tarjeta_alta(ent, perfil)
    return bool(await bot.send_message(chat_id, texto, buttons=botones, escape=False))


async def avisar_moderadores(ent: dict, perfil: dict) -> int:
    import bot
    n = 0
    for mod in bot._moderadores_con_telegram():
        try:
            if await avisar_moderadores_a(int(mod["telegram_chat_id"]), ent, perfil):
                n += 1
        except Exception:
            pass
    return n


async def decidir(chat_id: int, perfil: dict | None, entidad_id: str, aprobar: bool) -> None:
    import bot
    if (perfil or {}).get("rol") != "moderador":
        await bot.send_message(chat_id, "⚠️ Solo un moderador puede aprobar altas.")
        return
    filas = sb.table("entidades").select("*").eq("id", entidad_id).limit(1).execute().data
    ent = filas[0] if filas else None
    alta = _alta_de(ent)
    if not ent or alta.get("estado") != "pendiente":
        await bot.send_message(chat_id, "ℹ️ Esa solicitud ya se ha revisado.")
        return
    solicitante = alta.get("solicitante")
    alta.update({"estado": "aprobada" if aprobar else "rechazada", "revisada_por": perfil["id"],
                 "revisada": datetime.now(timezone.utc).isoformat()})
    _guardar_alta(ent, alta)
    if aprobar:
        sb.table("entidades").update({"owner_user_id": solicitante}).eq("id", ent["id"]).execute()
        await bot.send_message(chat_id, f"✅ «{ent['nombre']}» aprobada. Ya tiene responsable.")
        await bot.avisar_usuario(
            solicitante,
            f"🎉 ¡El moderador ha aprobado «{ent['nombre']}»! Ya eres su responsable.\n\n"
            + ("Desde ahora reviso vuestra página cada día. Cuando publiquéis un plan, te "
               "preguntaré aquí «¿Lo subimos a la agenda?» y lo que falte (lo puedes contestar con "
               "una nota de voz 🎤)." if ent.get("facebook_page_url") else
               "Puedes proponer vuestros planes aquí mismo (texto, foto del cartel o nota de voz).")
            + "\n\nAl principio un moderador revisa cada plan; con buenas valoraciones, se "
              "publicarán solos.")
    else:
        if alta.get("creada_en_alta"):
            # Se creó solo para esta solicitud y no tiene planes: no se deja una entidad falsa
            tiene = sb.table("eventos").select("id").eq("entidad_organizadora_id", ent["id"]).limit(1).execute().data
            if not tiene:
                sb.table("ingestas").update({"entidad_id": None}).eq("entidad_id", ent["id"]).execute()
                sb.table("entidades").delete().eq("id", ent["id"]).execute()
        await bot.send_message(chat_id, f"🚫 Solicitud de «{ent['nombre']}» rechazada.")
        await bot.avisar_usuario(
            solicitante,
            f"😕 El moderador no ha aprobado el alta de «{ent['nombre']}». Si crees que es un "
            "error, escribe «hablar con una persona». Puedes seguir proponiendo planes aquí.")


def pendientes() -> list[dict]:
    """Solicitudes sin revisar (para /moderar)."""
    filas = sb.table("entidades").select("*").execute().data or []
    return [e for e in filas if _alta_de(e).get("estado") == "pendiente"]
