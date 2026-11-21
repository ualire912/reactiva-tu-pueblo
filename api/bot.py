from collections import Counter
from datetime import datetime, timedelta, timezone
import inspect
import os
import re
import sys
import unicodedata
from typing import Any

# rag.py, ingesta.py... hacen "import ia" / "from db import": necesitan la carpeta api/
# en la ruta de imports también cuando el bot se arranca desde la raíz (bot_polling.py)
_API_DIR = os.path.dirname(os.path.abspath(__file__))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
import httpx

try:
    from api.db import sb
    from api.ics import generar_ics
    from api.telegram_api import (
        answer_callback,
        delete_message,
        edit_message_reply_markup,
        escape_html,
        get_file_bytes,
        send_audio,
        send_document,
        send_message,
        send_photo,
    )
except ImportError:
    from db import sb
    from ics import generar_ics
    from telegram_api import (
        answer_callback,
        delete_message,
        edit_message_reply_markup,
        escape_html,
        get_file_bytes,
        send_audio,
        send_document,
        send_message,
        send_photo,
    )

router = APIRouter(tags=["telegram"])

MADRID_TZ = ZoneInfo("Europe/Madrid")
ESPERANDO_CAMPO: dict[int, dict[str, str]] = {}
ESPERANDO_RESENA: dict[int, str] = {}
# Comentario dicho junto con la valoración ("al torneo le pongo un 4 porque...")
# que se guarda cuando el usuario pulsa el botón rate:{event_id}:{n}
COMENTARIO_PENDIENTE: dict[int, dict[str, str]] = {}
CONFIRMADOS_EN_MEMORIA: set[str] = set()

DIAS_SEMANA = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
MESES_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

PREGUNTAS_CAMPOS: dict[str, str] = {
    "title": "📝 ¿Cuál es el título del plan?",
    "starts_at": "🕒 ¿Qué día y a qué hora es?",
    "municipio": "🏘️ ¿En qué pueblo es?",
    "location_name": "📍 ¿Dónde es exactamente?",
    "price": "💶 ¿Cuánto cuesta? (o escribe 'gratis')",
    "max_capacity": "👥 ¿Cuántas plazas hay? (o 'sin límite')",
    "inscripcion_hasta": "⏳ ¿Hasta qué día y hora se puede apuntar la gente?",
}


def _import_submodules(*names: str):
    for name in names:
        for mod_name in (f"api.{name}", name):
            try:
                return __import__(mod_name, fromlist=[name])
            except Exception:
                continue
    return None


def _get_ia_module():
    return _import_submodules("ia")


def _get_rag_module():
    return _import_submodules("rag")


def _get_voz_module():
    return _import_submodules("voz")


def _get_ingesta_module():
    return _import_submodules("ingesta")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def send_chat_action(chat_id: int | str, action: str = "typing") -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/sendChatAction"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"chat_id": chat_id, "action": action})
    except Exception:
        pass


def _format_fecha_es(iso_str: str | None) -> str:
    if not iso_str:
        return "Fecha por confirmar"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(MADRID_TZ)
        dia_sem = DIAS_SEMANA[local_dt.weekday()]
        mes = MESES_ES[local_dt.month - 1]
        hora = local_dt.strftime("%H:%M")
        anio = f" de {local_dt.year}" if local_dt.year != datetime.now(MADRID_TZ).year else ""
        return f"{dia_sem} {local_dt.day} de {mes}{anio} a las {hora}"
    except Exception:
        return str(iso_str)


def _format_precio_es(precio_raw: Any) -> str:
    if precio_raw in (0, "0", 0.0, None, ""):
        return "Gratis"
    try:
        precio_num = float(precio_raw)
        if precio_num == 0:
            return "Gratis"
        return f"{int(precio_num)} €" if precio_num.is_integer() else f"{precio_num:.2f} €"
    except Exception:
        return str(precio_raw)


def _get_perfil_by_user_id(user_id: str) -> dict[str, Any] | None:
    res = sb.table("profiles").select("*").eq("id", user_id).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


def _get_perfil_by_chat_id(chat_id: int) -> dict[str, Any] | None:
    res = sb.table("profiles").select("*").eq("telegram_chat_id", chat_id).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


def _get_evento(event_id: str) -> dict[str, Any] | None:
    res = sb.table("eventos").select("*").eq("id", event_id).limit(1).execute()
    rows = res.data or []
    return rows[0] if rows else None


def _get_municipio_nombre(municipio_id: int | None) -> str:
    if municipio_id is None:
        return "el pueblo"
    res = sb.table("municipios").select("nombre").eq("id", municipio_id).limit(1).execute()
    rows = res.data or []
    return rows[0]["nombre"] if rows else "el pueblo"


def _wants_audio(perfil: dict[str, Any] | None, force_audio: bool = False) -> bool:
    # Solo manda audio si el usuario envió una nota de voz o pidió audio expresamente
    return bool(force_audio)


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _evento_terminado(ev: dict[str, Any] | None) -> bool:
    """Terminado por estado o por hora real (el estado puede tardar en recalcularse)."""
    if not ev:
        return False
    if ev.get("status") == "finalizado":
        return True
    if ev.get("status") == "cancelado":
        return False
    fin = _parse_dt(ev.get("ends_at") or ev.get("starts_at"))
    return bool(fin and fin <= datetime.now(timezone.utc))


def _evento_admite_inscripcion(ev: dict[str, Any] | None) -> bool:
    if not ev or ev.get("status") not in {"abierto", "completo"}:
        return False
    ahora = datetime.now(timezone.utc)
    inicio = _parse_dt(ev.get("starts_at"))
    if inicio and inicio <= ahora:
        return False
    hasta = _parse_dt(ev.get("inscripcion_hasta"))
    return not (hasta and hasta <= ahora)


def _botones_estrellas(ev_id: str) -> list[list[tuple[str, str]]]:
    return [[(f"{n}⭐", f"rate:{ev_id}:{n}") for n in range(1, 6)]]


def _botones_evento(
    ev_id: str, estado_insc: str | None, ev: dict[str, Any] | None = None
) -> list[list[tuple[str, str]]] | None:
    if ev is not None and ev.get("status") == "cancelado":
        return None
    if ev is not None and _evento_terminado(ev):
        # Evento acabado: nada de apuntarse; quien tuvo plaza puede valorarlo
        return _botones_estrellas(ev_id) if estado_insc == "confirmada" else None
    inicio = _parse_dt(ev.get("starts_at")) if ev is not None else None
    if ev is not None and inicio and inicio <= datetime.now(timezone.utc):
        # En curso: ni apuntarse ni borrarse
        return [[("📅 Calendario", f"ics:{ev_id}")]]
    if ev is not None and estado_insc not in {"confirmada", "lista_de_espera"} \
            and not _evento_admite_inscripcion(ev):
        return [[("📅 Calendario", f"ics:{ev_id}")]]
    if estado_insc in {"confirmada", "lista_de_espera"}:
        return [
            [
                ("❌ Borrarme", f"cancel:{ev_id}"),
                ("📅 Calendario", f"ics:{ev_id}"),
            ],
            [
                ("🚗 Llevo coche", f"coche:ofrece:{ev_id}"),
                ("🙋 Busco coche", f"coche:busca:{ev_id}"),
            ],
        ]
    return [
        [
            ("✅ Apuntarme", f"insc:{ev_id}"),
            ("👎 No me interesa", f"ignorar:{ev_id}"),
        ]
    ]


def _render_tarjeta_evento_html(
    ev: dict[str, Any], estado_insc: str | None = None
) -> str:
    titulo = ev.get("title") or "Plan"
    fecha_es = _format_fecha_es(ev.get("starts_at"))
    lugar = ev.get("location_name") or ev.get("municipio") or "Por confirmar"
    muni_id = ev.get("municipio_id")
    if muni_id and not ev.get("municipio"):
        muni_nombre = _get_municipio_nombre(muni_id)
        if muni_nombre != "el pueblo" and muni_nombre.lower() not in str(lugar).lower():
            lugar = f"{lugar} ({muni_nombre})"

    precio_txt = _format_precio_es(ev.get("price"))

    if ev.get("status") == "cancelado":
        estado_linea = "\n\n<blockquote>❌ <b>Este plan se ha cancelado</b></blockquote>"
    elif ev.get("status") == "pospuesto":
        estado_linea = "\n\n<blockquote>⏸️ <b>Este plan está aplazado</b></blockquote>"
    elif _evento_terminado(ev) and estado_insc == "valorada":
        estado_linea = "\n\n<blockquote>🏁 <b>Ya se celebró</b> · ✅ Ya lo valoraste. ¡Gracias!</blockquote>"
    elif _evento_terminado(ev):
        estado_linea = (
            "\n\n<blockquote>🏁 <b>Ya se celebró.</b> ¿Qué tal fue? Puntúalo con las estrellas.</blockquote>"
            if estado_insc == "confirmada"
            else "\n\n<blockquote>🏁 <b>Este plan ya se celebró</b></blockquote>"
        )
    elif estado_insc == "confirmada":
        estado_linea = "\n\n<blockquote>🟢 <b>Ya estás apuntado/a</b></blockquote>"
    elif estado_insc == "lista_de_espera":
        estado_linea = "\n\n<blockquote>🟡 <b>En lista de espera</b></blockquote>"
    else:
        estado_linea = ""

    return (
        f"📌 <b>{escape_html(str(titulo))}</b>\n\n"
        f"🕒 {escape_html(fecha_es)}\n"
        f"📍 {escape_html(str(lugar))}\n"
        f"💶 {escape_html(precio_txt)}"
        f"{estado_linea}"
    )


async def _enviar_respuesta(
    chat_id: int,
    texto: str,
    buttons: list[list[tuple[str, str]]] | None = None,
    perfil: dict[str, Any] | None = None,
    force_audio: bool = False,
) -> None:
    await send_message(chat_id, texto, buttons=buttons, escape=True)
    if _wants_audio(perfil, force_audio=force_audio):
        voz_mod = _get_voz_module()
        if voz_mod and hasattr(voz_mod, "texto_a_voz"):
            try:
                mp3 = await _maybe_await(voz_mod.texto_a_voz(texto))
                if mp3:
                    await send_audio(chat_id, mp3, caption="🔊 Pulsa para escuchar")
            except Exception:
                pass


async def _asegurar_perfil_telegram(
    chat_id: int, first_name: str | None = None
) -> dict[str, Any] | None:
    perfil = _get_perfil_by_chat_id(chat_id)
    if perfil:
        return perfil

    nombre = (first_name or "Vecino/a").strip() or "Vecino/a"
    email_sintetico = f"tg{chat_id}@reactiva.local"

    try:
        sb.auth.admin.create_user(
            {
                "email": email_sintetico,
                "email_confirm": True,
                "user_metadata": {"full_name": nombre},
            }
        )
    except Exception:
        pass

    prof_res = sb.table("profiles").select("*").eq("email", email_sintetico).limit(1).execute()
    rows = prof_res.data or []
    if rows:
        user_id = rows[0]["id"]
        prefs = rows[0].get("preferencias") or {"categorias": [], "municipios": [], "audio": False}
        sb.table("profiles").update(
            {
                "full_name": rows[0].get("full_name") or nombre,
                "telegram_chat_id": chat_id,
                "telegram_linked": True,
                "preferencias": prefs,
            }
        ).eq("id", user_id).execute()
        return _get_perfil_by_user_id(user_id)

    return None


async def _cmd_start(chat_id: int, args: str, first_name: str | None) -> None:
    code = args.strip()
    if code.startswith("VINCULAR-"):
        rpc_res = sb.rpc("vincular_telegram", {"p_code": code, "p_chat_id": chat_id}).execute()
        linked_user_id = rpc_res.data
        if linked_user_id:
            perfil = _get_perfil_by_user_id(str(linked_user_id))
            nombre = (perfil or {}).get("full_name") or first_name or "vecino/a"
            await _enviar_respuesta(
                chat_id,
                f"✅ ¡Hola, {nombre}! Tu cuenta ya está conectada.\n\nPulsa el botón de abajo para ver los próximos planes en tu pueblo:",
                buttons=[
                    [("📅 Ver los planes", "cmd:agenda")],
                ],
                perfil=perfil,
                force_audio=False,
            )
        else:
            await send_message(
                chat_id,
                "⚠️ Ese código ha caducado o no es válido. Pide uno nuevo en la aplicación.",
            )
        return

    perfil = await _asegurar_perfil_telegram(chat_id, first_name)
    nombre = (perfil or {}).get("full_name") or first_name or "vecino/a"
    await _enviar_respuesta(
        chat_id,
        f"👋 ¡Hola, {nombre}! Bienvenido/a a Reactiva tu pueblo.\n\nPuedes escribirme o mandarme una nota de voz preguntando qué actividades hay, o pulsar el botón de abajo:",
        buttons=[
            [("📅 Ver los planes", "cmd:agenda")],
            [("📋 Mis planes apuntados", "cmd:mis_planes")],
        ],
        perfil=perfil,
        force_audio=False,
    )


def _obtener_estados_inscripcion(
    perfil: dict[str, Any] | None, event_ids: list[str]
) -> dict[str, str]:
    inscritos_map: dict[str, str] = {}
    if perfil and perfil.get("id") and event_ids:
        insc_rows = (
            sb.table("inscripciones")
            .select("event_id,status")
            .eq("user_id", perfil["id"])
            .in_("event_id", event_ids)
            .in_("status", ["confirmada", "lista_de_espera"])
            .execute()
            .data
        ) or []
        for r in insc_rows:
            if r.get("event_id") and r.get("status"):
                inscritos_map[str(r["event_id"])] = str(r["status"])
    return inscritos_map


async def _enviar_tarjetas_eventos(
    chat_id: int,
    eventos: list[dict[str, Any]],
    perfil: dict[str, Any] | None = None,
    force_audio: bool = False,
) -> None:
    event_ids = [str(ev["id"]) for ev in eventos if ev.get("id")]
    inscritos_map = _obtener_estados_inscripcion(perfil, event_ids)

    for ev in eventos:
        ev_id = str(ev.get("id") or "")
        estado_insc = inscritos_map.get(ev_id) if ev_id else None
        botones = _botones_evento(ev_id, estado_insc, ev) if ev_id else None
        html_body = _render_tarjeta_evento_html(ev, estado_insc=estado_insc)
        await send_message(chat_id, html_body, buttons=botones, escape=False)

    if force_audio:
        resumen_audio = " ".join(
            f"{ev.get('title') or 'Plan'}, el {_format_fecha_es(ev.get('starts_at'))}, en {ev.get('location_name') or ev.get('municipio') or 'el pueblo'}."
            for ev in eventos
        )
        voz_mod = _get_voz_module()
        if voz_mod and hasattr(voz_mod, "texto_a_voz"):
            try:
                mp3 = await _maybe_await(voz_mod.texto_a_voz(f"Estos son los planes disponibles: {resumen_audio}"))
                if mp3:
                    await send_audio(chat_id, mp3, caption="🔊 Escucha el resumen")
            except Exception:
                pass


async def _cmd_agenda(
    chat_id: int,
    perfil: dict[str, Any] | None = None,
    force_audio: bool = False,
    municipio_filtro: str | None = None,
    categoria_filtro: str | None = None,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    limit_iso = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()

    query = (
        sb.table("eventos")
        .select("*")
        .eq("status", "abierto")
        .gte("starts_at", now_iso)
        .lte("starts_at", limit_iso)
        .order("starts_at")
        .limit(10)
    )
    rows = (query.execute().data) or []

    if municipio_filtro:
        muni_lower = municipio_filtro.lower()
        munis = {
            m["id"]: (m["nombre"] or "").lower()
            for m in ((sb.table("municipios").select("id,nombre").execute().data) or [])
        }
        rows = [
            ev
            for ev in rows
            if muni_lower in munis.get(ev.get("municipio_id"), "")
            or muni_lower in (ev.get("location_name") or "").lower()
        ]

    if categoria_filtro:
        cat_lower = categoria_filtro.lower()
        rows = [ev for ev in rows if cat_lower in (ev.get("category") or "").lower()]

    rows = rows[:5]
    if not rows:
        await _enviar_respuesta(
            chat_id,
            "📅 Ahora mismo no hay planes abiertos para los próximos días.",
            buttons=[[("📅 Volver a mirar", "cmd:agenda")]],
            perfil=perfil,
            force_audio=force_audio,
        )
        return

    await _enviar_tarjetas_eventos(chat_id, rows, perfil=perfil, force_audio=force_audio)


async def _cmd_mis_planes(chat_id: int, perfil: dict[str, Any] | None) -> None:
    if not perfil:
        await send_message(chat_id, "⚠️ Primero escribe /start para empezar.")
        return

    user_id = perfil["id"]
    insc_rows = (
        sb.table("inscripciones")
        .select("*")
        .eq("user_id", user_id)
        .in_("status", ["confirmada", "lista_de_espera"])
        .execute()
        .data
    ) or []

    if not insc_rows:
        await _enviar_respuesta(
            chat_id,
            "📋 Todavía no estás apuntado/a a ningún plan.",
            buttons=[[("📅 Ver los planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=False,
        )
        return

    ids = [str(r["event_id"]) for r in insc_rows if r.get("event_id")]
    evs = {str(e["id"]): e for e in (sb.table("eventos").select("*").in_("id", ids).execute().data or [])}
    estado_de = {str(r["event_id"]): str(r.get("status") or "confirmada") for r in insc_rows}
    vividos, proximos, cancelados = [], [], []
    for ev_id, ev in evs.items():
        if ev.get("status") == "cancelado":
            cancelados.append(ev)
        elif _evento_terminado(ev):
            if estado_de[ev_id] == "confirmada":  # en lista de espera no llegó a ir
                vividos.append(ev)
        else:
            proximos.append(ev)
    vividos.sort(key=lambda e: str(e.get("starts_at") or ""), reverse=True)
    proximos.sort(key=lambda e: str(e.get("starts_at") or ""))
    if vividos:  # los ya valorados no vuelven a pedir estrellas
        valorados = sb.table("resenas").select("event_id").eq("user_id", user_id).in_(
            "event_id", [str(e["id"]) for e in vividos]).execute().data or []
        for r in valorados:
            estado_de[str(r["event_id"])] = "valorada"

    async def _tarjetas(lista: list[dict[str, Any]]) -> None:
        for ev in lista[:5]:
            ev_id = str(ev["id"])
            await send_message(
                chat_id,
                _render_tarjeta_evento_html(ev, estado_insc=estado_de[ev_id]),
                buttons=_botones_evento(ev_id, estado_de[ev_id], ev),
                escape=False,
            )

    if vividos:
        await send_message(chat_id, f"🏁 Has participado en {len(vividos)} plan(es):")
        await _tarjetas(vividos)
    if proximos:
        await send_message(chat_id, f"📋 Estás apuntado/a a {len(proximos)} plan(es) que aún no se han hecho:")
        await _tarjetas(proximos)
    if cancelados:
        await send_message(
            chat_id,
            "❌ Estos planes a los que te apuntaste se cancelaron:\n"
            + "\n".join(f"• {e.get('title')} ({_format_fecha_es(e.get('starts_at'))})" for e in cancelados),
        )
    if not (vividos or proximos or cancelados):
        await send_message(chat_id, "📋 Todavía no has participado en ningún plan.")


async def _toggle_audio(
    chat_id: int, perfil: dict[str, Any] | None, enabled: bool | None = None
) -> None:
    await send_message(
        chat_id,
        "🔊 Cuando me mandes una nota de voz te responderé por texto y con audio. Si me escribes, te responderé solo por texto.",
    )


async def _procesar_inscripcion(
    chat_id: int,
    perfil: dict[str, Any] | None,
    event_id: str,
    source_message_id: int | None = None,
) -> None:
    if not perfil:
        perfil = await _asegurar_perfil_telegram(chat_id)
    if not perfil:
        await send_message(chat_id, "⚠️ Escribe /start antes de apuntarte.")
        return

    ev = _get_evento(event_id)

    ya_inscrita = (
        sb.table("inscripciones")
        .select("status")
        .eq("user_id", perfil["id"])
        .eq("event_id", event_id)
        .in_("status", ["confirmada", "lista_de_espera"])
        .limit(1)
        .execute()
        .data
        or []
    )
    if ya_inscrita:
        estado_previo = str(ya_inscrita[0].get("status") or "confirmada")
        if source_message_id:
            await edit_message_reply_markup(
                chat_id, source_message_id, _botones_evento(event_id, estado_previo, ev)
            )
        if ev and source_message_id:
            await delete_message(chat_id, source_message_id)
            await send_message(
                chat_id,
                _render_tarjeta_evento_html(ev, estado_insc=estado_previo),
                buttons=_botones_evento(event_id, estado_previo, ev),
                escape=False,
            )
        return

    # No se puede apuntar a un plan terminado, empezado o con la inscripción cerrada
    # (también cubre la reactivación de una inscripción cancelada, que el trigger no vigila)
    if not ev or not _evento_admite_inscripcion(ev):
        if ev and source_message_id:
            await edit_message_reply_markup(
                chat_id, source_message_id, _botones_evento(event_id, None, ev) or []
            )
        if _evento_terminado(ev):
            aviso = "🏁 Este plan ya se celebró, así que no admite inscripciones."
        elif ev:
            aviso = "⚠️ Este plan ya está cerrado y no admite más inscripciones."
        else:
            aviso = "⚠️ No encuentro ese plan."
        await send_message(chat_id, aviso)
        return

    try:
        res = (
            sb.table("inscripciones")
            .insert({"user_id": perfil["id"], "event_id": event_id})
            .execute()
        )
        rows = res.data or []
        estado = str(rows[0].get("status") if rows else "confirmada")

        if ev and source_message_id:
            await delete_message(chat_id, source_message_id)
            await send_message(
                chat_id,
                _render_tarjeta_evento_html(ev, estado_insc=estado),
                buttons=_botones_evento(event_id, estado, ev),
                escape=False,
            )
        elif source_message_id:
            await edit_message_reply_markup(
                chat_id, source_message_id, _botones_evento(event_id, estado, ev)
            )

        if estado == "lista_de_espera":
            await send_message(
                chat_id,
                "⏳ Ahora mismo están todas las plazas llenas, pero te hemos apuntado en la lista de espera. Si queda un sitio libre, te avisaremos.",
            )
        else:
            await send_message(
                chat_id,
                "✅ ¡Ya estás apuntado/a! En la tarjeta de arriba tienes las opciones para guardar la fecha en tu calendario o compartir coche.",
            )
    except Exception as exc:
        msg = str(exc)
        if "23505" in msg or "duplicate" in msg.lower():
            cancelada = (
                sb.table("inscripciones")
                .select("id")
                .eq("user_id", perfil["id"])
                .eq("event_id", event_id)
                .eq("status", "cancelada")
                .limit(1)
                .execute()
                .data
                or []
            )
            if cancelada:
                sb.table("inscripciones").update({"status": "confirmada"}).eq(
                    "id", cancelada[0]["id"]
                ).execute()
                if ev and source_message_id:
                    await delete_message(chat_id, source_message_id)
                    await send_message(
                        chat_id,
                        _render_tarjeta_evento_html(ev, estado_insc="confirmada"),
                        buttons=_botones_evento(event_id, "confirmada", ev),
                        escape=False,
                    )
                elif source_message_id:
                    await edit_message_reply_markup(
                        chat_id, source_message_id, _botones_evento(event_id, "confirmada", ev)
                    )
                await send_message(
                    chat_id,
                    "✅ ¡Te has vuelto a apuntar!",
                )
        elif "no admite inscripciones" in msg.lower():
            await send_message(chat_id, "⚠️ Este plan ya está cerrado y no admite más inscripciones.")
        else:
            await send_message(chat_id, "⚠️ No se pudo completar la inscripción ahora mismo. Inténtalo de nuevo.")


async def _guardar_coche(
    chat_id: int,
    perfil: dict[str, Any] | None,
    event_id: str,
    modo: str,
    plazas: int = 0,
) -> None:
    if not perfil:
        perfil = await _asegurar_perfil_telegram(chat_id)
    if not perfil:
        await send_message(chat_id, "⚠️ Escribe /start primero.")
        return

    user_id = perfil["id"]
    muni_id = perfil.get("municipio_id")
    muni_nombre = _get_municipio_nombre(muni_id)

    payload: dict[str, Any] = {
        "user_id": user_id,
        "event_id": event_id,
        "transporte": "ofrece" if modo == "ofrece" else "busca",
        "transporte_plazas": plazas if modo == "ofrece" else 0,
        "transporte_desde_municipio_id": muni_id,
    }

    existing = (
        sb.table("inscripciones")
        .select("id,status")
        .eq("user_id", user_id)
        .eq("event_id", event_id)
        .limit(1)
        .execute()
        .data
    ) or []

    try:
        if existing:
            if existing[0].get("status") == "cancelada":
                payload["status"] = "confirmada"
            sb.table("inscripciones").update(payload).eq("id", existing[0]["id"]).execute()
        else:
            sb.table("inscripciones").insert(payload).execute()
    except Exception as exc:
        if "no admite inscripciones" in str(exc).lower():
            await send_message(chat_id, "⚠️ Este plan ya no admite inscripciones.")
            return
        await send_message(chat_id, "⚠️ No se pudo guardar tu opción de coche.")
        return

    muni_insc = (
        sb.table("inscripciones")
        .select("transporte,transporte_plazas,transporte_desde_municipio_id")
        .eq("event_id", event_id)
        .execute()
        .data
    ) or []

    if modo == "ofrece":
        buscan = sum(
            1
            for r in muni_insc
            if r.get("transporte") == "busca"
            and (muni_id is None or r.get("transporte_desde_municipio_id") == muni_id)
        )
        await send_message(
            chat_id,
            f"🚗 ¡Perfecto! Hemos anotado que llevas {plazas} plaza(s) libre(s) desde {muni_nombre}. Ahora mismo hay {buscan} vecino(s) buscando coche para este plan.",
        )
    else:
        ofrecidas = sum(
            int(r.get("transporte_plazas") or 0)
            for r in muni_insc
            if r.get("transporte") == "ofrece"
            and (muni_id is None or r.get("transporte_desde_municipio_id") == muni_id)
        )
        await send_message(
            chat_id,
            f"🙋 ¡Anotado! Hemos registrado que buscas coche desde {muni_nombre}. Ahora mismo hay {ofrecidas} plaza(s) ofrecida(s) para ir.",
        )


async def _enviar_ics_evento(chat_id: int, event_id: str) -> None:
    ev = _get_evento(event_id)
    if not ev:
        await send_message(chat_id, "⚠️ No he encontrado ese plan.")
        return
    ics_text = generar_ics(ev)
    await send_document(
        chat_id,
        "evento.ics",
        ics_text.encode("utf-8"),
        caption=f"📅 Toca este archivo para guardar '{ev.get('title') or 'Evento'}' en tu calendario",
    )


def _pregunta_para_campo(campo: str) -> str:
    return PREGUNTAS_CAMPOS.get(campo, f"❓ Me falta el dato de {campo}. ¿Me lo puedes decir?")


async def _responder_con_rag(
    chat_id: int,
    perfil: dict[str, Any] | None,
    consulta: str,
    force_audio: bool = False,
) -> None:
    await send_chat_action(chat_id, "typing")
    rag_mod = _get_rag_module()
    if not rag_mod or not hasattr(rag_mod, "preguntar_agenda"):
        await _cmd_agenda(chat_id, perfil=perfil, force_audio=force_audio)
        return

    res = await _maybe_await(rag_mod.preguntar_agenda(consulta))
    if not isinstance(res, dict):
        await _enviar_respuesta(
            chat_id,
            "🤔 No te he entendido del todo bien. ¿Quieres que te enseñe los próximos planes?",
            buttons=[[("📅 Ver los planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=force_audio,
        )
        return

    eventos_sugeridos = res.get("eventos") or []
    eventos_validos: list[dict[str, Any]] = []
    if isinstance(eventos_sugeridos, list):
        for ev in eventos_sugeridos[:5]:
            if isinstance(ev, dict):
                ev_id = ev.get("id")
                ev_completo = _get_evento(str(ev_id)) if ev_id else None
                eventos_validos.append(ev_completo or ev)

    if eventos_validos:
        # Primero la respuesta (cuántos hay, de qué fechas...) y luego las tarjetas
        cabecera = (res.get("cabecera") or "").strip()
        if cabecera:
            await send_message(chat_id, cabecera)
        await _enviar_tarjetas_eventos(
            chat_id,
            eventos_validos,
            perfil=perfil,
            force_audio=force_audio,
        )
        return

    respuesta_txt = (res.get("respuesta") or "No he encontrado planes con esos datos.").strip()
    await _enviar_respuesta(
        chat_id,
        respuesta_txt,
        buttons=[[("📅 Ver todos los planes", "cmd:agenda")]],
        perfil=perfil,
        force_audio=force_audio,
    )


_RE_QUIERE_VALORAR = re.compile(
    r"\b(resena|resenas|valorar|valoracion|valorarlo|valorarla|puntuar|puntuacion|"
    r"puntua\w*|estrellas?|calificar|opinar sobre|mi opinion)\b"
    r"|\ble (?:pongo|doy|pondria|daria) (?:un|una|el)?\s*(?:[1-5]|uno|dos|tres|cuatro|cinco)\b"
    r"|\b[1-5] de 5\b"
)


_RE_MIS_EVENTOS = re.compile(
    r"\b(he participado|he estado|he ido|fui|he asistido|asisti|estoy apuntad[oa]|"
    r"me he apuntado|me apunte|estoy inscrit[oa]|me inscribi|mis planes|mis eventos|"
    r"mis actividades|mis inscripciones)\b"
)
_RE_INTERESAN = re.compile(
    r"(me interes|me gustar|me pueden gustar|me podrian gustar|para mi\b|recomiend|"
    r"recomendac|mis gustos|a mi gusto|que me va)"
)
_NOMBRE_CATEGORIA = {"gastronomia": "gastronomía", "otro": "otros planes"}


async def _recomendar_eventos(
    chat_id: int, perfil: dict[str, Any] | None, texto: str, force_audio: bool = False
) -> None:
    """Planes abiertos (en las fechas/pueblo/categoría pedidos) ordenados según lo que
    suele apuntarse el usuario: misma categoría y mismos pueblos que su historial."""
    rag_mod = _get_rag_module()
    f: dict[str, Any] = {}
    if rag_mod and hasattr(rag_mod, "extraer_filtros"):
        try:
            f = await _maybe_await(rag_mod.extraer_filtros(texto)) or {}
        except Exception:
            f = {}
    ahora = datetime.now(timezone.utc)
    desde = f.get("desde") or ahora
    q = (
        sb.table("eventos").select("*").in_("status", ["abierto", "completo"])
        .gte("starts_at", max(desde, ahora).isoformat())
    )
    if f.get("hasta"):
        q = q.lt("starts_at", f["hasta"].isoformat())
    if f.get("categoria"):
        q = q.eq("category", f["categoria"])
    if f.get("municipio") and rag_mod and hasattr(rag_mod, "_municipio_id"):
        mid = rag_mod._municipio_id(f["municipio"])
        if mid:
            q = q.eq("municipio_id", mid)
    evs = q.order("starts_at").limit(50).execute().data or []
    rango = rag_mod.describir_filtros(f) if rag_mod and hasattr(rag_mod, "describir_filtros") else ""

    # Historial: a qué se ha apuntado (aunque luego se borrara, dice lo que le gusta)
    historial = []
    if perfil:
        historial = (
            sb.table("inscripciones").select("event_id,status,eventos(category,municipio_id)")
            .eq("user_id", perfil["id"]).execute().data
            or []
        )
    apuntado = {str(h["event_id"]) for h in historial if h.get("status") in ("confirmada", "lista_de_espera")}
    gustos = Counter((h.get("eventos") or {}).get("category") for h in historial)
    gustos.pop(None, None)
    pueblos = Counter((h.get("eventos") or {}).get("municipio_id") for h in historial)
    pueblos.pop(None, None)
    mi_pueblo = (perfil or {}).get("municipio_id")

    def puntos(e: dict[str, Any]) -> int:
        return (2 * gustos.get(e.get("category"), 0) + pueblos.get(e.get("municipio_id"), 0)
                + (1 if e.get("municipio_id") == mi_pueblo else 0))

    ya = [e for e in evs if str(e["id"]) in apuntado]
    candidatos = sorted((e for e in evs if str(e["id"]) not in apuntado),
                        key=lambda e: (-puntos(e), str(e.get("starts_at") or "")))

    if not candidatos and not ya:
        await _enviar_respuesta(
            chat_id, f"📅 No hay planes abiertos {rango}.".replace("  ", " ").replace(" .", "."),
            buttons=[[("📅 Ver todos los planes", "cmd:agenda")]], perfil=perfil, force_audio=force_audio,
        )
        return

    if candidatos:
        if gustos:
            top = ", ".join(_NOMBRE_CATEGORIA.get(c, c) for c, _ in gustos.most_common(3))
            cab = f"✨ Por lo que sueles apuntarte ({top}), te recomiendo {rango}:"
        else:
            cab = f"✨ Aún no sé qué te gusta (no te has apuntado a nada). Estos son los planes {rango}:"
        await send_message(chat_id, cab.replace(" :", ":").replace("  ", " "))
        await _enviar_tarjetas_eventos(chat_id, candidatos[:4], perfil=perfil, force_audio=force_audio)
    if ya:
        await send_message(
            chat_id,
            "🟢 Y ya estás apuntado/a a:\n"
            + "\n".join(f"• {e.get('title')} — {_format_fecha_es(e.get('starts_at'))}" for e in ya),
        )


def _norm_txt(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn").lower()


def _eventos_valorables(perfil: dict[str, Any]) -> list[dict[str, Any]]:
    """Planes terminados en los que el usuario tuvo plaza confirmada y aún no ha valorado."""
    insc = (
        sb.table("inscripciones")
        .select("event_id")
        .eq("user_id", perfil["id"])
        .eq("status", "confirmada")
        .execute()
        .data
        or []
    )
    ids = [str(r["event_id"]) for r in insc if r.get("event_id")]
    if not ids:
        return []
    ya = (
        sb.table("resenas").select("event_id").eq("user_id", perfil["id"]).in_("event_id", ids)
        .execute().data
        or []
    )
    valorados = {str(r["event_id"]) for r in ya}
    evs = sb.table("eventos").select("*").in_("id", ids).execute().data or []
    evs = [e for e in evs if str(e["id"]) not in valorados and _evento_terminado(e)]
    evs.sort(key=lambda e: str(e.get("starts_at") or ""), reverse=True)
    return evs[:5]


async def _manejar_resena_libre(
    chat_id: int, perfil: dict[str, Any] | None, texto: str, force_audio: bool = False
) -> None:
    """El usuario quiere valorar un plan escribiendo o hablando (no con los botones)."""
    if not perfil:
        perfil = await _asegurar_perfil_telegram(chat_id)
    if not perfil:
        await send_message(chat_id, "⚠️ Escribe /start antes de valorar un plan.")
        return

    valorables = _eventos_valorables(perfil)
    if not valorables:
        await _enviar_respuesta(
            chat_id,
            "⭐ Ahora mismo no tienes planes pendientes de valorar. "
            "Solo se pueden valorar los planes a los que te apuntaste y que ya terminaron.",
            buttons=[[("📋 Mis planes", "cmd:mis_planes")]],
            perfil=perfil,
            force_audio=force_audio,
        )
        return

    # ¿Ha dicho ya a qué plan y cuántas estrellas? ("al torneo le pongo un 4")
    rag_mod = _get_rag_module()
    detectado = None
    if rag_mod and hasattr(rag_mod, "_detectar_resena"):
        try:
            detectado = await _maybe_await(rag_mod._detectar_resena(texto))
        except Exception:
            detectado = None
    propuesta = (detectado or {}).get("resena") if isinstance(detectado, dict) else None
    ids_valorables = {str(e["id"]): e for e in valorables}
    if propuesta and str(propuesta.get("event_id")) in ids_valorables:
        ev = ids_valorables[str(propuesta["event_id"])]
        n = max(1, min(5, int(propuesta.get("estrellas") or 5)))
        comentario = (propuesta.get("comentario") or "").strip()
        if comentario:
            COMENTARIO_PENDIENTE[chat_id] = {"event_id": str(ev["id"]), "comentario": comentario}
        else:
            COMENTARIO_PENDIENTE.pop(chat_id, None)
        await _enviar_respuesta(
            chat_id,
            f"⭐ ¿Quieres valorar «{ev.get('title')}» con {n} estrella(s)"
            + (f" y el comentario «{comentario}»" if comentario else "")
            + "? Pulsa para confirmar o elige otra puntuación.",
            buttons=[[(f"✅ Sí, {n}⭐", f"rate:{ev['id']}:{n}")]] + _botones_estrellas(str(ev["id"])),
            perfil=perfil,
            force_audio=force_audio,
        )
        return

    COMENTARIO_PENDIENTE.pop(chat_id, None)
    await send_message(
        chat_id,
        "⭐ Estos son los planes a los que fuiste y que puedes valorar. Pulsa las estrellas:",
    )
    for ev in valorables:
        await send_message(
            chat_id,
            _render_tarjeta_evento_html(ev, estado_insc="confirmada"),
            buttons=_botones_estrellas(str(ev["id"])),
            escape=False,
        )


async def _procesar_texto_o_voz(
    chat_id: int,
    perfil: dict[str, Any] | None,
    texto: str | None = None,
    audio_bytes: bytes | None = None,
) -> None:
    es_audio = audio_bytes is not None and len(audio_bytes) > 0
    if es_audio:
        await send_chat_action(chat_id, "record_voice")

    if chat_id in ESPERANDO_RESENA:
        event_id = ESPERANDO_RESENA.pop(chat_id)
        comentario = (texto or "").strip()
        ia_mod = _get_ia_module()
        if not comentario and es_audio and ia_mod and hasattr(ia_mod, "entender_mensaje"):
            parsed = await _maybe_await(ia_mod.entender_mensaje(audio_bytes=audio_bytes))
            comentario = (parsed or {}).get("texto") or ""
        if perfil and comentario:
            sb.table("resenas").update({"comentario": comentario}).eq("event_id", event_id).eq(
                "user_id", perfil["id"]
            ).execute()
            await _enviar_respuesta(
                chat_id,
                "💬 ¡Comentario guardado! Muchas gracias por compartir tu opinión.",
                perfil=perfil,
                force_audio=es_audio,
            )
        else:
            await send_message(chat_id, "⚠️ No he podido leer el comentario.")
        return

    if chat_id in ESPERANDO_CAMPO:
        espera = ESPERANDO_CAMPO[chat_id]
        ingesta_id = espera["ingesta_id"]
        campo = espera["campo"]
        respuesta = (texto or "").strip()
        ia_mod = _get_ia_module()
        if not respuesta and es_audio and ia_mod and hasattr(ia_mod, "entender_mensaje"):
            parsed = await _maybe_await(ia_mod.entender_mensaje(audio_bytes=audio_bytes))
            respuesta = (parsed or {}).get("texto") or ""

        ingesta_mod = _get_ingesta_module()
        if ingesta_mod and hasattr(ingesta_mod, "completar_campo"):
            faltantes = (
                await _maybe_await(ingesta_mod.completar_campo(ingesta_id, campo, respuesta)) or []
            )
            if faltantes:
                siguiente = str(faltantes[0])
                ESPERANDO_CAMPO[chat_id] = {"ingesta_id": ingesta_id, "campo": siguiente}
                await send_message(chat_id, _pregunta_para_campo(siguiente))
            else:
                ESPERANDO_CAMPO.pop(chat_id, None)
                await pedir_confirmacion_ingesta(ingesta_id)
        return

    texto_limpio = (texto or "").strip().lower()
    if not es_audio and texto_limpio in {"hola", "agenda", "planes", "ayuda"}:
        if texto_limpio == "ayuda":
            await send_message(
                chat_id,
                "ℹ️ Cómo usar este chat:\n\n"
                "• Pulsa 'Ver los planes' o escribe /agenda para ver las actividades.\n"
                "• Escribe lo que buscas (por ejemplo: 'eventos de deporte') o mándame una nota de voz.\n"
                "• Usa /mis_planes para ver a qué actividades estás apuntado/a.",
            )
        else:
            await _cmd_agenda(chat_id, perfil=perfil, force_audio=False)
        return

    ia_mod = _get_ia_module()
    if not ia_mod or not hasattr(ia_mod, "entender_mensaje"):
        await _enviar_respuesta(
            chat_id,
            "🤔 No te he entendido bien. ¿Quieres que te enseñe los planes?",
            buttons=[[("📅 Ver los planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=es_audio,
        )
        return

    if not es_audio:
        await send_chat_action(chat_id, "typing")

    try:
        if es_audio:
            resultado = await _maybe_await(ia_mod.entender_mensaje(audio_bytes=audio_bytes))
        else:
            resultado = await _maybe_await(ia_mod.entender_mensaje(text=texto))
    except Exception:
        await _enviar_respuesta(
            chat_id,
            "🤔 No te he entendido bien. ¿Quieres que te enseñe los planes?",
            buttons=[[("📅 Ver los planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=es_audio,
        )
        return

    if not isinstance(resultado, dict):
        await _enviar_respuesta(
            chat_id,
            "🤔 No te he entendido bien. ¿Quieres que te enseñe los planes?",
            buttons=[[("📅 Ver los planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=es_audio,
        )
        return

    intencion = resultado.get("intencion", "otro")
    texto_reconocido = (resultado.get("texto") or texto or "").strip()

    # Quiere dejar una reseña: NO se busca en la agenda (si no, salen tarjetas para apuntarse)
    if _RE_QUIERE_VALORAR.search(_norm_txt(texto_reconocido)):
        await _manejar_resena_libre(chat_id, perfil, texto_reconocido, force_audio=es_audio)
        return

    # Preguntas sobre lo suyo: "en los que he participado", "a qué estoy apuntado"
    norm = _norm_txt(texto_reconocido)
    if _RE_MIS_EVENTOS.search(norm):
        if not perfil:
            perfil = await _asegurar_perfil_telegram(chat_id)
        await _cmd_mis_planes(chat_id, perfil=perfil)
        return

    # "Eventos que me interesan", "recomiéndame algo": según a qué se suele apuntar
    if _RE_INTERESAN.search(norm):
        if not perfil:
            perfil = await _asegurar_perfil_telegram(chat_id)
        await _recomendar_eventos(chat_id, perfil, texto_reconocido, force_audio=es_audio)
        return

    if intencion in {"buscar", "pregunta"}:
        consulta = texto_reconocido or (texto or "").strip()
        await _responder_con_rag(chat_id, perfil=perfil, consulta=consulta, force_audio=es_audio)
    elif intencion == "proponer":
        if not perfil:
            perfil = await _asegurar_perfil_telegram(chat_id)
        ing_row = (
            sb.table("ingestas")
            .insert(
                {
                    "origen": "telegram",
                    "user_id": perfil["id"] if perfil else None,
                    "raw_text": texto_reconocido,
                    "estado": "nueva",
                }
            )
            .execute()
            .data
            or []
        )
        ingesta_id = ing_row[0]["id"] if ing_row else None
        ingesta_mod = _get_ingesta_module()
        if ingesta_id and ingesta_mod and hasattr(ingesta_mod, "procesar_ingesta"):
            await _maybe_await(ingesta_mod.procesar_ingesta(ingesta_id))
        else:
            await _enviar_respuesta(
                chat_id,
                "📝 Hemos recibido tu propuesta de actividad y la hemos guardado.",
                perfil=perfil,
                force_audio=es_audio,
            )
    elif intencion == "apuntarse":
        now_iso = datetime.now(timezone.utc).isoformat()
        evs = (
            sb.table("eventos")
            .select("id,title,starts_at")
            .eq("status", "abierto")
            .gte("starts_at", now_iso)
            .order("starts_at")
            .limit(1)
            .execute()
            .data
            or []
        )
        if evs:
            ev = evs[0]
            await _enviar_respuesta(
                chat_id,
                f"🎟️ ¿Quieres apuntarte a '{ev.get('title')}'?",
                buttons=[[("✅ Sí, apuntarme", f"insc:{ev['id']}")]],
                perfil=perfil,
                force_audio=es_audio,
            )
        else:
            await _enviar_respuesta(
                chat_id,
                "📅 No he encontrado ningún plan abierto ahora mismo.",
                buttons=[[("📅 Ver los planes", "cmd:agenda")]],
                perfil=perfil,
                force_audio=es_audio,
            )
    else:
        await _enviar_respuesta(
            chat_id,
            "🤔 No te he entendido bien. ¿Quieres que te enseñe los próximos planes?",
            buttons=[[("📅 Ver los planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=es_audio,
        )


async def _manejar_foto(chat_id: int, perfil: dict[str, Any] | None, msg: dict[str, Any]) -> None:
    if not perfil:
        perfil = await _asegurar_perfil_telegram(chat_id, (msg.get("from") or {}).get("first_name"))

    photos = msg.get("photo") or []
    if not photos:
        return

    mayor_foto = photos[-1]
    file_id = mayor_foto.get("file_id")
    if not file_id:
        return

    img_bytes = await get_file_bytes(file_id)
    if not img_bytes:
        await send_message(chat_id, "⚠️ No he podido descargar la imagen del cartel.")
        return

    path = f"telegram/{chat_id}/{mayor_foto.get('file_unique_id', 'cartel')}.jpg"
    try:
        sb.storage.from_("posters").upload(path, img_bytes, {"content-type": "image/jpeg"})
        public_url = sb.storage.from_("posters").get_public_url(path)
    except Exception:
        public_url = None

    caption = (msg.get("caption") or "").strip()
    ing_rows = (
        sb.table("ingestas")
        .insert(
            {
                "origen": "telegram",
                "user_id": perfil["id"] if perfil else None,
                "raw_text": caption,
                "image_url": public_url,
                "estado": "nueva",
            }
        )
        .execute()
        .data
        or []
    )

    ingesta_id = ing_rows[0]["id"] if ing_rows else None
    ingesta_mod = _get_ingesta_module()
    if ingesta_id and ingesta_mod and hasattr(ingesta_mod, "procesar_ingesta"):
        await _maybe_await(ingesta_mod.procesar_ingesta(ingesta_id))
    else:
        await send_message(
            chat_id,
            "🖼️ Cartel recibido. Estamos leyendo los datos del evento.",
        )


async def _manejar_callback(cb: dict[str, Any]) -> None:
    cb_id = cb.get("id")
    data = cb.get("data") or ""
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    msg_id = msg.get("message_id")
    from_user = cb.get("from") or {}

    if cb_id:
        await answer_callback(cb_id)

    if not chat_id:
        return

    chat_id_int = int(chat_id)
    msg_id_int = int(msg_id) if isinstance(msg_id, int) else None
    perfil = await _asegurar_perfil_telegram(chat_id_int, from_user.get("first_name"))

    if data == "cmd:agenda":
        await _cmd_agenda(chat_id_int, perfil=perfil, force_audio=False)
    elif data == "cmd:agenda_audio":
        await _cmd_agenda(chat_id_int, perfil=perfil, force_audio=True)
    elif data == "cmd:mis_planes":
        await _cmd_mis_planes(chat_id_int, perfil=perfil)
    elif data == "audio:on":
        await _toggle_audio(chat_id_int, perfil=perfil, enabled=True)
    elif data == "audio:off":
        await _toggle_audio(chat_id_int, perfil=perfil, enabled=False)
    elif data.startswith("ignorar:"):
        if msg_id_int:
            await delete_message(chat_id_int, msg_id_int)
    elif data.startswith("insc:"):
        event_id = data.split(":", 1)[1]
        await _procesar_inscripcion(
            chat_id_int, perfil=perfil, event_id=event_id, source_message_id=msg_id_int
        )
    elif data.startswith("coche:ofrece:"):
        event_id = data.split(":", 2)[2]
        await send_message(
            chat_id_int,
            "🚗 ¿Cuántas plazas libres llevas en tu coche?",
            buttons=[
                [
                    ("1 plaza", f"plazas:{event_id}:1"),
                    ("2 plazas", f"plazas:{event_id}:2"),
                ],
                [
                    ("3 plazas", f"plazas:{event_id}:3"),
                    ("4 plazas", f"plazas:{event_id}:4"),
                ],
            ],
        )
    elif data.startswith("plazas:"):
        _, event_id, plazas_str = data.split(":", 2)
        await _guardar_coche(
            chat_id_int, perfil=perfil, event_id=event_id, modo="ofrece", plazas=int(plazas_str)
        )
    elif data.startswith("coche:busca:"):
        event_id = data.split(":", 2)[2]
        await _guardar_coche(chat_id_int, perfil=perfil, event_id=event_id, modo="busca", plazas=0)
    elif data.startswith("ics:"):
        event_id = data.split(":", 1)[1]
        await _enviar_ics_evento(chat_id_int, event_id=event_id)
    elif data.startswith("cancel:") and perfil:
        event_id = data.split(":", 1)[1]
        ev = _get_evento(event_id)
        inicio = _parse_dt(ev.get("starts_at")) if ev else None
        if _evento_terminado(ev) or (inicio and inicio <= datetime.now(timezone.utc)):
            if msg_id_int:
                await edit_message_reply_markup(
                    chat_id_int, msg_id_int, _botones_evento(event_id, "confirmada", ev) or []
                )
            await send_message(chat_id_int, "🏁 Este plan ya empezó o terminó: ya no puedes borrarte.")
            return
        sb.table("inscripciones").update({"status": "cancelada"}).eq("user_id", perfil["id"]).eq(
            "event_id", event_id
        ).execute()
        if ev and msg_id_int:
            await delete_message(chat_id_int, msg_id_int)
            await send_message(
                chat_id_int,
                _render_tarjeta_evento_html(ev, estado_insc=None),
                buttons=_botones_evento(event_id, None, ev),
                escape=False,
            )
        elif msg_id_int:
            await edit_message_reply_markup(
                chat_id_int, msg_id_int, _botones_evento(event_id, None)
            )
        await send_message(
            chat_id_int,
            "❌ Te has borrado del plan. Tu plaza queda libre.",
        )
    elif data.startswith("conf:si:"):
        ingesta_id = data.split(":", 2)[2]
        if ingesta_id in CONFIRMADOS_EN_MEMORIA:
            return

        ing_rows = (
            sb.table("ingestas")
            .select("estado,event_id")
            .eq("id", ingesta_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if ing_rows and ing_rows[0].get("estado") == "confirmada":
            CONFIRMADOS_EN_MEMORIA.add(ingesta_id)
            return

        ingesta_mod = _get_ingesta_module()
        if ingesta_mod and hasattr(ingesta_mod, "crear_evento_desde_ingesta"):
            ev_creado = await _maybe_await(ingesta_mod.crear_evento_desde_ingesta(ingesta_id))
            if isinstance(ev_creado, dict) and "error" in ev_creado:
                faltantes = ev_creado.get("missing") or []
                campo_falta = str(faltantes[0]) if faltantes else "price"
                ESPERANDO_CAMPO[chat_id_int] = {"ingesta_id": ingesta_id, "campo": campo_falta}
                await send_message(chat_id_int, _pregunta_para_campo(campo_falta))
                return

            CONFIRMADOS_EN_MEMORIA.add(ingesta_id)
            ESPERANDO_CAMPO.pop(chat_id_int, None)
            estado_ev = (ev_creado or {}).get("status")
            if estado_ev == "abierto":
                await send_message(chat_id_int, "✅ ¡Plan publicado!")
            else:
                await send_message(chat_id_int, "📩 Enviado para revisión antes de publicar.")
        else:
            CONFIRMADOS_EN_MEMORIA.add(ingesta_id)
            sb.table("ingestas").update({"estado": "confirmada"}).eq("id", ingesta_id).execute()
            await send_message(chat_id_int, "✅ ¡Plan publicado!")
    elif data.startswith("conf:no:"):
        ingesta_id = data.split(":", 2)[2]
        ESPERANDO_CAMPO.pop(chat_id_int, None)
        sb.table("ingestas").update({"estado": "descartada"}).eq("id", ingesta_id).execute()
        await send_message(chat_id_int, "🗑️ Propuesta descartada.")
    elif data.startswith("rate:") and perfil:
        _, event_id, estrellas_str = data.split(":", 2)
        estrellas = max(1, min(5, int(estrellas_str)))
        ev = _get_evento(event_id)
        insc = (
            sb.table("inscripciones")
            .select("id,status")
            .eq("user_id", perfil["id"])
            .eq("event_id", event_id)
            .eq("status", "confirmada")
            .limit(1)
            .execute()
            .data
            or []
        )
        evento_terminado = False
        if ev:
            fin_raw = ev.get("ends_at") or ev.get("starts_at")
            if fin_raw:
                try:
                    fin_dt = datetime.fromisoformat(str(fin_raw).replace("Z", "+00:00"))
                    if fin_dt.tzinfo is None:
                        fin_dt = fin_dt.replace(tzinfo=timezone.utc)
                    evento_terminado = fin_dt <= datetime.now(timezone.utc)
                except Exception:
                    evento_terminado = False

        if not insc or not evento_terminado:
            await send_message(
                chat_id_int,
                "⚠️ Solo puedes valorar actividades confirmadas que ya hayan terminado.",
            )
            return

        sb.table("resenas").upsert(
            {"event_id": event_id, "user_id": perfil["id"], "estrellas": estrellas},
            on_conflict="event_id,user_id",
        ).execute()
        sb.table("inscripciones").update({"asistio": True}).eq("id", insc[0]["id"]).execute()
        if msg_id_int:  # quitar las estrellas para no valorar dos veces
            await edit_message_reply_markup(chat_id_int, msg_id_int, [])
        pendiente = COMENTARIO_PENDIENTE.pop(chat_id_int, None)
        if pendiente and pendiente.get("event_id") == event_id and pendiente.get("comentario"):
            sb.table("resenas").update({"comentario": pendiente["comentario"]}).eq(
                "event_id", event_id
            ).eq("user_id", perfil["id"]).execute()
            await send_message(
                chat_id_int,
                f"⭐ ¡Gracias por dar {estrellas} estrella(s) y tu comentario! Tu opinión ayuda a otros vecinos.",
            )
            return
        ESPERANDO_RESENA[chat_id_int] = event_id
        await send_message(
            chat_id_int,
            f"⭐ ¡Gracias por dar {estrellas} estrella(s)! Si quieres añadir un comentario, escríbelo o mándame una nota de voz.",
        )


async def procesar_update(update: dict[str, Any]) -> None:
    if not isinstance(update, dict):
        return

    if "callback_query" in update:
        await _manejar_callback(update["callback_query"])
        return

    msg = update.get("message")
    if not isinstance(msg, dict):
        return

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return
    chat_id_int = int(chat_id)
    from_user = msg.get("from") or {}
    perfil = await _asegurar_perfil_telegram(chat_id_int, from_user.get("first_name"))

    if "photo" in msg and msg["photo"]:
        await _manejar_foto(chat_id_int, perfil, msg)
        return

    if "voice" in msg and isinstance(msg["voice"], dict):
        file_id = msg["voice"].get("file_id")
        if file_id:
            audio_bytes = await get_file_bytes(file_id)
            await _procesar_texto_o_voz(chat_id_int, perfil, audio_bytes=audio_bytes)
        return

    text = msg.get("text")
    if isinstance(text, str) and text.strip():
        clean = text.strip()
        if clean.startswith("/"):
            parts = clean.split(maxsplit=1)
            cmd = parts[0].split("@", 1)[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            if cmd == "/start":
                await _cmd_start(chat_id_int, args, from_user.get("first_name"))
            elif cmd == "/agenda":
                await _cmd_agenda(chat_id_int, perfil=perfil, force_audio=False)
            elif cmd == "/mis_planes":
                await _cmd_mis_planes(chat_id_int, perfil=perfil)
            elif cmd == "/proponer":
                await send_message(
                    chat_id_int,
                    "📝 Mándame una foto del cartel o cuéntame el plan por texto o nota de voz.",
                )
            elif cmd == "/audio":
                await _toggle_audio(chat_id_int, perfil=perfil, enabled=None)
            elif cmd == "/ayuda":
                await send_message(
                    chat_id_int,
                    "ℹ️ Opciones disponibles:\n\n"
                    "/agenda — Ver los próximos planes\n"
                    "/mis_planes — Ver a qué planes estás apuntado/a\n"
                    "/proponer — Proponer una actividad nueva\n"
                    "/ayuda — Ver esta ayuda",
                )
            else:
                await _procesar_texto_o_voz(chat_id_int, perfil, texto=clean)
        else:
            await _procesar_texto_o_voz(chat_id_int, perfil, texto=clean)


async def avisar_usuario(
    user_id: str,
    texto: str,
    buttons: list[list[tuple[str, str]]] | None = None,
) -> bool:
    try:
        perfil = _get_perfil_by_user_id(user_id)
        if not perfil:
            return False
        if perfil.get("notificaciones_activas") is False:
            return False
        chat_id = perfil.get("telegram_chat_id")
        if not chat_id:
            return False

        chat_id_int = int(chat_id)
        sent = await send_message(chat_id_int, texto, buttons=buttons, escape=True)
        return bool(sent)
    except Exception:
        return False


async def pedir_confirmacion_ingesta(ingesta_id: str) -> bool:
    rows = sb.table("ingestas").select("*").eq("id", ingesta_id).limit(1).execute().data or []
    if not rows:
        return False
    ing = rows[0]

    dest_user_id = ing.get("user_id")
    if not dest_user_id and ing.get("entidad_id"):
        ent_rows = (
            sb.table("entidades")
            .select("owner_user_id")
            .eq("id", ing["entidad_id"])
            .limit(1)
            .execute()
            .data
            or []
        )
        if ent_rows:
            dest_user_id = ent_rows[0].get("owner_user_id")

    if not dest_user_id:
        return False

    perfil = _get_perfil_by_user_id(str(dest_user_id))
    chat_id = (perfil or {}).get("telegram_chat_id")
    if not chat_id:
        return False

    chat_id_int = int(chat_id)
    ext = ing.get("extracted") or {}
    missing = ing.get("missing") or []

    titulo = ext.get("title") or "Plan detectado"
    fecha_es = _format_fecha_es(ext.get("starts_at"))
    muni = ext.get("municipio") or "Por confirmar"
    lugar = ext.get("location_name") or "Por confirmar"
    precio_txt = _format_precio_es(ext.get("price"))
    plazas = ext.get("max_capacity")
    plazas_txt = "Sin límite" if plazas is None else str(plazas)

    resumen = (
        f"📋 Resumen del plan:\n\n"
        f"• Título: {titulo}\n"
        f"• Fecha: {fecha_es}\n"
        f"• Lugar: {muni} — {lugar}\n"
        f"• Precio: {precio_txt}\n"
        f"• Plazas: {plazas_txt}"
    )
    botones: list[list[tuple[str, str]]] = [
        [
            ("✅ Sí, publícalo", f"conf:si:{ingesta_id}"),
            ("❌ No", f"conf:no:{ingesta_id}"),
        ]
    ]

    image_url = ing.get("image_url") or ext.get("image_url") or ext.get("poster_url")
    if image_url:
        await send_photo(chat_id_int, image_url, caption=resumen, buttons=botones, escape=True)
    else:
        await send_message(chat_id_int, resumen, buttons=botones, escape=True)

    if missing:
        primer_campo = str(missing[0])
        ESPERANDO_CAMPO[chat_id_int] = {"ingesta_id": ingesta_id, "campo": primer_campo}
        await send_message(chat_id_int, _pregunta_para_campo(primer_campo))

    sb.table("ingestas").update({"estado": "esperando_confirmacion"}).eq("id", ingesta_id).execute()
    return True


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, bg: BackgroundTasks) -> dict[str, bool]:
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if expected_secret and header_secret != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    payload = await request.json()
    bg.add_task(procesar_update, payload)
    return {"ok": True}