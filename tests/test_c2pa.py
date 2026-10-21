"""C2PA con un lector independiente (c2patool) y las muestras oficiales fijadas en el lock.

c2patool se ejecuta sin red: ajustes propios (sin descargar manifiestos remotos ni OCSP ni
listas de confianza) y un proxy inexistente por si acaso. Los PNG/JPEG firmados se crean aquí
con el certificado de ejemplo de c2patool, sin sello de tiempo (sin servidor).
"""

import json
import os
import shutil
import subprocess

import pytest

from aiclean import cli
from aiclean.herramientas import rutas
from corpus_imagenes import apps, jpeg_base, nucleo_jpeg, pixeles, png_base

pytestmark = [pytest.mark.c2patool, pytest.mark.exiftool]

MUESTRAS_JPEG = ["adobe-20220124-C.jpg", "adobe-20220124-CA.jpg", "adobe-20220124-CACA.jpg",
                 "adobe-20220124-CIE-sig-CA.jpg", "adobe-20220124-E-uri-CA.jpg"]


@pytest.fixture
def c2pa(tmp_path):
    """Ejecuta c2patool sin red y devuelve (código, salida)."""
    ajustes = tmp_path / "c2pa-ajustes.json"
    ajustes.write_text(json.dumps({"verify": {"remote_manifest_fetch": False, "ocsp_fetch": False,
                                              "verify_trust": False}}), encoding="ascii")
    env = dict(os.environ, HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="")

    def ejecutar(*args):
        r = subprocess.run([str(rutas.c2patool()), "--settings", str(ajustes), *map(str, args)],
                           capture_output=True, env=env, timeout=120)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")

    return ejecutar


def limpiar(tmp_path, origen, *extra):
    destino = tmp_path / "in" / origen.name
    destino.parent.mkdir(exist_ok=True)
    shutil.copyfile(origen, destino)
    informe = tmp_path / "i.json"
    codigo = cli.main([str(destino), "-o", str(tmp_path / "out"), *extra, "--report-file", str(informe)])
    res = json.loads(informe.read_text(encoding="utf-8"))["resultados"][0]
    return codigo, res, tmp_path / "out" / origen.name


@pytest.mark.parametrize("nombre", MUESTRAS_JPEG)
def test_muestras_oficiales_jpeg(tmp_path, c2pa, nombre):
    origen = rutas.carpeta_tools() / "c2pa-muestras" / nombre
    codigo, salida = c2pa(origen)
    assert codigo == 0 and "active_manifest" in salida                 # antes: C2PA
    codigo, res, limpio = limpiar(tmp_path, origen)
    assert res["estado"] == "limpio" and res["verificacion"] == "superada"
    assert ("c2pa", "procedencia") in [(h["categoria"], h["motivo"]) for h in res["hallazgos"]]
    codigo, salida = c2pa(limpio)
    assert codigo != 0 and "No claim found" in salida                   # después: nada
    datos, original = limpio.read_bytes(), origen.read_bytes()
    assert nucleo_jpeg(datos) == nucleo_jpeg(original) and pixeles(datos) == pixeles(original)
    assert apps(datos, 0xE1, b"Exif") == apps(original, 0xE1, b"Exif")   # el EXIF, intacto en modo IA


def _firmar(tmp_path, c2pa, nombre, datos):
    sin = tmp_path / f"sin-{nombre}"
    sin.write_bytes(datos)
    muestra = (rutas.carpeta_tools() / "c2patool" / "sample").as_posix()
    manifiesto = tmp_path / "manifiesto.json"
    manifiesto.write_text(json.dumps({
        "alg": "es256", "private_key": f"{muestra}/es256_private.key", "sign_cert": f"{muestra}/es256_certs.pem",
        "claim_generator_info": [{"name": "aiclean-tests", "version": "1"}],
        "assertions": [{"label": "c2pa.actions", "data": {"actions": [{
            "action": "c2pa.created",
            "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"}]}}],
    }), encoding="ascii")
    firmado = tmp_path / nombre
    codigo, salida = c2pa(sin, "-m", manifiesto, "-o", firmado, "-f")
    assert codigo == 0, salida
    return firmado


@pytest.mark.parametrize("nombre,base", [("a.png", png_base("RGBA")), ("a.jpg", jpeg_base())])
def test_firmados_en_local(tmp_path, c2pa, nombre, base):
    firmado = _firmar(tmp_path, c2pa, nombre, base)
    codigo, salida = c2pa(firmado)
    assert codigo == 0 and "trainedAlgorithmicMedia" in salida
    codigo, res, limpio = limpiar(tmp_path, firmado)
    assert res["estado"] == "limpio" and res["verificacion"] == "superada"
    codigo, salida = c2pa(limpio)
    assert codigo != 0 and "No claim found" in salida
    assert pixeles(limpio.read_bytes()) == pixeles(firmado.read_bytes())


def test_referencia_a_manifiesto_remoto_se_quita(tmp_path, c2pa):
    origen = rutas.carpeta_tools() / "c2pa-muestras" / "libpng-test_with_url.png"
    codigo, salida = c2pa(origen)
    assert codigo != 0 and "must fetch remote manifests" in salida     # c2patool no descarga nada
    codigo, res, limpio = limpiar(tmp_path, origen)
    assert res["estado"] == "limpio"
    codigo, salida = c2pa(limpio)
    assert "No claim found" in salida and "remote" not in salida


def test_png_oficial_sin_manifiesto(tmp_path, c2pa):
    origen = rutas.carpeta_tools() / "c2pa-muestras" / "sample1.png"
    codigo, salida = c2pa(origen)
    assert "No claim found" in salida
    codigo, res, limpio = limpiar(tmp_path, origen)
    assert res["estado"] in ("sin_cambios", "limpio")
    assert all(h["categoria"] != "c2pa" for h in res["hallazgos"])
