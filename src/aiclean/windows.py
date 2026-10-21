"""Cosas propias de Windows: marca de la web (Zone.Identifier), OneDrive y portapapeles."""

from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Zone.Identifier (Mark of the Web): flujo NTFS que guarda de dónde se descargó el archivo

FLUJO_MOTW = "Zone.Identifier"


@dataclass(frozen=True)
class MarcaWeb:
    crudo: bytes
    zona: int | None
    host_url: str | None
    referrer_url: str | None


def leer_marca_web(ruta: Path) -> MarcaWeb | None:
    try:
        with open(f"{ruta}:{FLUJO_MOTW}", "rb") as f:
            crudo = f.read()
    except OSError:
        return None
    valores = {}
    for linea in crudo.decode("utf-8", "replace").splitlines():
        clave, sep, valor = linea.partition("=")
        if sep:
            valores[clave.strip().lower()] = valor.strip()
    zona = valores.get("zoneid")
    return MarcaWeb(
        crudo=crudo,
        zona=int(zona) if zona and zona.isdigit() else None,
        host_url=valores.get("hosturl"),
        referrer_url=valores.get("referrerurl"),
    )


def reescribir_marca_web(marca: MarcaWeb, quitar: set[str]) -> bytes:
    """Contenido de la marca sin los campos indicados (en minúsculas: "hosturl"...).

    Se conserva todo lo demás tal cual (ZoneId, LastWriterPackageFamilyName...), con sus
    finales de línea: Windows sigue sabiendo que el archivo viene de internet.
    """
    lineas = []
    for linea in marca.crudo.splitlines(keepends=True):
        clave, sep, _ = linea.partition(b"=")
        if sep and clave.strip().decode("utf-8", "replace").lower() in quitar:
            continue
        lineas.append(linea)
    return b"".join(lineas)


def escribir_marca_web(contenido: bytes, destino: Path) -> None:
    with open(f"{destino}:{FLUJO_MOTW}", "wb") as f:
        f.write(contenido)


# ---------------------------------------------------------------------------
# Flujos NTFS: --in-place reemplaza el archivo y perdería los que no sean la marca de la web

_FLUJOS_CONOCIDOS = {"::$data", f":{FLUJO_MOTW.lower()}:$data"}
_ERROR_HANDLE_EOF = 38          # FindFirstStreamW: el archivo no tiene ningún flujo


class _DatosFlujo(ctypes.Structure):
    # WIN32_FIND_STREAM_DATA: LARGE_INTEGER StreamSize + WCHAR cStreamName[MAX_PATH + 36]
    _fields_ = [("tamano", ctypes.c_longlong), ("nombre", ctypes.c_wchar * 296)]


if sys.platform == "win32":
    _k32_flujos = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32_flujos.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32_flujos.FindFirstStreamW.restype = wintypes.HANDLE
    _k32_flujos.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32_flujos.FindNextStreamW.restype = wintypes.BOOL
    _k32_flujos.FindClose.argtypes = [wintypes.HANDLE]
    _k32_flujos.FindClose.restype = wintypes.BOOL
    _HANDLE_INVALIDO = wintypes.HANDLE(-1).value


def flujos_ntfs(ruta: Path) -> list[str]:
    """Nombres de los flujos de datos del archivo ("::$DATA", ":Zone.Identifier:$DATA"...)."""
    datos = _DatosFlujo()
    h = _k32_flujos.FindFirstStreamW(str(ruta), 0, ctypes.byref(datos), 0)   # 0 = FindStreamInfoStandard
    if h == _HANDLE_INVALIDO or h is None:
        error = ctypes.get_last_error()
        if error == _ERROR_HANDLE_EOF:
            return []
        raise ctypes.WinError(error)
    try:
        nombres = [datos.nombre]
        while _k32_flujos.FindNextStreamW(h, ctypes.byref(datos)):
            nombres.append(datos.nombre)
        error = ctypes.get_last_error()
        if error != _ERROR_HANDLE_EOF:
            raise ctypes.WinError(error)
        return nombres
    finally:
        _k32_flujos.FindClose(h)


def flujos_adicionales(ruta: Path) -> list[str]:
    """Flujos que no son el contenido ni la marca de la web."""
    return [n for n in flujos_ntfs(ruta) if n.lower() not in _FLUJOS_CONOCIDOS]


# ---------------------------------------------------------------------------
# OneDrive: los archivos "solo en línea" se descargarían al leerlos

_ATRIBUTOS_NUBE = 0x00001000 | 0x00040000 | 0x00400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS


def solo_en_la_nube(ruta: Path) -> bool:
    try:
        atributos = os.stat(ruta).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(atributos & _ATRIBUTOS_NUBE)


# ---------------------------------------------------------------------------
# Consola propia: el exe se abrió con doble clic o arrastrando archivos encima

def consola_propia() -> bool:
    """True si Windows creó la consola solo para este proceso (lanzado desde el Explorador) y la
    entrada y la salida son esa consola. Desde una terminal la comparten al menos dos procesos (la
    shell y aiclean), y con tuberías o archivos la entrada o la salida no son la consola."""
    if sys.platform != "win32":
        return False
    try:
        if not (sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty()):
            return False
        procesos = (wintypes.DWORD * 4)()
        return ctypes.WinDLL("kernel32", use_last_error=True).GetConsoleProcessList(procesos, 4) == 1
    except (OSError, ValueError, AttributeError):
        return False


def partir_linea(linea: str) -> list[str]:
    """Las rutas de una línea escrita o arrastrada a la consola, con las reglas de Windows para la
    línea de comandos (CommandLineToArgvW): comillas, espacios, varias rutas.

    Una ruta no puede llevar comillas, así que las barras justo antes de unas comillas son
    literales: "D:\\Mis cosas\\" es D:\\Mis cosas\\ y no una comilla escapada."""
    linea = re.sub(r'(\\+)"', lambda m: m.group(1) * 2 + '"', linea.strip())
    if not linea:
        return []
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    n = ctypes.c_int()
    # El primer elemento sigue otras reglas (el nombre del programa): va uno de relleno
    argv = shell32.CommandLineToArgvW(f"x {linea}", ctypes.byref(n))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[i] for i in range(1, n.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(argv, wintypes.HLOCAL))


# ---------------------------------------------------------------------------
# Portapapeles

CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_LOCALE = 1, 7, 13, 16
_SINTETIZADOS = {CF_TEXT, CF_OEMTEXT, CF_UNICODETEXT, CF_LOCALE}   # Windows los rehace desde el texto
GMEM_MOVEABLE = 0x0002


class ErrorPortapapeles(Exception):
    pass


@dataclass
class Portapapeles:
    texto: str | None = None
    html: bytes | None = None                                    # formato "HTML Format" (CF_HTML)
    con_url: list[tuple[str, str]] = field(default_factory=list)  # (formato, URL de origen)
    otros: list[str] = field(default_factory=list)               # formatos que se perderían al reescribir


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    _user32.EnumClipboardFormats.restype = wintypes.UINT
    _user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterClipboardFormatW.restype = wintypes.UINT
    _user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClipboardFormatNameW.restype = ctypes.c_int
    _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = wintypes.LPVOID
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalSize.restype = ctypes.c_size_t
    _kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalFree.restype = wintypes.HGLOBAL


class _Abierto:
    """with _Abierto(): ... abre el portapapeles (reintenta si otro programa lo tiene)."""

    def __enter__(self):
        if sys.platform != "win32":
            raise ErrorPortapapeles("el portapapeles solo funciona en Windows")
        for _ in range(20):
            if _user32.OpenClipboard(None):
                return self
            time.sleep(0.05)
        raise ErrorPortapapeles("otro programa tiene el portapapeles ocupado; vuelve a intentarlo")

    def __exit__(self, *exc):
        _user32.CloseClipboard()
        return False


def _formato_html() -> int:
    return _user32.RegisterClipboardFormatW("HTML Format")


def _nombre_formato(fmt: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    if _user32.GetClipboardFormatNameW(fmt, buf, 256):
        return buf.value
    return f"formato estándar {fmt}"


def _leer_bytes(fmt: int) -> bytes | None:
    h = _user32.GetClipboardData(fmt)
    if not h:
        return None
    talla = _kernel32.GlobalSize(h)
    p = _kernel32.GlobalLock(h)
    if not p:
        return None
    try:
        return ctypes.string_at(p, talla)
    finally:
        _kernel32.GlobalUnlock(h)


_URL = re.compile(r"https?://[^\s\x00\"'<>]+")


def leer_portapapeles() -> Portapapeles:
    pp = Portapapeles()
    with _Abierto():
        fmt_html = _formato_html()
        fmt = _user32.EnumClipboardFormats(0)
        formatos = []
        while fmt:
            formatos.append(fmt)
            fmt = _user32.EnumClipboardFormats(fmt)
        for fmt in formatos:
            if fmt == CF_UNICODETEXT:
                datos = _leer_bytes(fmt)
                if datos is not None:
                    pp.texto = datos.decode("utf-16-le", "replace").split("\x00", 1)[0]
            elif fmt == fmt_html:
                datos = _leer_bytes(fmt)
                if datos is not None:
                    pp.html = datos.split(b"\x00", 1)[0]
            elif fmt not in _SINTETIZADOS:
                nombre = _nombre_formato(fmt)
                pp.otros.append(nombre)
                if "url" in nombre.lower():
                    datos = _leer_bytes(fmt) or b""
                    texto = datos.decode("utf-16-le" if b"\x00" in datos[:64] else "utf-8", "replace")
                    m = _URL.search(texto)
                    if m:
                        pp.con_url.append((nombre, m.group(0)))
    return pp


def _poner(fmt: int, datos: bytes) -> None:
    h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(datos))
    if not h:
        raise ErrorPortapapeles("no hay memoria para el portapapeles")
    p = _kernel32.GlobalLock(h)
    ctypes.memmove(p, datos, len(datos))
    _kernel32.GlobalUnlock(h)
    if not _user32.SetClipboardData(fmt, h):
        _kernel32.GlobalFree(h)
        raise ErrorPortapapeles(f"Windows no aceptó los datos (error {ctypes.get_last_error()})")


def escribir_portapapeles(texto: str | None, html: bytes | None) -> None:
    """Sustituye el portapapeles por este texto (y HTML). El resto de formatos se pierde."""
    with _Abierto():
        if not _user32.EmptyClipboard():
            raise ErrorPortapapeles("no se pudo vaciar el portapapeles")
        if texto is not None:
            _poner(CF_UNICODETEXT, (texto + "\x00").encode("utf-16-le"))
        if html is not None:
            _poner(_formato_html(), html + b"\x00")


# ---------------------------------------------------------------------------
# CF_HTML: cabecera con desplazamientos en bytes + HTML en UTF-8

_CABECERA_HTML = re.compile(rb"([A-Za-z]+):([^\r\n]*)\r?\n")


@dataclass
class HtmlPortapapeles:
    antes: str          # HTML desde StartHTML hasta StartFragment
    fragmento: str      # lo copiado
    despues: str        # desde EndFragment hasta EndHTML
    source_url: str | None
    version: str = "0.9"


def leer_cf_html(datos: bytes) -> HtmlPortapapeles:
    campos: dict[str, bytes] = {}
    pos = 0
    while True:
        m = _CABECERA_HTML.match(datos, pos)    # la cabecera acaba donde empieza el "<" del HTML
        if not m:
            break
        campos[m.group(1).decode("ascii").lower()] = m.group(2).strip()
        pos = m.end()
    try:
        ini_frag = int(campos["startfragment"])
        fin_frag = int(campos["endfragment"])
        ini_html = int(campos.get("starthtml", b"-1"))
        fin_html = int(campos.get("endhtml", b"-1"))
    except (KeyError, ValueError) as e:
        raise ValueError("cabecera CF_HTML incompleta") from e
    if ini_html < 0:
        ini_html = ini_frag
    if fin_html < 0:
        fin_html = fin_frag
    if not (pos <= ini_html <= ini_frag <= fin_frag <= fin_html <= len(datos)):
        raise ValueError("desplazamientos CF_HTML fuera de rango")
    url = campos.get("sourceurl")
    return HtmlPortapapeles(
        antes=datos[ini_html:ini_frag].decode("utf-8"),
        fragmento=datos[ini_frag:fin_frag].decode("utf-8"),
        despues=datos[fin_frag:fin_html].decode("utf-8"),
        source_url=url.decode("utf-8", "replace") if url else None,
        version=campos.get("version", b"0.9").decode("ascii", "replace"),
    )


def escribir_cf_html(h: HtmlPortapapeles) -> bytes:
    antes, frag, despues = (s.encode("utf-8") for s in (h.antes, h.fragmento, h.despues))

    def cabecera(ini_html: int, fin_html: int, ini_frag: int, fin_frag: int) -> bytes:
        lineas = [
            f"Version:{h.version}",
            f"StartHTML:{ini_html:010d}",
            f"EndHTML:{fin_html:010d}",
            f"StartFragment:{ini_frag:010d}",
            f"EndFragment:{fin_frag:010d}",
        ]
        if h.source_url:
            lineas.append(f"SourceURL:{h.source_url}")
        return ("\r\n".join(lineas) + "\r\n").encode("utf-8")

    largo = len(cabecera(0, 0, 0, 0))     # los números van con ancho fijo
    ini_html = largo
    ini_frag = ini_html + len(antes)
    fin_frag = ini_frag + len(frag)
    fin_html = fin_frag + len(despues)
    return cabecera(ini_html, fin_html, ini_frag, fin_frag) + antes + frag + despues
