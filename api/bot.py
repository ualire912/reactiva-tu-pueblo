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
# Chats que propusieron un plan con una nota de voz: el resumen se les manda también en audio
CHATS_CON_VOZ: set[int] = set()
# Chats a los que se les han pedido los datos de un plan: su siguiente mensaje es la propuesta
ESPERANDO_PROPUESTA: set[int] = set()
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
    "municipio": "🏘️ ¿En qué pueblo es? (Lucainena de las Torres, Senés, Tahal, Uleila del Campo o Velefique)",
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


_ICONO_CATEGORIA: dict[str, tuple[str, str]] = {
    "senderismo": ("🥾", "Senderismo"),
    "deporte": ("⚽", "Deporte"),
    "cultura": ("🎭", "Cultura"),
    "gastronomia": ("🍲", "Gastronomía"),
    "fiestas": ("🎉", "Fiestas"),
    "taller": ("🧶", "Taller"),
    "voluntariado": ("🤝", "Voluntariado"),
    "otro": ("📌", "Otros planes"),
}


def _render_tarjeta_evento_html(
    ev: dict[str, Any], estado_insc: str | None = None
) -> str:
    titulo = ev.get("title") or "Plan"
    icono, categoria = _ICONO_CATEGORIA.get(str(ev.get("category") or ""), ("📌", ""))

    # 📅 Miércoles 25 de noviembre · 14:00
    fecha_es = _format_fecha_es(ev.get("starts_at"))
    fecha_txt = fecha_es.replace(" a las ", " · ")

    # 📍 Plaza Mayor · Senés
    pueblo = ev.get("municipio") or ""
    if not pueblo and ev.get("municipio_id"):
        pueblo = _get_municipio_nombre(ev.get("municipio_id"))
        pueblo = "" if pueblo == "el pueblo" else pueblo
    lugar = ev.get("location_name") or ""
    if lugar and pueblo and pueblo.lower() not in lugar.lower():
        donde = f"{lugar} · {pueblo}"
    else:
        donde = lugar or pueblo or "Lugar por confirmar"

    # 💶 Gratis · 👥 Quedan 8 plazas
    detalles = [f"💶 {_format_precio_es(ev.get('price'))}"]
    maximo, apuntados = ev.get("max_capacity"), ev.get("confirmed_count") or 0
    abierto = not _evento_terminado(ev) and ev.get("status") not in ("cancelado", "pospuesto")
    if maximo and abierto:
        libres = max(0, int(maximo) - int(apuntados))
        detalles.append("👥 Completo (lista de espera)" if libres == 0
                        else f"👥 Queda{'n' if libres != 1 else ''} {libres} plaza{'s' if libres != 1 else ''}")

    # Estado, en una línea sencilla y en cursiva (sin recuadro)
    if ev.get("status") == "cancelado":
        estado = "❌ Este plan se ha cancelado"
    elif ev.get("status") == "pospuesto":
        estado = "⏸️ Este plan está aplazado"
    elif _evento_terminado(ev) and estado_insc == "valorada":
        estado = "🏁 Ya se celebró · ✅ ya lo valoraste, ¡gracias!"
    elif _evento_terminado(ev):
        estado = ("🏁 Ya se celebró · ¿qué tal fue? Puntúalo con las estrellas 👇"
                  if estado_insc == "confirmada" else "🏁 Ya se celebró")
    elif estado_insc == "confirmada":
        estado = "✅ Ya estás apuntado/a"
    elif estado_insc == "lista_de_espera":
        estado = "⏳ Estás en la lista de espera"
    else:
        estado = ""

    lineas = [f"{icono} <b>{escape_html(str(titulo))}</b>"]
    if categoria:
        lineas.append(f"<i>{escape_html(categoria)}</i>")
    lineas += [
        "",
        f"📅 {escape_html(fecha_txt)}",
        f"📍 {escape_html(donde)}",
        escape_html("   ·   ".join(detalles)),
    ]
    if estado:
        lineas += ["", f"<i>{escape_html(estado)}</i>"]
    return "\n".join(lineas)


async def _enviar_respuesta(
    chat_id: int,
    texto: str,
    buttons: list[list[tuple[str, str]]] | None = None,
    perfil: dict[str, Any] | None = None,
    force_audio: bool = False,
) -> None:
    await send_message(chat_id, texto, buttons=buttons, escape=True)
    if _wants_audio(perfil, force_audio=force_audio):
        await _enviar_audio(chat_id, texto)


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
                f"✅ ¡Hola, {nombre}! Tu cuenta ya está conectada a Telegram.\n\n"
                "A partir de ahora te avisaré de tus planes y podrás apuntarte desde aquí. "
                "¿En qué puedo ayudarte?",
                buttons=MENU_PRINCIPAL,
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
    if perfil is not None and not perfil.get("full_name") and first_name:
        perfil = {**perfil, "full_name": first_name}
    await _saludar(chat_id, perfil)


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
    intro: str | None = None,
    extra: str | None = None,
) -> None:
    """Manda una tarjeta por evento. Si el usuario habló por voz, también un audio que
    empieza por `intro` (lo mismo que el texto de cabecera) y acaba con `extra`."""
    event_ids = [str(ev["id"]) for ev in eventos if ev.get("id")]
    inscritos_map = _obtener_estados_inscripcion(perfil, event_ids)

    for ev in eventos:
        ev_id = str(ev.get("id") or "")
        estado_insc = inscritos_map.get(ev_id) if ev_id else None
        botones = _botones_evento(ev_id, estado_insc, ev) if ev_id else None
        html_body = _render_tarjeta_evento_html(ev, estado_insc=estado_insc)
        await send_message(chat_id, html_body, buttons=botones, escape=False)

    if force_audio:
        await _enviar_audio(
            chat_id,
            (intro or "Estos son los planes:") + " " + _resumen_hablado(eventos, inscritos_map)
            + (" " + extra if extra else ""),
        )


def _fecha_hablada(iso: str | None) -> str:
    """'sábado 12 de diciembre a las 7 y media de la tarde': fácil de entender en un audio."""
    dt = _parse_dt(iso)
    if not dt:
        return "fecha por confirmar"
    d = dt.astimezone(MADRID_TZ)
    h, m = d.hour, d.minute
    momento = ("de la madrugada" if h < 6 else "de la mañana" if h < 12 else
               "del mediodía" if h < 14 else "de la tarde" if h < 21 else "de la noche")
    h12 = h % 12 or 12
    minutos = {0: "", 15: " y cuarto", 30: " y media", 45: " menos cuarto"}.get(m)
    if m == 45:
        h12 = h12 % 12 + 1
    if minutos is None:
        minutos = f" y {m}"
    hora = f"a {'la' if h12 == 1 else 'las'} {h12}{minutos} {momento}"
    anio = f" de {d.year}" if d.year != datetime.now(MADRID_TZ).year else ""
    return f"{DIAS_SEMANA[d.weekday()].lower()} {d.day} de {MESES_ES[d.month - 1]}{anio} {hora}"


def _frase_evento(ev: dict[str, Any], estado_insc: str | None = None) -> str:
    """Un evento contado en voz alta: qué, cuándo, dónde (con pueblo) y cómo está."""
    fecha = _fecha_hablada(ev.get("starts_at"))
    pueblo = ev.get("municipio") or (
        _get_municipio_nombre(ev.get("municipio_id")) if ev.get("municipio_id") else "")
    lugar = ev.get("location_name") or ""
    donde = f"{lugar}, en {pueblo}" if lugar and pueblo and pueblo != "el pueblo" else (lugar or pueblo)
    frase = f"{ev.get('title') or 'Plan'}, el {fecha}" + (f", en {donde}" if donde else "")
    if ev.get("status") == "cancelado":
        frase += ". Está cancelado"
    elif _evento_terminado(ev):
        frase += ". Ya se celebró"
    elif estado_insc == "confirmada":
        frase += ". Ya estás apuntado"
    elif estado_insc == "lista_de_espera":
        frase += ". Estás en lista de espera"
    return frase + "."


def _resumen_hablado(eventos: list[dict[str, Any]], estados: dict[str, str] | None = None) -> str:
    return " ".join(_frase_evento(ev, (estados or {}).get(str(ev.get("id")))) for ev in eventos)


async def _enviar_audio(chat_id: int, texto: str) -> None:
    """Manda `texto` como nota de voz (solo se llama si el usuario habló por voz)."""
    voz_mod = _get_voz_module()
    if not voz_mod or not hasattr(voz_mod, "texto_a_voz"):
        return
    try:
        mp3 = await _maybe_await(voz_mod.texto_a_voz(texto))
        if mp3:
            await send_audio(chat_id, mp3, caption="🔊 Pulsa para escuchar")
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

    cab = "Próximos planes en los pueblos"
    await send_message(chat_id, f"📅 <b>{cab}</b> (los {len(rows)} más cercanos)", escape=False)
    await _enviar_tarjetas_eventos(chat_id, rows, perfil=perfil, force_audio=force_audio,
                                   intro=f"{cab}:")
    await send_message(chat_id, "¿Buscas algo en concreto? Pregúntame, por ejemplo: "
                                "«¿qué hay de senderismo en Velefique?» 🙂",
                       buttons=[[("✨ Recomiéndame", "cmd:recomendar"), ("❓ Ayuda", "cmd:ayuda")]])


async def _cmd_mis_planes(
    chat_id: int, perfil: dict[str, Any] | None, force_audio: bool = False
) -> None:
    if not perfil:
        await _enviar_respuesta(chat_id, "⚠️ Primero escribe /start para empezar.",
                                force_audio=force_audio)
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
            force_audio=force_audio,
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
        await send_message(chat_id, f"🏁 <b>Planes en los que has participado</b> ({len(vividos)})", escape=False)
        await _tarjetas(vividos)
    if proximos:
        await send_message(chat_id, f"📋 <b>Tus próximos planes</b> ({len(proximos)})", escape=False)
        await _tarjetas(proximos)
    if cancelados:
        await send_message(
            chat_id,
            "❌ Se cancelaron estos planes a los que te apuntaste:\n"
            + "\n".join(f"• {e.get('title')} ({_format_fecha_es(e.get('starts_at'))})" for e in cancelados),
        )
    if not (vividos or proximos or cancelados):
        await send_message(chat_id, "📋 Todavía no has participado en ningún plan.")

    if force_audio:  # lo mismo, contado en voz alta
        partes = []
        if vividos:
            partes.append(f"Has participado en {len(vividos)} plan{'es' if len(vividos) > 1 else ''}: "
                          + _resumen_hablado(vividos[:5]))
        if proximos:
            partes.append(f"Estás apuntado a {len(proximos)} plan{'es' if len(proximos) > 1 else ''} "
                          "que aún no se han hecho: " + _resumen_hablado(proximos[:5], estado_de))
        if cancelados:
            partes.append("Se cancelaron: " + " ".join(f"{e.get('title')}." for e in cancelados))
        await _enviar_audio(chat_id, " ".join(partes) or "Todavía no has participado en ningún plan.")


# ══════════════════════════════════════════════════════════════
#  Gestión de propuestas: mis propuestas, editar, cancelar y moderar por Telegram
# ══════════════════════════════════════════════════════════════
_ESTADO_PROPUESTA = {
    "borrador_ia": "🕓 En revisión: un moderador tiene que completarlo o confirmarlo",
    "pendiente_moderacion": "🕓 En revisión del moderador",
    "abierto": "✅ Publicado", "completo": "✅ Publicado (completo)",
    "inscripcion_cerrada": "✅ Publicado (inscripción cerrada)", "en_curso": "▶️ En curso",
    "finalizado": "🏁 Ya se celebró", "cancelado": "❌ Cancelado", "pospuesto": "⏸️ Aplazado",
    "rechazado": "🚫 No se ha publicado",
}
_CAMPOS_EDITABLES = {
    "starts_at": ("📅 Fecha y hora", "📅 ¿Cuál es la nueva fecha y hora? (por ejemplo: «sábado 12 de diciembre a las 18:00»)"),
    "location_name": ("📍 Lugar", "📍 ¿Dónde es ahora?"),
    "price": ("💶 Precio", "💶 ¿Cuánto cuesta? (o «gratis»)"),
    "max_capacity": ("👥 Plazas", "👥 ¿Cuántas plazas hay? (o «sin límite»)"),
    "title": ("🏷️ Título", "🏷️ ¿Qué título le ponemos?"),
    "description": ("📝 Descripción", "📝 Escribe la nueva descripción."),
}
ESPERANDO_EDICION: dict[int, dict[str, str]] = {}   # chat -> {event_id, campo}
ESPERANDO_MOTIVO: dict[int, str] = {}                # chat (moderador) -> event_id a rechazar


def _es_moderador(perfil: dict[str, Any] | None) -> bool:
    return bool(perfil) and perfil.get("rol") == "moderador"


def _puede_gestionar(perfil: dict[str, Any] | None, ev: dict[str, Any] | None) -> bool:
    return bool(perfil and ev) and (ev.get("created_by") == perfil.get("id") or _es_moderador(perfil))


def _ya_empezo(ev: dict[str, Any]) -> bool:
    inicio = _parse_dt(ev.get("starts_at"))
    return bool(inicio and inicio <= datetime.now(timezone.utc))


async def _avisar_a(user_id: str | None, texto: str,
                    buttons: list[list[tuple[str, str]]] | None = None) -> bool:
    if not user_id:
        return False
    try:
        return await avisar_usuario(str(user_id), texto, buttons)
    except Exception:
        return False


async def _avisar_apuntados(event_id: str, texto: str) -> int:
    """Avisa a todos los apuntados (confirmados y en lista de espera) de un cambio."""
    filas = sb.table("inscripciones").select("user_id").eq("event_id", event_id) \
        .in_("status", ["confirmada", "lista_de_espera"]).execute().data or []
    n = 0
    for f in filas:
        if await _avisar_a(f.get("user_id"), texto):
            n += 1
    return n


def _tarjeta_propuesta(ev: dict[str, Any], para_moderador: bool = False) -> str:
    html_txt = _render_tarjeta_evento_html(ev)
    estado = _ESTADO_PROPUESTA.get(str(ev.get("status")), str(ev.get("status")))
    extra = f"\n\n<b>{escape_html(estado)}</b>"
    if ev.get("status") == "rechazado" and ev.get("moderation_note"):
        extra += f"\n<i>Motivo: {escape_html(str(ev['moderation_note']))}</i>"
    if para_moderador:
        flags = ev.get("ai_flags") or {}
        if isinstance(flags, dict):
            if flags.get("revision"):
                extra += f"\n<i>Por qué se revisa: {escape_html(str(flags['revision']))}</i>"
            falta = [c for c in (flags.get("missing") or []) if c not in ("max_capacity",)]
            if falta:
                extra += f"\n<i>Falta: {escape_html(', '.join(falta))}</i>"
            if flags.get("dudas"):
                extra += f"\n<i>Dudas de la IA: {escape_html('; '.join(map(str, flags['dudas']))[:200])}</i>"
        if ev.get("source_url"):
            extra += f"\n🔗 {escape_html(str(ev['source_url']))}"
    return html_txt + extra


def _botones_propuesta(ev: dict[str, Any]) -> list[list[tuple[str, str]]] | None:
    if ev.get("status") in ("cancelado", "rechazado", "finalizado") or _ya_empezo(ev):
        return None
    return [[("✏️ Editar", f"edit:{ev['id']}"), ("❌ Cancelar evento", f"cancelev:{ev['id']}")]]


async def _cmd_mis_propuestas(chat_id: int, perfil: dict[str, Any] | None,
                              force_audio: bool = False) -> None:
    if not perfil:
        await send_message(chat_id, "⚠️ Primero escribe /start para empezar.")
        return
    evs = sb.table("eventos").select("*").eq("created_by", perfil["id"]) \
        .order("starts_at", desc=True).limit(10).execute().data or []
    sin_confirmar = sb.table("ingestas").select("id,extracted,estado").eq("user_id", perfil["id"]) \
        .eq("origen", "telegram").in_("estado", ["esperando_confirmacion", "extraida"]) \
        .is_("event_id", "null").order("created_at", desc=True).limit(5).execute().data or []
    if not evs and not sin_confirmar:
        await _enviar_respuesta(chat_id, "📝 Todavía no has propuesto ningún plan. "
                                         "¿Quieres proponer uno?",
                                buttons=[[("📝 Proponer un plan", "cmd:proponer")]],
                                perfil=perfil, force_audio=force_audio)
        return
    await send_message(chat_id, f"🗂️ <b>Tus propuestas</b> ({len(evs) + len(sin_confirmar)})", escape=False)
    for ev in evs:
        await send_message(chat_id, _tarjeta_propuesta(ev), buttons=_botones_propuesta(ev), escape=False)
    for ing in sin_confirmar:
        titulo = (ing.get("extracted") or {}).get("title") or "Plan sin título"
        await send_message(chat_id, f"📝 «{titulo}»: aún no lo has confirmado. "
                                    "Revisa el resumen que te mandé y pulsa «Sí, publícalo».")
    if force_audio:
        resumen = " ".join(f"{e.get('title')}: {_ESTADO_PROPUESTA.get(str(e.get('status')), '')}."
                           for e in evs[:5])
        await _enviar_audio(chat_id, f"Tienes {len(evs)} propuestas. {resumen}")


async def _menu_editar(chat_id: int, perfil: dict[str, Any] | None, event_id: str) -> None:
    ev = _get_evento(event_id)
    if not _puede_gestionar(perfil, ev):
        await send_message(chat_id, "⚠️ Solo quien organiza el plan (o un moderador) puede cambiarlo.")
        return
    if ev.get("status") in ("cancelado", "finalizado") or _ya_empezo(ev):
        await send_message(chat_id, "⚠️ Este plan ya empezó, terminó o está cancelado: no se puede cambiar.")
        return
    botones = [[(etq, f"editc:{event_id}:{campo}")] for campo, (etq, _) in _CAMPOS_EDITABLES.items()]
    await send_message(chat_id, f"✏️ ¿Qué quieres cambiar de «{ev.get('title')}»?", buttons=botones)


async def _aplicar_edicion(chat_id: int, perfil: dict[str, Any] | None, respuesta: str,
                           force_audio: bool = False) -> None:
    pendiente = ESPERANDO_EDICION.pop(chat_id, None)
    if not pendiente:
        return
    ev = _get_evento(pendiente["event_id"])
    campo = pendiente["campo"]
    if not _puede_gestionar(perfil, ev):
        await send_message(chat_id, "⚠️ No puedes cambiar este plan.")
        return
    respuesta = (respuesta or "").strip()
    ingesta_mod = _get_ingesta_module()
    cambios: dict[str, Any] = {}
    if campo == "price" and ingesta_mod:
        v = ingesta_mod.interpretar_precio(respuesta)
        if v is not None:
            cambios["price"] = v
    elif campo == "max_capacity" and ingesta_mod:
        v = ingesta_mod.interpretar_plazas(respuesta)
        if v == getattr(ingesta_mod, "_SIN_LIMITE", object()):
            cambios["max_capacity"] = None
        elif v is not None:
            cambios["max_capacity"] = v
    elif campo in ("title", "location_name", "description") and respuesta:
        cambios[campo] = respuesta[:500 if campo == "description" else 200]
    elif campo == "starts_at" and respuesta:
        ia_mod = _get_ia_module()
        nuevo = ia_mod.extraer_evento(f"{ev.get('title')}. Nueva fecha y hora: {respuesta}") if ia_mod else {}
        if nuevo.get("starts_at"):
            cambios["starts_at"] = nuevo["starts_at"]
            antes, despues = _parse_dt(ev.get("starts_at")), _parse_dt(nuevo["starts_at"])
            if ev.get("ends_at") and antes and despues:  # misma duración
                fin = _parse_dt(ev["ends_at"])
                cambios["ends_at"] = (fin + (despues - antes)).isoformat() if fin else None
    if not cambios:
        ESPERANDO_EDICION[chat_id] = pendiente  # se le vuelve a preguntar
        await _enviar_respuesta(chat_id, "🤔 No lo he entendido. " + _CAMPOS_EDITABLES[campo][1],
                                perfil=perfil, force_audio=force_audio)
        return
    cambios["sequence"] = int(ev.get("sequence") or 0) + 1  # el .ics del calendario se actualiza
    sb.table("eventos").update(cambios).eq("id", ev["id"]).execute()
    ev = _get_evento(ev["id"]) or ev
    await send_message(chat_id, "✅ Cambio guardado:\n\n" + _render_tarjeta_evento_html(ev),
                       buttons=_botones_propuesta(ev), escape=False)
    if force_audio:
        await _enviar_audio(chat_id, f"Cambio guardado en {ev.get('title')}.")
    if ev.get("status") not in ("pendiente_moderacion", "borrador_ia", "rechazado"):
        que = _CAMPOS_EDITABLES[campo][0].split(" ", 1)[1].lower()
        n = await _avisar_apuntados(ev["id"], f"ℹ️ Ha cambiado {que} de «{ev.get('title')}»:\n\n"
                                              + re.sub(r"<[^>]+>", "", _render_tarjeta_evento_html(ev)))
        if n:
            await send_message(chat_id, f"📣 He avisado a {n} persona(s) apuntada(s).")


async def _cancelar_evento(chat_id: int, perfil: dict[str, Any] | None, event_id: str) -> None:
    ev = _get_evento(event_id)
    if not _puede_gestionar(perfil, ev):
        await send_message(chat_id, "⚠️ Solo quien organiza el plan (o un moderador) puede cancelarlo.")
        return
    if ev.get("status") in ("cancelado", "finalizado") or _ya_empezo(ev):
        await send_message(chat_id, "⚠️ Este plan ya empezó, terminó o estaba cancelado.")
        return
    sb.table("eventos").update({"status": "cancelado", "sequence": int(ev.get("sequence") or 0) + 1}) \
        .eq("id", event_id).execute()
    n = await _avisar_apuntados(event_id, f"❌ Se ha cancelado «{ev.get('title')}» "
                                          f"({_format_fecha_es(ev.get('starts_at'))}). Sentimos las molestias.")
    await send_message(chat_id, f"❌ «{ev.get('title')}» cancelado."
                                + (f" He avisado a {n} persona(s) apuntada(s)." if n else ""))


# ── Moderar por Telegram ──────────────────────────────────────
def _moderadores_con_telegram() -> list[dict[str, Any]]:
    return sb.table("profiles").select("id,telegram_chat_id,full_name").eq("rol", "moderador") \
        .not_.is_("telegram_chat_id", "null").execute().data or []


def _botones_moderar(event_id: str) -> list[list[tuple[str, str]]]:
    return [[("✅ Aprobar", f"mod_ok:{event_id}"), ("🚫 Rechazar", f"mod_no:{event_id}")],
            [("✏️ Editar antes", f"edit:{event_id}")]]


async def avisar_moderadores(event_id: str) -> int:
    """Una propuesta queda pendiente -> los moderadores la reciben con botones. -> nº avisados."""
    ev = _get_evento(event_id)
    if not ev:
        return 0
    n = 0
    for m in _moderadores_con_telegram():
        if str(m.get("id")) == str(ev.get("created_by")):
            continue  # no se modera a sí mismo
        try:
            await send_message(int(m["telegram_chat_id"]),
                               "🛡️ <b>Propuesta para revisar</b>\n\n" + _tarjeta_propuesta(ev, True),
                               buttons=_botones_moderar(event_id), escape=False)
            n += 1
        except Exception:
            pass
    return n


async def _cmd_moderar(chat_id: int, perfil: dict[str, Any] | None) -> None:
    if not _es_moderador(perfil):
        await send_message(chat_id, "⚠️ Esta opción es solo para moderadores.")
        return
    evs = sb.table("eventos").select("*").in_("status", ["pendiente_moderacion", "borrador_ia"]) \
        .order("created_at").limit(10).execute().data or []
    if not evs:
        await send_message(chat_id, "🛡️ No hay propuestas pendientes de revisar. ¡Todo al día!")
        return
    await send_message(chat_id, f"🛡️ <b>Propuestas pendientes</b> ({len(evs)})", escape=False)
    for ev in evs:
        await send_message(chat_id, _tarjeta_propuesta(ev, True),
                           buttons=_botones_moderar(ev["id"]), escape=False)


def _marcar_aviso_moderacion(ev: dict[str, Any]) -> None:
    if ev.get("created_by"):
        try:
            sb.table("notificaciones").upsert(
                {"user_id": ev["created_by"], "event_id": ev["id"], "tipo": "moderacion",
                 "canal": "telegram", "motivo": ev.get("status")},
                on_conflict="user_id,event_id,tipo").execute()
        except Exception:
            pass


async def _moderar(chat_id: int, perfil: dict[str, Any] | None, event_id: str,
                   aprobar: bool, motivo: str | None = None) -> None:
    if not _es_moderador(perfil):
        await send_message(chat_id, "⚠️ Solo los moderadores pueden aprobar o rechazar.")
        return
    ev = _get_evento(event_id)
    if not ev or ev.get("status") not in ("pendiente_moderacion", "borrador_ia"):
        await send_message(chat_id, "ℹ️ Esta propuesta ya la ha revisado otra persona.")
        return
    if aprobar:
        if not (ev.get("title") and ev.get("starts_at") and ev.get("municipio_id")):
            await send_message(chat_id, "⚠️ Le faltan datos (título, fecha u hora, pueblo). "
                                        "Edítalo antes de aprobarlo.", buttons=[[("✏️ Editar", f"edit:{event_id}")]])
            return
        sb.table("eventos").update({"status": "abierto", "moderation_note": f"Aprobado por {perfil.get('full_name') or 'un moderador'}"}) \
            .eq("id", event_id).execute()
        try:
            sb.rpc("actualizar_estados_eventos", {}).execute()  # por si ya está completo, etc.
        except Exception:
            pass
        ev = _get_evento(event_id) or ev
        await send_message(chat_id, f"✅ «{ev.get('title')}» aprobado y publicado.")
        await _avisar_a(ev.get("created_by"), f"🎉 ¡Tu plan «{ev.get('title')}» ya está publicado! "
                                              "Los vecinos ya pueden verlo y apuntarse.")
    else:
        motivo = (motivo or "").strip() or "No cumple las normas de la agenda"
        sb.table("eventos").update({"status": "rechazado", "moderation_note": motivo[:300]}) \
            .eq("id", event_id).execute()
        ev = _get_evento(event_id) or ev
        await send_message(chat_id, f"🚫 «{ev.get('title')}» rechazado. Se lo he explicado a quien lo propuso.")
        await _avisar_a(ev.get("created_by"), f"🚫 Tu plan «{ev.get('title')}» no se ha publicado.\n"
                                              f"Motivo: {motivo}\n\nSi quieres, corrígelo y vuelve a proponerlo.")
    _marcar_aviso_moderacion(ev)


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
            "🤔 Perdona, no te he entendido bien. Prueba a decírmelo de otra forma, por ejemplo: «¿qué hay este finde en Tahal?», o elige una opción:",
            buttons=MENU_PRINCIPAL,
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
            if len(cabecera) <= 140 and cabecera.endswith(":"):  # "Hay 3 planes…:" -> título
                await send_message(chat_id, f"🔎 <b>{escape_html(cabecera)}</b>", escape=False)
            else:  # respuesta redactada por la IA: texto normal, con sus **negritas**
                html_txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape_html(cabecera))
                await send_message(chat_id, "💬 " + html_txt.replace("*", ""), escape=False)
        await _enviar_tarjetas_eventos(
            chat_id,
            eventos_validos,
            perfil=perfil,
            force_audio=force_audio,
            intro=cabecera or None,
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
_RE_MIS_PROPUESTAS = re.compile(
    r"\b(mis propuestas|lo que he propuesto|que he propuesto|planes que he propuesto|"
    r"eventos que he (creado|propuesto|organizado)|mis eventos (creados|propuestos)|"
    r"(editar|cambiar|modificar|cancelar|anular) (mi|el) (evento|plan))\b"
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

    ya_txt = ("✅ Y ya estás apuntado/a a:\n"
              + "\n".join(f"• {e.get('title')} — {_format_fecha_es(e.get('starts_at'))}" for e in ya)) if ya else ""
    if candidatos:
        if gustos:
            top = ", ".join(_NOMBRE_CATEGORIA.get(c, c) for c, _ in gustos.most_common(3))
            cab = f"✨ Por lo que sueles apuntarte ({top}), te recomiendo {rango}:"
        else:
            cab = f"✨ Aún no sé qué te gusta (no te has apuntado a nada). Estos son los planes {rango}:"
        cab = cab.replace(" :", ":").replace("  ", " ")
        await send_message(chat_id, f"<b>{escape_html(cab)}</b>", escape=False)
        await _enviar_tarjetas_eventos(
            chat_id, candidatos[:4], perfil=perfil, force_audio=force_audio, intro=cab,
            extra=("Y ya estás apuntado a: " + _resumen_hablado(ya)) if ya else None,
        )
        if ya_txt:
            await send_message(chat_id, ya_txt)
    elif ya:
        await send_message(chat_id, ya_txt)
        if force_audio:
            await _enviar_audio(chat_id, "No hay más planes que recomendarte en esas fechas. "
                                         "Ya estás apuntado a: " + _resumen_hablado(ya))


# ── Conversación: saludo, gracias, ayuda y menú principal ─────────────────────
MENU_PRINCIPAL: list[list[tuple[str, str]]] = [
    [("📅 Próximos planes", "cmd:agenda"), ("✨ Recomiéndame", "cmd:recomendar")],
    [("📋 Mis planes", "cmd:mis_planes"), ("⭐ Valorar un plan", "cmd:valorar")],
    [("📝 Proponer un plan", "cmd:proponer"), ("🗂️ Mis propuestas", "cmd:mis_propuestas")],
    [("❓ Ayuda", "cmd:ayuda")],
]
_RE_SALUDO = re.compile(r"^(hola+|holi|buenas|buenos dias|buenas tardes|buenas noches|hey|ey|"
                        r"saludos|que tal|que pasa|wenas)\b")
_RE_GRACIAS = re.compile(r"^(gracias|muchas gracias|mil gracias|vale|ok|okey|perfecto|genial|"
                         r"estupendo|de acuerdo|entendido)\b")
_RE_AYUDA = re.compile(r"^(ayuda|menu|help|opciones|que puedes hacer|que sabes hacer|"
                       r"como funciona|como funcionas|como te uso|para que sirves)\b")

TEXTO_EJEMPLOS = (
    "Puedes escribirme o mandarme una nota de voz 🎤 con lo que necesites. Por ejemplo:\n\n"
    "🔎 «¿Qué hay este fin de semana en Tahal?»\n"
    "✨ «Recomiéndame algo para la semana que viene»\n"
    "🎟️ «Quiero apuntarme a las migas solidarias»\n"
    "📋 «¿A qué planes estoy apuntado?»\n"
    "⭐ «Quiero valorar un plan al que fui»\n"
    "📝 «Propongo una ruta el sábado en Senés»"
)
TEXTO_PROPONER = ("📝 ¡Genial que quieras organizar algo!\n\n"
                  "Mándame una foto del cartel 📸, o cuéntame el plan por escrito o con una nota "
                  "de voz 🎤: qué es, en qué pueblo, qué día y a qué hora, dónde se queda, "
                  "cuánto cuesta y cuántas plazas hay.\n\n"
                  "Te enseñaré un resumen para que lo confirmes antes de publicarlo.")
AUDIO_EJEMPLOS = ("Puedes preguntarme qué planes hay, pedirme que te recomiende algo, apuntarte a un "
                  "plan, ver tus planes, valorar uno al que fuiste o proponer uno nuevo. "
                  "Escríbeme o mándame una nota de voz.")


def _nombre_de(perfil: dict[str, Any] | None) -> str:
    nombre = ((perfil or {}).get("full_name") or "").strip()
    return f", {nombre.split()[0]}" if nombre else ""


async def _saludar(chat_id: int, perfil: dict[str, Any] | None, force_audio: bool = False) -> None:
    texto = (f"👋 ¡Hola{_nombre_de(perfil)}! Soy el asistente de <b>Reactiva tu pueblo</b>.\n"
             f"¿En qué puedo ayudarte?\n\n{escape_html(TEXTO_EJEMPLOS)}\n\n"
             "O usa los botones de abajo 👇")
    await send_message(chat_id, texto, buttons=MENU_PRINCIPAL, escape=False)
    if force_audio:
        await _enviar_audio(chat_id, f"¡Hola{_nombre_de(perfil)}! Soy el asistente de Reactiva tu "
                                     f"pueblo. ¿En qué puedo ayudarte? {AUDIO_EJEMPLOS}")


async def _mostrar_ayuda(chat_id: int, force_audio: bool = False) -> None:
    texto = ("ℹ️ <b>Esto es lo que puedo hacer por ti</b>\n\n" + escape_html(TEXTO_EJEMPLOS)
             + "\n\n<b>Comandos</b>\n/agenda · próximos planes\n/mis_planes · tus planes\n"
             "/proponer · proponer un plan\n/mis_propuestas · lo que has propuesto (editar o cancelar)\n"
             "/moderar · propuestas por revisar (moderadores)\n/ayuda · esta ayuda")
    await send_message(chat_id, texto, buttons=MENU_PRINCIPAL, escape=False)
    if force_audio:
        await _enviar_audio(chat_id, AUDIO_EJEMPLOS)


async def _charla(chat_id: int, perfil: dict[str, Any] | None, texto: str,
                  force_audio: bool = False) -> bool:
    """Contesta saludos, gracias y peticiones de ayuda. True si lo ha contestado."""
    n = re.sub(r"[^a-z0-9 ]", " ", _norm_txt(texto)).strip()
    n = re.sub(r"\s+", " ", n)
    if not n:
        return False
    # Lo que queda al quitar saludos y muletillas: si queda algo, es una petición de verdad
    # ("hola, ¿qué hay este finde?") y la contesta el resto del bot
    resto = re.sub(r"\b(hola+|holi|buenas|buenos|dias|tardes|noches|hey|ey|saludos|que tal|"
                   r"que pasa|wenas|como estas|como va|gracias|muchas|mil|vale|ok|okey|perfecto|"
                   r"genial|estupendo|de acuerdo|entendido|asistente|bot|por favor)\b", " ", n)
    solo_charla = not resto.strip()
    if _RE_AYUDA.match(n) and len(n.split()) <= 5:
        await _mostrar_ayuda(chat_id, force_audio=force_audio)
        return True
    if _RE_SALUDO.match(n) and solo_charla:
        await _saludar(chat_id, perfil, force_audio=force_audio)
        return True
    if _RE_GRACIAS.match(n) and solo_charla:
        await _enviar_respuesta(chat_id, "😊 ¡De nada! ¿Te ayudo con algo más?",
                                buttons=MENU_PRINCIPAL, perfil=perfil, force_audio=force_audio)
        return True
    return False


def _tiene_detalles_de_plan(texto: str) -> bool:
    """¿Cuenta ya algo del plan (día, hora, sitio...) o solo dice que quiere proponer uno?"""
    n = _norm_txt(texto)
    if len(n.split()) < 6:
        return False
    return bool(re.search(
        r"\d|lunes|martes|miercoles|jueves|viernes|sabado|domingo|manana|pasado|semana|finde|"
        r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|"
        r"diciembre|a las|plaza|calle|ermita|iglesia|polideportivo|en (?:senes|tahal|velefique|"
        r"uleila|lucainena)", n))


async def _crear_propuesta(chat_id: int, perfil: dict[str, Any] | None, texto: str,
                           es_audio: bool = False) -> None:
    """Guarda la propuesta, la IA saca los datos y se le pide confirmación al autor.
    Si no se puede sacar un plan, se le dice (antes se quedaba callado)."""
    if not perfil:
        perfil = await _asegurar_perfil_telegram(chat_id)
    filas = sb.table("ingestas").insert({
        "origen": "telegram", "user_id": perfil["id"] if perfil else None,
        "raw_text": texto, "estado": "nueva",
    }).execute().data or []
    ingesta_id = filas[0]["id"] if filas else None
    ingesta_mod = _get_ingesta_module()
    if not ingesta_id or not ingesta_mod or not hasattr(ingesta_mod, "procesar_ingesta"):
        await _enviar_respuesta(chat_id, "📝 He guardado tu propuesta. Un moderador la revisará.",
                                perfil=perfil, force_audio=es_audio)
        return
    await send_chat_action(chat_id, "typing")
    if es_audio:  # el resumen para confirmar también se le dirá en voz alta
        CHATS_CON_VOZ.add(chat_id)
    await _maybe_await(ingesta_mod.procesar_ingesta(ingesta_id))
    fila = (sb.table("ingestas").select("estado").eq("id", ingesta_id).execute().data or [{}])[0]
    estado = fila.get("estado")
    if estado == "esperando_confirmacion":
        return  # ya le ha llegado el resumen con "¿Publico este plan?"
    CHATS_CON_VOZ.discard(chat_id)
    if estado == "spam":
        await _enviar_respuesta(
            chat_id,
            "🚫 Tu mensaje parece publicidad o spam, así que no lo publico. Aquí solo se "
            "comparten planes y actividades de los pueblos de la Mancomunidad "
            "(Lucainena de las Torres, Senés, Tahal, Uleila del Campo y Velefique).",
            perfil=perfil, force_audio=es_audio)
        return
    if estado == "descartada":
        motivo = (sb.table("ingestas").select("spam_reason").eq("id", ingesta_id).execute().data
                  or [{}])[0].get("spam_reason") or ""
        if re.search(r"fuera|mancomunidad|otro pueblo|no es de", motivo, re.I):
            await _enviar_respuesta(
                chat_id, "📍 Solo puedo publicar planes de los pueblos de la Mancomunidad: "
                         "Lucainena de las Torres, Senés, Tahal, Uleila del Campo y Velefique.",
                perfil=perfil, force_audio=es_audio)
            return
    if estado in ("descartada", "spam"):
        ESPERANDO_PROPUESTA.add(chat_id)
        await _enviar_respuesta(
            chat_id,
            "🤔 No he sabido sacar un plan de tu mensaje. Cuéntamelo con más detalle: qué es, "
            "en qué pueblo, qué día y a qué hora (por ejemplo: «Taller de esparto el sábado 12 "
            "de diciembre a las 17:00 en el centro social de Tahal, gratis»).",
            perfil=perfil, force_audio=es_audio)
    elif estado == "error":
        await _enviar_respuesta(chat_id, "⚠️ Ahora mismo no puedo leer tu propuesta. "
                                         "Inténtalo de nuevo en unos minutos.",
                                perfil=perfil, force_audio=es_audio)
    else:  # extraída pero sin poder pedir confirmación por aquí
        await _enviar_respuesta(chat_id, "📝 He recibido tu propuesta. Un moderador la "
                                         "revisará antes de publicarla.",
                                perfil=perfil, force_audio=es_audio)


def _planes_para_apuntarse(texto: str) -> list[dict[str, Any]]:
    """'Quiero apuntarme a las migas' -> planes que admiten inscripción, el que más se
    parece a lo dicho primero. Si no nombra ninguno, los más próximos."""
    rag_mod = _get_rag_module()
    if rag_mod and hasattr(rag_mod, "extraer_filtros"):
        try:
            f = rag_mod.extraer_filtros(texto)
            f.update({"pasado": False, "solo_finalizados": False})
            if not f.get("generica"):
                # Nombra un plan concreto ("el banco de libros"): la categoría que crean
                # adivinar las reglas no debe dejarlo fuera
                f["categoria"] = None
            if f.get("generica"):
                filas = rag_mod.buscar_por_fecha(f, limite=10)
            else:
                filas = rag_mod.buscar_eventos(texto, f, limite=10)
            ids = [str(r["event_id"]) for r in filas]
            if ids:
                evs = {str(e["id"]): e for e in
                       sb.table("eventos").select("*").in_("id", ids).execute().data or []}
                abiertos = [evs[i] for i in ids if i in evs and _evento_admite_inscripcion(evs[i])]
                if abiertos:
                    # Desempate por palabras del título ("ruta de las MINAS"): no depende
                    # de que los embeddings de la IA estén disponibles
                    vacias = getattr(rag_mod, "_VACIAS", set())
                    dichas = {t for t in re.findall(r"[a-z0-9]+", _norm_txt(texto))
                              if len(t) >= 4 and t not in vacias}

                    def coincidencias(e: dict[str, Any]) -> int:
                        titulo = re.findall(r"[a-z0-9]+", _norm_txt(e.get("title") or ""))
                        return sum(any(t.startswith(d[:5]) or d.startswith(t[:5]) for t in titulo
                                       if len(t) >= 4) for d in dichas)

                    abiertos.sort(key=coincidencias, reverse=True)  # estable: respeta el orden previo
                    return abiertos
        except Exception as e:
            print(f"[bot] búsqueda para apuntarse falló: {e!r}")
    # Plan B: los próximos planes abiertos
    evs = (
        sb.table("eventos").select("*").in_("status", ["abierto", "completo"])
        .gte("starts_at", datetime.now(timezone.utc).isoformat())
        .order("starts_at").limit(10).execute().data
        or []
    )
    return [e for e in evs if _evento_admite_inscripcion(e)]


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
        "⭐ Estos son los planes a los que fuiste y aún no has valorado. Pulsa las estrellas debajo de cada uno 👇",
    )
    for ev in valorables:
        await send_message(
            chat_id,
            _render_tarjeta_evento_html(ev, estado_insc="confirmada"),
            buttons=_botones_estrellas(str(ev["id"])),
            escape=False,
        )
    if force_audio:
        await _enviar_audio(
            chat_id,
            "Estos son los planes a los que fuiste y que puedes valorar: "
            + " ".join(_frase_evento(ev).replace(". Ya se celebró", "") for ev in valorables)
            + " Pulsa las estrellas debajo del que quieras valorar.",
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

    # Respuestas que el bot estaba esperando: un dato a editar o el motivo de un rechazo
    if chat_id in ESPERANDO_EDICION or chat_id in ESPERANDO_MOTIVO:
        dicho = (texto or "").strip()
        if not dicho and es_audio:
            ia_mod = _get_ia_module()
            if ia_mod and hasattr(ia_mod, "entender_mensaje"):
                dicho = ((await _maybe_await(ia_mod.entender_mensaje(audio_bytes=audio_bytes))) or {}).get("texto") or ""
        if _norm_txt(dicho).strip(" .!") in {"cancelar", "nada", "dejalo", "olvidalo"}:
            ESPERANDO_EDICION.pop(chat_id, None)
            ESPERANDO_MOTIVO.pop(chat_id, None)
            await send_message(chat_id, "👌 Vale, lo dejamos como estaba.")
            return
        if chat_id in ESPERANDO_MOTIVO:
            await _moderar(chat_id, perfil, ESPERANDO_MOTIVO.pop(chat_id), aprobar=False, motivo=dicho)
        else:
            await _aplicar_edicion(chat_id, perfil, dicho, force_audio=es_audio)
        return

    if chat_id in ESPERANDO_PROPUESTA:  # le pedimos los datos del plan: esto es la propuesta
        ESPERANDO_PROPUESTA.discard(chat_id)
        propuesta = (texto or "").strip()
        ia_mod = _get_ia_module()
        if not propuesta and es_audio and ia_mod and hasattr(ia_mod, "entender_mensaje"):
            parsed = await _maybe_await(ia_mod.entender_mensaje(audio_bytes=audio_bytes))
            propuesta = (parsed or {}).get("texto") or ""
        if propuesta and not await _charla(chat_id, perfil, propuesta, force_audio=es_audio):
            await _crear_propuesta(chat_id, perfil, propuesta, es_audio)
            return
        if propuesta:
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

    # Saludos, gracias y ayuda: se contestan como un asistente (sin gastar IA)
    if not es_audio and await _charla(chat_id, perfil, texto or "", force_audio=False):
        return
    if not es_audio and _norm_txt(texto or "").strip(" .!¡?¿") in {"agenda", "planes", "ver planes"}:
        await _cmd_agenda(chat_id, perfil=perfil, force_audio=False)
        return

    ia_mod = _get_ia_module()
    if not ia_mod or not hasattr(ia_mod, "entender_mensaje"):
        await _enviar_respuesta(
            chat_id,
            "🤔 Perdona, no te he entendido bien. Prueba a decírmelo de otra forma, por ejemplo: «¿qué hay este finde en Tahal?», o elige una opción:",
            buttons=MENU_PRINCIPAL,
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
            "🤔 Perdona, no te he entendido bien. Prueba a decírmelo de otra forma, por ejemplo: «¿qué hay este finde en Tahal?», o elige una opción:",
            buttons=MENU_PRINCIPAL,
            perfil=perfil,
            force_audio=es_audio,
        )
        return

    if not isinstance(resultado, dict):
        await _enviar_respuesta(
            chat_id,
            "🤔 Perdona, no te he entendido bien. Prueba a decírmelo de otra forma, por ejemplo: «¿qué hay este finde en Tahal?», o elige una opción:",
            buttons=MENU_PRINCIPAL,
            perfil=perfil,
            force_audio=es_audio,
        )
        return

    intencion = resultado.get("intencion", "otro")
    texto_reconocido = (resultado.get("texto") or texto or "").strip()

    # Nota de voz que solo era un saludo / gracias / pedir ayuda
    if es_audio and await _charla(chat_id, perfil, texto_reconocido, force_audio=True):
        return

    # Quiere dejar una reseña: NO se busca en la agenda (si no, salen tarjetas para apuntarse)
    if _RE_QUIERE_VALORAR.search(_norm_txt(texto_reconocido)):
        await _manejar_resena_libre(chat_id, perfil, texto_reconocido, force_audio=es_audio)
        return

    # Preguntas sobre lo suyo: "en los que he participado", "a qué estoy apuntado"
    norm = _norm_txt(texto_reconocido)
    if _RE_MIS_PROPUESTAS.search(norm):
        if not perfil:
            perfil = await _asegurar_perfil_telegram(chat_id)
        await _cmd_mis_propuestas(chat_id, perfil, force_audio=es_audio)
        return
    if _RE_MIS_EVENTOS.search(norm):
        if not perfil:
            perfil = await _asegurar_perfil_telegram(chat_id)
        await _cmd_mis_planes(chat_id, perfil=perfil, force_audio=es_audio)
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
        if not _tiene_detalles_de_plan(texto_reconocido):
            # "Quiero proponer un evento": aún no ha dicho cuál -> se le piden los datos
            # y su siguiente mensaje (o nota de voz) se toma como la propuesta
            ESPERANDO_PROPUESTA.add(chat_id)
            await _enviar_respuesta(chat_id, TEXTO_PROPONER, perfil=perfil, force_audio=es_audio)
        else:
            await _crear_propuesta(chat_id, perfil, texto_reconocido, es_audio)
    elif intencion == "apuntarse":
        evs = _planes_para_apuntarse(texto_reconocido)
        if evs:
            ev = evs[0]
            fecha = _format_fecha_es(ev.get("starts_at"))
            botones = [[(f"✅ Sí, a «{ev.get('title')}»", f"insc:{ev['id']}")]]
            otros = evs[1:3]
            for o in otros:
                botones.append([(f"Otro: {o.get('title')} ({_format_fecha_es(o.get('starts_at')).split(' a las ')[0]})",
                                 f"insc:{o['id']}")])
            await _enviar_respuesta(
                chat_id,
                f"🎟️ ¿Quieres apuntarte a «{ev.get('title')}» ({fecha[:1].lower() + fecha[1:]})?"
                + (" Si era otro, elígelo abajo." if otros else ""),
                buttons=botones,
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
            "🤔 Perdona, no te he entendido bien. Prueba a decírmelo de otra forma, por ejemplo: «¿qué hay este finde en Tahal?», o elige una opción:",
            buttons=MENU_PRINCIPAL,
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
    elif data == "cmd:recomendar":
        await _recomendar_eventos(chat_id_int, perfil, "recomiéndame algo")
    elif data == "cmd:valorar":
        await _manejar_resena_libre(chat_id_int, perfil, "quiero valorar un plan")
    elif data == "cmd:proponer":
        ESPERANDO_PROPUESTA.add(chat_id_int)
        await send_message(chat_id_int, TEXTO_PROPONER)
    elif data == "cmd:ayuda":
        await _mostrar_ayuda(chat_id_int)
    elif data == "cmd:mis_propuestas":
        await _cmd_mis_propuestas(chat_id_int, perfil)
    elif data == "cmd:moderar":
        await _cmd_moderar(chat_id_int, perfil)
    elif data.startswith("edit:"):
        await _menu_editar(chat_id_int, perfil, data.split(":", 1)[1])
    elif data.startswith("editc:"):
        _, ev_id, campo = data.split(":", 2)
        if campo in _CAMPOS_EDITABLES and _puede_gestionar(perfil, _get_evento(ev_id)):
            ESPERANDO_EDICION[chat_id_int] = {"event_id": ev_id, "campo": campo}
            await send_message(chat_id_int, _CAMPOS_EDITABLES[campo][1]
                               + "\n(Escribe «cancelar» para dejarlo como estaba.)")
    elif data.startswith("cancelev:"):
        ev_id = data.split(":", 1)[1]
        ev = _get_evento(ev_id)
        if _puede_gestionar(perfil, ev):
            await send_message(chat_id_int, f"❓ ¿Seguro que quieres cancelar «{ev.get('title')}»? "
                                            "Se avisará a todas las personas apuntadas.",
                               buttons=[[("❌ Sí, cancelarlo", f"cancelev_si:{ev_id}"),
                                         ("↩️ No", "cmd:mis_propuestas")]])
    elif data.startswith("cancelev_si:"):
        if msg_id_int:
            await edit_message_reply_markup(chat_id_int, msg_id_int, [])
        await _cancelar_evento(chat_id_int, perfil, data.split(":", 1)[1])
    elif data.startswith("mod_ok:"):
        if msg_id_int:
            await edit_message_reply_markup(chat_id_int, msg_id_int, [])
        await _moderar(chat_id_int, perfil, data.split(":", 1)[1], aprobar=True)
    elif data.startswith("mod_no:"):
        if _es_moderador(perfil):
            ESPERANDO_MOTIVO[chat_id_int] = data.split(":", 1)[1]
            await send_message(chat_id_int, "🚫 ¿Por qué no se publica? Escribe el motivo "
                                            "(se lo diré a quien lo propuso).")
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
                ESPERANDO_PROPUESTA.add(chat_id_int)
                await send_message(chat_id_int, TEXTO_PROPONER)
            elif cmd == "/audio":
                await _toggle_audio(chat_id_int, perfil=perfil, enabled=None)
            elif cmd in ("/ayuda", "/help", "/menu"):
                await _mostrar_ayuda(chat_id_int)
            elif cmd == "/mis_propuestas":
                await _cmd_mis_propuestas(chat_id_int, perfil)
            elif cmd == "/moderar":
                await _cmd_moderar(chat_id_int, perfil)
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

    icono = _ICONO_CATEGORIA.get(str(ext.get("category") or ""), ("📌", ""))[0]
    resumen = (
        f"📝 <b>¿Publico este plan?</b>\n\n"
        f"{icono} <b>{escape_html(titulo)}</b>\n"
        f"📅 {escape_html(fecha_es.replace(' a las ', ' · '))}\n"
        f"📍 {escape_html(lugar)} · {escape_html(muni)}\n"
        f"💶 {escape_html(precio_txt)}   ·   👥 {escape_html(plazas_txt)}"
        f"{' plazas' if plazas else ''}\n\n"
        f"<i>Revisa que esté todo bien y pulsa un botón 👇</i>"
    )
    botones: list[list[tuple[str, str]]] = [
        [
            ("✅ Sí, publícalo", f"conf:si:{ingesta_id}"),
            ("❌ No", f"conf:no:{ingesta_id}"),
        ]
    ]

    image_url = ing.get("image_url") or ext.get("image_url") or ext.get("poster_url")
    if image_url:
        await send_photo(chat_id_int, image_url, caption=resumen, buttons=botones, escape=False)
    else:
        await send_message(chat_id_int, resumen, buttons=botones, escape=False)

    pregunta = ""
    if missing:
        primer_campo = str(missing[0])
        ESPERANDO_CAMPO[chat_id_int] = {"ingesta_id": ingesta_id, "campo": primer_campo}
        pregunta = _pregunta_para_campo(primer_campo)
        await send_message(chat_id_int, pregunta)

    if chat_id_int in CHATS_CON_VOZ:  # lo propuso con una nota de voz -> también en audio
        CHATS_CON_VOZ.discard(chat_id_int)
        await _enviar_audio(
            chat_id_int,
            f"He entendido este plan: {titulo}, el {_fecha_hablada(ext.get('starts_at'))}, "
            f"en {lugar}, {muni}. "
            f"Precio: {precio_txt}. Plazas: {plazas_txt}. "
            + (pregunta if pregunta else "Si está bien, pulsa «Sí, publícalo»."),
        )

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