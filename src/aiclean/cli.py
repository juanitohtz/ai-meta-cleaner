"""Argumentos de la línea de comandos y códigos de salida.

Códigos (si se dan varios casos, gana el primero de esta lista):
  1 algún error · 4 algún resultado parcial (queda algo sin tratar) ·
  3 --dry-run encontró algo que quitar · 0 bien.
  2 uso incorrecto · 130 cancelado con Ctrl+C.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aiclean import __version__, informe, interactivo, motor, salida, windows
from aiclean.formatos import texto
from aiclean.herramientas import rutas
from aiclean.modelos import Estado, Modo

SALIDA_OK, SALIDA_ERROR, SALIDA_USO, SALIDA_ENCONTRADO, SALIDA_PARCIAL = 0, 1, 2, 3, 4

CABECERA = f"aiclean {__version__} · quita metadatos y marcas de procedencia de IA (texto, JPEG y PNG)"

# Solo formatos soportados en esta versión (lo comprueba un test)
EJEMPLOS = [
    ("aiclean foto.png", "crea foto_clean.png; el original no se toca"),
    ("aiclean imagen.png --dry-run", "muestra qué hay de IA, sin tocar nada"),
    ("aiclean C:\\Fotos -r -o C:\\Limpias", "toda la carpeta, con el mismo árbol en otra"),
    ("aiclean *.txt --in-place --report", "sobrescribe y muestra un resumen"),
    ("aiclean --clipboard", "limpia el texto copiado (p. ej. de ChatGPT)"),
    ('cmd /c "aiclean - < x.txt > y.txt"', "entrada estándar → salida estándar"),
]

AYUDA: list[salida.Seccion] = [
    ("USO", [
        ("aiclean [opciones] RUTA ...", "limpia archivos o carpetas"),
        ("aiclean [opciones] -", "entrada estándar → salida estándar"),
        ("aiclean --clipboard [opciones]", "limpia el portapapeles"),
    ], "Con doble clic (o arrastrando archivos o carpetas sobre aiclean.exe) se abre el modo interactivo: "
       "pide rutas, muestra cada paso y crea copias nombre_clean.ext."),
    ("ENTRADAS", [
        ("RUTA ...", "archivos o carpetas; admite comodines (*.png)"),
        ("-", "lee de la entrada estándar y escribe el texto limpio en la salida estándar"),
        ("-r, --recursive", "entra también en las subcarpetas"),
        ("--clipboard", "limpia el texto y el HTML del portapapeles"),
    ], ""),
    ("QUÉ SE HACE", [
        ("(por defecto)", "quita solo lo que delata una IA"),
        ("--all", "quita todos los metadatos prescindibles"),
        ("--dry-run", "muestra lo que encuentra, sin tocar nada"),
        ("--normalize", "texto plano: espacios raros → espacio normal; quita SHY y WJ"),
    ], "--normalize puede cambiar la tipografía y dónde se cortan las líneas; no se aplica a HTML, XML ni SVG."),
    ("DÓNDE SE GUARDA", [
        ("(por defecto)", "crea nombre_clean.ext junto al original"),
        ("-o, --output-dir CARPETA", "guarda los limpios en esa carpeta, con el mismo árbol"),
        ("--in-place", "sobrescribe el original (reemplazo atómico y verificado)"),
    ], ""),
    ("OTRAS OPCIONES", [
        ("--report", "resumen al final"),
        ("--report-file ARCHIVO", "informe completo en JSON"),
        ("--report-sensitive", "incluye los valores sensibles (prompts, URL completas)"),
        ("-j, --jobs N", "archivos en paralelo (por defecto 1)"),
        ("--exiftool RUTA", "exiftool.exe para las imágenes (por defecto, el de tools\\)"),
        ("-V, --version", "muestra la versión"),
        ("-h, --help", "muestra esta ayuda"),
    ], "Sin --report-sensitive, la pantalla y el JSON solo dicen qué campo y dónde; los errores nunca muestran "
       "contenido de los archivos."),
    ("EJEMPLOS", EJEMPLOS,
     "En PowerShell 5.1 la tubería pasa el texto en ASCII y los invisibles llegan como «?»: usa archivos, "
     "--clipboard o cmd /c."),
    ("CÓDIGOS DE SALIDA", [
        ("0", "bien"), ("1", "algún error"), ("2", "uso incorrecto"), ("3", "--dry-run encontró algo que quitar"),
        ("4", "parcial: queda algo sin tratar"), ("130", "cancelado con Ctrl+C"),
    ], "Si se dan varios casos, gana el primero de esta lista: 1, 4, 3, 0."),
]

USO_CORTO = "uso: aiclean [opciones] RUTA ...   (aiclean --help para ver todas las opciones)\n"

_TRADUCCIONES = (
    ("the following arguments are required", "faltan argumentos"),
    ("unrecognized arguments", "argumentos no reconocidos"),
    ("expected one argument", "falta el valor"),
    ("not allowed with argument", "no se puede usar junto con"),
    ("invalid int value", "número no válido"),
    ("ambiguous option", "opción ambigua"),
    ("could match", "puede ser"),
    ("argument ", "opción "),
)


class _Parser(argparse.ArgumentParser):
    def format_usage(self) -> str:
        return USO_CORTO

    def error(self, message: str):
        for ingles, espanol in _TRADUCCIONES:
            message = message.replace(ingles, espanol)
        self.print_usage(sys.stderr)
        self.exit(SALIDA_USO, f"aiclean: error: {message}\n")


def _crear_parser() -> argparse.ArgumentParser:
    # La ayuda se escribe en AYUDA y la pinta salida.mostrar_ayuda; argparse solo lee las opciones
    p = _Parser(prog="aiclean", add_help=False)
    entradas = p.add_argument_group("entradas")
    entradas.add_argument(
        "rutas", nargs="*", metavar="RUTA",
        help="archivos o carpetas (admite comodines como *.png); - lee de la entrada estándar",
    )
    entradas.add_argument("-r", "--recursive", action="store_true", help="entra también en las subcarpetas")
    entradas.add_argument("--clipboard", action="store_true", help="limpia el texto del portapapeles")

    que = p.add_argument_group("qué se hace")
    que.add_argument("--dry-run", action="store_true", help="solo muestra lo que encuentra, sin tocar nada")
    que.add_argument("--all", action="store_true", help="quita todos los metadatos, no solo los de IA")
    que.add_argument(
        "--normalize", action="store_true",
        help="en texto plano, cambia NBSP/NNBSP y otros espacios raros por un espacio normal y quita "
             "SHY y WORD JOINER (puede cambiar la tipografía y dónde se cortan las líneas; "
             "no se aplica a HTML/XML/SVG)",
    )

    dest = p.add_argument_group("dónde se guarda").add_mutually_exclusive_group()
    dest.add_argument("--in-place", action="store_true", help="sobrescribe el original (por defecto crea *_clean)")
    dest.add_argument("-o", "--output-dir", metavar="CARPETA", type=Path, help="guarda los limpios en esta carpeta")

    otras = p.add_argument_group("otras opciones")
    otras.add_argument("--report", action="store_true", help="resumen al final: procesados, qué se quitó, errores")
    otras.add_argument("--report-file", metavar="ARCHIVO", type=Path, help="guarda el informe completo en JSON")
    otras.add_argument(
        "--report-sensitive", action="store_true",
        help="muestra también los valores sensibles (texto oculto recuperado, URL completas) en pantalla "
             "y en el JSON; sin ella solo salen categoría, campo, cantidad, posiciones y el servicio de IA "
             "(o «URL de procedencia» si no es de la lista). Los mensajes de error nunca los incluyen",
    )
    otras.add_argument("-j", "--jobs", metavar="N", type=int, default=1, help="archivos en paralelo (por defecto 1)")
    otras.add_argument(
        "--exiftool", metavar="RUTA", type=Path,
        help="exiftool.exe que se usa para las imágenes (por defecto, el de tools\\; nunca se busca en el PATH)",
    )
    otras.add_argument("-V", "--version", action="version", version=f"aiclean {__version__}", help="muestra la versión")
    otras.add_argument("-h", "--help", action="store_true", help="muestra esta ayuda")
    return p


def main(argv: list[str] | None = None) -> int:
    """Con consola propia (doble clic o archivos arrastrados sobre aiclean.exe) y solo rutas, o
    nada: modo interactivo (interactivo.py). Con consola propia y opciones: se ejecuta como siempre
    y "Pulsa Enter para salir" al final, también tras un error. Ctrl+C sale sin pausa. Desde una
    terminal, un script o con tuberías: sin bucle ni pausa."""
    salida.preparar_flujos()
    propia = _consola_propia()
    argv = sys.argv[1:] if argv is None else list(argv)
    if propia and all(not a.startswith("-") for a in argv):
        return _interactivo(argv)
    try:
        codigo = _ejecutar(argv, propia)
    except SystemExit as e:                              # argparse: uso incorrecto o --version
        if not propia:
            raise
        codigo = e.code if isinstance(e.code, int) else SALIDA_USO
    if propia and codigo != 130:
        salida.pausa()
    return codigo


def _consola_propia() -> bool:
    return windows.consola_propia()


def _interactivo(rutas: list[str]) -> int:
    """Al salir (Enter vacío) la ventana se cierra; el código es el de toda la sesión."""
    try:
        resultados = interactivo.Sesion().ejecutar(rutas)
    except KeyboardInterrupt:
        salida.error("cancelado")
        return 130
    except Exception as e:                               # noqa: BLE001 - sin volcar el mensaje (puede llevar contenido)
        salida.error(motor.error_interno(e))
        salida.pausa()
        return SALIDA_ERROR
    return _codigo(resultados, dry_run=False)


def _ejecutar(argv: list[str], propia: bool) -> int:
    parser = _crear_parser()
    args = parser.parse_args(argv)
    if args.help:
        salida.mostrar_ayuda(CABECERA, AYUDA)
        return SALIDA_OK

    estandar = args.rutas == ["-"]
    if "-" in args.rutas and not estandar:
        parser.error("- (entrada estándar) va solo, sin más rutas")
    if args.clipboard and args.rutas:
        parser.error("--clipboard no lleva rutas")
    if (estandar or args.clipboard) and (args.in_place or args.output_dir or args.recursive):
        parser.error("--in-place, -o y -r son para archivos, no para la entrada estándar ni el portapapeles")
    if not args.rutas and not args.clipboard:
        if propia:
            salida.bienvenida(__version__)
            return SALIDA_OK
        salida.mostrar_ayuda(CABECERA, AYUDA, sys.stderr)
        return SALIDA_USO
    if args.jobs < 1:
        parser.error("-j necesita un número mayor que 0")
    if args.exiftool is not None and not args.exiftool.is_file():
        parser.error("--exiftool: no existe ese archivo")
    rutas.indicar("exiftool", args.exiftool)

    opciones = motor.Opciones(
        modo=Modo.TODO if args.all else Modo.IA,
        dry_run=args.dry_run,
        in_place=args.in_place,
        carpeta_salida=args.output_dir,
        recursivo=args.recursive,
        trabajos=args.jobs,
        normalizar=args.normalize,
    )
    sensible = args.report_sensitive
    try:
        if estandar:
            resultados = [_entrada_estandar(opciones, sensible)]
        elif args.clipboard:
            resultados = [motor.procesar_portapapeles(opciones)]
            salida.mostrar_resultado(resultados[0], detalle=True, sensible=sensible)
        else:
            resultados = motor.procesar(motor.expandir(args.rutas, opciones), opciones)
            for r in resultados:
                salida.mostrar_resultado(r, detalle=args.dry_run, sensible=sensible)
    except KeyboardInterrupt:
        salida.error("cancelado")
        return 130
    except Exception as e:                               # noqa: BLE001 - sin volcar el mensaje (puede llevar contenido)
        salida.error(motor.error_interno(e))
        return SALIDA_ERROR

    if args.report:
        informe.mostrar_resumen(resultados, args.dry_run)
    if args.report_file:
        try:
            informe.guardar_json(resultados, args.report_file, _opciones_json(args), sensible)
        except OSError as e:
            salida.error(f"no se pudo guardar el informe: {e.strerror or type(e).__name__}")
            return SALIDA_ERROR
    return _codigo(resultados, args.dry_run)


def _entrada_estandar(opciones: motor.Opciones, sensible: bool):
    salida.usar_stderr()
    if sys.stdin is None:
        res, _ = motor.procesar_bytes_texto(b"", opciones, "entrada estándar")
        return res
    if sys.stdin.isatty():
        salida.aviso("Pega o escribe el texto y termina con Ctrl+Z y Enter.")
    datos = sys.stdin.buffer.read()
    res, limpio = motor.procesar_bytes_texto(datos, opciones, "entrada estándar")
    if not opciones.dry_run:
        _escribir_stdout(limpio)          # el texto pedido: no se censura
    salida.mostrar_resultado(res, detalle=opciones.dry_run, sensible=sensible)   # diagnóstico (stderr)
    return res


def _escribir_stdout(datos: bytes) -> None:
    if sys.stdout is None:
        return
    if sys.stdout.isatty():
        # En la consola se escribe como texto (Unicode correcto); redirigido, los bytes tal cual
        try:
            sys.stdout.write(texto.decodificar(datos).texto)
        except texto.ErrorTexto:
            sys.stdout.buffer.write(datos)
    else:
        sys.stdout.buffer.write(datos)
    sys.stdout.flush()


def _opciones_json(args) -> dict:
    return {
        "rutas": args.rutas, "recursivo": args.recursive, "portapapeles": args.clipboard,
        "dry_run": args.dry_run, "all": args.all, "in_place": args.in_place,
        "output_dir": str(args.output_dir) if args.output_dir else None, "jobs": args.jobs,
        "normalize": args.normalize, "report_sensitive": args.report_sensitive,
        "exiftool": str(args.exiftool) if args.exiftool else None,
    }


def _codigo(resultados, dry_run: bool) -> int:
    if any(r.estado is Estado.ERROR for r in resultados):
        return SALIDA_ERROR
    if any(r.estado is Estado.PARCIAL for r in resultados):
        return SALIDA_PARCIAL
    if dry_run and any(r.estado is Estado.ENCONTRADO for r in resultados):
        return SALIDA_ENCONTRADO
    return SALIDA_OK
