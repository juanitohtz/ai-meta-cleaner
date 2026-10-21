"""Dónde están las herramientas externas.

Orden: la ruta indicada con --exiftool, y si no, tools\\ junto al exe (o en la raíz del proyecto
cuando se ejecuta desde el código fuente). Nunca se buscan en el PATH ni en el directorio de los
archivos procesados: un exiftool.exe plantado ahí no se ejecuta nunca.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class HerramientaNoDisponible(Exception):
    pass


NO_HAY_EXIFTOOL = "ExifTool no está disponible: ejecuta build.ps1 -SoloHerramientas o usa --exiftool RUTA"

_indicadas: dict[str, Path] = {}


def indicar(nombre: str, ruta: Path | None) -> None:
    """Ruta dada en la línea de comandos (--exiftool). None la olvida."""
    if ruta is None:
        _indicadas.pop(nombre, None)
    else:
        _indicadas[nombre] = Path(os.path.abspath(ruta))


def carpeta_tools() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "tools"
    return Path(__file__).resolve().parents[3] / "tools"      # src\aiclean\herramientas\ -> raíz


def exiftool() -> Path:
    ruta = _indicadas.get("exiftool") or carpeta_tools() / "exiftool" / "exiftool.exe"
    if not ruta.is_file():
        raise HerramientaNoDisponible(NO_HAY_EXIFTOOL)
    return ruta


def c2patool() -> Path | None:
    """Solo para los tests."""
    ruta = carpeta_tools() / "c2patool" / "c2patool.exe"
    return ruta if ruta.is_file() else None
