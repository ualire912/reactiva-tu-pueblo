"""
api/cartel_vibrante.py · Cartel con estilo de cartel de fiestas de pueblo (sin IA)

generar(evento, foto, credito) -> bytes JPEG 1080x1620 (2:3, vertical)

Inspirado en los carteles hechos a mano de los ayuntamientos: el pueblo en grande arriba,
un lema inclinado, el título sobre una brocha, la foto real del pueblo de fondo,
"¿TE APUNTAS?" en amarillo, sello de plazas, recuadro de lugar con icono, franja de
fecha y precio y un lema final. Colores según la categoría.
Nunca inventa: si un dato no está, ese bloque no se pinta (o pone "Por confirmar").
"""
from __future__ import annotations

import io
import os
import random
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ANCHO, ALTO = 1080, 1620
AMARILLO = "#FFD21F"
_AQUI = os.path.dirname(os.path.abspath(__file__))
_FUENTE_NEGRITA = os.path.join(_AQUI, "fuentes", "DejaVuSans-Bold.ttf")
_FUENTE_NORMAL = os.path.join(_AQUI, "fuentes", "DejaVuSans.ttf")

# categoría -> (color principal oscuro, color secundario, tono del suelo, lema arriba, lema abajo)
PALETAS = {
    "deporte":      ("#0D2B6E", "#1E63C6", "#1F6B2A", "¡EL DEPORTE NOS UNE!", "PARTICIPA • DISFRUTA • JUEGA"),
    "senderismo":   ("#123F22", "#2E8B47", "#2C5A22", "¡A CAMINAR SE HA DICHO!", "CAMINA • RESPIRA • DISFRUTA"),
    "cultura":      ("#3A1257", "#7A3FB0", "#2B1240", "¡CULTURA PARA TODOS!", "DESCUBRE • APRENDE • DISFRUTA"),
    "gastronomia":  ("#6B1E08", "#D0541B", "#5A2A0C", "¡A LA MESA!", "PRUEBA • COMPARTE • DISFRUTA"),
    "fiestas":      ("#6B0A35", "#D12F6E", "#4A0A27", "¡VIVE LA FIESTA!", "BAILA • RÍE • CELEBRA"),
    "taller":       ("#4A2D06", "#B8761C", "#3B2508", "¡APRENDE Y CREA!", "APRENDE • CREA • COMPARTE"),
    "voluntariado": ("#0A4442", "#1E9C93", "#0C3B33", "¡ECHA UNA MANO!", "AYUDA • SUMA • TRANSFORMA"),
    "otro":         ("#1C2340", "#3E58B8", "#1B2333", "¡NO TE LO PIERDAS!", "PARTICIPA • DISFRUTA • COMPARTE"),
}
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


# ── Utilidades de dibujo ──────────────────────────────────────
def _f(tam: int, negrita: bool = True) -> ImageFont.FreeTypeFont:
    ruta = _FUENTE_NEGRITA if negrita else _FUENTE_NORMAL
    if os.path.exists(ruta):
        return ImageFont.truetype(ruta, tam)
    return ImageFont.load_default(size=tam)


def _capa_texto(texto: str, tam: int, relleno: str, borde: int = 0, color_borde: str = "#0B1A3A",
                estrecho: float = 0.82, inclinado: float = 0.0, negrita: bool = True,
                sombra: bool = True) -> Image.Image:
    """Texto como imagen RGBA: más estrecho (aire de letra de cartel) e inclinado si se pide."""
    fuente = _f(tam, negrita)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = tmp.textbbox((0, 0), texto, font=fuente, stroke_width=borde)
    pad = borde + 12
    capa = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    if sombra:
        d.text((pad - x0 + 5, pad - y0 + 6), texto, font=fuente, fill=(0, 0, 0, 140),
               stroke_width=borde, stroke_fill=(0, 0, 0, 140))
    d.text((pad - x0, pad - y0), texto, font=fuente, fill=relleno,
           stroke_width=borde, stroke_fill=color_borde)
    if estrecho != 1:
        capa = capa.resize((max(1, int(capa.width * estrecho)), capa.height), Image.LANCZOS)
    if inclinado:
        extra = int(abs(inclinado) * capa.height)
        capa = capa.transform((capa.width + extra, capa.height), Image.AFFINE,
                              (1, inclinado, -extra if inclinado > 0 else 0, 0, 1, 0), Image.BICUBIC)
    return capa


def _pegar_centrado(base: Image.Image, capa: Image.Image, cx: int, cy: int, ancho_max: int | None = None):
    if ancho_max and capa.width > ancho_max:
        capa = capa.resize((ancho_max, int(capa.height * ancho_max / capa.width)), Image.LANCZOS)
    base.alpha_composite(capa, (int(cx - capa.width / 2), int(cy - capa.height / 2)))
    return capa


def _brocha(base: Image.Image, caja: tuple[int, int, int, int], color: str, semilla: int,
            rasgado: int = 14, inclinacion: float = -1.5) -> None:
    """Franja tipo brochazo: bordes irregulares y un poco torcida."""
    x0, y0, x1, y1 = caja
    rnd = random.Random(semilla)
    capa = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    pasos = 26
    arriba = [(x0 + (x1 - x0) * i / pasos, y0 + rnd.randint(-rasgado // 2, rasgado // 2)) for i in range(pasos + 1)]
    derecha = [(x1 + rnd.randint(-rasgado, rasgado), y0 + (y1 - y0) * k / 5) for k in range(1, 5)]
    abajo = [(x1 - (x1 - x0) * i / pasos, y1 + rnd.randint(-rasgado // 2, rasgado // 2)) for i in range(pasos + 1)]
    izquierda = [(x0 + rnd.randint(-rasgado, rasgado), y1 - (y1 - y0) * k / 5) for k in range(1, 5)]
    d.polygon(arriba + derecha + abajo + izquierda, fill=color)
    # Rastros sueltos de la brocha
    for _ in range(6):
        yy = rnd.randint(y0, y1)
        d.line([(x0 - rnd.randint(10, 40), yy), (x0 + rnd.randint(10, 60), yy)], fill=color, width=rnd.randint(2, 5))
        yy = rnd.randint(y0, y1)
        d.line([(x1 - rnd.randint(10, 60), yy), (x1 + rnd.randint(10, 40), yy)], fill=color, width=rnd.randint(2, 5))
    capa = capa.rotate(inclinacion, center=((x0 + x1) / 2, (y0 + y1) / 2), resample=Image.BICUBIC)
    base.alpha_composite(capa)


def _caja_redonda(base: Image.Image, caja, color: str, borde: str | None = None, radio: int = 26):
    capa = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    sombra = (caja[0] + 6, caja[1] + 8, caja[2] + 6, caja[3] + 8)
    d.rounded_rectangle(sombra, radius=radio, fill=(0, 0, 0, 110))
    capa = capa.filter(ImageFilter.GaussianBlur(6))
    d = ImageDraw.Draw(capa)
    d.rounded_rectangle(caja, radius=radio, fill=color, outline=borde, width=4 if borde else 0)
    base.alpha_composite(capa)


def _icono_lugar(d: ImageDraw.ImageDraw, x: int, y: int, tam: int, color: str, fondo: str):
    r = tam // 2
    d.ellipse((x - r, y - r, x + r, y + r), fill=color)
    d.polygon([(x - r * 0.86, y + r * 0.45), (x + r * 0.86, y + r * 0.45), (x, y + tam * 1.25)], fill=color)
    d.ellipse((x - r * 0.42, y - r * 0.42, x + r * 0.42, y + r * 0.42), fill=fondo)


def _icono_calendario(d: ImageDraw.ImageDraw, x: int, y: int, tam: int, color: str, fondo: str):
    d.rounded_rectangle((x, y, x + tam, y + tam), radius=6, fill=color)
    d.rectangle((x + 4, y + tam * 0.3, x + tam - 4, y + tam - 4), fill=fondo)
    for fila in range(2):
        for col in range(3):
            cx = x + 8 + col * (tam - 16) / 2.4
            cy = y + tam * 0.42 + fila * tam * 0.25
            d.rectangle((cx, cy, cx + tam * 0.13, cy + tam * 0.13), fill=color)
    for dx in (0.28, 0.72):
        d.rectangle((x + tam * dx - 3, y - 6, x + tam * dx + 3, y + 8), fill=color)


def _partir(texto: str, fuente, ancho: int, max_lineas: int) -> list[str]:
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lineas, actual = [], ""
    for p in (texto or "").split():
        prueba = f"{actual} {p}".strip()
        if tmp.textlength(prueba, font=fuente) <= ancho:
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


def _fecha_txt(iso) -> str | None:
    if not iso:
        return None
    try:
        from ia import TZ
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return None
    hora = "" if (d.hour, d.minute) == (0, 0) else f" · {d:%H:%M}"
    return f"{_DIAS[d.weekday()].capitalize()} {d.day} de {_MESES[d.month - 1]}{hora}"


def _precio_txt(precio) -> str | None:
    if precio is None or precio == "":
        return None
    try:
        v = float(precio)
    except (TypeError, ValueError):
        return str(precio)
    if v == 0:
        return "Gratis"
    return f"{v:g} €".replace(".", ",")


def _fondo(foto: bytes | None, principal: str, suelo: str) -> Image.Image:
    """Foto del pueblo a todo el cartel, con cielo de color arriba y suelo oscuro abajo."""
    base = Image.new("RGBA", (ANCHO, ALTO), principal)
    if foto:
        img = ImageOps.fit(Image.open(io.BytesIO(foto)).convert("RGB"), (ANCHO, ALTO),
                           method=Image.LANCZOS, centering=(0.5, 0.45))
        img = ImageOps.autocontrast(img, cutoff=1)
        base = img.convert("RGBA")
    # Degradados: arriba al color de la categoría (se lee el título), abajo al tono del suelo
    capa = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    p = Image.new("RGB", (1, 1), principal).getpixel((0, 0))
    s = Image.new("RGB", (1, 1), suelo).getpixel((0, 0))
    for y in range(ALTO):  # degradados continuos (sin cortes visibles)
        if y < 760:  # cielo: opaco arriba, se desvanece hacia la foto
            a = int(250 * (1 - y / 760) ** 0.9)
            d.line([(0, y), (ANCHO, y)], fill=p + (a,))
        if y > 860:  # suelo: aparece poco a poco
            a = int(min(235, 240 * ((y - 860) / 760) ** 0.75))
            d.line([(0, y), (ANCHO, y)], fill=s + (a,))
    base.alpha_composite(capa)
    return base


# ── El cartel ─────────────────────────────────────────────────
def generar(evento: dict, foto: bytes | None = None, credito: str | None = None) -> bytes:
    cat = evento.get("category") if evento.get("category") in PALETAS else "otro"
    principal, secundario, suelo, lema, lema_final = PALETAS[cat]
    semilla = sum(map(ord, str(evento.get("title") or ""))) or 7
    img = _fondo(foto, principal, suelo)
    d = ImageDraw.Draw(img)

    # 1. El pueblo en grande
    pueblo = (evento.get("municipio") or "").upper()
    if pueblo:
        cab = _capa_texto(f"{pueblo} (ALMERÍA)", 104, "white", borde=6, color_borde=principal, estrecho=0.8)
        _pegar_centrado(img, cab, ANCHO // 2, 105, ancho_max=ANCHO - 70)
        _brocha(img, (130, 178, ANCHO - 130, 192), AMARILLO, semilla, rasgado=6, inclinacion=-0.8)

    # 2. Lema inclinado
    lema_capa = _capa_texto(lema, 92, "white", borde=6, color_borde=principal, estrecho=0.78, inclinado=0.22)
    _pegar_centrado(img, lema_capa, ANCHO // 2, 285, ancho_max=ANCHO - 60)

    # 3. Título sobre una brocha oscura (1 o 2 líneas)
    titulo = (evento.get("title") or "Plan en el pueblo").upper()
    lineas = _partir(titulo, _f(68), int((ANCHO - 200) / 0.82), 2)
    alto_banda = 96 if len(lineas) == 1 else 170
    y_banda = 380
    _brocha(img, (70, y_banda, ANCHO - 70, y_banda + alto_banda), principal, semilla + 1, rasgado=18)
    for i, linea in enumerate(lineas):
        capa = _capa_texto(linea, 68, "white", borde=0, estrecho=0.82, sombra=False)
        _pegar_centrado(img, capa, ANCHO // 2, y_banda + alto_banda // 2 + (i - (len(lineas) - 1) / 2) * 76,
                        ancho_max=ANCHO - 180)
    _brocha(img, (170, y_banda + alto_banda + 14, ANCHO - 170, y_banda + alto_banda + 26), AMARILLO,
            semilla + 2, rasgado=5, inclinacion=-0.6)

    # 4. ¿TE APUNTAS? y descripción
    y_apuntas = 880
    _pegar_centrado(img, _capa_texto("¿TE APUNTAS?", 150, AMARILLO, borde=8, color_borde=principal,
                                     estrecho=0.8, inclinado=0.2), ANCHO // 2, y_apuntas, ancho_max=ANCHO - 80)
    desc = (evento.get("description") or "").strip()
    if desc:
        f_desc = _f(38)
        for i, linea in enumerate(_partir(desc, f_desc, ANCHO - 200, 3)):
            _pegar_centrado(img, _capa_texto(linea, 38, "white", borde=3, color_borde="#071226", estrecho=0.92),
                            ANCHO // 2, y_apuntas + 115 + i * 50)

    # 5. Sello de precio a la izquierda (círculo amarillo)
    precio = _precio_txt(evento.get("price"))
    cx, cy, r = 205, 1265, 150
    sello = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ds = ImageDraw.Draw(sello)
    ds.ellipse((cx - r + 8, cy - r + 10, cx + r + 8, cy + r + 10), fill=(0, 0, 0, 120))
    sello = sello.filter(ImageFilter.GaussianBlur(8))
    ds = ImageDraw.Draw(sello)
    ds.ellipse((cx - r, cy - r, cx + r, cy + r), fill=AMARILLO, outline="white", width=8)
    img.alpha_composite(sello)
    if precio:
        arriba = "ENTRADA" if precio == "Gratis" else "PRECIO"
        _pegar_centrado(img, _capa_texto(arriba, 34, principal, estrecho=0.85, sombra=False), cx, cy - 52)
        _pegar_centrado(img, _capa_texto(precio.upper(), 78, principal, estrecho=0.8, sombra=False), cx, cy + 18,
                        ancho_max=2 * r - 40)
    else:
        _pegar_centrado(img, _capa_texto("PRECIO", 40, principal, estrecho=0.85, sombra=False), cx, cy - 25)
        _pegar_centrado(img, _capa_texto("por confirmar", 32, principal, estrecho=0.9, sombra=False), cx, cy + 25)

    # 6. Sello de plazas (brocha amarilla)
    x_der = 395
    _brocha(img, (x_der, 1125, ANCHO - 40, 1250), AMARILLO, semilla + 3, rasgado=12, inclinacion=-2)
    plazas = evento.get("max_capacity")
    # Sin número de plazas no se dice "libres" (sería inventar): solo una invitación
    principal_txt = f"{plazas} PLAZAS DISPONIBLES" if plazas else "¡TE ESPERAMOS!"
    secundario_txt = "¡Apúntate y asegura la tuya!" if plazas else "¡Ven con tu familia y amigos!"
    _pegar_centrado(img, _capa_texto(principal_txt, 52, principal, estrecho=0.78, inclinado=0.15, sombra=False),
                    (x_der + ANCHO - 40) // 2, 1168, ancho_max=ANCHO - 40 - x_der - 40)
    _pegar_centrado(img, _capa_texto(secundario_txt, 36, principal, estrecho=0.85,
                                     inclinado=0.2, sombra=False),
                    (x_der + ANCHO - 40) // 2, 1220, ancho_max=ANCHO - 40 - x_der - 50)

    # 7. Recuadro del lugar
    caja = (x_der, 1270, ANCHO - 40, 1438)
    _caja_redonda(img, caja, principal, borde="white")
    d = ImageDraw.Draw(img)
    _icono_lugar(d, caja[0] + 62, caja[1] + 62, 58, AMARILLO, principal)
    lugar = evento.get("location_name") or "Por confirmar"
    _pegar_centrado(img, _capa_texto("LUGAR", 36, AMARILLO, estrecho=0.85, sombra=False),
                    (caja[0] + 120 + caja[2]) // 2, caja[1] + 34)
    con_pueblo = bool(pueblo) and pueblo.lower() not in lugar.lower()
    lineas_lugar = _partir(lugar, _f(30), int((caja[2] - caja[0] - 150) / 0.88), 2 if con_pueblo else 3)
    if con_pueblo:
        lineas_lugar.append(f"{evento.get('municipio')}, Almería")
    for i, linea in enumerate(lineas_lugar):
        _pegar_centrado(img, _capa_texto(linea, 30, "white", estrecho=0.88, sombra=False),
                        (caja[0] + 120 + caja[2]) // 2, caja[1] + 76 + i * 34, ancho_max=caja[2] - caja[0] - 150)

    # 8. Franja de fecha (y hora)
    caja_f = (40, 1452, ANCHO - 40, 1530)
    _caja_redonda(img, caja_f, principal, borde=AMARILLO, radio=22)
    d = ImageDraw.Draw(img)
    _icono_calendario(d, caja_f[0] + 26, caja_f[1] + 20, 40, AMARILLO, principal)
    fecha = _fecha_txt(evento.get("starts_at")) or "Por confirmar"
    etiqueta = _capa_texto("FECHA Y HORA:", 34, AMARILLO, estrecho=0.85, sombra=False)
    valor = _capa_texto(fecha, 34, "white", estrecho=0.9, sombra=False)
    x = caja_f[0] + 90
    img.alpha_composite(etiqueta, (x, caja_f[1] + (78 - etiqueta.height) // 2))
    img.alpha_composite(valor, (x + etiqueta.width + 4, caja_f[1] + (78 - valor.height) // 2))

    # 9. Lema final sobre brocha blanca
    _brocha(img, (90, 1545, ANCHO - 90, 1605), "white", semilla + 4, rasgado=10, inclinacion=-1)
    _pegar_centrado(img, _capa_texto(lema_final, 48, principal, estrecho=0.8, inclinado=0.2, sombra=False),
                    ANCHO // 2, 1574, ancho_max=ANCHO - 220)

    # 10. Crédito de la foto (licencia CC: hay que citarla)
    if foto and credito:
        cred = _capa_texto(credito, 18, "white", estrecho=1, negrita=False, borde=2, color_borde="#000000",
                           sombra=False)
        img.alpha_composite(cred, (ANCHO - cred.width - 16, y_banda + alto_banda + 34))

    salida = io.BytesIO()
    img.convert("RGB").save(salida, "JPEG", quality=90, optimize=True)
    return salida.getvalue()
