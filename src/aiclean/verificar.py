"""Comprobación después de limpiar: si algo no cuadra, la salida se descarta.

- Texto: el resultado tiene que ser el original con solo los cambios permitidos (alineación
  independiente del limpiador), la misma codificación y BOM, y un re-escaneo sin hallazgos.
  Permitido: quitar caracteres quitables; con --normalize en texto plano, además quitar
  SHY/WJ y cambiar los espacios raros por espacio. En marcado, las zonas excluidas tienen
  que quedar idénticas y en los comentarios solo se quitan invisibles literales.
- Copia sin cambios de contenido (p. ej. solo se reescribe la marca de la web): mismos bytes.

Los mensajes de error dicen qué falló y dónde (línea), nunca el contenido.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from aiclean.formatos import imagen, texto
from aiclean.modelos import Modo, Preparado


class FalloVerificacion(Exception):
    pass


def verificar_texto(preparado: Preparado, limpio: Path, tipo: str, modo: Modo, normalizar: bool = False) -> None:
    # En texto los dos modos quitan lo mismo; modo se recibe por ser la firma común
    dec_o = texto.decodificar(preparado.datos)
    try:
        dec_l = texto.decodificar(limpio.read_bytes())
    except texto.ErrorTexto as e:
        raise FalloVerificacion(f"el resultado no se puede leer como texto: {e}") from e
    if (dec_o.codificacion, dec_o.bom) != (dec_l.codificacion, dec_l.bom):
        raise FalloVerificacion("el resultado cambió de codificación o de BOM")
    marcado = texto.marcado_de(tipo)
    verificar_cadena(dec_o.texto, dec_l.texto, marcado, normalizar)
    sin_restos(dec_l.texto, marcado, normalizar)


def sin_restos(limpio: str, marcado: str | None, normalizar: bool = False) -> None:
    """Re-escaneo: una segunda limpieza no debe encontrar nada."""
    _, restos = texto.limpiar_cadena(limpio, marcado, normalizar)
    if restos:
        raise FalloVerificacion(f"después de limpiar quedan {len(restos)} caracteres")


def verificar_cadena(original: str, limpio: str, marcado: str | None, normalizar: bool = False) -> None:
    """El texto limpio es el original con solo los cambios permitidos y ninguno más."""
    if marcado is None:
        _verificar_rapido(original, limpio, normalizar)
    else:
        _verificar_alineando(original, limpio, marcado)


def _verificar_rapido(original: str, limpio: str, normalizar: bool) -> None:
    quitables = texto.QUITABLES_NORMALIZAR if normalizar else texto.QUITABLES

    def esqueleto(s: str) -> str:
        s = quitables.sub("", s)
        return texto.ESPACIOS.sub(" ", s) if normalizar else s

    # 1) Sin lo quitable (y con los espacios normalizados si se pidió), son el mismo texto
    if esqueleto(original) != esqueleto(limpio):
        raise FalloVerificacion("el texto visible cambió")
    # 2) Lo quitable que queda en el limpio ya estaba en el original, en el mismo sitio
    #    del texto visible (no se ha añadido ni movido nada, solo quitado o normalizado)
    restos_o = iter(_situados(original, quitables))
    for c in _situados(limpio, quitables):
        if not any(c == o for o in restos_o):
            raise FalloVerificacion("el resultado tiene caracteres invisibles que no estaban en el original")


def _situados(s: str, quitables) -> list[tuple[str, int]]:
    """(carácter, posición en el texto sin quitables) de cada carácter quitable."""
    return [(m.group(), m.start() - k) for k, m in enumerate(quitables.finditer(s))]


def _verificar_alineando(original: str, limpio: str, marcado: str) -> None:
    zonas = texto._Zonas(texto.zonas_marcado(original))
    i = j = 0
    n, m = len(original), len(limpio)
    while i < n:
        zona = zonas.de(i)
        if zona is not None and zona.excluida:
            trozo = original[zona.inicio:zona.fin]
            if i != zona.inicio or not limpio.startswith(trozo, j):
                raise FalloVerificacion(_donde(original, i, "cambió una zona que no se debe tocar"))
            i, j = zona.fin, j + len(trozo)
            continue
        if zona is None and original[i] == "&":
            ent = texto.ENTIDAD.match(original, i)
            if ent:
                cp = texto.cp_de_entidad(ent, marcado)
                largo = ent.end() - i
                if limpio.startswith(ent.group(0), j):
                    i, j = i + largo, j + largo
                    continue
                if cp is not None and texto.es_quitable(cp):
                    i += largo
                    continue
        c = original[i]
        if j < m and c == limpio[j]:
            i += 1
            j += 1
        elif texto.es_quitable(ord(c)):
            i += 1
        else:
            raise FalloVerificacion(_donde(original, i, "el texto visible cambió"))
    if j != m:
        raise FalloVerificacion("el resultado tiene texto que no estaba en el original")


def _donde(texto_: str, pos: int, motivo: str) -> str:
    linea = texto_.count("\n", 0, pos) + 1
    return f"{motivo} (línea {linea})"


def verificar_copia(preparado: Preparado, limpio: Path) -> None:
    if sha256_de(limpio) != preparado.huella:
        raise FalloVerificacion("la copia no es idéntica al original")


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def verificar_imagen(preparado: Preparado, limpio: Path, tipo: str, modo: Modo, normalizar: bool = False) -> None:
    """Estructura, datos de imagen idénticos, metadatos esperados y segundo escaneo (formatos\\imagen.py)."""
    try:
        imagen.verificar(preparado.contexto, limpio.read_bytes())
    except imagen.NoCuadra as e:
        raise FalloVerificacion(str(e)) from None


# tipo -> comprobación específica (las siguientes etapas añaden audio/vídeo y documentos)
POR_TIPO = {tipo: verificar_texto for tipo in ("texto", "html", "xml", "svg")}
POR_TIPO.update({tipo: verificar_imagen for tipo in imagen.TIPOS})
