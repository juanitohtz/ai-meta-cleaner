"""Genera el icono de aiclean desde una sola definición de formas (solo desarrollo: usa Pillow).

    python assets\\generar_icono.py                  escribe icono.svg, icono-pequeno.svg e icono.ico
    python assets\\generar_icono.py --comprobar      falla si los archivos de assets\\ no son los que genera
    python assets\\generar_icono.py --vista-previa P  PNG con 16, 32 y 256 px sobre fondo claro y oscuro

Idea: una página clara con un destello de cuatro puntas ámbar (el «brillo de IA») al que una goma
verde ha borrado la parte de abajo a la izquierda. Hay dos variantes de las mismas formas: la
grande (32 px o más) y una simplificada, con trazos más gruesos y sin esquina doblada, para 16-24 px.
Los colores son los del sistema de diseño de las apps de escritorio.
"""

from __future__ import annotations

import io
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

AQUI = Path(__file__).resolve().parent
TAMANOS = [16, 20, 24, 32, 40, 48, 64, 128, 256]
PEQUENOS = {16, 20, 24}
SOBREMUESTREO = 8

FONDO, BORDE = "#121212", "#2C2C2C"
PAGINA, DOBLEZ = "#EDEDED", "#B8B8B8"
AMBAR = "#E0A84A"
VERDE, VERDE_OSCURO = "#3DBA4E", "#2B8F3A"

# Formas en un lienzo de 256 x 256. Cada variante: losa, página, destello, corte y goma.
# El corte es la recta x - y = corte: lo que queda por debajo a la izquierda está borrado. La goma
# se coloca sola junto al corte, del lado borrado (hueco = separación; avance = a lo largo del corte).
VARIANTES = {
    "grande": {
        "losa": (8, 8, 248, 248, 52), "borde": 5,
        "pagina": (58, 30, 198, 226, 18), "doblez": 40,
        "destello": (138, 112, 66, 0.30),            # centro x, centro y, radio de las puntas, cintura
        "corte": 0,
        "goma": (128, 46, 10, 6, 0), "funda": 0.34,  # largo, grueso, radio, hueco, avance
    },
    "pequeno": {
        "losa": (4, 4, 252, 252, 56), "borde": 0,
        "pagina": (40, 20, 216, 236, 26), "doblez": 0,
        "destello": (142, 108, 88, 0.34),
        "corte": 4,
        "goma": (150, 66, 16, 8, 0), "funda": 0.0,
    },
}


# ---------------------------------------------------------------------------
# Geometría (puntos en el lienzo de 256)

def puntos_destello(cx: float, cy: float, radio: float, cintura: float, pasos: int = 16) -> list[tuple[float, float]]:
    """Estrella de 4 puntas con lados cóncavos: curvas cuadráticas entre puntas, con el punto de
    control cerca del centro (cintura = fracción del radio)."""
    puntas = [(cx, cy - radio), (cx + radio, cy), (cx, cy + radio), (cx - radio, cy)]
    puntos = []
    for i in range(4):
        a, b = puntas[i], puntas[(i + 1) % 4]
        angulo = math.radians(-45 + 90 * i)
        control = (cx + math.cos(angulo) * radio * cintura, cy + math.sin(angulo) * radio * cintura)
        for k in range(pasos):
            t = k / pasos
            puntos.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * control[0] + t ** 2 * b[0],
                           (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * control[1] + t ** 2 * b[1]))
    return puntos


def rectangulo_girado(cx: float, cy: float, largo: float, grueso: float, radio: float, grados: float,
                      pasos: int = 8) -> list[tuple[float, float]]:
    """Rectángulo de esquinas redondeadas, centrado en (cx, cy) y girado."""
    x0, y0, x1, y1 = -largo / 2, -grueso / 2, largo / 2, grueso / 2
    esquinas = [(x1 - radio, y0 + radio, -90), (x1 - radio, y1 - radio, 0),
                (x0 + radio, y1 - radio, 90), (x0 + radio, y0 + radio, 180)]
    local = []
    for ex, ey, inicio in esquinas:
        for k in range(pasos + 1):
            a = math.radians(inicio + 90 * k / pasos)
            local.append((ex + radio * math.cos(a), ey + radio * math.sin(a)))
    g = math.radians(grados)
    return [(cx + x * math.cos(g) - y * math.sin(g), cy + x * math.sin(g) + y * math.cos(g)) for x, y in local]


def pagina(v: dict, pasos: int = 8) -> list[tuple[float, float]]:
    """Contorno de la página: esquinas redondeadas y, si hay doblez, la de arriba a la derecha cortada."""
    x0, y0, x1, y1, r = v["pagina"]
    d = v["doblez"]

    def arco(cx, cy, inicio):
        return [(cx + r * math.cos(math.radians(inicio + 90 * k / pasos)),
                 cy + r * math.sin(math.radians(inicio + 90 * k / pasos))) for k in range(pasos + 1)]

    arriba_derecha = [(x1 - d, y0), (x1, y0 + d)] if d else arco(x1 - r, y0 + r, -90)
    return (arco(x0 + r, y0 + r, 180) + arriba_derecha + arco(x1 - r, y1 - r, 0) + arco(x0 + r, y1 - r, 90))


def goma(v: dict) -> tuple[list, list]:
    largo, grueso, r, hueco, avance = v["goma"]
    cx0, cy0, _, _ = v["destello"]
    k = v["corte"]
    # Punto del corte más cercano al centro del destello, desplazado hacia el lado borrado
    m = (cx0 - cy0 - k) / 2
    px, py = cx0 - m, cy0 + m
    n = (grueso / 2 + hueco) / math.sqrt(2)
    a = avance / math.sqrt(2)
    cx, cy = px - n + a, py + n + a
    cuerpo = rectangulo_girado(cx, cy, largo, grueso, r, 45)
    funda = []
    if v["funda"]:
        # La funda (parte oscura) en el extremo de abajo a la izquierda
        f = largo * v["funda"]
        d = (largo - f) / 2
        g = math.radians(45)
        funda = rectangulo_girado(cx - d * math.cos(g), cy - d * math.sin(g), f, grueso, r, 45)
    return cuerpo, funda


# ---------------------------------------------------------------------------
# SVG

def _p(puntos) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in puntos)


def svg(nombre: str) -> str:
    v = VARIANTES[nombre]
    x0, y0, x1, y1, r = v["losa"]
    px0, py0, px1, py1, pr = v["pagina"]
    cuerpo, funda = goma(v)
    k = v["corte"]
    partes = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">',
        "  <title>aiclean</title>",
        f'  <defs><clipPath id="sin-borrar"><polygon points="{_p([(k, 0), (256, 0), (256, 256 - k)])}"/></clipPath></defs>',
        f'  <rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{r}" fill="{FONDO}"'
        + (f' stroke="{BORDE}" stroke-width="{v["borde"]}"/>' if v["borde"] else "/>"),
    ]
    partes.append(f'  <polygon points="{_p(pagina(v))}" fill="{PAGINA}"/>')
    if v["doblez"]:
        d = v["doblez"]
        partes.append(f'  <polygon points="{_p([(px1 - d, py0), (px1, py0 + d), (px1 - d, py0 + d)])}" fill="{DOBLEZ}"/>')
    partes.append(f'  <polygon points="{_p(puntos_destello(*v["destello"]))}" fill="{AMBAR}" clip-path="url(#sin-borrar)"/>')
    partes.append(f'  <polygon points="{_p(cuerpo)}" fill="{VERDE}"/>')
    if funda:
        partes.append(f'  <polygon points="{_p(funda)}" fill="{VERDE_OSCURO}"/>')
    partes.append("</svg>")
    return "\n".join(partes) + "\n"


# ---------------------------------------------------------------------------
# Pillow

def dibujar(tamano: int) -> Image.Image:
    v = VARIANTES["pequeno" if tamano in PEQUENOS else "grande"]
    lado = tamano * SOBREMUESTREO
    f = lado / 256

    def esc(puntos):
        return [(x * f, y * f) for x, y in puntos]

    im = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1, r = v["losa"]
    d.rounded_rectangle((x0 * f, y0 * f, x1 * f, y1 * f), radius=r * f, fill=FONDO,
                        outline=BORDE if v["borde"] else None, width=round(v["borde"] * f) if v["borde"] else 0)
    px0, py0, px1, py1, pr = v["pagina"]
    d.polygon(esc(pagina(v)), fill=PAGINA)
    if v["doblez"]:
        dd = v["doblez"]
        d.polygon(esc([(px1 - dd, py0), (px1, py0 + dd), (px1 - dd, py0 + dd)]), fill=DOBLEZ)
    # Destello, recortado por la diagonal x - y = corte (lo de abajo a la izquierda está borrado)
    capa = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(capa).polygon(esc(puntos_destello(*v["destello"])), fill=AMBAR)
    mascara = Image.new("L", im.size, 0)
    k = v["corte"]
    ImageDraw.Draw(mascara).polygon(esc([(k, 0), (256, 0), (256, 256 - k)]), fill=255)
    im.paste(capa, (0, 0), Image.composite(capa, Image.new("RGBA", im.size), mascara).getchannel("A"))
    cuerpo, funda = goma(v)
    d.polygon(esc(cuerpo), fill=VERDE)
    if funda:
        d.polygon(esc(funda), fill=VERDE_OSCURO)
    return im.resize((tamano, tamano), Image.Resampling.LANCZOS)


def ico() -> bytes:
    imagenes = [dibujar(t) for t in TAMANOS]
    b = io.BytesIO()
    imagenes[-1].save(b, "ICO", sizes=[(t, t) for t in TAMANOS], append_images=imagenes[:-1])
    return b.getvalue()


def archivos() -> dict[str, bytes]:
    return {"icono.svg": svg("grande").encode("ascii"), "icono-pequeno.svg": svg("pequeno").encode("ascii"),
            "icono.ico": ico()}


def vista_previa(destino: Path) -> None:
    tam = [16, 32, 256]
    escala = {16: 8, 32: 4, 256: 1}                  # ampliados sin suavizar para ver los píxeles
    celda = 256 + 32
    hoja = Image.new("RGB", (celda * 6, celda * 2 + 40), "#FFFFFF")
    d = ImageDraw.Draw(hoja)
    for fila, (fondo, texto) in enumerate((("#F2F2F2", "#333333"), ("#121212", "#CCCCCC"))):
        d.rectangle((0, fila * (celda + 20), hoja.width, fila * (celda + 20) + celda + 20), fill=fondo)
        for col, t in enumerate(tam):
            real = dibujar(t)
            # el tamaño real a la izquierda y ampliado a la derecha
            x = col * 2 * celda + 16
            y = fila * (celda + 20) + 16
            hoja.paste(real, (x, y), real)
            grande = real.resize((t * escala[t], t * escala[t]), Image.Resampling.NEAREST)
            hoja.paste(grande, (x + celda, y), grande)
            d.text((x, y + 256 + 4), f"{t} px (real y x{escala[t]})", fill=texto)
    hoja.save(destino)


def main(args: list[str]) -> int:
    if args[:1] == ["--vista-previa"]:
        vista_previa(Path(args[1]))
        return 0
    nuevos = archivos()
    if args[:1] == ["--comprobar"]:
        distintos = [n for n, datos in nuevos.items() if not (AQUI / n).is_file() or (AQUI / n).read_bytes() != datos]
        if distintos:
            print("el icono no coincide con lo que genera el script:", ", ".join(distintos))
            return 1
        print("icono comprobado:", ", ".join(nuevos))
        return 0
    for nombre, datos in nuevos.items():
        (AQUI / nombre).write_bytes(datos)
    print("escritos:", ", ".join(nuevos))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
