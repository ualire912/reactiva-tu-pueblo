"""
api/cartel.py  ·  C1  ·  Cartel automático a partir de los datos del evento (SIN IA)

generar_cartel(evento: dict) -> bytes   (PNG 1080x1350, formato vertical para móvil)

- 0 tokens, instantáneo, nunca inventa: solo pinta los datos que hay.
- Colores por categoría con contraste alto (texto blanco sobre fondo oscuro, WCAG AA).
- Fuentes DejaVu incluidas en api/fuentes/ (funciona igual en Windows y en Render).
"""
from __future__ import annotations

import io
import os
from datetime import datetime

import json
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps

ANCHO, ALTO = 1080, 1350
MARGEN = 80
_AQUI = os.path.dirname(os.path.abspath(__file__))
_FUENTES = os.path.join(_AQUI, "fuentes")
_FOTOS = os.path.join(_AQUI, "fotos")  # fotos/<pueblo>.jpg, fotos/cat-<categoria>.jpg, creditos.json

# Fondo, acento y nombre por categoría (todos con contraste > 7:1 con el blanco)
ESTILOS = {
    "senderismo":   ("#1F4D2B", "#A7D7A1", "Senderismo"),
    "deporte":      ("#0B3C6E", "#9CC9F5", "Deporte"),
    "cultura":      ("#4A1F5C", "#D9B3E8", "Cultura"),
    "gastronomia":  ("#7A2E0E", "#F5C08A", "Gastronomía"),
    "fiestas":      ("#7D1037", "#F7A8C4", "Fiestas"),
    "taller":       ("#5C3A0B", "#F2D08B", "Taller"),
    "voluntariado": ("#0E5552", "#9FE0DA", "Voluntariado"),
    "otro":         ("#2E3440", "#C8D0E0", "Evento"),
}
_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def _fuente(tam: int, negrita: bool = True):
    nombre = "DejaVuSans-Bold.ttf" if negrita else "DejaVuSans.ttf"
    for ruta in (os.path.join(_FUENTES, nombre),
                 rf"C:\Windows\Fonts\{'arialbd' if negrita else 'arial'}.ttf"):
        if os.path.exists(ruta):
            return ImageFont.truetype(ruta, tam)
    return ImageFont.load_default(size=tam)


def _partir(draw, texto: str, fuente, ancho_max: int, max_lineas: int) -> list[str]:
    """Parte el texto en líneas que quepan en ancho_max."""
    palabras, lineas, actual = (texto or "").split(), [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if draw.textlength(prueba, font=fuente) <= ancho_max:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    if len(lineas) > max_lineas:
        lineas = lineas[:max_lineas]
        lineas[-1] = lineas[-1].rstrip(".,;") + "…"
    return lineas


def _fecha(iso) -> tuple[str, str] | None:
    """-> ('Sábado 5 de diciembre', '11:00 – 13:00') en hora de España."""
    if not iso:
        return None
    from ia import TZ
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}", f"{d:%H:%M}"


def _slug(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").lower()
    return "-".join("".join(c if c.isalnum() else " " for c in t).split())


def _buscar_foto(evento: dict) -> tuple[bytes | None, str | None]:
    """Foto del pueblo o, si no hay, de la categoría. -> (bytes, crédito)."""
    try:
        with open(os.path.join(_FOTOS, "creditos.json"), encoding="utf-8") as f:
            creditos = json.load(f)
    except Exception:
        creditos = {}
    candidatas = []
    if evento.get("municipio"):
        candidatas.append(_slug(evento["municipio"]))
    candidatas.append(f"cat-{evento.get('category') or 'otro'}")
    for nombre in candidatas:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            ruta = os.path.join(_FOTOS, nombre + ext)
            if os.path.exists(ruta):
                with open(ruta, "rb") as f:
                    return f.read(), creditos.get(nombre + ext)
    return None, None


def generar_cartel(evento: dict, foto: bytes | None = None, credito: str | None = None) -> bytes:
    """PNG del cartel. Con foto (la del organizador o la del pueblo) -> diseño con foto;
    sin ninguna foto -> diseño de color. Solo usa los datos presentes en `evento`."""
    if foto is None:
        foto, credito = _buscar_foto(evento)
    if foto:
        try:
            return _cartel_con_foto(evento, foto, credito)
        except Exception as e:
            print(f"[cartel] foto no válida, uso el diseño de color: {e}")
    return _cartel_color(evento)


def _texto(d, xy, txt, fuente, relleno="white", sombra=4):
    """Texto con borde oscuro: se lee sobre cualquier foto."""
    d.text(xy, txt, font=fuente, fill=relleno, stroke_width=sombra, stroke_fill="#000000")


def _cartel_con_foto(evento: dict, foto: bytes, credito: str | None) -> bytes:
    fondo, acento, nombre_cat = ESTILOS.get(evento.get("category") or "otro", ESTILOS["otro"])
    base = ImageOps.fit(Image.open(io.BytesIO(foto)).convert("RGB"), (ANCHO, ALTO),
                        method=Image.LANCZOS, centering=(0.5, 0.4))

    # Oscurecido arriba (título) y abajo (datos) para que el texto tenga contraste
    capa = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    for y in range(620):
        dc.line((0, y, ANCHO, y), fill=(0, 0, 0, int(190 * (1 - y / 620))))
    r, g, b = Image.new("RGB", (1, 1), fondo).getpixel((0, 0))
    panel_y = ALTO - 600
    dc.rectangle((0, panel_y, ANCHO, ALTO), fill=(r, g, b, 235))
    img = Image.alpha_composite(base.convert("RGBA"), capa).convert("RGB")
    d = ImageDraw.Draw(img)
    util = ANCHO - 2 * MARGEN

    # Categoría + título grande
    y = 60
    f_cat = _fuente(36)
    ancho_cat = d.textlength(nombre_cat.upper(), font=f_cat)
    d.rounded_rectangle((MARGEN, y, MARGEN + ancho_cat + 56, y + 68), radius=34, fill=acento)
    d.text((MARGEN + 28, y + 12), nombre_cat.upper(), font=f_cat, fill=fondo)
    y += 110
    titulo = (evento.get("title") or "Evento").strip().upper()
    for tam in (120, 100, 84, 70):
        f_tit = _fuente(tam)
        lineas = _partir(d, titulo, f_tit, util, 4)
        if len(lineas) <= 3 or tam == 70:
            break
    for linea in lineas:
        _texto(d, (MARGEN, y), linea, f_tit, sombra=6)
        y += int(tam * 1.1)

    # Crédito de la foto (obligatorio en fotos de Wikimedia Commons)
    if credito:
        f_cr = _fuente(22, negrita=False)
        w = d.textlength(credito, font=f_cr)
        _texto(d, (ANCHO - MARGEN - w, panel_y - 40), credito, f_cr, sombra=2)

    # Panel inferior: FECHA | LUGAR | HORA
    y = panel_y + 40
    cols = []
    fecha = _fecha(evento.get("starts_at"))
    if fecha:
        cols.append(("FECHA", fecha[0].split(" ", 1)[1] if " " in fecha[0] else fecha[0]))
    lugar = ", ".join(x for x in (evento.get("location_name"), evento.get("municipio")) if x)
    if lugar:
        cols.append(("LUGAR", lugar))
    if fecha:
        hora = fecha[1]
        fin = _fecha(evento.get("ends_at"))
        if fin and fin[0] == fecha[0]:
            hora += f"–{fin[1]}"
        cols.append(("HORA", hora))
    if cols:
        anchos = {"FECHA": 0.33, "LUGAR": 0.43, "HORA": 0.24}
        total = sum(anchos[c[0]] for c in cols)
        x = MARGEN
        f_lab, f_val = _fuente(32), _fuente(40)
        for k, (etq, valor) in enumerate(cols):
            w = int(util * anchos[etq] / total)
            d.text((x, y), etq, font=f_lab, fill=acento)
            yy = y + 50
            for linea in _partir(d, valor, f_val, w - 30, 3):
                d.text((x, yy), linea, font=f_val, fill="white")
                yy += 50
            if k < len(cols) - 1:
                d.line((x + w - 18, y, x + w - 18, y + 200), fill=acento, width=3)
            x += w
    y = panel_y + 280

    extras = []
    if evento.get("price") is not None:
        p = float(evento["price"])
        extras.append("Gratis" if p == 0 else f"{p:g} €".replace(".", ","))
    if evento.get("max_capacity"):
        extras.append(f"{evento['max_capacity']} plazas")
    if evento.get("is_accessible"):
        extras.append("Accesible")
    ins = _fecha(evento.get("inscripcion_hasta"))
    if ins:
        extras.append(f"Inscripción hasta {ins[0].split(' ', 1)[1]}")
    f_ext = _fuente(38)
    x = MARGEN
    for e in extras:
        w = d.textlength(e, font=f_ext) + 50
        if x + w > ANCHO - MARGEN:
            x, y = MARGEN, y + 78
        d.rounded_rectangle((x, y, x + w, y + 62), radius=31, outline=acento, width=4)
        d.text((x + 25, y + 10), e, font=f_ext, fill="white")
        x += w + 20

    # Pie
    d.rectangle((0, ALTO - 130, ANCHO, ALTO), fill=acento)
    d.text((MARGEN, ALTO - 112), "Reactiva tu pueblo", font=_fuente(42), fill=fondo)
    d.text((MARGEN, ALTO - 58), "Apúntate en la app o en Telegram", font=_fuente(30, negrita=False),
           fill=fondo)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _cartel_color(evento: dict) -> bytes:
    """Diseño sin foto (de respaldo)."""
    fondo, acento, nombre_cat = ESTILOS.get(evento.get("category") or "otro", ESTILOS["otro"])
    img = Image.new("RGB", (ANCHO, ALTO), fondo)
    d = ImageDraw.Draw(img)
    util = ANCHO - 2 * MARGEN

    # Decoración: círculos suaves arriba a la derecha y franja inferior
    d.ellipse((ANCHO - 260, -260, ANCHO + 200, 200), outline=acento, width=6)
    d.ellipse((ANCHO - 170, -170, ANCHO + 110, 110), outline=acento, width=3)
    d.rectangle((0, ALTO - 170, ANCHO, ALTO), fill=acento)

    y = MARGEN + 20
    # Etiqueta de categoría
    f_cat = _fuente(36)
    ancho_cat = d.textlength(nombre_cat.upper(), font=f_cat)
    d.rounded_rectangle((MARGEN, y, MARGEN + ancho_cat + 56, y + 68), radius=34, fill=acento)
    d.text((MARGEN + 28, y + 12), nombre_cat.upper(), font=f_cat, fill=fondo)
    y += 130

    # Título (se ajusta el tamaño a lo largo que sea)
    titulo = (evento.get("title") or "Evento").strip()
    for tam in (110, 92, 78, 66):
        f_tit = _fuente(tam)
        lineas = _partir(d, titulo, f_tit, util, 4)
        if len(lineas) <= 3 or tam == 66:
            break
    for linea in lineas:
        d.text((MARGEN, y), linea, font=f_tit, fill="white")
        y += int(tam * 1.15)
    y += 40
    d.line((MARGEN, y, MARGEN + 160, y), fill=acento, width=10)
    y += 60

    # Datos: cada uno en su línea, solo si existe (nunca se inventa)
    f_dato, f_sub = _fuente(54), _fuente(44, negrita=False)
    fecha = _fecha(evento.get("starts_at"))
    if fecha:
        hora = fecha[1]
        fin = _fecha(evento.get("ends_at"))
        if fin and fin[0] == fecha[0]:
            hora += f" – {fin[1]}"
        d.text((MARGEN, y), fecha[0], font=f_dato, fill="white")
        y += 70
        d.text((MARGEN, y), f"{hora} h", font=f_sub, fill=acento)
        y += 90

    lugar = ", ".join(x for x in (evento.get("location_name"), evento.get("municipio")) if x)
    if lugar:
        for linea in _partir(d, lugar, f_dato, util, 2):
            d.text((MARGEN, y), linea, font=f_dato, fill="white")
            y += 70
        y += 20

    extras = []
    precio = evento.get("price")
    if precio is not None:
        p = float(precio)
        extras.append("Gratis" if p == 0 else f"{p:g} €".replace(".", ","))
    if evento.get("max_capacity"):
        extras.append(f"{evento['max_capacity']} plazas")
    if evento.get("is_accessible"):
        extras.append("Accesible")
    if extras:
        f_ext = _fuente(44)
        x = MARGEN
        for e in extras:
            w = d.textlength(e, font=f_ext) + 56
            if x + w > ANCHO - MARGEN:
                x, y = MARGEN, y + 90
            d.rounded_rectangle((x, y, x + w, y + 72), radius=36, outline=acento, width=4)
            d.text((x + 28, y + 12), e, font=f_ext, fill="white")
            x += w + 24
        y += 110

    ins = _fecha(evento.get("inscripcion_hasta"))
    if ins and y < ALTO - 280:
        for linea in _partir(d, f"Inscripción hasta el {ins[0].lower()}", f_sub, util, 2):
            d.text((MARGEN, y), linea, font=f_sub, fill=acento)
            y += 56

    # Pie
    d.text((MARGEN, ALTO - 140), "Reactiva tu pueblo", font=_fuente(44), fill=fondo)
    d.text((MARGEN, ALTO - 80), "Apúntate en la app o en Telegram", font=_fuente(34, negrita=False),
           fill=fondo)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
