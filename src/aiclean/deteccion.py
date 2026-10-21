"""Tipo real de un archivo por su firma (magic bytes), no por la extensión."""

from __future__ import annotations

import codecs
import re
import zipfile
from pathlib import Path

CABECERA = 8192

TIPOS_TEXTO = {"texto", "html", "xml", "svg"}

# Marcas ISO-BMFF (ftyp) -> tipo
_MARCAS_HEIF = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"}
_MARCAS_AVIF = {b"avif", b"avis"}
_MARCAS_M4A = {b"M4A ", b"M4B ", b"M4P "}

_ODF = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/epub+zip": "epub",
}

_HTML = re.compile(r"<!doctype\s+html|<html[\s>]", re.I)
_SVG = re.compile(r"<svg[\s>]", re.I)

_EXT_HTML = {".html", ".htm", ".xhtml"}
_EXT_XML = {".xml", ".xsl", ".xslt", ".rss", ".atom", ".plist", ".xaml", ".resx", ".csproj"}


def detectar(ruta: Path) -> str:
    """Devuelve el tipo real: "png", "jpeg", ..., "texto", "html", "xml", "svg" o "desconocido"."""
    with open(ruta, "rb") as f:
        cab = f.read(CABECERA)
    tipo = tipo_binario(cab)
    if tipo == "zip":
        return _tipo_zip(ruta)
    if tipo:
        return tipo
    texto = tipo_texto(cab)
    if texto == "texto":
        ext = ruta.suffix.lower()
        if ext in _EXT_HTML:
            return "html"
        if ext == ".svg":
            return "svg"
        if ext in _EXT_XML:
            return "xml"
    return texto or "desconocido"


def tipo_binario(cab: bytes) -> str | None:
    """Tipo de un archivo binario por sus primeros bytes, o None si no se reconoce."""
    if cab.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if cab.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if cab[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if cab[:4] == b"RIFF" and len(cab) >= 12:
        return {b"WEBP": "webp", b"WAVE": "wav", b"AVI ": "avi"}.get(cab[8:12], "riff")
    if cab[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "tiff"
    if cab[4:8] == b"ftyp":
        return _tipo_isobmff(cab)
    if cab.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm" if b"webm" in cab[:64] else "mkv"
    if cab.startswith(b"fLaC"):
        return "flac"
    if cab.startswith(b"OggS"):
        return "ogg"
    if cab.startswith(b"ID3"):
        return "mp3"
    if _parece_utf16_con_bom(cab):
        return None        # FF FE / FE FF también parecen una trama MPEG
    if len(cab) >= 2 and cab[0] == 0xFF and (cab[1] & 0xE0) == 0xE0:
        # Sincronía de trama MPEG: capa 00 es AAC (ADTS), el resto MP1/2/3
        return "aac" if (cab[1] >> 1) & 0b11 == 0 else "mp3"
    if b"%PDF-" in cab[:1024]:
        return "pdf"
    if cab.startswith(b"PK\x03\x04"):
        return "zip"
    return None


def _parece_utf16_con_bom(cab: bytes) -> bool:
    if cab[:2] not in (b"\xff\xfe", b"\xfe\xff"):
        return False
    if cab.startswith(b"\xff\xfe\x00\x00"):
        return True        # BOM de UTF-32 LE
    codec = "utf-16-le" if cab[:2] == b"\xff\xfe" else "utf-16-be"
    cuerpo = cab[2:2 + (len(cab) - 2) // 2 * 2]
    texto = codecs.getincrementaldecoder(codec)(errors="strict")
    try:
        muestra = texto.decode(cuerpo, final=False)
    except UnicodeDecodeError:
        return False
    return all(c >= " " or c in "\t\r\n\x0c" for c in muestra)


def _tipo_isobmff(cab: bytes) -> str:
    talla = int.from_bytes(cab[0:4], "big")
    marca = cab[8:12]
    compatibles = {cab[i:i + 4] for i in range(16, min(talla, len(cab)) - 3, 4)}
    todas = {marca} | compatibles
    if marca in _MARCAS_AVIF:
        return "avif"
    if marca in _MARCAS_HEIF:
        return "heif"
    if marca == b"qt  ":
        return "mov"
    if marca in _MARCAS_M4A:
        return "m4a"
    if todas & _MARCAS_AVIF and not todas & {b"isom", b"mp41", b"mp42"}:
        return "avif"
    if todas & _MARCAS_HEIF and not todas & {b"isom", b"mp41", b"mp42"}:
        return "heif"
    return "mp4"


def _tipo_zip(ruta: Path) -> str:
    try:
        with zipfile.ZipFile(ruta) as z:
            nombres = set(z.namelist())
            if "mimetype" in nombres:
                mime = z.read("mimetype").decode("ascii", "replace").strip()
                return _ODF.get(mime, "zip")
            if "[Content_Types].xml" in nombres:
                if "word/document.xml" in nombres:
                    return "docx"
                if "xl/workbook.xml" in nombres:
                    return "xlsx"
                if "ppt/presentation.xml" in nombres:
                    return "pptx"
    except (zipfile.BadZipFile, OSError, KeyError):
        return "desconocido"
    return "zip"


def tipo_texto(cab: bytes) -> str | None:
    """"texto", "html", "xml" o "svg" si los bytes parecen texto; None si parecen binarios."""
    if cab.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")):
        return "texto"     # UTF-16/32 con BOM: lleva NUL, no se puede mirar más
    if b"\x00" in cab:
        return None
    cuerpo = cab[3:] if cab.startswith(b"\xef\xbb\xbf") else cab
    try:
        muestra = codecs.getincrementaldecoder("utf-8")().decode(cuerpo, final=False)
    except UnicodeDecodeError:
        try:
            muestra = cuerpo.decode("cp1252")
        except UnicodeDecodeError:
            return None
    inicio = muestra[:2048]
    if _HTML.search(inicio):
        return "html"
    if _SVG.search(inicio):
        return "svg"
    if inicio.lstrip().startswith("<?xml"):
        return "xml"
    return "texto"
