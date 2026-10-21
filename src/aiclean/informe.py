"""Resumen final (--report) e informe JSON (--report-file)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from rich import box
from rich.markup import escape
from rich.table import Table

import unicodedata

from aiclean import __version__, firmas
from aiclean.formatos import texto
from aiclean.herramientas import exiftool
from aiclean.modelos import TEXTO_SIN_CAMBIOS, Estado, Resultado
from aiclean.salida import CATEGORIAS, GRIS, ROJO, VERDE, consola, pintar

_TITULOS = {
    Estado.LIMPIO: "limpios",
    Estado.PARCIAL: "parciales (queda algo sin tratar)",
    Estado.ENCONTRADO: "con algo que quitar",
    Estado.SIN_CAMBIOS: f"en los que {TEXTO_SIN_CAMBIOS}",
    Estado.OMITIDO: "omitidos",
    Estado.ERROR: "con error",
}
_TITULOS_UNO = {
    Estado.LIMPIO: "limpio",
    Estado.PARCIAL: "parcial (queda algo sin tratar)",
    Estado.ENCONTRADO: "con algo que quitar",
    Estado.SIN_CAMBIOS: f"en el que {TEXTO_SIN_CAMBIOS}",
    Estado.OMITIDO: "omitido",
    Estado.ERROR: "con error",
}

# Resultados cuyos hallazgos cuentan en la tabla: lo que se quitó o, en --dry-run, se quitaría
_CUENTAN = {Estado.LIMPIO, Estado.PARCIAL, Estado.ENCONTRADO}


def mostrar_resumen(resultados: list[Resultado], dry_run: bool) -> None:
    c = consola()
    c.print()
    c.print(pintar("Resumen", "bold"))
    cuentas = {e: sum(1 for r in resultados if r.estado is e) for e in Estado}
    color = {Estado.LIMPIO: VERDE, Estado.SIN_CAMBIOS: VERDE, Estado.ERROR: ROJO, Estado.OMITIDO: GRIS}
    partes = [pintar(f"{n} {(_TITULOS_UNO if n == 1 else _TITULOS)[e]}", color.get(e, "")) for e, n in cuentas.items() if n]
    c.print(f"{len(resultados)} procesados: " + ", ".join(partes))

    totales: dict[tuple[str, str, str, bool], list[int]] = {}
    for r in resultados:
        if r.estado not in _CUENTAN:
            continue
        for h in r.hallazgos:
            donde = h.ubicacion if not h.tratado else ""
            t = totales.setdefault((h.categoria, h.clave, donde, h.tratado), [0, 0])
            t[0] += h.cantidad
            t[1] += 1
    if totales:
        titulo = "Se encontró" if dry_run else "Hallazgos (tratado: sí = eliminado; no = queda tal cual)"
        # En --dry-run no se ha quitado nada: la columna dice lo que se haría
        si, no = ("se quitaría", "quedaría sin tratar") if dry_run else (pintar("sí", VERDE), "no")
        c.print()
        c.print(pintar(titulo, "bold"))
        tabla = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, header_style="dim")
        tabla.add_column("Tipo")
        tabla.add_column("Qué")
        tabla.add_column("Veces", justify="right")
        tabla.add_column("Fuentes", justify="right")
        tabla.add_column("Acción" if dry_run else "Tratado")
        for (cat, clave, donde, tratado), (veces, fuentes) in sorted(totales.items(), key=lambda kv: -kv[1][0]):
            que = f"{clave} ({donde})" if donde else clave
            tabla.add_row(
                escape(CATEGORIAS.get(cat, cat)), escape(que), str(veces), str(fuentes), si if tratado else no,
            )
        c.print(tabla)

    errores = [r for r in resultados if r.estado is Estado.ERROR]
    if errores:
        c.print()
        c.print(pintar("Errores", f"bold {ROJO}"))
        for r in errores:
            c.print(f"  · {escape(r.fuente)}: {pintar(r.mensaje, ROJO)}")


def versiones() -> dict:
    return {
        "aiclean": __version__,
        "reglas": firmas.VERSION_REGLAS,
        "tablas_unicode": texto.VERSION_TABLAS_UNICODE,
        "unicodedata": unicodedata.unidata_version,
        "exiftool": exiftool.version_usada(),             # None si no se ha usado
    }


def guardar_json(resultados: list[Resultado], ruta: Path, opciones: dict, sensible: bool = False) -> None:
    """Sin --report-sensitive no lleva valores (texto oculto, URL completas...), solo
    categoría, campo, cantidad, ubicación, posiciones, motivo, estado, acción y verificación.
    es_ia se mantiene por compatibilidad: dice si se quita en modo IA, no prueba autoría."""
    datos = {
        **versiones(),
        "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
        "valores_sensibles": sensible,
        "opciones": opciones,
        "resultados": [r.a_dict(sensible) for r in resultados],
    }
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
