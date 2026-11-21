"""
Genera los 8 carteles SINTÉTICOS del set de pruebas (api/tests/carteles/*.jpg).
Son inventados a propósito (para medir el extractor), nunca se guardan en la BD.
Cada uno prueba un caso difícil; lo esperado está en golden.json.

Uso (desde la carpeta api):  python tests/generar_carteles.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter

AQUI = os.path.dirname(os.path.abspath(__file__))
DESTINO = os.path.join(AQUI, "carteles")

CARTELES = {
    # nombre: (color de fondo, líneas, ¿borroso?)
    "cartel_01_con_anio": ("#fdf3d0", ["ROMERÍA DE SANTIAGO", "Lucainena de las Torres",
                                       "Sábado 25 de julio de 2026", "Salida 10:00 h desde la Plaza",
                                       "Entrada libre"], False),
    "cartel_02_sin_anio": ("#e3f1ff", ["TALLER DE ESPARTO", "Centro social de Tahal",
                                       "Viernes 11 de diciembre", "18:00 h", "Precio: 5 €"], False),
    "cartel_03_sin_hora": ("#e8ffe3", ["MERCADILLO SOLIDARIO", "Plaza Mayor de Senés",
                                       "Domingo 13 de diciembre de 2026", "¡Os esperamos!"], False),
    "cartel_04_gratis": ("#fff0f5", ["CINE DE VERANO EN INVIERNO", "Salón social de Velefique",
                                     "Jueves 3 de diciembre de 2026 · 19:30 h", "GRATIS"], False),
    "cartel_05_donativo": ("#f3e8ff", ["CONCIERTO DE NAVIDAD", "Iglesia de Uleila del Campo",
                                       "Sábado 19 de diciembre de 2026 · 20:00 h",
                                       "Donativo: 3 € para Cáritas"], False),
    "cartel_06_inscripcion": ("#fffbe0", ["RUTA DE LAS MINAS", "Lucainena de las Torres",
                                          "Domingo 6 de diciembre de 2026 · 9:00 h",
                                          "Plazas: 30 · Inscripción hasta el 3 de diciembre",
                                          "Precio: 2 €"], False),
    "cartel_07_spam": ("#ffffff", ["¡¡GANA 5.000 € DESDE CASA!!", "Apuesta ya en www.apuestas-x.biz",
                                   "Bono de bienvenida 200%", "Sin riesgo, 100% seguro"], False),
    "cartel_08_borroso": ("#f0f0f0", ["FIESTA DE LA INMACULADA", "Velefique",
                                      "Martes 8 de diciembre de 2026", "18:00 h · Plaza de la Iglesia",
                                      "Chocolate con migas"], True),
}


def main():
    os.makedirs(DESTINO, exist_ok=True)
    for nombre, (fondo, lineas, borroso) in CARTELES.items():
        img = Image.new("RGB", (900, 1200), fondo)
        d = ImageDraw.Draw(img)
        d.rectangle([30, 30, 870, 1170], outline="#333333", width=6)
        y = 160
        for k, linea in enumerate(lineas):
            tam = 64 if k == 0 else 44
            d.text((70, y), linea, fill="#111111", font_size=tam)
            y += 150 if k == 0 else 110
        if borroso:
            img = img.filter(ImageFilter.GaussianBlur(3))
        img.save(os.path.join(DESTINO, f"{nombre}.jpg"), "JPEG", quality=85)
    print(f"{len(CARTELES)} carteles en {DESTINO}")


if __name__ == "__main__":
    main()
