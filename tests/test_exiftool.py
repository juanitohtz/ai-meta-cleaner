"""ExifTool y sus fallos: ausente, colgado, que se cierra, que falla o que devuelve otra cosa.

En todos los casos el archivo da ERROR, no se publica ninguna salida y no quedan temporales
(ni las copias .in./.out. que usa ExifTool). El texto sigue funcionando sin ExifTool.
"""

import json
import sys

import pytest

from aiclean import cli, motor
from aiclean.herramientas import exiftool, rutas
from corpus_imagenes import ASCII, app1_exif, ascii_, insertar_jpeg, jpeg_base, tiff

CON_EXIF = insertar_jpeg(jpeg_base(), [app1_exif(tiff([
    (0x010F, ASCII, ascii_("Canon")), (0x0131, ASCII, ascii_("Midjourney")), (0x013B, ASCII, ascii_("Ana Garcia"))]))])


def ejecutar(tmp_path, *rutas_):
    informe = tmp_path / "i.json"
    codigo = cli.main([*map(str, rutas_), "--report-file", str(informe)])
    return codigo, json.loads(informe.read_text(encoding="utf-8"))["resultados"]


def sobrantes(carpeta):
    return sorted(p.name for p in carpeta.rglob("*") if ".aiclean-" in p.name or "_clean" in p.name)


def test_sin_exiftool_la_imagen_da_error_y_el_texto_sigue(tmp_path, monkeypatch):
    monkeypatch.setattr(rutas, "carpeta_tools", lambda: tmp_path / "no-hay-tools")
    imagen = tmp_path / "a.jpg"
    imagen.write_bytes(CON_EXIF)
    texto_ = tmp_path / "b.txt"
    texto_.write_bytes(("hola" + chr(0x200B) + "\n").encode())
    codigo, res = ejecutar(tmp_path, imagen, texto_)
    assert codigo == 1
    assert res[0]["estado"] == "error" and res[0]["mensaje"] == rutas.NO_HAY_EXIFTOOL
    assert res[1]["estado"] == "limpio"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jpg", "b.txt", "b_clean.txt", "i.json"]


def test_opcion_exiftool_que_no_existe_es_error_de_uso(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main([str(tmp_path / "a.jpg"), "--exiftool", str(tmp_path / "no.exe")])
    assert e.value.code == 2


def test_la_busqueda_no_usa_el_path(tmp_path, monkeypatch):
    falso = tmp_path / "exiftool.exe"
    falso.write_bytes(b"no")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rutas, "carpeta_tools", lambda: tmp_path / "no-hay-tools")
    with pytest.raises(rutas.HerramientaNoDisponible):
        rutas.exiftool()


@pytest.mark.parametrize("script,mensaje", [
    ("import time; time.sleep(30)", "ExifTool no respondió en 1 s"),
    ("import sys; sys.exit(0)", "ExifTool se cerró inesperadamente"),
])
def test_proceso_colgado_o_cerrado(script, mensaje):
    p = exiftool._Proceso(rutas.carpeta_tools(), orden=[sys.executable, "-c", script])
    with pytest.raises(exiftool.ErrorExifTool) as e:
        p.ejecutar(["-ver"], tiempo=1)
    assert str(e.value) == mensaje
    p.matar()
    assert p.p.poll() is not None


def test_argumento_con_salto_de_linea_no_se_envia():
    p = exiftool._Proceso(rutas.carpeta_tools(), orden=[sys.executable, "-c", "import time; time.sleep(5)"])
    with pytest.raises(exiftool.ErrorExifTool):
        p.ejecutar(["-IPTC:By-line-=a\n-o\nC:\\otro.jpg"])
    p.matar()


@pytest.mark.exiftool
@pytest.mark.parametrize("modo", [[], ["--in-place"]])
def test_exiftool_que_falla_no_deja_nada(tmp_path, monkeypatch, modo):
    def falla(entrada, salida, args):
        salida.write_bytes(b"a medias")
        raise exiftool.ErrorExifTool("ExifTool no pudo escribir el archivo (prueba)")

    monkeypatch.setattr(exiftool, "escribir", falla)
    a = tmp_path / "a.jpg"
    a.write_bytes(CON_EXIF)
    codigo, res = ejecutar(tmp_path, a, *modo)
    assert codigo == 1 and res[0]["estado"] == "error"
    assert res[0]["mensaje"] == "ExifTool no pudo escribir el archivo (prueba)"
    assert a.read_bytes() == CON_EXIF and sobrantes(tmp_path) == []


@pytest.mark.exiftool
@pytest.mark.parametrize("modo", [[], ["--in-place"]])
def test_salida_de_exiftool_que_no_cuadra_no_se_publica(tmp_path, monkeypatch, modo):
    real = exiftool.escribir
    monkeypatch.setattr(exiftool, "escribir", lambda e, s, args: real(e, s, [*args, "-IFD0:Make="]))   # quita de más
    a = tmp_path / "a.jpg"
    a.write_bytes(CON_EXIF)
    codigo, res = ejecutar(tmp_path, a, *modo)
    assert codigo == 1 and res[0]["estado"] == "error" and res[0]["verificacion"] == "fallida"
    assert res[0]["mensaje"] == "el EXIF cambió más de lo previsto"
    assert a.read_bytes() == CON_EXIF and sobrantes(tmp_path) == []


@pytest.mark.exiftool
def test_exiftool_real_con_entrada_que_no_existe():
    with pytest.raises(exiftool.ErrorExifTool) as e:
        exiftool.escribir(rutas.carpeta_tools() / "no-existe.jpg", rutas.carpeta_tools() / "no-salida.jpg", ["-IFD0:Make="])
    assert str(e.value).startswith("ExifTool no pudo escribir el archivo")
    assert "no-existe" not in str(e.value)                              # sin rutas en el mensaje
    exiftool.cerrar_todos()


@pytest.mark.parametrize("nombre,es", [
    (".a.jpg.aiclean-0123abcd.tmp", True),
    (".a.jpg.aiclean-0123abcd.in.jpg", True),
    (".a.png.aiclean-89abcdef.out.png", True),
    ("a.aiclean-0123abcd.in.jpg", False),
    (".a.jpg.aiclean-xyz.in.jpg", False),
    ("foto.in.jpg", False),
])
def test_patron_de_temporales(nombre, es):
    assert motor._es_temporal(nombre) is es


def test_recorrer_salta_las_copias_de_exiftool(tmp_path):
    (tmp_path / ".a.jpg.aiclean-0123abcd.in.jpg").write_bytes(CON_EXIF)
    (tmp_path / "b.txt").write_bytes(b"hola\n")
    entradas = motor.expandir([str(tmp_path)], motor.Opciones())
    assert [e.ruta.name for e in entradas] == ["b.txt"]
