from datetime import datetime, timedelta, timezone
import inspect
import os
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
import httpx

try:
    from api.db import sb
    from api.ics import generar_ics
    from api.telegram_api import (
        answer_callback,
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
CONFIRMADOS_EN_MEMORIA: set[str] = set()

PREGUNTAS_CAMPOS: dict[str, str] = {
    "title": "📝 ¿Cuál es el título del plan?",
    "starts_at": "🕒 ¿Qué día y a qué hora es?",
    "municipio": "🏘️ ¿En qué pueblo es?",
    "location_name": "📍 ¿Dónde es exactamente?",
    "price": "💶 ¿Cuánto cuesta? (o 'gratis')",
    "max_capacity": "👥 ¿Cuántas plazas hay? (o 'sin límite')",
    "inscripcion_hasta": "⏳ ¿Hasta qué fecha y hora se puede inscribir?",
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
    """Envía sendChatAction ('typing' o 'record_voice') sin romper si falla."""
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
        return "Por confirmar"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(MADRID_TZ)
        return local_dt.strftime("%d/%m/%Y a las %H:%M")
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
        return "Comarca"
    res = sb.table("municipios").select("nombre").eq("id", municipio_id).limit(1).execute()
    rows = res.data or []
    return rows[0]["nombre"] if rows else "Comarca"


def _wants_audio(perfil: dict[str, Any] | None, force_audio: bool = False) -> bool:
    # Solo envía audio cuando se recibe una nota de voz o se pide audio explícitamente
    return bool(force_audio)


def _botones_evento(ev_id: str, estado_insc: str | None) -> list[list[tuple[str, str]]]:
    if estado_insc in {"confirmada", "lista_de_espera"}:
        fila_accion: list[tuple[str, str]] = [
            ("❌ Borrarme", f"cancel:{ev_id}"),
            ("📅 Calendario", f"ics:{ev_id}"),
        ]
    else:
        fila_accion = [
            ("✅ Apuntarme", f"insc:{ev_id}"),
            ("📅 Calendario", f"ics:{ev_id}"),
        ]
    return [
        fila_accion,
        [
            ("🚗 Llevo coche", f"coche:ofrece:{ev_id}"),
            ("🙋 Busco coche", f"coche:busca:{ev_id}"),
        ],
    ]


def _render_tarjeta_evento_html(
    ev: dict[str, Any], estado_insc: str | None = None
) -> str:
    titulo = ev.get("title") or "Plan"
    fecha_es = _format_fecha_es(ev.get("starts_at"))
    lugar = ev.get("location_name") or ev.get("municipio") or "Por confirmar"
    precio_txt = _format_precio_es(ev.get("price"))

    if estado_insc == "confirmada":
        estado_linea = "\n<blockquote>🟢 <b>Ya estás apuntado/a</b></blockquote>"
    elif estado_insc == "lista_de_espera":
        estado_linea = "\n<blockquote>🟡 <b>En lista de espera</b></blockquote>"
    else:
        estado_linea = ""

    return (
        f"📌 <b>{escape_html(str(titulo))}</b>\n"
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
                    await send_audio(chat_id, mp3, caption="Escúchalo aquí")
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
                f"✅ Cuenta vinculada, {nombre}. Ya puedes usar el bot para ver planes, apuntarte y recibir avisos.",
                buttons=[
                    [("📅 Ver planes", "cmd:agenda"), ("🔊 Planes en audio", "cmd:agenda_audio")],
                ],
                perfil=perfil,
                force_audio=False,
            )
        else:
            await send_message(
                chat_id,
                "⚠️ Ese código no vale, genera otro en la app.",
            )
        return

    perfil = await _asegurar_perfil_telegram(chat_id, first_name)
    nombre = (perfil or {}).get("full_name") or first_name or "vecino/a"
    await _enviar_respuesta(
        chat_id,
        f"👋 Hola, {nombre}. Soy el asistente de Reactiva tu pueblo. Escríbeme o mándame una nota de voz para ver planes, preguntar o apuntarte.",
        buttons=[
            [("📅 Ver planes", "cmd:agenda"), ("🔊 Planes en audio", "cmd:agenda_audio")],
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
        botones = _botones_evento(ev_id, estado_insc) if ev_id else None
        html_body = _render_tarjeta_evento_html(ev, estado_insc=estado_insc)
        await send_message(chat_id, html_body, buttons=botones, escape=False)

    if force_audio:
        resumen_audio = " ".join(
            f"{ev.get('title') or 'Plan'}, {_format_fecha_es(ev.get('starts_at'))}, en {ev.get('location_name') or ev.get('municipio') or 'el pueblo'}."
            for ev in eventos
        )
        voz_mod = _get_voz_module()
        if voz_mod and hasattr(voz_mod, "texto_a_voz"):
            try:
                mp3 = await _maybe_await(voz_mod.texto_a_voz(f"Estos son los planes: {resumen_audio}"))
                if mp3:
                    await send_audio(chat_id, mp3, caption="Escúchalo aquí")
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
    limit_iso = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

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
            "📅 No hay planes abiertos en los próximos 7 días con ese filtro.",
            buttons=[[("📅 Ver toda la agenda", "cmd:agenda")]],
            perfil=perfil,
            force_audio=force_audio,
        )
        return

    await _enviar_tarjetas_eventos(chat_id, rows, perfil=perfil, force_audio=force_audio)


async def _cmd_mis_planes(chat_id: int, perfil: dict[str, Any] | None) -> None:
    if not perfil:
        await send_message(chat_id, "⚠️ Primero escribe /start para activar tu perfil.")
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
            "📋 Aún no estás apuntado/a a ningún plan.",
            buttons=[[("📅 Ver planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=False,
        )
        return

    for ins in insc_rows:
        ev = _get_evento(ins["event_id"])
        if not ev:
            continue
        ev_id = str(ev["id"])
        estado_insc = str(ins.get("status") or "confirmada")
        html_body = _render_tarjeta_evento_html(ev, estado_insc=estado_insc)
        await send_message(
            chat_id,
            html_body,
            buttons=_botones_evento(ev_id, estado_insc),
            escape=False,
        )


async def _toggle_audio(
    chat_id: int, perfil: dict[str, Any] | None, enabled: bool | None = None
) -> None:
    if not perfil:
        perfil = await _asegurar_perfil_telegram(chat_id)
    if not perfil:
        await send_message(chat_id, "⚠️ No pude encontrar tu perfil.")
        return

    prefs = dict(perfil.get("preferencias") or {"categorias": [], "municipios": [], "audio": False})
    nuevo_estado = (not bool(prefs.get("audio", False))) if enabled is None else bool(enabled)
    prefs["audio"] = nuevo_estado

    sb.table("profiles").update({"preferencias": prefs}).eq("id", perfil["id"]).execute()
    perfil["preferencias"] = prefs

    if nuevo_estado:
        await _enviar_respuesta(
            chat_id,
            "🔊 Modo voz listo: cuando me envíes una nota de voz te responderé con texto y audio; si me escribes, te responderé solo por texto.",
            buttons=[[("📅 Escuchar agenda ahora", "cmd:agenda_audio")]],
            perfil=perfil,
            force_audio=True,
        )
    else:
        await send_message(
            chat_id,
            "🔇 Respuestas en audio desactivadas para texto. Si me mandas una nota de voz seguiré respondiendo en voz y texto.",
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
                chat_id, source_message_id, _botones_evento(event_id, estado_previo)
            )
        if estado_previo == "lista_de_espera":
            await send_message(
                chat_id,
                "<blockquote>🟡 <b>En lista de espera</b></blockquote>",
                buttons=[[("❌ Borrarme", f"cancel:{event_id}")]],
                escape=False,
            )
        else:
            await send_message(
                chat_id,
                "<blockquote>🟢 <b>Ya estás apuntado/a</b></blockquote>",
                buttons=[[("❌ Borrarme", f"cancel:{event_id}")]],
                escape=False,
            )
        return

    try:
        res = (
            sb.table("inscripciones")
            .insert({"user_id": perfil["id"], "event_id": event_id})
            .execute()
        )
        rows = res.data or []
        estado = str(rows[0].get("status") if rows else "confirmada")
        if source_message_id:
            await edit_message_reply_markup(
                chat_id, source_message_id, _botones_evento(event_id, estado)
            )
        if estado == "lista_de_espera":
            await _enviar_respuesta(
                chat_id,
                "⏳ No quedan plazas libres ahora mismo: estás en lista de espera y te avisaremos si se libera una.",
                buttons=[[("❌ Borrarme", f"cancel:{event_id}")]],
                perfil=perfil,
                force_audio=False,
            )
        else:
            await _enviar_respuesta(
                chat_id,
                "✅ Apuntado/a correctamente. ¿Quieres ofrecer o buscar coche para este plan?",
                buttons=[
                    [
                        ("🚗 Llevo coche", f"coche:ofrece:{event_id}"),
                        ("🙋 Busco coche", f"coche:busca:{event_id}"),
                    ],
                    [
                        ("❌ Borrarme", f"cancel:{event_id}"),
                        ("📅 Calendario", f"ics:{event_id}"),
                    ],
                ],
                perfil=perfil,
                force_audio=False,
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
                if source_message_id:
                    await edit_message_reply_markup(
                        chat_id, source_message_id, _botones_evento(event_id, "confirmada")
                    )
                await _enviar_respuesta(
                    chat_id,
                    "✅ Te has vuelto a apuntar correctamente.",
                    buttons=[
                        [
                            ("❌ Borrarme", f"cancel:{event_id}"),
                            ("📅 Calendario", f"ics:{event_id}"),
                        ]
                    ],
                    perfil=perfil,
                    force_audio=False,
                )
            else:
                if source_message_id:
                    await edit_message_reply_markup(
                        chat_id, source_message_id, _botones_evento(event_id, "confirmada")
                    )
                await send_message(
                    chat_id,
                    "<blockquote>🟢 <b>Ya estás apuntado/a</b></blockquote>",
                    buttons=[[("❌ Borrarme", f"cancel:{event_id}")]],
                    escape=False,
                )
        elif "no admite inscripciones" in msg.lower():
            await send_message(chat_id, "⚠️ Ese plan ya no admite inscripciones.")
        else:
            await send_message(chat_id, "⚠️ No se pudo completar la inscripción en este momento.")


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
            estado_insc = payload.get("status") or existing[0].get("status") or "confirmada"
        else:
            inserted = sb.table("inscripciones").insert(payload).execute().data or []
            estado_insc = inserted[0].get("status") if inserted else "confirmada"
    except Exception as exc:
        if "no admite inscripciones" in str(exc).lower():
            await send_message(chat_id, "⚠️ Ese plan ya no admite inscripciones.")
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
        estado_txt = (
            "inscripción confirmada" if estado_insc == "confirmada" else "en lista de espera"
        )
        await _enviar_respuesta(
            chat_id,
            f"🚗 Anotado que llevas {plazas} plaza(s) desde {muni_nombre} ({estado_txt}). Hay {buscan} vecino/a(s) de {muni_nombre} buscando coche para este plan.",
            perfil=perfil,
            force_audio=False,
        )
    else:
        ofrecidas = sum(
            int(r.get("transporte_plazas") or 0)
            for r in muni_insc
            if r.get("transporte") == "ofrece"
            and (muni_id is None or r.get("transporte_desde_municipio_id") == muni_id)
        )
        await _enviar_respuesta(
            chat_id,
            f"🙋 Anotado que buscas coche desde {muni_nombre}. Hay {ofrecidas} plaza(s) ofrecida(s) desde {muni_nombre}; también puedes escribir en el chat del evento para coordinar.",
            perfil=perfil,
            force_audio=False,
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
        caption=f"📅 {ev.get('title') or 'Evento'}",
    )


def _pregunta_para_campo(campo: str) -> str:
    return PREGUNTAS_CAMPOS.get(campo, f"❓ Me falta el dato de {campo}. ¿Me lo puedes indicar?")


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
            "🤔 No te he entendido bien, ¿quieres ver la agenda?",
            buttons=[[("📅 Ver planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=force_audio,
        )
        return

    eventos_sugeridos = res.get("eventos") or []
    eventos_validos: list[dict[str, Any]] = []
    if isinstance(eventos_sugeridos, list):
        for ev in eventos_sugeridos[:4]:
            if isinstance(ev, dict):
                ev_id = ev.get("id")
                ev_completo = _get_evento(str(ev_id)) if ev_id else None
                eventos_validos.append(ev_completo or ev)

    if eventos_validos:
        await _enviar_tarjetas_eventos(
            chat_id,
            eventos_validos,
            perfil=perfil,
            force_audio=force_audio,
        )
        return

    respuesta_txt = (res.get("respuesta") or "Aquí tienes la información.").strip()
    await _enviar_respuesta(
        chat_id,
        respuesta_txt,
        buttons=[[("📅 Ver planes", "cmd:agenda")]],
        perfil=perfil,
        force_audio=force_audio,
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
                "💬 ¡Comentario guardado! Muchas gracias por tu opinión.",
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
                "ℹ️ Usa /agenda para ver planes, /mis_planes para tus inscripciones, /proponer para sugerir un plan o mándame una nota de voz.",
            )
        else:
            await _cmd_agenda(chat_id, perfil=perfil, force_audio=False)
        return

    ia_mod = _get_ia_module()
    if not ia_mod or not hasattr(ia_mod, "entender_mensaje"):
        await _enviar_respuesta(
            chat_id,
            "🤔 No te he entendido bien, ¿quieres ver la agenda?",
            buttons=[[("📅 Ver planes", "cmd:agenda")]],
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
            "🤔 No te he entendido bien, ¿quieres ver la agenda?",
            buttons=[[("📅 Ver planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=es_audio,
        )
        return

    if not isinstance(resultado, dict):
        await _enviar_respuesta(
            chat_id,
            "🤔 No te he entendido bien, ¿quieres ver la agenda?",
            buttons=[[("📅 Ver planes", "cmd:agenda")]],
            perfil=perfil,
            force_audio=es_audio,
        )
        return

    intencion = resultado.get("intencion", "otro")
    texto_reconocido = (resultado.get("texto") or texto or "").strip()

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
                "📝 He recibido tu propuesta de plan y la he dejado registrada.",
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
                "📅 No he encontrado un plan abierto para apuntarte ahora mismo.",
                buttons=[[("📅 Ver planes", "cmd:agenda")]],
                perfil=perfil,
                force_audio=es_audio,
            )
    else:
        await _enviar_respuesta(
            chat_id,
            "🤔 No te he entendido bien, ¿quieres ver la agenda?",
            buttons=[[("📅 Ver planes", "cmd:agenda")]],
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
            "🖼️ Cartel recibido. Estamos extrayendo los datos del evento.",
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
    elif data == "audio:on":
        await _toggle_audio(chat_id_int, perfil=perfil, enabled=True)
    elif data == "audio:off":
        await _toggle_audio(chat_id_int, perfil=perfil, enabled=False)
    elif data.startswith("insc:"):
        event_id = data.split(":", 1)[1]
        await _procesar_inscripcion(
            chat_id_int, perfil=perfil, event_id=event_id, source_message_id=msg_id_int
        )
    elif data.startswith("coche:ofrece:"):
        event_id = data.split(":", 2)[2]
        await send_message(
            chat_id_int,
            "🚗 ¿Cuántas plazas libres llevas?",
            buttons=[
                [
                    ("1", f"plazas:{event_id}:1"),
                    ("2", f"plazas:{event_id}:2"),
                    ("3", f"plazas:{event_id}:3"),
                    ("4", f"plazas:{event_id}:4"),
                ]
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
        sb.table("inscripciones").update({"status": "cancelada"}).eq("user_id", perfil["id"]).eq(
            "event_id", event_id
        ).execute()
        if msg_id_int:
            await edit_message_reply_markup(
                chat_id_int, msg_id_int, _botones_evento(event_id, None)
            )
        await send_message(
            chat_id_int,
            "❌ Te has borrado del plan. Tu plaza queda libre.",
            buttons=[[("✅ Volver a apuntarme", f"insc:{event_id}")]],
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
                await send_message(chat_id_int, "¡Publicado!")
            else:
                await send_message(chat_id_int, "Enviado al moderador")
        else:
            CONFIRMADOS_EN_MEMORIA.add(ingesta_id)
            sb.table("ingestas").update({"estado": "confirmada"}).eq("id", ingesta_id).execute()
            await send_message(chat_id_int, "¡Publicado!")
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
                "⚠️ Solo puedes valorar eventos confirmados que ya hayan finalizado.",
            )
            return

        sb.table("resenas").upsert(
            {"event_id": event_id, "user_id": perfil["id"], "estrellas": estrellas},
            on_conflict="event_id,user_id",
        ).execute()
        sb.table("inscripciones").update({"asistio": True}).eq("id", insc[0]["id"]).execute()
        ESPERANDO_RESENA[chat_id_int] = event_id
        await send_message(
            chat_id_int,
            f"⭐ ¡Gracias por tu valoración de {estrellas} estrella(s)! ¿Quieres añadir un comentario? Escríbelo o mándame un audio.",
        )


async def procesar_update(update: dict[str, Any]) -> None:
    """Procesa un Update entrante de Telegram (webhook o polling)."""
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
                    "📝 Mándame una foto del cartel, un texto o un audio contando el plan.",
                )
            elif cmd == "/audio":
                await _toggle_audio(chat_id_int, perfil=perfil, enabled=None)
            elif cmd == "/ayuda":
                await send_message(
                    chat_id_int,
                    "ℹ️ Comandos disponibles:\n/agenda - Planes de los próximos días\n/mis_planes - Planes a los que te has apuntado\n/proponer - Proponer un plan\n/audio - Activar o desactivar audio\n/ayuda - Cómo funciona",
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
    """Envía un aviso por Telegram al usuario si tiene chat vinculado y notificaciones activas."""
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
        if not sent:
            return False

        prefs = perfil.get("preferencias") or {}
        if bool(prefs.get("audio", False)):
            voz_mod = _get_voz_module()
            if voz_mod and hasattr(voz_mod, "texto_a_voz"):
                mp3 = await _maybe_await(voz_mod.texto_a_voz(texto))
                if mp3:
                    await send_audio(chat_id_int, mp3, caption="Aviso en audio")
        return True
    except Exception:
        return False


async def pedir_confirmacion_ingesta(ingesta_id: str) -> bool:
    """Pide confirmación por Telegram al autor o dueño de entidad para una ingesta extraída."""
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
    precio = ext.get("price")
    precio_txt = _format_precio_es(precio)
    plazas = ext.get("max_capacity")
    plazas_txt = "Sin límite" if plazas is None else str(plazas)

    resumen = (
        f"📋 Resumen del plan:\n"
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