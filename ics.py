from datetime import datetime, timedelta, timezone
from typing import Any


def _to_ics_utc(dt_val: Any) -> str:
    if isinstance(dt_val, datetime):
        dt = dt_val
    elif isinstance(dt_val, str) and dt_val:
        dt = datetime.fromisoformat(dt_val.replace("Z", "+00:00"))
    else:
        dt = datetime.now(timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape_ics(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def generar_ics(evento: dict[str, Any]) -> str:
    """Genera un contenido VCALENDAR (.ics) básico para un evento."""
    ev_id = str(evento.get("id") or "evento")
    title = _escape_ics(str(evento.get("title") or "Evento Reactiva tu pueblo"))
    description = _escape_ics(str(evento.get("description") or ""))
    location = _escape_ics(str(evento.get("location_name") or ""))
    sequence = int(evento.get("sequence") or 0)

    dtstart_str = _to_ics_utc(evento.get("starts_at"))

    if evento.get("ends_at"):
        dtend_str = _to_ics_utc(evento.get("ends_at"))
    else:
        start_iso = str(evento.get("starts_at") or datetime.now(timezone.utc).isoformat()).replace(
            "Z", "+00:00"
        )
        try:
            start_dt = datetime.fromisoformat(start_iso)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except Exception:
            start_dt = datetime.now(timezone.utc)
        dur = int(evento.get("duration_minutes") or 60)
        dtend_str = _to_ics_utc(start_dt + timedelta(minutes=dur))

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Reactiva tu pueblo//Hackathon UAL//ES",
        "BEGIN:VEVENT",
        f"UID:{ev_id}@reactiva",
        f"SEQUENCE:{sequence}",
        f"DTSTART:{dtstart_str}",
        f"DTEND:{dtend_str}",
        f"SUMMARY:{title}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(lines) + "\r\n"