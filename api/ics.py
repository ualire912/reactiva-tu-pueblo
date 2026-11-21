"""B1 · Calendario .ics y fechas en hora de España.

generar_ics(evento) -> str
  - UID fijo {id}@reactiva y SEQUENCE = eventos.sequence: así, si cambia la
    hora, el calendario ACTUALIZA el evento en vez de duplicarlo.
  - Fechas en UTC (terminan en Z).
"""
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def parse_fecha(valor) -> datetime | None:
    """ISO de Supabase (o datetime) -> datetime con zona. None si viene vacío."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fecha_es(valor) -> str:
    """'sábado 29 de noviembre a las 09:30' en hora de España."""
    dt = parse_fecha(valor)
    if not dt:
        return "fecha por confirmar"
    d = dt.astimezone(MADRID)
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]} a las {d:%H:%M}"


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escapar(texto) -> str:
    """Escapa texto para iCalendar: \\ ; , y saltos de línea."""
    t = str(texto or "")
    t = t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return t.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _plegar(linea: str) -> str:
    """Las líneas .ics no deben pasar de 75 bytes: se parten con CRLF + espacio."""
    trozos, actual, tam = [], "", 0
    for ch in linea:
        b = len(ch.encode("utf-8"))
        limite = 75 if not trozos else 74  # las siguientes llevan un espacio delante
        if tam + b > limite:
            trozos.append(actual)
            actual, tam = ch, b
        else:
            actual += ch
            tam += b
    trozos.append(actual)
    return "\r\n ".join(trozos)


def generar_ics(evento: dict) -> str:
    """evento = fila de 'eventos' (+ clave opcional 'municipio' con el nombre)."""
    inicio = parse_fecha(evento["starts_at"])
    fin = parse_fecha(evento.get("ends_at")) or inicio + timedelta(hours=2)
    frontend = os.environ.get("FRONTEND_URL", "").rstrip("/")

    lugar = ", ".join(x for x in [evento.get("location_name"), evento.get("municipio")] if x)
    estado = "CANCELLED" if evento.get("status") == "cancelado" else "CONFIRMED"

    lineas = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Reactiva tu pueblo//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{evento['id']}@reactiva",
        f"SEQUENCE:{int(evento.get('sequence') or 0)}",
        f"DTSTAMP:{_utc(datetime.now(timezone.utc))}",
        f"DTSTART:{_utc(inicio)}",
        f"DTEND:{_utc(fin)}",
        f"SUMMARY:{_escapar(evento.get('title'))}",
        f"LOCATION:{_escapar(lugar)}",
        f"DESCRIPTION:{_escapar(evento.get('description'))}",
    ]
    if frontend:
        lineas.append(f"URL:{frontend}/evento/{evento['id']}")
    lineas += [f"STATUS:{estado}", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_plegar(l) for l in lineas) + "\r\n"
