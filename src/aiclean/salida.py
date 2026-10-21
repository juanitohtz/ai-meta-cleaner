"""Salida por pantalla (rich, en español).

Colores sobrios: verde para lo correcto (limpio, sin cambios), rojo para los errores y gris para
lo secundario; el resto en el color normal. El texto dice lo mismo sin colores, que se quitan solos
si la salida está redirigida, con NO_COLOR o con TERM=dumb (lo hace rich).
"""

from __future__ import annotations

import sys
import textwrap

from rich.console import Console
from rich.markup import escape
from rich.padding import Padding
from rich.table import Table

from aiclean.modelos import Estado, Hallazgo, Resultado

# soft_wrap: una línea por archivo aunque la salida vaya redirigida a un log
_consola = Console(highlight=False, soft_wrap=True)


def usar_stderr() -> None:
    """Todo lo que se muestra va a stderr (cuando stdout lleva el texto limpio)."""
    global _consola
    _consola = Console(highlight=False, soft_wrap=True, stderr=True)


def consola() -> Console:
    return _consola


VERDE, ROJO, GRIS = "green", "red", "dim"
ANCHO_AYUDA = 100

_ESTADOS = {
    Estado.LIMPIO: ("limpio", VERDE),
    Estado.PARCIAL: ("parcial", ""),
    Estado.SIN_CAMBIOS: ("sin cambios", VERDE),  # el mensaje dice "no se encontró nada en lo inspeccionado"
    Estado.ENCONTRADO: ("encontrado", ""),
    Estado.OMITIDO: ("omitido", GRIS),
    Estado.ERROR: ("error", ROJO),
}


def pintar(texto: str, estilo: str) -> str:
    """Markup de rich con el texto escapado (sin estilo, el texto tal cual)."""
    return f"[{estilo}]{escape(texto)}[/]" if estilo else escape(texto)


def flecha() -> str:
    # Redirigida, la salida puede ir en cp1252, que no tiene "→"
    return "→" if _consola.is_terminal else "->"

CATEGORIAS = {
    "invisible": "invisible",
    "espacio": "espacio raro",
    "formato": "carácter de formato (SHY/WJ)",
    "bidi": "control bidi",
    "etiqueta": "tags Unicode",
    "selector": "selector de variación",
    "windows": "Windows",
    "procedencia": "procedencia",
    "portapapeles": "portapapeles",
    "no_tratado": "no tratado",
    "c2pa": "C2PA",
    "xmp": "XMP",
    "exif": "EXIF",
    "iptc": "IPTC",
    "png": "PNG",
    "comentario": "comentario",
    "app": "segmento APP",
}


_PLURALES = {
    "invisible": "invisibles",
    "espacio": "espacios raros",
    "formato": "caracteres de formato (SHY/WJ)",
    "bidi": "controles bidi",
    "selector": "selectores de variación",
    "windows": "campos de la marca de la web",
    "procedencia": "datos de procedencia",
    "no_tratado": "sin tratar",
    "c2pa": "manifiestos C2PA",
    "xmp": "campos XMP",
    "exif": "campos EXIF",
    "iptc": "campos IPTC",
    "png": "datos PNG",
    "comentario": "comentarios",
    "app": "segmentos APP",
}
_SINGULARES_RESUMEN = {"windows": "campo de la marca de la web", "procedencia": "dato de procedencia", "no_tratado": "sin tratar"}


def resumen_hallazgos(hallazgos: list[Hallazgo]) -> str:
    cuentas: dict[str, int] = {}
    for h in hallazgos:
        cuentas[h.categoria] = cuentas.get(h.categoria, 0) + h.cantidad
    partes = []
    for cat, n in cuentas.items():
        if n == 1:
            nombre = _SINGULARES_RESUMEN.get(cat, CATEGORIAS.get(cat, cat))
        else:
            nombre = _PLURALES.get(cat, CATEGORIAS.get(cat, cat))
        partes.append(f"{n} {nombre}")
    return ", ".join(partes)


def mostrar_resultado(res: Resultado, detalle: bool, sensible: bool = False) -> None:
    """Una línea por fuente; con detalle, un renglón por hallazgo.

    Los valores sensibles (texto oculto, URL completas) solo salen con sensible=True
    (--report-sensitive); si no, se muestra su resumen seguro.
    """
    etiqueta, color = _ESTADOS[res.estado]
    linea = f"{pintar(f'{etiqueta:<12}', color)}{escape(res.fuente)}"
    if res.destino and res.destino != res.fuente:
        linea += f" {pintar(flecha(), GRIS)} {pintar(res.destino, GRIS)}"
    if res.hallazgos:
        linea += f"  {pintar('(' + resumen_hallazgos(res.hallazgos) + ')', GRIS)}"
    if res.mensaje:
        linea += f"  {pintar(res.mensaje, ROJO if res.estado is Estado.ERROR else GRIS)}"
    _consola.print(linea)
    if detalle:
        for h in res.hallazgos:
            mostrar_hallazgo(h, sensible)


def mostrar_hallazgo(h: Hallazgo, sensible: bool = False) -> None:
    partes = [f"{escape(CATEGORIAS.get(h.categoria, h.categoria))}: {escape(h.clave)}"]
    if h.cantidad > 1:
        partes.append(f"×{h.cantidad}")
    if h.ubicacion:
        partes.append(f"[dim]en {escape(h.ubicacion)}[/]")
    if h.posiciones:
        mas = "…" if h.cantidad > len(h.posiciones) else ""
        partes.append(f"[dim]({escape('; '.join(h.posiciones))}{mas})[/]")
    if not h.es_ia:
        partes.append("[dim][--all][/]")
    _consola.print("    · " + " ".join(partes))
    detalle = h.valor if sensible else h.resumen
    if detalle:
        _consola.print(f"      [dim]{escape(detalle)}[/]")


def aviso(texto: str) -> None:
    _consola.print(escape(texto))


def error(texto: str) -> None:
    Console(stderr=True, highlight=False).print(pintar(f"aiclean: {texto}", ROJO))


# ---------------------------------------------------------------------------
# Ayuda, bienvenida del doble clic y pausa

Seccion = tuple[str, list[tuple[str, str]], str]          # título, filas (columna, texto), nota


def texto_ayuda(cabecera: str, secciones: list[Seccion]) -> str:
    """La ayuda en texto plano: una línea por opción, como mucho ANCHO_AYUDA columnas."""
    lineas = [cabecera]
    for titulo, filas, nota in secciones:
        ancho = max(len(a) for a, _ in filas)
        lineas += ["", titulo]
        for a, b in filas:
            lineas.append(f"  {a:<{ancho}}  {b}".rstrip().replace("→", "->"))   # cp1252 no tiene "→"
        if nota:
            lineas += textwrap.wrap(nota, ANCHO_AYUDA - 2, initial_indent="  ", subsequent_indent="  ")
    return "\n".join(lineas) + "\n"


def mostrar_ayuda(cabecera: str, secciones: list[Seccion], archivo=None, terminal: bool | None = None) -> None:
    """En una consola, con títulos en negrita y columnas alineadas; redirigida, texto_ayuda().
    terminal: forzar o no la consola (para los tests)."""
    archivo = archivo or sys.stdout
    c = Console(file=archivo, highlight=False, force_terminal=terminal)
    if not c.is_terminal:
        archivo.write(texto_ayuda(cabecera, secciones))
        archivo.flush()
        return
    c = Console(file=archivo, highlight=False, force_terminal=terminal, width=min(c.width, ANCHO_AYUDA))
    c.print(pintar(cabecera, "bold"))
    for titulo, filas, nota in secciones:
        c.print()
        c.print(pintar(titulo, "bold"))
        tabla = Table.grid(padding=(0, 2))
        tabla.add_column(no_wrap=True)
        tabla.add_column()
        for a, b in filas:
            tabla.add_row(escape(a), escape(b))
        c.print(Padding(tabla, (0, 0, 0, 2)))
        if nota:
            c.print(Padding(pintar(nota, GRIS), (0, 0, 0, 2)))


def bienvenida(version: str) -> None:
    c = _consola
    c.print(pintar(f"aiclean {version}", "bold"))
    c.print("Quita metadatos y marcas de procedencia de IA de texto, JPEG y PNG,")
    c.print("sin recomprimir y verificando cada archivo. El original nunca se toca:")
    c.print("al lado se crea una copia limpia (nombre_clean.ext).")
    c.print(pintar("Desde una terminal: aiclean --help para todas las opciones.", GRIS))


def pausa() -> None:
    """Espera Enter antes de que se cierre la ventana (doble clic o archivos arrastrados)."""
    _consola.print()
    _consola.print("Pulsa Enter para salir.", end="")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def preparar_flujos() -> None:
    """Una salida redirigida en cp1252 no debe romperse con caracteres que no caben."""
    for flujo in (sys.stdout, sys.stderr):
        if flujo is not None and hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="replace")
