"""Modo interactivo: aiclean.exe abierto con doble clic o con archivos arrastrados encima.

Siempre en modo IA y con copias nombre_clean.ext junto al original (nunca --in-place). Cada
archivo se muestra paso a paso (analizando, lo encontrado, limpiando, verificando, resultado);
con más de PASO_A_PASO_MAX archivos en una tanda, una línea por archivo. Con varios, el resumen
al final. Después pregunta por más rutas hasta que se pulsa Enter sin escribir nada.

Nunca se muestran valores sensibles: solo categoría, campo, su resumen seguro y el motivo.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.markup import escape

from aiclean import __version__, informe, motor, salida, windows
from aiclean.formatos import imagen
from aiclean.herramientas import exiftool
from aiclean.modelos import AMPLIO, GENERADOR, PROCEDENCIA, UNICODE, Estado, Hallazgo, Resultado

PASO_A_PASO_MAX = 20

PREGUNTA = "Arrastra aquí un archivo o carpeta (o escribe la ruta) y pulsa Enter. Enter vacío para salir."
AVISO_SYNTHID = ("La marca invisible de los píxeles (SynthID y similares), si la hay, sigue: "
                 "aiclean no toca los píxeles.")

_MOTIVOS = {PROCEDENCIA: "procedencia de IA", GENERADOR: "generador de IA", UNICODE: "carácter oculto",
            AMPLIO: "metadato prescindible"}
_SI = {"s", "si", "sí", "y", "yes"}
_CARPETAS_IGNORADAS = {".git", ".hg", ".svn"}


class Sesion:
    def __init__(self) -> None:
        self.c = salida.consola()
        self.opciones = motor.Opciones()                 # modo IA, copias _clean
        self.resultados: list[Resultado] = []
        self.aviso_synthid = False

    # --- Bucle -------------------------------------------------------------------------------

    def ejecutar(self, rutas: list[str]) -> list[Resultado]:
        """Procesa las rutas iniciales (las arrastradas sobre el exe) y pregunta por más hasta
        Enter vacío. Ctrl+C se propaga (KeyboardInterrupt); la entrada cerrada termina."""
        salida.bienvenida(__version__)
        if rutas:
            self.tanda(rutas)
        while True:
            self.c.print()
            self.c.print(PREGUNTA)
            try:
                linea = input("> ")
            except EOFError:
                break
            rutas = windows.partir_linea(linea)
            if not rutas:
                break
            self.tanda(rutas)
        return self.resultados

    def tanda(self, rutas: list[str]) -> None:
        entradas: list[tuple[motor.Entrada, str]] = []
        for texto_ruta in rutas:
            ruta = Path(texto_ruta)
            if not ruta.exists() and not motor.hay_comodines(texto_ruta):
                self.c.print()
                self.c.print(salida.pintar(f"No existe: {texto_ruta}", salida.ROJO))
                continue
            recursivo = ruta.is_dir() and _tiene_subcarpetas(ruta) and self.preguntar_subcarpetas(ruta)
            opciones = motor.Opciones(recursivo=recursivo)
            for e in motor.expandir([texto_ruta], opciones):
                entradas.append((e, _nombre(e, ruta)))
        if not entradas:
            return
        resultados = []
        try:
            for entrada, nombre in entradas:
                if len(entradas) <= PASO_A_PASO_MAX:
                    res = self.paso_a_paso(entrada, nombre)
                else:
                    res = motor.procesar_archivo(entrada, self.opciones)
                    salida.mostrar_resultado(res, detalle=False)
                self.avisar_synthid(res)
                resultados.append(res)
        finally:
            exiftool.cerrar_todos()
            self.resultados += resultados
        if len(resultados) > 1:
            informe.mostrar_resumen(resultados, dry_run=False)

    def preguntar_subcarpetas(self, carpeta: Path) -> bool:
        self.c.print()
        respuesta = input(f"¿Incluir también las subcarpetas de «{carpeta.name or carpeta}»? (s/N) ")
        return respuesta.strip().lower() in _SI

    # --- Un archivo ------------------------------------------------------------------------------

    def paso_a_paso(self, entrada: motor.Entrada, nombre: str) -> Resultado:
        c = self.c
        c.print()
        c.print(salida.pintar(nombre, "bold"))
        mostrados = False

        def progreso(fase: str, hallazgos: list[Hallazgo]) -> None:
            nonlocal mostrados
            if fase == motor.LIMPIANDO:
                self.mostrar_hallazgos(hallazgos)
                mostrados = True
            c.print(f"  {fase.capitalize()}…")

        res = motor.procesar_archivo(entrada, self.opciones, progreso)
        if res.hallazgos and not mostrados:
            self.mostrar_hallazgos(res.hallazgos)       # parcial sin nada que quitar
        c.print("  " + _linea_resultado(res))
        return res

    def mostrar_hallazgos(self, hallazgos: list[Hallazgo]) -> None:
        filas = [(_describir(h), _motivo(h)) for h in hallazgos]
        ancho = min(max(len(a) for a, _ in filas), 56)
        self.c.print("  Encontrado:")
        for que, motivo in filas:
            self.c.print(f"    · {escape(que.ljust(ancho))}  {salida.pintar(motivo, salida.GRIS)}")

    def avisar_synthid(self, res: Resultado) -> None:
        if self.aviso_synthid or res.tipo not in imagen.TIPOS or res.estado not in (Estado.LIMPIO, Estado.PARCIAL):
            return
        if any(h.motivo in (PROCEDENCIA, GENERADOR) for h in res.hallazgos):
            self.aviso_synthid = True
            self.c.print("  " + salida.pintar(AVISO_SYNTHID, salida.GRIS))


def _tiene_subcarpetas(carpeta: Path) -> bool:
    try:
        with os.scandir(carpeta) as it:
            return any(d.is_dir() and d.name not in _CARPETAS_IGNORADAS for d in it)
    except OSError:
        return False


def _nombre(entrada: motor.Entrada, indicada: Path) -> str:
    """Cómo se llama al archivo en pantalla: su nombre, o carpeta\\sub\\nombre si salió de una carpeta."""
    if indicada.is_dir():
        try:
            return str(Path(indicada.name or str(indicada)) / entrada.ruta.relative_to(indicada))
        except ValueError:
            pass
    return entrada.ruta.name or str(entrada.ruta)


def _describir(h: Hallazgo) -> str:
    texto = f"{salida.CATEGORIAS.get(h.categoria, h.categoria)}: {h.clave}"
    if h.cantidad > 1:
        texto += f" ×{h.cantidad}"
    if h.resumen:                                        # versión segura del valor, nunca el valor
        texto += f" — {h.resumen}"
    return texto


def _motivo(h: Hallazgo) -> str:
    if not h.tratado:
        return f"queda sin tratar (en {h.ubicacion})" if h.ubicacion else "queda sin tratar"
    return _MOTIVOS.get(h.motivo, h.motivo)


def _linea_resultado(res: Resultado) -> str:
    p, f = salida.pintar, salida.flecha()
    destino = f" {p(f, salida.GRIS)} {p(res.destino, '')}" if res.destino and res.destino != res.fuente else ""
    nota = f"  {p(res.mensaje, salida.GRIS)}" if res.mensaje else ""
    if res.estado is Estado.LIMPIO:
        return p("limpio", salida.VERDE) + destino + nota
    if res.estado is Estado.PARCIAL:
        return "parcial" + destino + nota
    if res.estado is Estado.SIN_CAMBIOS:
        return p("sin cambios", salida.VERDE) + (f": {p(res.mensaje, '')}" if res.mensaje else "")
    if res.estado is Estado.OMITIDO:
        return p(f"omitido: {res.mensaje}", salida.GRIS)
    return p(f"error: {res.mensaje}", salida.ROJO)
