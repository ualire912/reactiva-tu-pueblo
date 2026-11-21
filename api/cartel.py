"""
api/cartel.py  ·  C1  ·  Cartel automático a partir de los datos del evento (SIN IA)

generar_cartel(evento: dict) -> bytes   (JPEG 1080x1350, formato vertical para móvil)

- 0 tokens, instantáneo, nunca inventa: solo pinta los datos que hay.
- Colores por categoría con contraste alto (texto blanco sobre fondo oscuro, WCAG AA).
- Fuentes en api/fuentes/: Lilita One y Kalam (licencia OFL, ver OFL-LilitaOne-Kalam.txt)
  y DejaVu de respaldo. Funciona igual en Windows y en Render.
"""
from __future__ import annotations

import io
import os
from datetime import datetime

import json
import math
import random
import unicodedata

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

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


# ══════════════════════════════════════════════════════════════
#  Diseño "cartel de pueblo": letrero de madera con el título, cinta con lema,
#  tarjetas de papel con fecha / hora / lugar, pegatina de precio y franja de plazas.
# ══════════════════════════════════════════════════════════════
LEMAS = {
    "senderismo":   "¡Descubre nuestros caminos!",
    "deporte":      "¡Participa, disfruta y juega!",
    "cultura":      "¡Cultura para todos los vecinos!",
    "gastronomia":  "¡Disfruta de la buena comida en nuestro pueblo!",
    "fiestas":      "¡Vente de fiesta al pueblo!",
    "taller":       "¡Aprende con tus vecinos!",
    "voluntariado": "¡Échanos una mano!",
    "otro":         "¡Te esperamos en el pueblo!",
}
CON_GUIRNALDA = {"fiestas", "gastronomia", "cultura", "otro"}
PAPEL = (250, 240, 220)
MADERA = (233, 196, 140)
MADERA_OSCURA = (120, 74, 36)
TINTA = (58, 36, 18)
AMARILLO = (247, 196, 48)


def _f(nombre: str, tam: int):
    """Fuentes del cartel; si faltan, DejaVu (siempre está en api/fuentes)."""
    ruta = os.path.join(_FUENTES, nombre)
    if os.path.exists(ruta):
        return ImageFont.truetype(ruta, tam)
    return _fuente(tam, negrita=True)


def _titular(tam):
    return _f("LilitaOne-Regular.ttf", tam)


def _mano(tam, negrita=True):
    return _f("Kalam-Bold.ttf" if negrita else "Kalam-Regular.ttf", tam)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return Image.new("RGB", (1, 1), hex_color).getpixel((0, 0))


def _ajustar_titulo(d, texto: str, ancho: int, max_lineas: int, tamanos) -> tuple:
    """El tamaño más grande con el que el título cabe sin cortar palabras."""
    for tam in tamanos:
        f = _titular(tam)
        palabras = texto.split()
        if any(d.textlength(p, font=f) > ancho for p in palabras):
            continue  # una palabra no cabe entera: más pequeño
        lineas = _partir(d, texto, f, ancho, 99)
        if len(lineas) <= max_lineas:
            return f, tam, lineas
    f = _titular(tamanos[-1])
    return f, tamanos[-1], _partir(d, texto, f, ancho, max_lineas)


def _pegar_girado(lienzo: Image.Image, capa: Image.Image, centro: tuple[int, int], grados: float,
                  sombra: bool = True) -> None:
    """Pega una pieza (RGBA) girada, con sombra suave debajo."""
    girada = capa.rotate(grados, resample=Image.BICUBIC, expand=True)
    x = int(centro[0] - girada.width / 2)
    y = int(centro[1] - girada.height / 2)
    if sombra:
        mascara = girada.split()[3].point(lambda a: int(a * 0.45))
        sombra_img = Image.new("RGBA", girada.size, (0, 0, 0, 0))
        sombra_img.putalpha(mascara)
        sombra_img = sombra_img.filter(ImageFilter.GaussianBlur(12))
        lienzo.alpha_composite(sombra_img, (x + 8, y + 12))
    lienzo.alpha_composite(girada, (x, y))


def _papel_rasgado(ancho: int, alto: int, color, semilla: int) -> Image.Image:
    """Rectángulo de papel con los bordes irregulares."""
    rnd = random.Random(semilla)
    capa = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    puntos = []
    for x in range(0, ancho + 1, 24):
        puntos.append((x, rnd.randint(0, 9)))
    for y in range(0, alto + 1, 24):
        puntos.append((ancho - rnd.randint(0, 9), y))
    for x in range(ancho, -1, -24):
        puntos.append((x, alto - rnd.randint(0, 9)))
    for y in range(alto, -1, -24):
        puntos.append((rnd.randint(0, 9), y))
    d.polygon(puntos, fill=color)
    return capa


def _brocha(ancho: int, alto: int, color, semilla: int) -> Image.Image:
    """Trazo de rotulador/brocha (para lemas y la franja de plazas)."""
    rnd = random.Random(semilla)
    capa = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    arriba = [(x, rnd.randint(0, int(alto * 0.12))) for x in range(18, ancho - 17, 30)]
    abajo = [(x, alto - rnd.randint(0, int(alto * 0.12))) for x in range(ancho - 18, 17, -30)]
    d.polygon([(0, alto * 0.25)] + arriba + [(ancho, alto * 0.2), (ancho - 10, alto * 0.8)] + abajo
              + [(6, alto * 0.85)], fill=color)
    return capa


# ── Iconos dibujados (sin imágenes externas) ──
def _icono(d, tipo: str, cx: int, cy: int, r: int, color) -> None:
    w = max(4, r // 6)
    if tipo == "calendario":
        d.rounded_rectangle((cx - r, cy - r * 0.8, cx + r, cy + r), radius=r // 4, outline=color, width=w)
        d.rectangle((cx - r, cy - r * 0.8, cx + r, cy - r * 0.35), fill=color)
        for dx in (-r * 0.45, r * 0.45):
            d.line((cx + dx, cy - r * 1.05, cx + dx, cy - r * 0.6), fill=color, width=w)
        for fila in range(2):
            for col in range(3):
                x = cx - r * 0.55 + col * r * 0.55
                y = cy + fila * r * 0.45
                d.rectangle((x - r * 0.12, y - r * 0.1, x + r * 0.12, y + r * 0.14), fill=color)
    elif tipo == "reloj":
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=w)
        d.line((cx, cy, cx, cy - r * 0.6), fill=color, width=w)
        d.line((cx, cy, cx + r * 0.45, cy + r * 0.2), fill=color, width=w)
        d.ellipse((cx - w, cy - w, cx + w, cy + w), fill=color)
    elif tipo == "lugar":
        d.ellipse((cx - r * 0.75, cy - r, cx + r * 0.75, cy + r * 0.5), fill=color)
        d.polygon([(cx - r * 0.62, cy - r * 0.05), (cx + r * 0.62, cy - r * 0.05), (cx, cy + r * 1.05)], fill=color)
        d.ellipse((cx - r * 0.3, cy - r * 0.55, cx + r * 0.3, cy + r * 0.05), fill=PAPEL)
    elif tipo == "personas":
        for dx, esc in ((-r * 0.85, 0.75), (r * 0.85, 0.75), (0, 1.0)):
            cab = r * 0.32 * esc
            x = cx + dx
            d.ellipse((x - cab, cy - r * 0.55 * esc - cab, x + cab, cy - r * 0.55 * esc + cab), fill=color)
            d.rounded_rectangle((x - r * 0.48 * esc, cy - r * 0.1 * esc, x + r * 0.48 * esc, cy + r * 0.75),
                                radius=int(r * 0.3 * esc), fill=color)


def _guirnalda(lienzo: Image.Image) -> None:
    """Guirnalda de bombillas arriba (planes festivos)."""
    luces = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(luces)
    puntos = [(x, 34 + int(46 * math.sin(math.pi * x / ANCHO))) for x in range(-20, ANCHO + 21, 20)]
    d.line(puntos, fill=(40, 30, 20, 230), width=4, joint="curve")
    for x in range(40, ANCHO, 104):
        y = 34 + int(46 * math.sin(math.pi * x / ANCHO)) + 8
        d.rectangle((x - 7, y - 4, x + 7, y + 8), fill=(60, 50, 40, 255))
        d.ellipse((x - 22, y + 2, x + 22, y + 46), fill=(255, 214, 120, 90))
        d.ellipse((x - 12, y + 8, x + 12, y + 38), fill=(255, 236, 170, 255))
    brillo = luces.filter(ImageFilter.GaussianBlur(3))
    lienzo.alpha_composite(brillo)
    lienzo.alpha_composite(luces)


def _fondo_sin_foto(fondo_hex: str, acento_hex: str) -> Image.Image:
    """Fondo de color con degradado y lunares suaves, si no hay foto."""
    a, b = _rgb(fondo_hex), _rgb(acento_hex)
    img = Image.new("RGB", (ANCHO, ALTO), a)
    d = ImageDraw.Draw(img)
    for y in range(ALTO):
        t = y / ALTO
        d.line((0, y, ANCHO, y), fill=tuple(int(a[i] * (1 - 0.35 * t) + b[i] * 0.12 * (1 - t)) for i in range(3)))
    capa = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    rnd = random.Random(7)
    for _ in range(26):
        x, y, r = rnd.randint(0, ANCHO), rnd.randint(0, ALTO), rnd.randint(30, 140)
        dc.ellipse((x - r, y - r, x + r, y + r), fill=(*b, 22))
    return Image.alpha_composite(img.convert("RGBA"), capa)


def _textos_tarjetas(evento: dict, pueblo: str) -> list[str]:
    """[fecha, hora, lugar] con lo que haya (nunca se inventa)."""
    textos = []
    fecha = _fecha(evento.get("starts_at"))
    if fecha:
        anio = ""
        try:
            from ia import TZ
            anio = f", {datetime.fromisoformat(str(evento['starts_at']).replace('Z', '+00:00')).astimezone(TZ).year}"
        except Exception:
            pass
        textos.append(fecha[0] + anio)
        fin = _fecha(evento.get("ends_at"))
        textos.append(f"De {fecha[1]} a {fin[1]} h" if fin and fin[0] == fecha[0] else f"A partir de las {fecha[1]} h")
    lugar = evento.get("location_name")
    if lugar and lugar.lower() != "por confirmar":
        textos.append(lugar + (f" de {pueblo}" if pueblo and pueblo.lower() not in lugar.lower() else ""))
    elif pueblo:
        textos.append(f"{pueblo} · lugar por confirmar")
    return textos


def _cartel(evento: dict, foto: bytes | None, credito: str | None) -> bytes:
    cat = evento.get("category") or "otro"
    fondo_hex, acento_hex, nombre_cat = ESTILOS.get(cat, ESTILOS["otro"])
    fondo = _rgb(fondo_hex)

    # 1 · Fondo: foto del pueblo (más viva) o color
    if foto:
        base = ImageOps.fit(Image.open(io.BytesIO(foto)).convert("RGB"), (ANCHO, ALTO),
                            method=Image.LANCZOS, centering=(0.5, 0.45))
        base = ImageEnhance.Color(base).enhance(1.25)
        base = ImageEnhance.Contrast(base).enhance(1.06)
        lienzo = base.convert("RGBA")
    else:
        lienzo = _fondo_sin_foto(fondo_hex, acento_hex)

    # Velos para que se lea todo: suave arriba, fuerte abajo
    velo = Image.new("RGBA", (ANCHO, ALTO), (0, 0, 0, 0))
    dv = ImageDraw.Draw(velo)
    for y in range(ALTO):
        if y < 260:
            a = int(110 * (1 - y / 260))
        elif y > 640:
            a = int(205 * min(1, (y - 640) / 520))
        else:
            a = 0
        if a:
            dv.line((0, y, ANCHO, y), fill=(12, 10, 8, a))
    lienzo.alpha_composite(velo)
    d = ImageDraw.Draw(lienzo)

    if cat in CON_GUIRNALDA:
        _guirnalda(lienzo)

    # 2 · Sello del pueblo (arriba a la derecha)
    pueblo = (evento.get("municipio") or "").strip()
    if pueblo:
        sello = Image.new("RGBA", (300, 230), (0, 0, 0, 0))
        ds = ImageDraw.Draw(sello)
        ds.ellipse((6, 6, 294, 224), fill=(*fondo, 245), outline=(255, 255, 255, 235), width=5)
        f_p = _mano(44 if len(pueblo) <= 10 else 34)
        lineas = _partir(ds, pueblo, f_p, 230, 2)
        yy = 115 - (len(lineas) * 46 + 34) // 2
        for linea in lineas:
            ds.text((150, yy), linea, font=f_p, fill="white", anchor="ma")
            yy += 46
        ds.text((150, yy + 2), "(Almería)", font=_mano(30, False), fill=(255, 255, 255, 230), anchor="ma")
        _pegar_girado(lienzo, sello, (ANCHO - 175, 205), 6)

    # 3 · Letrero de madera con el título (si no cabe todo, se reduce un poco)
    titulo = (evento.get("title") or "Evento").strip()
    medida = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    limite_y = ALTO - 245  # donde empieza la franja de plazas
    for escala in (1.0, 0.9, 0.8, 0.7):
        tams = tuple(int(t * escala) for t in (104, 92, 80, 70, 62, 54))
        f_t, tam_t, lineas_t = _ajustar_titulo(medida, titulo, 640, 3, tams)
        alto_t = int(len(lineas_t) * tam_t * 1.08) + 70
        f_l = _mano(int(44 * escala))
        f_d = _mano(int(42 * escala))
        alto_cinta = 70 + int(52 * escala) * len(_partir(medida, LEMAS.get(cat, LEMAS["otro"]), f_l, 600, 2))
        alto_tarjetas = sum(40 + int(50 * escala) * len(_partir(medida, t, f_d, 470, 3)) + 34
                            for t in _textos_tarjetas(evento, pueblo))
        if 140 + alto_t + 10 + alto_cinta + 40 + alto_tarjetas <= limite_y:
            break
    letrero = Image.new("RGBA", (720, alto_t), (0, 0, 0, 0))
    dl = ImageDraw.Draw(letrero)
    dl.rounded_rectangle((0, 0, 719, alto_t - 1), radius=26, fill=(*MADERA, 255), outline=(*MADERA_OSCURA, 255), width=6)
    dl.line((22, 16, 698, 18), fill=(*MADERA_OSCURA, 55), width=2)  # tablones arriba y abajo
    dl.line((22, alto_t - 18, 698, alto_t - 16), fill=(*MADERA_OSCURA, 55), width=2)
    yy = 35
    for linea in lineas_t:
        dl.text((360, yy), linea, font=f_t, fill=TINTA, anchor="ma")
        yy += int(tam_t * 1.08)
    centro_letrero = (400, 140 + alto_t // 2)
    for x in (centro_letrero[0] - 250, centro_letrero[0] + 250):  # cuerdas
        d.line((x, 0, x + 4, centro_letrero[1] - alto_t // 2 + 14), fill=(150, 110, 60, 255), width=8)
    _pegar_girado(lienzo, letrero, centro_letrero, 2)
    y = centro_letrero[1] + alto_t // 2 + 10

    # 4 · Cinta amarilla con el lema
    lema = LEMAS.get(cat, LEMAS["otro"])
    paso_l = int(52 * escala)
    lineas_l = _partir(d, lema, f_l, 600, 2)
    cinta = _brocha(700, 70 + paso_l * len(lineas_l), (*AMARILLO, 250), 3)
    dc = ImageDraw.Draw(cinta)
    yy = 38
    for linea in lineas_l:
        dc.text((350, yy), linea, font=f_l, fill=TINTA, anchor="ma")
        yy += paso_l
    _pegar_girado(lienzo, cinta, (420, y + cinta.height // 2 + 6), 3)
    y += cinta.height + 40

    # 5 · Tarjetas de papel: fecha, hora y lugar
    tarjetas = list(zip(("calendario", "reloj", "lugar"), _textos_tarjetas(evento, pueblo)))         if evento.get("starts_at") else [("lugar", t) for t in _textos_tarjetas(evento, pueblo)]

    paso_d = int(50 * escala)
    for k, (icono, texto) in enumerate(tarjetas):
        lineas = _partir(d, texto, f_d, 470, 3)
        alto = 40 + paso_d * len(lineas)
        papel = _papel_rasgado(620, alto + 20, (*PAPEL, 248), k + 11)
        dp = ImageDraw.Draw(papel)
        _icono(dp, icono, 62, (alto + 20) // 2, 30, (*fondo, 255))
        yy = (alto + 20) // 2 - paso_d // 2 * len(lineas) - 4
        for linea in lineas:
            dp.text((118, yy), linea, font=f_d, fill=TINTA)
            yy += paso_d
        _pegar_girado(lienzo, papel, (60 + 310, y + papel.height // 2), (-1.5, 1, -0.5)[k % 3])
        y += papel.height + 14

    # 6 · Pegatina con el precio (a la derecha de las tarjetas)
    precio = evento.get("price")
    if precio is not None:
        p = float(precio)
        texto_p = "GRATIS" if p == 0 else f"{p:g} €".replace(".", ",")
        peg = Image.new("RGBA", (260, 260), (0, 0, 0, 0))
        dg = ImageDraw.Draw(peg)
        puntas = []
        for i in range(32):
            ang = math.pi * 2 * i / 32
            rr = 128 if i % 2 == 0 else 112
            puntas.append((130 + rr * math.cos(ang), 130 + rr * math.sin(ang)))
        dg.polygon(puntas, fill=(*AMARILLO, 255))
        dg.ellipse((30, 30, 230, 230), outline=(*TINTA, 255), width=4)
        f_pr = _titular(50 if len(texto_p) <= 6 else 42)
        dg.text((130, 108), texto_p, font=f_pr, fill=TINTA, anchor="mm")
        dg.text((130, 160), "entrada" if p == 0 else "por persona", font=_mano(28), fill=TINTA, anchor="mm")
        _pegar_girado(lienzo, peg, (ANCHO - 175, min(y - 160, ALTO - 470)), -10)

    # 7 · Franja de plazas / accesibilidad
    partes = []
    if evento.get("max_capacity"):
        partes.append(f"Plazas limitadas: {evento['max_capacity']} personas")
    else:
        partes.append("Plazas sin límite")
    if evento.get("is_accessible"):
        partes.append("Accesible")
    franja = _brocha(ANCHO - 70, 150, (*fondo, 245), 5)
    df = ImageDraw.Draw(franja)
    _icono(df, "personas", 90, 70, 40, (255, 255, 255, 255))
    df.text((170, 24), "¡Apúntate ya!", font=_mano(48), fill="white")
    df.text((170, 80), " · ".join(partes), font=_mano(34, False), fill=(255, 255, 255, 235))
    _pegar_girado(lienzo, franja, (ANCHO // 2, ALTO - 150), -1.5)

    # 8 · Pie y crédito de la foto (obligatorio en las de Wikimedia Commons)
    d = ImageDraw.Draw(lienzo)
    d.text((ANCHO // 2, ALTO - 46), "Reactiva tu pueblo · Apúntate en la web o en Telegram",
           font=_fuente(26), fill=(255, 255, 255, 235), anchor="mm")
    out = lienzo.convert("RGB")
    if credito:
        d2 = ImageDraw.Draw(out)
        f_cr = _fuente(18, negrita=False)
        w = d2.textlength(credito, font=f_cr)
        d2.text((ANCHO - 24 - w, ALTO - 24), credito, font=f_cr, fill="white", stroke_width=2, stroke_fill="black")

    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _cartel_con_foto(evento: dict, foto: bytes, credito: str | None) -> bytes:
    return _cartel(evento, foto, credito)


def _cartel_color(evento: dict) -> bytes:
    """Sin ninguna foto: el mismo diseño sobre un fondo de color."""
    return _cartel(evento, None, None)
