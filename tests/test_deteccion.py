import zipfile

import pytest

from aiclean import deteccion


@pytest.mark.parametrize("cabecera,tipo", [
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "png"),
    (b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpeg"),
    (b"GIF89a" + b"\x00" * 10, "gif"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
    (b"II*\x00\x08\x00\x00\x00", "tiff"),
    (b"MM\x00*\x00\x00\x00\x08", "tiff"),
    (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic", "heif"),
    (b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1miaf", "avif"),
    (b"\x00\x00\x00\x18ftypqt  \x00\x00\x02\x00qt  ", "mov"),
    (b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom", "m4a"),
    (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2", "mp4"),
    (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm", "webm"),
    (b"\x1a\x45\xdf\xa3\xa3\x42\x86\x81\x01\x42\x82\x88matroska", "mkv"),
    (b"fLaC\x00\x00\x00\x22", "flac"),
    (b"OggS\x00\x02", "ogg"),
    (b"ID3\x04\x00\x00", "mp3"),
    (b"\xff\xfb\x90\x64\x00", "mp3"),
    (b"\xff\xf1\x50\x80", "aac"),
    (b"%PDF-1.7\n", "pdf"),
])
def test_firmas_binarias(tmp_path, cabecera, tipo):
    ruta = tmp_path / "archivo.bin"
    ruta.write_bytes(cabecera)
    assert deteccion.detectar(ruta) == tipo


def test_la_extension_no_manda(tmp_path):
    ruta = tmp_path / "en_realidad_jpeg.png"
    ruta.write_bytes(b"\xff\xd8\xff\xe1" + b"\x00" * 30)
    assert deteccion.detectar(ruta) == "jpeg"


@pytest.mark.parametrize("contenido,nombre,tipo", [
    ("hola\u200b".encode(), "a.txt", "texto"),
    ("<!DOCTYPE html><html><body>x</body></html>".encode(), "a.txt", "html"),
    ("<svg xmlns='http://www.w3.org/2000/svg'></svg>".encode(), "a.dat", "svg"),
    (b'<?xml version="1.0"?><a/>', "a.cfg", "xml"),
    (b"<p>sin doctype</p>", "pagina.html", "html"),
    (b"\xff\xfeh\x00o\x00l\x00a\x00", "utf16.txt", "texto"),
    ("año".encode("cp1252"), "ansi.txt", "texto"),
    (b"", "vacio.txt", "texto"),
])
def test_texto(tmp_path, contenido, nombre, tipo):
    ruta = tmp_path / nombre
    ruta.write_bytes(contenido)
    assert deteccion.detectar(ruta) == tipo


def test_utf16_no_se_confunde_con_mp3(tmp_path):
    ruta = tmp_path / "u.txt"
    ruta.write_bytes("\ufeffHola mundo".encode("utf-16-le"))
    assert deteccion.detectar(ruta) == "texto"


def test_binario_desconocido(tmp_path):
    ruta = tmp_path / "x.bin"
    ruta.write_bytes(b"\x01\x02\x00\x03" * 100)
    assert deteccion.detectar(ruta) == "desconocido"


@pytest.mark.parametrize("entradas,tipo", [
    ({"[Content_Types].xml": "", "word/document.xml": ""}, "docx"),
    ({"[Content_Types].xml": "", "xl/workbook.xml": ""}, "xlsx"),
    ({"[Content_Types].xml": "", "ppt/presentation.xml": ""}, "pptx"),
    ({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": ""}, "odt"),
    ({"mimetype": "application/vnd.oasis.opendocument.spreadsheet"}, "ods"),
    ({"otra.txt": "x"}, "zip"),
])
def test_zip(tmp_path, entradas, tipo):
    ruta = tmp_path / "doc.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        for nombre, contenido in entradas.items():
            z.writestr(nombre, contenido)
    assert deteccion.detectar(ruta) == tipo
