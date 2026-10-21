"""ExifTool en modo -stay_open: un proceso por hilo, peticiones emparejadas con -execute{N}.

- Ruta absoluta (rutas.exiftool), sin shell y con el directorio de trabajo en la carpeta de
  ExifTool, no en la de los archivos.
- -config "" para que no se cargue ningún .ExifTool_config del usuario.
- Los argumentos van por la entrada estándar en UTF-8 (-charset filename=utf8): así funcionan
  las rutas con caracteres no ASCII.
- Cada petición tiene un tiempo máximo; si se pasa, el proceso se mata y el archivo da error.
- Nunca se usa -fast.
"""

from __future__ import annotations

import atexit
import queue
import re
import subprocess
import threading
from pathlib import Path

from aiclean.herramientas import rutas

TIEMPO_MAXIMO = 60.0
_CREAR_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErrorExifTool(Exception):
    pass


class _Proceso:
    def __init__(self, exe: Path, orden: list[str] | None = None):
        # orden: solo para los tests (un "ExifTool" que se cuelga o se cierra)
        self.exe = exe
        self.p = subprocess.Popen(
            orden or [str(exe), "-config", "", "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(exe.parent), creationflags=_CREAR_SIN_VENTANA,
        )
        self.n = 0
        self.salida: queue.Queue = queue.Queue()
        self.errores: queue.Queue = queue.Queue()
        for flujo, cola in ((self.p.stdout, self.salida), (self.p.stderr, self.errores)):
            threading.Thread(target=_leer_lineas, args=(flujo, cola), daemon=True).start()

    def ejecutar(self, args: list[str], tiempo: float = TIEMPO_MAXIMO) -> tuple[str, str]:
        """(stdout, stderr) de una petición."""
        if any("\n" in a or "\r" in a for a in args):
            raise ErrorExifTool("argumento no válido para ExifTool (salto de línea)")
        self.n += 1
        marca = f"{{ready{self.n}}}"
        lineas = ["-charset", "filename=utf8", *args, "-echo4", marca, f"-execute{self.n}"]
        try:
            self.p.stdin.write(("\n".join(lineas) + "\n").encode("utf-8"))
            self.p.stdin.flush()
        except OSError as e:
            self.matar()
            raise ErrorExifTool("ExifTool se cerró inesperadamente") from e
        try:
            salida = _hasta(self.salida, marca, tiempo)
            errores = _hasta(self.errores, marca, tiempo)
        except ErrorExifTool:
            self.matar()
            raise
        return salida, errores

    def cerrar(self) -> None:
        if self.p.poll() is None:
            try:
                self.p.stdin.write(b"-stay_open\nFalse\n")
                self.p.stdin.flush()
                self.p.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                self.matar()

    def matar(self) -> None:
        if self.p.poll() is None:
            self.p.kill()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                pass


def _leer_lineas(flujo, cola: queue.Queue) -> None:
    for linea in iter(flujo.readline, b""):
        cola.put(linea.decode("utf-8", "replace").rstrip("\r\n"))
    cola.put(None)                                               # el proceso terminó


def _hasta(cola: queue.Queue, marca: str, tiempo: float) -> str:
    lineas = []
    while True:
        try:
            linea = cola.get(timeout=tiempo)
        except queue.Empty:
            raise ErrorExifTool(f"ExifTool no respondió en {tiempo:.0f} s") from None
        if linea is None:
            raise ErrorExifTool("ExifTool se cerró inesperadamente")
        if linea == marca:
            return "\n".join(lineas)
        lineas.append(linea)


# ---------------------------------------------------------------------------
# Un proceso por hilo (-j N lanza N procesos); todos se cierran al acabar el lote

_local = threading.local()
_todos: list[_Proceso] = []
_cerrojo = threading.Lock()
_version: str | None = None


def _proceso() -> _Proceso:
    global _version
    p = getattr(_local, "proceso", None)
    if p is None or p.p.poll() is not None:
        p = _Proceso(rutas.exiftool())
        _local.proceso = p
        with _cerrojo:
            _todos.append(p)
        if _version is None:
            salida, _ = p.ejecutar(["-ver"])
            _version = salida.strip() or None
    return p


def cerrar_todos() -> None:
    with _cerrojo:
        procesos, _todos[:] = list(_todos), []
    for p in procesos:
        p.cerrar()
    _local.__dict__.pop("proceso", None)


atexit.register(cerrar_todos)


def version_usada() -> str | None:
    """Versión de ExifTool si se ha usado en esta ejecución (para el informe)."""
    return _version


_ERROR = re.compile(r"^Error:\s*(.*?)(\s+-\s+.*)?$")


def escribir(entrada: Path, salida: Path, args: list[str]) -> None:
    """Aplica los cambios args a entrada y deja el resultado en salida (que no debe existir).

    Los mensajes de error se resumen sin la ruta ni valores del archivo."""
    p = _proceso()
    stdout, stderr = p.ejecutar([*args, "-o", str(salida), str(entrada)])
    errores = [_ERROR.match(l) for l in stderr.splitlines()]
    errores = [m.group(1) for m in errores if m]
    if errores or "1 image files created" not in stdout or not salida.is_file():
        motivo = errores[0][:80] if errores else "no creó la salida"
        raise ErrorExifTool(f"ExifTool no pudo escribir el archivo ({motivo})")
