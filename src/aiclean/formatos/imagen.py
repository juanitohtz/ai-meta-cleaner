"""Imágenes JPEG y PNG estáticas: recorrido propio, objetivos por campo, escritura y verificación.

Reparto del trabajo:
- Código propio: recorrido de segmentos JPEG (sin buscar EOI por bytes: se siguen las
  longitudes y, tras cada SOS, los datos de entropía hasta el siguiente marcador real) y de
  chunks PNG (longitud, tipo y CRC); variantes que se omiten; C2PA (APP11 / caBX), XMP
  (formatos\\xmp.py), chunks de texto, COM, EXIF mínimo de --all y datos tras IEND.
- ExifTool: solo borrar campos EXIF e IPTC, sobre una copia de los mismos bytes analizados
  (.<nombre>.aiclean-XXXX.in.<ext>, que se borra siempre).
La verificación compara la salida con lo esperado con sus propios lectores, sin fiarse de ExifTool.
"""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from aiclean import firmas
from aiclean.formatos import xmp
from aiclean.herramientas import exiftool, rutas
from aiclean.modelos import AMPLIO, GENERADOR, PROCEDENCIA, Hallazgo, Modo, Omitido, Preparado

TIPOS = ("jpeg", "png")
EXTENSION = {"jpeg": "jpg", "png": "png"}
_NUL = bytes(1)
_FF = bytes([0xFF])
_MAX_DESCOMPRIMIDO = 64 * 1024 * 1024


class Rota(Exception):
    """La estructura del archivo no se puede recorrer."""


class NoCuadra(Exception):
    """La salida no es la esperada (verificación)."""


# ---------------------------------------------------------------------------
# JPEG: recorrido

@dataclass
class Pieza:
    marcador: int        # 0 = datos de entropía (tras un SOS)
    crudo: bytes         # bytes tal cual (marcador, longitud y carga)
    carga: bytes = b""   # datos del segmento sin marcador ni longitud


def recorrer_jpeg(datos: bytes) -> tuple[list[Pieza], bytes]:
    """(piezas en orden, datos tras EOI)."""
    n = len(datos)
    if datos[:2] != bytes.fromhex("ffd8"):
        raise Rota("no empieza por SOI")
    piezas = [Pieza(0xD8, datos[:2])]
    i = 2
    while True:
        if i >= n or datos[i] != 0xFF:
            raise Rota("se esperaba un marcador")
        j = i
        while j < n and datos[j] == 0xFF:               # bytes de relleno antes del marcador
            j += 1
        if j >= n:
            raise Rota("falta EOI")
        m = datos[j]
        if m == 0xD9:
            piezas.append(Pieza(0xD9, datos[i:j + 1]))
            return piezas, datos[j + 1:]
        if m in (0x00, 0x01) or 0xD0 <= m <= 0xD8:
            raise Rota("marcador fuera de lugar")
        if j + 3 > n:
            raise Rota("segmento truncado")
        largo = struct.unpack(">H", datos[j + 1:j + 3])[0]
        fin = j + 1 + largo
        if largo < 2 or fin > n:
            raise Rota("longitud de segmento imposible")
        piezas.append(Pieza(m, datos[i:fin], datos[j + 3:fin]))
        i = fin
        if m == 0xDA:
            k = i
            while True:
                k = datos.find(_FF, k)
                if k < 0:
                    raise Rota("datos de imagen sin final")
                f = k
                while f + 1 < n and datos[f + 1] == 0xFF:
                    f += 1
                if f + 1 >= n:
                    raise Rota("datos de imagen sin final")
                sig = datos[f + 1]
                if sig == 0x00 or 0xD0 <= sig <= 0xD7:  # byte de relleno 00 o marcador de reinicio
                    k = f + 2
                    continue
                break
            piezas.append(Pieza(0, datos[i:k]))
            i = k


_ID_EXIF = b"Exif" + bytes(2)
_ID_XMP = b"http://ns.adobe.com/xap/1.0/" + _NUL
_ID_XMP_EXT = b"http://ns.adobe.com/xmp/extension/" + _NUL
_ID_ICC = b"ICC_PROFILE" + _NUL
_ID_MPF = b"MPF" + _NUL
_ID_FPXR = b"FPXR" + _NUL
_ID_GAINMAP_ISO = b"urn:iso:std:iso:ts:21496:-1" + _NUL
_ID_PHOTOSHOP = b"Photoshop 3.0" + _NUL
_C2PA_UUID = bytes.fromhex("6332706100110010800000aa00389b71")


def clase_app(p: Pieza) -> str | None:
    """Qué es un segmento APPn/COM ("jfif", "exif", "xmp", "icc", "jumbf"...); None si no es APPn/COM."""
    m, c = p.marcador, p.carga
    if m == 0xFE:
        return "com"
    if not 0xE0 <= m <= 0xEF:
        return None
    reglas = {
        0xE0: ((b"JFIF" + _NUL, "jfif"), (b"JFXX" + _NUL, "jfxx")),
        0xE1: ((_ID_EXIF, "exif"), (_ID_XMP, "xmp"), (_ID_XMP_EXT, "xmp_ext")),
        0xE2: ((_ID_ICC, "icc"), (_ID_MPF, "mpf"), (_ID_FPXR, "fpxr"), (_ID_GAINMAP_ISO, "gainmap")),
        0xEB: ((b"JP", "jumbf"),),
        0xEC: ((b"Ducky", "ducky"),),
        0xED: ((_ID_PHOTOSHOP, "photoshop"),),
        0xEE: ((b"Adobe", "adobe"),),
    }
    for prefijo, nombre in reglas.get(m, ()):
        if c.startswith(prefijo):
            return nombre
    return "otro"


def _jumbf_app11(c: bytes) -> tuple[int, int, bytes | None]:
    """(En, Z, tipo de la caja jumd si es el primer segmento) de un APP11 JUMBF."""
    if len(c) < 16:
        raise Rota("APP11 truncado")
    en, z = struct.unpack(">HI", c[2:8])
    tipo = None
    if z == 1:
        lbox, tbox = struct.unpack(">I4s", c[8:16])
        pos = 16 + (8 if lbox == 1 else 0)
        if tbox == b"jumb" and c[pos + 4:pos + 8] == b"jumd":
            tipo = c[pos + 8:pos + 24]
    return en, z, tipo


# ---------------------------------------------------------------------------
# PNG: recorrido

_FIRMA_PNG = bytes.fromhex("89504e470d0a1a0a")
_CRITICOS = {"IHDR", "PLTE", "IDAT", "IEND"}
# Siempre se conservan (color y presentación, también pHYs, sBIT, bKGD, hIST y sPLT)
CONSERVADOS_PNG = {"tRNS", "iCCP", "sRGB", "gAMA", "cHRM", "cICP", "mDCV", "cLLI",
                   "pHYs", "sBIT", "bKGD", "hIST", "sPLT"}
_TEXTO_PNG = {"tEXt", "zTXt", "iTXt"}


@dataclass
class Chunk:
    tipo: str
    carga: bytes
    crudo: bytes
    crc_ok: bool = True


def recorrer_png(datos: bytes) -> tuple[list[Chunk], bytes]:
    """(chunks hasta IEND incluido, datos tras IEND)."""
    if datos[:8] != _FIRMA_PNG:
        raise Rota("sin la firma PNG")
    n = len(datos)
    i, chunks = 8, []
    while True:
        if i + 12 > n:
            raise Rota("falta IEND")
        largo = struct.unpack(">I", datos[i:i + 4])[0]
        tipo = datos[i + 4:i + 8]
        if largo > 0x7FFFFFFF or i + 12 + largo > n:
            raise Rota("longitud de chunk imposible")
        if not all(65 <= b <= 90 or 97 <= b <= 122 for b in tipo):
            raise Rota("tipo de chunk no válido")
        carga = datos[i + 8:i + 8 + largo]
        crc = struct.unpack(">I", datos[i + 8 + largo:i + 12 + largo])[0]
        chunks.append(Chunk(tipo.decode("ascii"), carga, datos[i:i + 12 + largo], zlib.crc32(tipo + carga) == crc))
        i += 12 + largo
        if tipo == b"IEND":
            return chunks, datos[i:]


def _crudo_png(tipo: str, carga: bytes) -> bytes:
    t = tipo.encode("ascii")
    return struct.pack(">I", len(carga)) + t + carga + struct.pack(">I", zlib.crc32(t + carga))


def _validar_png(chunks: list[Chunk]) -> None:
    """Omitido si el PNG no es uno estático y bien formado que se pueda verificar."""
    for c in chunks:
        if not c.crc_ok:
            raise Omitido(f"PNG con CRC inválido (chunk {c.tipo}): no se modifica")
    tipos = [c.tipo for c in chunks]
    if tipos[0] != "IHDR" or len(chunks[0].carga) != 13 or tipos.count("IHDR") != 1:
        raise Omitido("estructura PNG no válida (IHDR)")
    if {"acTL", "fcTL", "fdAT"} & set(tipos):
        raise Omitido("PNG animado (APNG): no soportado")
    if "dSIG" in tipos:
        raise Omitido("PNG firmado (dSIG): no se modifica")
    for t in tipos:
        if t[0].isupper() and t not in _CRITICOS:
            raise Omitido(f"PNG con un chunk crítico desconocido ({t}): no se modifica")
    idat = [k for k, t in enumerate(tipos) if t == "IDAT"]
    if not idat or idat != list(range(idat[0], idat[-1] + 1)):
        raise Omitido("estructura PNG no válida (IDAT)")
    if "PLTE" in tipos and tipos.index("PLTE") > idat[0]:
        raise Omitido("estructura PNG no válida (PLTE)")


def _descomprimir(datos: bytes) -> bytes | None:
    """None si está dañado o pasa del límite (bomba de compresión)."""
    d = zlib.decompressobj()
    try:
        salida = d.decompress(datos, _MAX_DESCOMPRIMIDO)
    except zlib.error:
        return None
    return None if d.unconsumed_tail else salida


@dataclass
class TextoPNG:
    clave: str
    texto: str | None       # None: no se pudo inspeccionar
    crudo_texto: bytes = b""


def texto_png(c: Chunk) -> TextoPNG:
    clave, _, resto = c.carga.partition(_NUL)
    nombre = clave.decode("latin-1")
    if c.tipo == "tEXt":
        return TextoPNG(nombre, resto.decode("latin-1"), resto)
    if c.tipo == "zTXt":
        cuerpo = _descomprimir(resto[1:]) if resto[:1] == _NUL else None
        return TextoPNG(nombre, cuerpo.decode("latin-1") if cuerpo is not None else None, cuerpo or b"")
    if len(resto) < 2:
        return TextoPNG(nombre, None)
    comprimido = resto[0] == 1
    _idioma, _, resto2 = resto[2:].partition(_NUL)
    _traducida, _, cuerpo = resto2.partition(_NUL)
    if comprimido:
        cuerpo = _descomprimir(cuerpo)
    if cuerpo is None:
        return TextoPNG(nombre, None)
    return TextoPNG(nombre, cuerpo.decode("utf-8", "replace"), cuerpo)


def _texto_con(c: Chunk, nuevo: bytes) -> bytes:
    """El chunk de texto c con otro texto, manteniendo tipo, clave, idioma y compresión."""
    clave, _, resto = c.carga.partition(_NUL)
    if c.tipo == "tEXt":
        return _crudo_png("tEXt", clave + _NUL + nuevo)
    if c.tipo == "zTXt":
        return _crudo_png("zTXt", clave + _NUL + _NUL + zlib.compress(nuevo, 9))
    comprimido = resto[0] == 1
    idioma, _, resto2 = resto[2:].partition(_NUL)
    traducida, _, _ = resto2.partition(_NUL)
    cuerpo = zlib.compress(nuevo, 9) if comprimido else nuevo
    return _crudo_png("iTXt", clave + _NUL + resto[:2] + idioma + _NUL + traducida + _NUL + cuerpo)


# ---------------------------------------------------------------------------
# EXIF (lector propio: para decidir y para verificar)

_TAM_TIPO = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
_PUNTEROS = {0x8769, 0x8825, 0xA005, 0x0201, 0x0202}


@dataclass
class Exif:
    orden: str
    entradas: dict[tuple[str, int], tuple[int, int, bytes]] = field(default_factory=dict)
    miniatura: bytes | None = None

    def sin_punteros(self) -> dict:
        return {k: v for k, v in self.entradas.items() if k[1] not in _PUNTEROS
                and not (k[0] == "IFD1" and k[1] == 0x0111)}


def leer_exif(t: bytes) -> Exif:
    if t[:2] == b"II":
        o = "<"
    elif t[:2] == b"MM":
        o = ">"
    else:
        raise Rota("EXIF sin orden de bytes")
    if len(t) < 8 or struct.unpack(o + "H", t[2:4])[0] != 42:
        raise Rota("EXIF no válido")
    exif = Exif(o)
    vistos: set[int] = set()

    def ifd(nombre: str, pos: int) -> int:
        if pos in vistos or pos + 2 > len(t):
            raise Rota("IFD fuera de rango")
        vistos.add(pos)
        cuenta = struct.unpack(o + "H", t[pos:pos + 2])[0]
        if cuenta > 4000 or pos + 6 + 12 * cuenta > len(t):
            raise Rota("IFD truncado")
        for k in range(cuenta):
            e = pos + 2 + 12 * k
            tag, tipo, n = struct.unpack(o + "HHI", t[e:e + 8])
            tam = _TAM_TIPO.get(tipo, 1) * n
            if tam <= 4:
                valor = t[e + 8:e + 8 + tam]
            else:
                p = struct.unpack(o + "I", t[e + 8:e + 12])[0]
                if p + tam > len(t):
                    raise Rota("valor EXIF fuera de rango")
                valor = t[p:p + tam]
            exif.entradas[(nombre, tag)] = (tipo, n, valor)
        return struct.unpack(o + "I", t[pos + 2 + 12 * cuenta:pos + 6 + 12 * cuenta])[0]

    def puntero(clave):
        return struct.unpack(o + "I", exif.entradas[clave][2][:4])[0]

    siguiente = ifd("IFD0", struct.unpack(o + "I", t[4:8])[0])
    for tag, nombre in ((0x8769, "ExifIFD"), (0x8825, "GPS")):
        if ("IFD0", tag) in exif.entradas:
            ifd(nombre, puntero(("IFD0", tag)))
    if ("ExifIFD", 0xA005) in exif.entradas:
        ifd("Interop", puntero(("ExifIFD", 0xA005)))
    if siguiente:
        ifd("IFD1", siguiente)
        if ("IFD1", 0x0201) in exif.entradas and ("IFD1", 0x0202) in exif.entradas:
            ini, tam = puntero(("IFD1", 0x0201)), puntero(("IFD1", 0x0202))
            exif.miniatura = t[ini:ini + tam]
    return exif


def texto_exif(exif: Exif, ifd: str, tag: int) -> str | None:
    e = exif.entradas.get((ifd, tag))
    if e is None:
        return None
    tipo, _, v = e
    if tag == 0x9286:                                     # UserComment: 8 bytes de juego de caracteres
        codigo, cuerpo = v[:8], v[8:]
        if codigo.startswith(b"UNICODE"):
            par = "utf-16-le" if exif.orden == "<" else "utf-16-be"
            if cuerpo[:2] in (bytes.fromhex("fffe"), bytes.fromhex("feff")):
                par = "utf-16"
            return cuerpo.decode(par, "replace").strip(chr(0) + " ")
        return cuerpo.decode("utf-8", "replace").strip(chr(0) + " ")
    if 0x9C9B <= tag <= 0x9C9F:                           # XP*: UTF-16LE
        return v.decode("utf-16-le", "replace").rstrip(chr(0))
    if tipo == 2:
        crudo = v.split(_NUL, 1)[0]
        try:
            return crudo.decode("utf-8")
        except UnicodeDecodeError:
            return crudo.decode("latin-1")
    return None


def exif_minimo(exif: Exif | None) -> bytes | None:
    """TIFF con solo Orientation (--all), o None si no había orientación."""
    if exif is None or ("IFD0", 0x0112) not in exif.entradas:
        return None
    o = exif.orden
    tipo, n, valor = exif.entradas[("IFD0", 0x0112)]
    if (tipo, n) != (3, 1):
        return None
    cab = (b"II" if o == "<" else b"MM") + struct.pack(o + "HI", 42, 8)
    # Un IFD con una entrada (Orientation, SHORT, 1 valor en línea) y sin IFD siguiente
    return cab + struct.pack(o + "HHHI", 1, 0x0112, 3, 1) + valor + bytes(2) + struct.pack(o + "I", 0)


# Campos EXIF que se miran en modo IA: (ifd, tag, nombre para ExifTool, papel)
_OBJETIVOS_EXIF = [
    ("IFD0", 0x0131, "Software", "programa"),
    ("IFD0", 0x013B, "Artist", "artista"),
    ("IFD0", 0x010E, "ImageDescription", "texto"),
    ("ExifIFD", 0x9286, "UserComment", "texto"),
    ("IFD0", 0x9C9C, "XPComment", "texto"),
]


# ---------------------------------------------------------------------------
# IPTC (IIM) e IRB de Photoshop

@dataclass
class Recurso:
    firma: bytes
    ident: int
    nombre: bytes            # cadena Pascal con su relleno
    datos: bytes


def leer_irb(c: bytes) -> list[Recurso]:
    i, recursos = 0, []
    while i + 12 <= len(c):
        firma = c[i:i + 4]
        if firma not in (b"8BIM", b"MeSa", b"PHUT", b"AgHg", b"DCSR"):
            if c[i:].strip(_NUL):
                raise Rota("IRB no válido")
            break
        ident = struct.unpack(">H", c[i + 4:i + 6])[0]
        largo_nombre = c[i + 6]
        tam_nombre = 1 + largo_nombre + ((1 + largo_nombre) % 2)
        nombre = c[i + 6:i + 6 + tam_nombre]
        pos = i + 6 + tam_nombre
        tam = struct.unpack(">I", c[pos:pos + 4])[0]
        if pos + 4 + tam > len(c):
            raise Rota("IRB truncado")
        recursos.append(Recurso(firma, ident, nombre, c[pos + 4:pos + 4 + tam]))
        i = pos + 4 + tam + (tam % 2)
    return recursos


def escribir_irb(recursos: list[Recurso]) -> bytes:
    partes = []
    for r in recursos:
        partes.append(r.firma + struct.pack(">H", r.ident) + r.nombre + struct.pack(">I", len(r.datos))
                      + r.datos + (_NUL if len(r.datos) % 2 else b""))
    return b"".join(partes)


def leer_iim(d: bytes) -> list[tuple[int, int, bytes]]:
    i, datos = 0, []
    while i + 5 <= len(d) and d[i] == 0x1C:
        reg, ds = d[i + 1], d[i + 2]
        largo = struct.unpack(">H", d[i + 3:i + 5])[0]
        i += 5
        if largo & 0x8000:
            n = largo & 0x7FFF
            largo = int.from_bytes(d[i:i + n], "big")
            i += n
        datos.append((reg, ds, d[i:i + largo]))
        i += largo
    return datos


def _iim_de(datos: list[tuple[int, int, bytes]]) -> str:
    utf8 = any(r == 1 and ds == 90 and v == bytes.fromhex("1b2547") for r, ds, v in datos)
    return "utf-8" if utf8 else "latin-1"


# Campos IPTC (registro 2) que se miran en modo IA: dataset -> (nombre para ExifTool, papel, lista)
_OBJETIVOS_IPTC = {
    65: ("OriginatingProgram", "programa", False),
    80: ("By-line", "artista", True),
    120: ("Caption-Abstract", "texto", False),
    110: ("Credit", "credito", False),
}
# Recursos del IRB que --all quita: IPTC, su resumen, miniaturas, XMP y EXIF copiados
_IRB_QUITABLES_ALL = {0x0404, 0x0425, 0x0409, 0x040C, 0x0424, 0x0422, 0x0423}


def _iptc_de_perfil(texto: str) -> list[tuple[int, int, bytes]] | None:
    """IIM de un chunk PNG "Raw profile type iptc" (formato de ImageMagick)."""
    lineas = texto.split("\n")
    try:
        crudo = bytes.fromhex("".join(lineas[3:]))
    except ValueError:
        return None
    if crudo.startswith(b"8BIM"):
        try:
            iptc = [r.datos for r in leer_irb(crudo) if r.ident == 0x0404]
        except Rota:
            return None
        return leer_iim(iptc[0]) if iptc else []
    return leer_iim(crudo)


# ---------------------------------------------------------------------------
# Decisiones por campo

def _decidir(papel: str, valor: str | None) -> tuple[str, str] | None:
    """(motivo, resumen) si el valor de un campo es de IA."""
    if not valor:
        return None
    if papel in ("programa", "artista"):
        nombre = firmas.generador(valor)
        return (GENERADOR, f"Generador: {nombre}") if nombre else None
    if papel == "texto":
        if firmas.es_prompt(valor):
            return PROCEDENCIA, f"prompt de {len(valor)} caracteres"
        nombre = firmas.firma_en_texto(valor)
        return (PROCEDENCIA, f"Firma: {nombre}") if nombre else None
    if papel == "credito":
        nombre = firmas.firma_en_texto(valor) or firmas.generador(valor)
        return (PROCEDENCIA, f"Firma: {nombre}") if nombre else None
    return None


# Claves de texto PNG estándar o conocidas: se muestran tal cual; las demás pueden ser
# datos personales y solo se informa de su longitud
_CLAVES_PUBLICAS = {
    "title", "author", "description", "copyright", "creation time", "software", "disclaimer",
    "warning", "source", "comment", "xml:com.adobe.xmp", "raw profile type iptc",
    "raw profile type exif", "raw profile type app1", "raw profile type xmp",
} | firmas.CLAVES_PNG_IA


def _cuantos(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _nombre_clave(clave: str) -> str:
    return f"«{clave}»" if clave.lower() in _CLAVES_PUBLICAS else f"(clave de {len(clave)} caracteres)"


# ---------------------------------------------------------------------------
# Plan: qué se quita y cómo

@dataclass
class Plan:
    tipo: str
    modo: Modo
    piezas: list = field(default_factory=list)          # Pieza (JPEG) o Chunk (PNG)
    trailer: bytes = b""
    quitar: set[int] = field(default_factory=set)
    reemplazar: dict[int, bytes] = field(default_factory=dict)
    exiftool: list[str] = field(default_factory=list)
    exif_quitar: set[tuple[str, int]] = field(default_factory=set)
    iptc_quitar: list[tuple[int, int, bytes]] = field(default_factory=list)
    quitar_trailer: bool = False
    guids_extendido: set[str] = field(default_factory=set)   # XMP extendido reescrito (JPEG)
    hallazgos: list[Hallazgo] = field(default_factory=list)

    @property
    def tratables(self) -> list[Hallazgo]:
        return [h for h in self.hallazgos if h.tratado]

    def hallazgo(self, categoria: str, clave: str, ubicacion: str, motivo: str, resumen: str = "",
                 valor: str = "", cantidad: int = 1, tratado: bool = True) -> None:
        self.hallazgos.append(Hallazgo(
            categoria=categoria, clave=clave, valor=valor, cantidad=cantidad, ubicacion=ubicacion,
            es_ia=motivo != AMPLIO, resumen=resumen, tratado=tratado, motivo=motivo,
        ))

    def no_tratado(self, clave: str, ubicacion: str, motivo: str, resumen: str, cantidad: int = 1) -> None:
        self.hallazgo("no_tratado", clave, ubicacion, motivo, resumen, cantidad=cantidad, tratado=False)


def planificar(datos: bytes, tipo: str, modo: Modo) -> Plan:
    """Analiza los bytes y decide qué se quita. Omitido si es una variante no soportada."""
    try:
        return _plan_jpeg(datos, modo) if tipo == "jpeg" else _plan_png(datos, modo)
    except Rota as e:
        raise Omitido(f"estructura {tipo.upper()} no válida ({e}): no se modifica") from None


def _xmp_objetivos(plan: Plan, idx: int, paquete: bytes, ubicacion: str, cabecera: bytes,
                   reconstruir, guids: dict[str, str] | None = None) -> xmp.Documento | None:
    """Objetivos IA de un paquete XMP. En modo IA, reemplaza la pieza por el paquete sin ellos (y
    con el GUID nuevo en xmpNote:HasExtendedXMP si se editó su XMP extendido)."""
    try:
        doc = xmp.analizar(paquete)
    except xmp.ErrorXMP:
        if plan.modo is Modo.TODO:
            plan.quitar.add(idx)
            plan.hallazgo("xmp", "XMP (ilegible)", ubicacion, AMPLIO, "paquete XMP que no se puede leer")
        else:
            plan.no_tratado("XMP ilegible", ubicacion, PROCEDENCIA, "no se puede inspeccionar")
        return None
    objetivos = xmp.objetivos_ia(doc)
    for o in objetivos:
        plan.hallazgo("xmp", o.clave, ubicacion, o.motivo, o.resumen, valor=o.valor, cantidad=o.cantidad)
    if plan.modo is Modo.TODO:
        plan.quitar.add(idx)
        resto = sum(1 for _ in xmp.propiedades(doc)) - len(objetivos)
        if resto > 0:
            plan.hallazgo("xmp", "resto de propiedades", ubicacion, AMPLIO, _cuantos(resto, "propiedad", "propiedades"), cantidad=resto)
    else:
        cambios = []
        guid = xmp.guid_extendido(doc)
        if guids and guid in guids:
            cambio = xmp.cambio_de_valor(doc, xmp.NS_XMPNOTE, "HasExtendedXMP", guids[guid])
            if cambio is None:
                raise Omitido("no se puede actualizar xmpNote:HasExtendedXMP: no se modifica")
            cambios.append(cambio)
        if objetivos or cambios:
            try:
                nuevo = xmp.quitar(paquete, [t for o in objetivos for t in o.tramos], cambios)
            except xmp.ErrorXMP:
                raise Omitido("el XMP no se puede editar de forma segura: no se modifica") from None
            plan.reemplazar[idx] = reconstruir(cabecera, nuevo)
    return doc


def _exif_objetivos(plan: Plan, exif: Exif, ubicacion: str) -> int:
    """Añade los objetivos IA de un EXIF (para ExifTool). Devuelve cuántos hay."""
    n = 0
    for ifd, tag, nombre, papel in _OBJETIVOS_EXIF:
        valor = texto_exif(exif, ifd, tag)
        decision = _decidir(papel, valor)
        if decision:
            motivo, resumen = decision
            plan.hallazgo("exif", nombre, ubicacion, motivo, resumen, valor=valor)
            plan.exif_quitar.add((ifd, tag))
            if plan.modo is Modo.IA:
                plan.exiftool.append(f"-{ifd}:{nombre}=")
            n += 1
    return n


def _exif_todo(plan: Plan, exif: Exif | None, ubicacion: str, ya: int) -> bytes | None:
    """--all: hallazgo del resto del EXIF y el TIFF mínimo que lo sustituye (o None)."""
    resto = len([k for k in (exif.sin_punteros() if exif else {}) if k != ("IFD0", 0x0112)]) - ya
    if exif is None or resto > 0 or exif.miniatura:
        plan.hallazgo("exif", "todo salvo Orientation", ubicacion, AMPLIO,
                      _cuantos(max(resto, 0), "campo", "campos") + (" y la miniatura" if exif and exif.miniatura else ""),
                      cantidad=max(resto, 1))
    return exif_minimo(exif)


def _iptc_objetivos(plan: Plan, iim: list[tuple[int, int, bytes]], ubicacion: str) -> int:
    codificacion = _iim_de(iim)
    n = 0
    for reg, ds, v in iim:
        if reg != 2 or ds not in _OBJETIVOS_IPTC:
            continue
        nombre, papel, lista = _OBJETIVOS_IPTC[ds]
        valor = v.decode(codificacion, "replace")
        decision = _decidir(papel, valor)
        if not decision:
            continue
        motivo, resumen = decision
        if plan.modo is Modo.IA:
            if lista and ("\n" in valor or "\r" in valor):
                plan.no_tratado(nombre, ubicacion, motivo, "valor con saltos de línea en una lista")
                continue
            plan.exiftool.append(f"-IPTC:{nombre}-={valor}" if lista else f"-IPTC:{nombre}=")
        plan.hallazgo("iptc", nombre, ubicacion, motivo, resumen, valor=valor)
        plan.iptc_quitar.append((reg, ds, v))
        n += 1
    if plan.modo is Modo.IA and n:
        plan.exiftool[:] = list(dict.fromkeys(plan.exiftool))
    return n


def _texto_libre(plan: Plan, texto: str, clave: str, ubicacion: str, categoria: str) -> bool:
    """Un comentario libre (COM, chunk de texto genérico) con firma o prompt de IA."""
    decision = _decidir("texto", texto) or _decidir("programa", texto)
    if decision:
        motivo, resumen = decision
        plan.hallazgo(categoria, clave, ubicacion, motivo, resumen, valor=texto)
        return True
    return False


# --- JPEG -------------------------------------------------------------------

_SOF_SOPORTADOS = {0xC0, 0xC1, 0xC2}
_SOF_NOMBRES = {0xC3: "sin pérdida", 0xC5: "diferencial", 0xC6: "diferencial", 0xC7: "diferencial",
                0xC9: "aritmético", 0xCA: "aritmético", 0xCB: "aritmético", 0xCD: "aritmético",
                0xCE: "aritmético", 0xCF: "aritmético"}


def _plan_jpeg(datos: bytes, modo: Modo) -> Plan:
    piezas, trailer = recorrer_jpeg(datos)
    plan = Plan("jpeg", modo, piezas, trailer)
    marcas = [p.marcador for p in piezas]
    if 0xDE in marcas or 0xDF in marcas:
        raise Omitido("JPEG jerárquico: variante que el verificador no cubre")
    if 0xCC in marcas:
        raise Omitido("JPEG aritmético: variante que el verificador no cubre")
    sofs = [m for m in marcas if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC)]
    if len(sofs) != 1:
        raise Omitido("JPEG con varias tramas: variante que el verificador no cubre")
    if sofs[0] not in _SOF_SOPORTADOS:
        raise Omitido(f"JPEG {_SOF_NOMBRES.get(sofs[0], 'desconocido')}: variante que el verificador no cubre")
    clases = [clase_app(p) for p in piezas]
    if "gainmap" in clases:                              # p. ej. las fotos HDR de iPhone (MPF + mapa)
        raise Omitido("JPEG Ultra HDR (mapa de ganancia ISO 21496-1): no soportado")
    if "mpf" in clases:
        raise Omitido("JPEG con varias imágenes (MPF/MPO): no soportado")

    # C2PA: grupos de APP11 por En; cualquier otro JUMBF (JPEG XT...) no se toca
    grupos: dict[int, list[int]] = {}
    tipos_en: dict[int, bytes | None] = {}
    for k, (p, c) in enumerate(zip(piezas, clases)):
        if c == "jumbf":
            en, z, t = _jumbf_app11(p.carga)
            grupos.setdefault(en, []).append(k)
            if z == 1:
                if en in tipos_en:                       # dos almacenes con el mismo En: no se sabe cuál es cuál
                    raise Omitido("APP11 con cajas JUMBF ambiguas (mismo número de caja): no se modifica")
                tipos_en[en] = t
        elif p.marcador == 0xEB:
            raise Omitido("APP11 que no es JUMBF: no soportado")
    for en, indices in grupos.items():
        if tipos_en.get(en) != _C2PA_UUID:
            raise Omitido("APP11 (JUMBF) que no es C2PA, p. ej. JPEG XT: no soportado")
        plan.quitar.update(indices)
        tam = sum(len(piezas[k].carga) for k in indices)
        plan.hallazgo("c2pa", "manifiesto JUMBF", "APP11", PROCEDENCIA, f"manifiesto de {tam} bytes")

    # XMP principal (las variantes que se omiten se ven en él) y extendido
    def con_cabecera(cab: bytes, paquete: bytes) -> bytes:
        carga = cab + paquete
        if len(carga) + 2 > 0xFFFF:
            raise Rota("XMP demasiado grande para APP1")
        return bytes([0xFF, 0xE1]) + struct.pack(">H", len(carga) + 2) + carga

    guids = _xmp_extendido(plan, piezas, clases)        # GUID viejo -> nuevo si se edita el extendido
    referenciados = set()
    for k, (p, c) in enumerate(zip(piezas, clases)):
        if c == "xmp":
            doc = _xmp_objetivos(plan, k, p.carga[len(_ID_XMP):], "XMP (APP1)", _ID_XMP, con_cabecera, guids)
            if doc is not None and xmp.es_ultra_hdr(doc):
                raise Omitido("JPEG Ultra HDR (mapa de ganancia): no soportado")
            if doc is not None and xmp.es_foto_en_movimiento(doc):
                raise Omitido("foto en movimiento (Motion Photo): no soportado")
            if doc is not None and xmp.guid_extendido(doc) in guids:
                referenciados.add(xmp.guid_extendido(doc))
    if set(guids) - referenciados:
        raise Omitido("XMP extendido sin referencia en el XMP principal: no se modifica")
    plan.guids_extendido = set(guids.values())

    # EXIF (el primero lo edita ExifTool; los duplicados no se pueden tratar por campo)
    exifs = [k for k, c in enumerate(clases) if c == "exif"]
    for n, k in enumerate(exifs):
        try:
            exif = leer_exif(piezas[k].carga[len(_ID_EXIF):])
        except Rota:
            exif = None
        if modo is Modo.TODO:
            ya = _exif_objetivos(plan, exif, "EXIF (APP1)") if exif else 0
            minimo = _exif_todo(plan, exif, "EXIF (APP1)", ya) if n == 0 else None
            if minimo is not None:
                plan.reemplazar[k] = _app1_exif(minimo)
            else:
                plan.quitar.add(k)
        elif exif is None:
            plan.no_tratado("EXIF ilegible", "EXIF (APP1)", PROCEDENCIA, "no se puede inspeccionar")
        elif n == 0:
            _exif_objetivos(plan, exif, "EXIF (APP1)")
        elif any(_decidir(papel, texto_exif(exif, ifd, tag)) for ifd, tag, _, papel in _OBJETIVOS_EXIF):
            plan.no_tratado("EXIF duplicado con datos de IA", "EXIF (APP1)", GENERADOR,
                            "solo se edita el primer bloque EXIF")

    # IPTC / IRB de Photoshop
    for k, c in enumerate(clases):
        if c != "photoshop":
            continue
        try:
            recursos = leer_irb(piezas[k].carga[len(_ID_PHOTOSHOP):])
        except Rota:
            if modo is Modo.TODO:
                plan.no_tratado("IRB de Photoshop ilegible", "APP13", AMPLIO, "no se puede inspeccionar")
            continue
        iim = [d for r in recursos if r.ident == 0x0404 for d in leer_iim(r.datos)]
        ya = _iptc_objetivos(plan, iim, "IPTC (APP13)")
        if modo is Modo.TODO:
            quedan = [r for r in recursos if r.ident not in _IRB_QUITABLES_ALL]
            if len(quedan) != len(recursos):
                resto = len(iim) - ya + len([r for r in recursos if r.ident in _IRB_QUITABLES_ALL and r.ident != 0x0404])
                if resto > 0:
                    plan.hallazgo("iptc", "IPTC y recursos de Photoshop", "APP13", AMPLIO, _cuantos(resto, "elemento", "elementos"), cantidad=resto)
                if quedan:
                    carga = _ID_PHOTOSHOP + escribir_irb(quedan)
                    plan.reemplazar[k] = bytes([0xFF, 0xED]) + struct.pack(">H", len(carga) + 2) + carga
                else:
                    plan.quitar.add(k)

    # COM y otros segmentos
    for k, (p, c) in enumerate(zip(piezas, clases)):
        if c == "com":
            texto = p.carga.decode("utf-8", "replace")
            if _texto_libre(plan, texto, "Comentario (COM)", "COM", "comentario"):
                plan.quitar.add(k)
            elif modo is Modo.TODO:
                plan.quitar.add(k)
                plan.hallazgo("comentario", "Comentario (COM)", "COM", AMPLIO, f"{len(texto)} caracteres", valor=texto)
        elif c in ("ducky", "fpxr") and modo is Modo.TODO:
            plan.quitar.add(k)
            nombre = "APP12 Ducky (Photoshop «Guardar para web»)" if c == "ducky" else "APP2 FlashPix (FPXR)"
            plan.hallazgo("app", nombre, f"APP{p.marcador - 0xE0}", AMPLIO, f"{len(p.carga)} bytes")
        elif c in ("otro", "jfxx") and modo is Modo.TODO:
            plan.no_tratado(f"Segmento APP{p.marcador - 0xE0} desconocido", f"APP{p.marcador - 0xE0}", AMPLIO,
                            "no se borra a ciegas")
    if trailer and modo is Modo.TODO:
        plan.no_tratado("Datos tras el final de la imagen (EOI)", "tras EOI", AMPLIO,
                        f"{len(trailer)} bytes; no se borran a ciegas")
    return plan


def _app1_exif(tiff: bytes) -> bytes:
    carga = _ID_EXIF + tiff
    return bytes([0xFF, 0xE1]) + struct.pack(">H", len(carga) + 2) + carga


_MAX_TROZO_EXT = 65400       # datos por segmento de XMP extendido (cabe en un APP1 con su cabecera)


def _trozos_extendido(piezas: list[Pieza], clases: list) -> dict[str, list[tuple[int, int, int, bytes]]]:
    """GUID -> [(índice de la pieza, longitud total, desplazamiento, datos)]."""
    grupos: dict[str, list[tuple[int, int, int, bytes]]] = {}
    for k, (p, c) in enumerate(zip(piezas, clases)):
        if c != "xmp_ext":
            continue
        cuerpo = p.carga[len(_ID_XMP_EXT):]
        if len(cuerpo) < 40:
            raise Rota("XMP extendido truncado")
        total, desp = struct.unpack(">II", cuerpo[32:40])
        grupos.setdefault(cuerpo[:32].decode("latin-1"), []).append((k, total, desp, cuerpo[40:]))
    return grupos


def ensamblar_extendido(trozos: list[tuple[int, int, int, bytes]]) -> bytes | None:
    """El paquete extendido completo, o None si los trozos no cuadran (huecos, solapes, longitud)."""
    totales = {t for _, t, _, _ in trozos}
    if len(totales) != 1:
        return None
    paquete = b""
    for _, _, desp, datos in sorted(trozos, key=lambda x: x[2]):
        if desp != len(paquete):
            return None
        paquete += datos
    return paquete if len(paquete) == totales.pop() else None


def segmentos_extendido(guid: str, paquete: bytes) -> bytes:
    partes = []
    for desp in range(0, len(paquete), _MAX_TROZO_EXT):
        carga = (_ID_XMP_EXT + guid.encode("ascii") + struct.pack(">II", len(paquete), desp)
                 + paquete[desp:desp + _MAX_TROZO_EXT])
        partes.append(bytes([0xFF, 0xE1]) + struct.pack(">H", len(carga) + 2) + carga)
    return b"".join(partes)


def _xmp_extendido(plan: Plan, piezas: list[Pieza], clases: list) -> dict[str, str]:
    """XMP extendido. Modo IA: si tiene objetivos, se edita, se recalcula su GUID (MD5 en
    hexadecimal, en mayúsculas) y se vuelve a partir en segmentos coherentes; devuelve
    {GUID viejo: GUID nuevo} para actualizar xmpNote:HasExtendedXMP. Si no se puede
    garantizar la coherencia, OMITIDO. --all: se quita entero."""
    grupos = _trozos_extendido(piezas, clases)
    if not grupos:
        return {}
    indices = [k for trozos in grupos.values() for k, *_ in trozos]
    if plan.modo is Modo.TODO:
        plan.quitar.update(indices)
        plan.hallazgo("xmp", "XMP extendido", "XMP (APP1)", AMPLIO, _cuantos(len(indices), "segmento", "segmentos"), cantidad=len(indices))
        return {}
    cambios = {}
    for guid, trozos in grupos.items():
        paquete = ensamblar_extendido(trozos)
        try:
            doc = xmp.analizar(paquete) if paquete is not None else None
        except xmp.ErrorXMP:
            doc = None
        if doc is None:
            plan.no_tratado("XMP extendido ilegible", "XMP extendido (APP1)", PROCEDENCIA, "no se puede inspeccionar")
            continue
        objetivos = xmp.objetivos_ia(doc)
        if not objetivos:
            continue
        if hashlib.md5(paquete).hexdigest().upper() != guid.upper():
            raise Omitido("XMP extendido incoherente (su MD5 no coincide con el GUID): no se modifica")
        try:
            nuevo = xmp.quitar(paquete, [t for o in objetivos for t in o.tramos])
        except xmp.ErrorXMP:
            raise Omitido("el XMP extendido no se puede editar de forma segura: no se modifica") from None
        for o in objetivos:
            plan.hallazgo("xmp", o.clave, "XMP extendido (APP1)", o.motivo, o.resumen, valor=o.valor, cantidad=o.cantidad)
        nuevo_guid = hashlib.md5(nuevo).hexdigest().upper()
        ks = sorted(k for k, *_ in trozos)
        plan.quitar.update(ks[1:])
        plan.reemplazar[ks[0]] = segmentos_extendido(nuevo_guid, nuevo)
        cambios[guid] = nuevo_guid
    return cambios


# --- PNG --------------------------------------------------------------------

def _plan_png(datos: bytes, modo: Modo) -> Plan:
    chunks, trailer = recorrer_png(datos)
    _validar_png(chunks)
    plan = Plan("png", modo, chunks, trailer)
    exifs = 0
    for k, c in enumerate(chunks):
        if c.tipo == "caBX":
            plan.quitar.add(k)
            plan.hallazgo("c2pa", "manifiesto JUMBF", "chunk caBX", PROCEDENCIA, f"manifiesto de {len(c.carga)} bytes")
        elif c.tipo in _TEXTO_PNG:
            _texto_png_plan(plan, k, c)
        elif c.tipo == "eXIf":
            try:
                exif = leer_exif(c.carga)
            except Rota:
                exif = None
            if modo is Modo.TODO:
                ya = _exif_objetivos(plan, exif, "chunk eXIf") if exif else 0
                minimo = _exif_todo(plan, exif, "chunk eXIf", ya) if exifs == 0 else None
                if minimo is not None:
                    plan.reemplazar[k] = _crudo_png("eXIf", minimo)
                else:
                    plan.quitar.add(k)
            elif exif is None:
                plan.no_tratado("EXIF ilegible", "chunk eXIf", PROCEDENCIA, "no se puede inspeccionar")
            elif exifs == 0:
                _exif_objetivos(plan, exif, "chunk eXIf")
            elif any(_decidir(papel, texto_exif(exif, ifd, tag)) for ifd, tag, _, papel in _OBJETIVOS_EXIF):
                plan.no_tratado("EXIF duplicado con datos de IA", "chunk eXIf", GENERADOR,
                                "solo se edita el primer bloque EXIF")
            exifs += 1
        elif c.tipo == "tIME" and modo is Modo.TODO:
            plan.quitar.add(k)
            plan.hallazgo("png", "Fecha de modificación (tIME)", "chunk tIME", AMPLIO)
        elif c.tipo in _CRITICOS or c.tipo in CONSERVADOS_PNG or c.tipo == "tIME":
            continue
        elif modo is Modo.TODO:
            plan.no_tratado(f"Chunk auxiliar desconocido ({c.tipo})", f"chunk {c.tipo}", AMPLIO, "no se borra a ciegas")
    # Midjourney guarda en Author el nombre de usuario: en los PNG identificados como suyos
    # también se quita en modo IA (en los demás, Author solo con --all)
    if any("Midjourney" in h.resumen for h in plan.tratables):
        for k, c in enumerate(chunks):
            if c.tipo not in _TEXTO_PNG:
                continue
            t = texto_png(c)
            if t.clave.lower() != "author" or not t.texto:
                continue
            if k in plan.quitar:                         # --all ya lo quitaba: el motivo es el mismo que en modo IA
                plan.hallazgos[:] = [h for h in plan.hallazgos
                                     if not (h.clave == "texto «Author»" and h.motivo == AMPLIO and h.valor == t.texto)]
            plan.quitar.add(k)
            plan.hallazgo("png", f"texto {_nombre_clave(t.clave)}", f"chunk {c.tipo}", PROCEDENCIA,
                          f"usuario del servicio ({len(t.texto)} caracteres)", valor=t.texto)
    if trailer and modo is Modo.TODO:
        plan.quitar_trailer = True
        plan.hallazgo("png", "Datos tras IEND", "tras IEND", AMPLIO, f"{len(trailer)} bytes")
    return plan


def _texto_png_plan(plan: Plan, k: int, c: Chunk) -> None:
    t = texto_png(c)
    clave = t.clave.lower()
    ubicacion = f"chunk {c.tipo}"
    if clave == "xml:com.adobe.xmp":
        if t.texto is None:
            if plan.modo is Modo.TODO:
                plan.quitar.add(k)
                plan.hallazgo("xmp", "XMP", ubicacion, AMPLIO, "paquete XMP")
            else:
                plan.no_tratado("XMP ilegible", ubicacion, PROCEDENCIA, "no se puede inspeccionar")
            return
        _xmp_objetivos(plan, k, t.crudo_texto, f"XMP ({c.tipo})", b"", lambda _cab, nuevo: _texto_con(c, nuevo))
        return
    if clave == "raw profile type iptc":
        iim = _iptc_de_perfil(t.texto) if t.texto is not None else None
        if iim is None:
            if plan.modo is Modo.TODO:
                plan.quitar.add(k)
                plan.hallazgo("iptc", "IPTC (ilegible)", ubicacion, AMPLIO)
            else:
                plan.no_tratado("IPTC ilegible", ubicacion, PROCEDENCIA, "no se puede inspeccionar")
            return
        ya = _iptc_objetivos(plan, iim, "IPTC (perfil en texto)")
        if plan.modo is Modo.TODO:
            plan.quitar.add(k)
            if len(iim) - ya > 0:
                plan.hallazgo("iptc", "resto de campos", ubicacion, AMPLIO, _cuantos(len(iim) - ya, "campo", "campos"),
                              cantidad=len(iim) - ya)
        return
    nombre = f"texto {_nombre_clave(t.clave)}"
    if clave in firmas.CLAVES_PNG_IA:
        plan.quitar.add(k)
        largo = len(t.texto) if t.texto is not None else len(c.carga)
        plan.hallazgo("png", nombre, ubicacion, PROCEDENCIA, f"parámetros de generación ({largo} caracteres)",
                      valor=t.texto or "")
        return
    if clave in firmas.CLAVES_PNG_CONDICIONALES:
        if t.texto is None:
            if plan.modo is Modo.TODO:
                plan.quitar.add(k)
                plan.hallazgo("png", nombre, ubicacion, AMPLIO, "no se pudo inspeccionar")
            else:
                plan.no_tratado(nombre, ubicacion, PROCEDENCIA, "comprimido dañado o demasiado grande")
            return
        papel = "programa" if clave in ("software", "source") else "texto"
        decision = _decidir(papel, t.texto) or (_decidir("programa", t.texto) if papel == "texto" else None)
        if decision:
            plan.quitar.add(k)
            plan.hallazgo("png", nombre, ubicacion, decision[0], decision[1], valor=t.texto)
            return
    if plan.modo is Modo.TODO:
        plan.quitar.add(k)
        largo = len(t.texto) if t.texto is not None else len(c.carga)
        plan.hallazgo("png", nombre, ubicacion, AMPLIO, f"{largo} caracteres", valor=t.texto or "")


# ---------------------------------------------------------------------------
# Escritura

def aplicar(plan: Plan) -> bytes:
    """Los cambios propios (sin ExifTool)."""
    cuerpo = b"".join(plan.reemplazar.get(k, p.crudo) for k, p in enumerate(plan.piezas) if k not in plan.quitar)
    if plan.tipo == "png":
        return _FIRMA_PNG + cuerpo + (b"" if plan.quitar_trailer else plan.trailer)
    return cuerpo + plan.trailer


def _escribir(plan: Plan, destino: Path) -> None:
    nuevo = aplicar(plan)
    if not plan.exiftool:
        destino.write_bytes(nuevo)
        return
    # ExifTool necesita la extensión real; la copia y su salida van junto al destino con el
    # patrón de temporales (.<nombre>.aiclean-XXXX.in.jpg / .out.jpg) y se borran siempre
    base = destino.name[:-4] if destino.name.endswith(".tmp") else destino.name
    ext = EXTENSION[plan.tipo]
    entrada = destino.with_name(f"{base}.in.{ext}")
    salida = destino.with_name(f"{base}.out.{ext}")
    try:
        entrada.write_bytes(nuevo)
        exiftool.escribir(entrada, salida, plan.exiftool)
        os.replace(salida, destino)
    finally:
        entrada.unlink(missing_ok=True)
        salida.unlink(missing_ok=True)


def preparar(ruta: Path, tipo: str, modo: Modo, normalizar: bool = False) -> Preparado:
    rutas.exiftool()                                       # sin ExifTool: error claro (también en --dry-run)
    datos = ruta.read_bytes()
    plan = planificar(datos, tipo, modo)
    return Preparado(
        hallazgos=plan.hallazgos, escribir=lambda destino: _escribir(plan, destino),
        datos=datos, huella=hashlib.sha256(datos).hexdigest(), contexto=plan,
    )


# ---------------------------------------------------------------------------
# Verificación (lectores propios; no depende de ExifTool)

def verificar(plan: Plan, nuevos: bytes) -> None:
    if plan.tipo == "jpeg":
        _verificar_jpeg(plan, nuevos)
    else:
        _verificar_png(plan, nuevos)
    # Segundo escaneo: no queda nada tratable y lo no tratado es lo mismo
    try:
        otra = planificar(nuevos, plan.tipo, plan.modo)
    except Omitido as e:
        raise NoCuadra(f"la salida ya no se puede analizar ({e})") from None
    if otra.tratables:
        raise NoCuadra(f"después de limpiar quedan {len(otra.tratables)} objetivos")
    if _no_tratados(otra) != _no_tratados(plan):
        raise NoCuadra("cambió lo que queda sin tratar")


def _no_tratados(plan: Plan) -> list:
    return sorted((h.clave, h.ubicacion, h.cantidad) for h in plan.hallazgos if not h.tratado)


def _esperado(plan: Plan) -> list:
    """Las piezas tras los cambios propios (lo que ExifTool recibió)."""
    if plan.tipo == "png":
        chunks, _ = recorrer_png(aplicar(plan))
        return chunks
    piezas, _ = recorrer_jpeg(aplicar(plan))
    return piezas


def _verificar_jpeg(plan: Plan, nuevos: bytes) -> None:
    try:
        despues, trailer = recorrer_jpeg(nuevos)
    except Rota as e:
        raise NoCuadra(f"la salida no es un JPEG válido ({e})") from None
    antes = plan.piezas
    esperado = _esperado(plan)

    def nucleo(ps):
        return [p.crudo for p in ps if clase_app(p) is None]

    if nucleo(antes) != nucleo(despues):
        raise NoCuadra("cambiaron los segmentos de imagen o los datos comprimidos")
    if trailer != plan.trailer:
        raise NoCuadra("cambiaron los datos tras el final de la imagen")

    def de_clase(ps, *clases):
        return [p.carga for p in ps if clase_app(p) in clases]

    for clase in ("jfif", "icc", "adobe", "jfxx", "otro"):
        if de_clase(antes, clase) != de_clase(despues, clase):
            raise NoCuadra(f"cambió un segmento que se conserva ({clase})")
    for clase in ("jumbf", "xmp", "xmp_ext", "ducky", "fpxr", "com"):
        if de_clase(esperado, clase) != de_clase(despues, clase):
            raise NoCuadra(f"el resultado no es el esperado ({clase})")
    _comprobar_extendido(plan, despues)
    _comparar_exif(plan, de_clase(esperado, "exif"), de_clase(despues, "exif"), len(_ID_EXIF))
    _comparar_irb(plan, de_clase(esperado, "photoshop"), de_clase(despues, "photoshop"))


def _comprobar_extendido(plan: Plan, despues: list[Pieza]) -> None:
    """El XMP extendido reescrito: trozos completos, MD5 = GUID y referenciado por el principal."""
    if not plan.guids_extendido:
        return
    clases = [clase_app(p) for p in despues]
    try:
        grupos = _trozos_extendido(despues, clases)
    except Rota:
        raise NoCuadra("el XMP extendido de la salida está truncado") from None
    principales = set()
    for p, c in zip(despues, clases):
        if c == "xmp":
            try:
                principales.add(xmp.guid_extendido(xmp.analizar(p.carga[len(_ID_XMP):])))
            except xmp.ErrorXMP:
                raise NoCuadra("el XMP de la salida no se puede leer") from None
    for guid in plan.guids_extendido:
        paquete = ensamblar_extendido(grupos.get(guid, []))
        if paquete is None or hashlib.md5(paquete).hexdigest().upper() != guid or guid not in principales:
            raise NoCuadra("el XMP extendido de la salida no es coherente")


def _verificar_png(plan: Plan, nuevos: bytes) -> None:
    try:
        despues, trailer = recorrer_png(nuevos)
        _validar_png(despues)
    except (Rota, Omitido) as e:
        raise NoCuadra(f"la salida no es un PNG válido ({e})") from None
    antes = plan.piezas
    esperado = _esperado(plan)

    def de_tipo(cs, tipo):
        return [c.carga for c in cs if c.tipo == tipo]

    if b"".join(de_tipo(antes, "IDAT")) != b"".join(de_tipo(despues, "IDAT")):
        raise NoCuadra("cambiaron los datos de imagen (IDAT)")
    for tipo in ("IHDR", "PLTE", "IEND", *sorted(CONSERVADOS_PNG)):
        if de_tipo(antes, tipo) != de_tipo(despues, tipo):
            raise NoCuadra(f"cambió un chunk que se conserva ({tipo})")
    conocidos = _CRITICOS | CONSERVADOS_PNG | _TEXTO_PNG | {"eXIf", "caBX", "tIME"}
    if sorted(c.crudo for c in antes if c.tipo not in conocidos) != sorted(c.crudo for c in despues if c.tipo not in conocidos):
        raise NoCuadra("cambió un chunk desconocido")
    for tipo in ("caBX", "tIME"):
        if de_tipo(esperado, tipo) != de_tipo(despues, tipo):
            raise NoCuadra(f"el resultado no es el esperado ({tipo})")
    # Los chunks de texto pueden cambiar de sitio (ExifTool los pone antes de IDAT)
    def textos(cs):
        return sorted((c.tipo, c.carga) for c in cs if c.tipo in _TEXTO_PNG and texto_png(c).clave.lower() != "raw profile type iptc")
    if textos(esperado) != textos(despues):
        raise NoCuadra("el resultado no es el esperado (chunks de texto)")
    _comparar_exif(plan, de_tipo(esperado, "eXIf"), de_tipo(despues, "eXIf"), 0)

    def perfiles_iptc(cs):
        return [t for t in (texto_png(c) for c in cs if c.tipo in _TEXTO_PNG) if t.clave.lower() == "raw profile type iptc"]

    pe, pd = perfiles_iptc(esperado), perfiles_iptc(despues)
    if plan.modo is Modo.IA and plan.iptc_quitar and len(pd) < len(pe):
        # ExifTool borra el perfil si el IPTC queda vacío
        pe = [t for t in pe if not (_iptc_de_perfil(t.texto or "") is not None
                                    and _iim_vacio(_sin(_iptc_de_perfil(t.texto or ""), plan.iptc_quitar)))]
    if len(pe) != len(pd):
        raise NoCuadra("el resultado no es el esperado (IPTC)")
    for a, b in zip(pe, pd):
        ia, ib = _iptc_de_perfil(a.texto or ""), _iptc_de_perfil(b.texto or "")
        if ia is None or ib is None or _sin(ia, plan.iptc_quitar) != _sin(ib, []):
            raise NoCuadra("el resultado no es el esperado (IPTC)")
    if trailer != (b"" if plan.quitar_trailer else plan.trailer):
        raise NoCuadra("cambiaron los datos tras IEND")


def _comparar_exif(plan: Plan, esperados: list[bytes], despues: list[bytes], salto: int) -> None:
    """El EXIF de salida es el esperado menos los campos que quita ExifTool. Si no queda ningún
    campo, ExifTool borra el bloque entero, y eso también vale."""
    if esperados and plan.exif_quitar and plan.modo is Modo.IA and len(despues) == len(esperados) - 1:
        try:
            primero = leer_exif(esperados[0][salto:])
        except Rota:
            raise NoCuadra("el EXIF de la salida no se puede leer") from None
        if any(k not in plan.exif_quitar for k in primero.sin_punteros()) or primero.miniatura:
            raise NoCuadra("el resultado no es el esperado (número de bloques EXIF)")
        esperados = esperados[1:]
        if esperados != despues:
            raise NoCuadra("el resultado no es el esperado (EXIF)")
        return
    if len(esperados) != len(despues):
        raise NoCuadra("el resultado no es el esperado (número de bloques EXIF)")
    for k, (e, d) in enumerate(zip(esperados, despues)):
        if k > 0 or not plan.exif_quitar or plan.modo is Modo.TODO:
            if e != d:
                raise NoCuadra("el resultado no es el esperado (EXIF)")
            continue
        try:
            ee, dd = leer_exif(e[salto:]), leer_exif(d[salto:])
        except Rota:
            raise NoCuadra("el EXIF de la salida no se puede leer") from None
        quedan = {kk: v for kk, v in ee.sin_punteros().items() if kk not in plan.exif_quitar}
        if quedan != dd.sin_punteros() or ee.miniatura != dd.miniatura:
            raise NoCuadra("el EXIF cambió más de lo previsto")


def _irb_vacio_tras_quitar(plan: Plan, carga: bytes) -> bool:
    """El APP13 se queda sin nada tras quitar los objetivos (ExifTool lo borra entero)."""
    try:
        recursos = leer_irb(carga[len(_ID_PHOTOSHOP):])
    except Rota:
        return False
    if any(r.ident not in (0x0404, 0x0425) for r in recursos):
        return False
    iim = [x for r in recursos if r.ident == 0x0404 for x in leer_iim(r.datos)]
    return _iim_vacio(_sin(iim, plan.iptc_quitar))


def _iim_vacio(iim: list) -> bool:
    """Sin más que versiones de registro o el juego de caracteres."""
    return all((r, ds) in ((1, 0), (1, 90), (2, 0)) for r, ds, _ in iim)


def _comparar_irb(plan: Plan, esperados: list[bytes], despues: list[bytes]) -> None:
    hay_iptc = plan.modo is Modo.IA and plan.iptc_quitar
    if hay_iptc and len(despues) < len(esperados):
        esperados = [e for e in esperados if not _irb_vacio_tras_quitar(plan, e)]
    if len(esperados) != len(despues):
        raise NoCuadra("el resultado no es el esperado (APP13)")
    for e, d in zip(esperados, despues):
        if not hay_iptc:
            if e != d:
                raise NoCuadra("el resultado no es el esperado (APP13)")
            continue
        try:
            re_, rd = leer_irb(e[len(_ID_PHOTOSHOP):]), leer_irb(d[len(_ID_PHOTOSHOP):])
        except Rota:
            raise NoCuadra("el APP13 de la salida no se puede leer") from None
        otros = lambda rs: [(r.firma, r.ident, r.datos) for r in rs if r.ident not in (0x0404, 0x0425)]
        if otros(re_) != otros(rd):
            raise NoCuadra("cambió un recurso de Photoshop que se conserva")
        ie = [x for r in re_ if r.ident == 0x0404 for x in leer_iim(r.datos)]
        id_ = [x for r in rd if r.ident == 0x0404 for x in leer_iim(r.datos)]
        if _sin(ie, plan.iptc_quitar) != _sin(id_, []):
            raise NoCuadra("el IPTC cambió más de lo previsto")


def _sin(iim: list[tuple[int, int, bytes]], quitar: list[tuple[int, int, bytes]]) -> list:
    """IIM sin los campos quitados ni la versión de registro (que ExifTool reescribe)."""
    resto = list(iim)
    for q in quitar:
        if q in resto:
            resto.remove(q)
    return sorted(x for x in resto if (x[0], x[1]) not in ((1, 0), (2, 0)))
