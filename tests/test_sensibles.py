"""Valores sensibles: por defecto no salen ni en el JSON ni en los diagnósticos (stdout/stderr).

Se esconden cadenas marcadas en texto oculto (tags Unicode), en URL de la marca de la web y
del portapapeles, y se comprueba que no aparecen salvo con --report-sensitive. Los errores
no las incluyen nunca. El texto que el usuario pide por la salida estándar no se censura.
"""

import json
import os
import subprocess
import sys

import pytest

from aiclean import cli, windows
from aiclean.formatos import texto
from conftest import RAIZ

MARCA = "SECRETO7781"
OCULTO = "".join(chr(0xE0000 + ord(c)) for c in f"prompt {MARCA}")
ZWSP = chr(0x200B)
URL_IA = f"https://{MARCA.lower()}.oaiusercontent.com/file-{MARCA}?token={MARCA}"


def contiene_marca(*textos):
    return any(MARCA in t or MARCA.lower() in t for t in textos)


def archivo_con_secretos(tmp_path):
    a = tmp_path / "a.txt"
    a.write_bytes(f"visible{OCULTO}{ZWSP}\n".encode())
    if sys.platform == "win32":
        with open(f"{a}:Zone.Identifier", "wb") as f:
            f.write(f"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl={URL_IA}\r\n".encode())
    return a


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_archivo_sin_report_sensitive(tmp_path, capsys, extra):
    a = archivo_con_secretos(tmp_path)
    informe = tmp_path / "i.json"
    cli.main([str(a), "--report", "--report-file", str(informe), *extra])
    salida = capsys.readouterr()
    datos = informe.read_text(encoding="utf-8")
    assert not contiene_marca(salida.out, salida.err, datos)
    hallazgos = json.loads(datos)["resultados"][0]["hallazgos"]
    tags = next(h for h in hallazgos if h["categoria"] == "etiqueta")
    assert tags["resumen"] == f"texto oculto de {len('prompt ' + MARCA)} caracteres" and "valor" not in tags
    if sys.platform == "win32":
        url = next(h for h in hallazgos if h["categoria"] == "windows")
        assert url["clave"] == "Zone.Identifier: HostUrl"
        assert url["resumen"] == "OpenAI (oaiusercontent.com)"        # sin el subdominio ni la ruta
        assert url["es_ia"] is True


def test_archivo_con_report_sensitive(tmp_path, capsys):
    a = archivo_con_secretos(tmp_path)
    informe = tmp_path / "i.json"
    cli.main([str(a), "--dry-run", "--report-sensitive", "--report-file", str(informe)])
    salida = capsys.readouterr()
    assert MARCA in salida.out                                    # diagnóstico autorizado
    datos = json.loads(informe.read_text(encoding="utf-8"))
    assert datos["valores_sensibles"] is True
    assert any(MARCA in h.get("valor", "") for h in datos["resultados"][0]["hallazgos"])


def test_informe_lleva_versiones(tmp_path):
    a = archivo_con_secretos(tmp_path)
    informe = tmp_path / "i.json"
    cli.main([str(a), "--dry-run", "--report-file", str(informe)])
    datos = json.loads(informe.read_text(encoding="utf-8"))
    assert {"aiclean", "reglas", "tablas_unicode", "unicodedata"} <= datos.keys()
    assert datos["valores_sensibles"] is False


# --- Entrada estándar: diagnóstico en stderr, texto pedido en stdout -----------

def ejecutar(args, entrada):
    env = dict(os.environ, PYTHONPATH=str(RAIZ / "src"))
    return subprocess.run([sys.executable, "-m", "aiclean", *args], input=entrada, capture_output=True, env=env)


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_stdin_sin_report_sensitive(tmp_path, extra):
    informe = tmp_path / "i.json"
    entrada = f"El texto {MARCA}-VISIBLE se pide tal cual{OCULTO}\n".encode()
    r = ejecutar(["-", "--report-file", str(informe), *extra], entrada)
    err = r.stderr.decode("utf-8", "replace")
    assert MARCA not in err and MARCA not in informe.read_text(encoding="utf-8")
    if not extra:
        # la salida estándar es el texto que pidió el usuario: no se censura
        assert r.stdout == f"El texto {MARCA}-VISIBLE se pide tal cual\n".encode()


def test_stdin_con_report_sensitive():
    r = ejecutar(["-", "--dry-run", "--report-sensitive"], f"x{OCULTO}".encode())
    assert MARCA in r.stderr.decode("utf-8", "replace")


# --- Portapapeles simulado -----------------------------------------------------

def _poner_secretos(pp):
    html = windows.escribir_cf_html(windows.HtmlPortapapeles(
        "<html><body><!--StartFragment-->", f"<p>hola{OCULTO}</p>", "<!--EndFragment--></body></html>", URL_IA))
    pp.poner(f"hola{OCULTO}", html, con_url=[("Chromium internal source URL", URL_IA)])


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_portapapeles_sin_report_sensitive(tmp_path, capsys, portapapeles_simulado, extra):
    _poner_secretos(portapapeles_simulado)
    informe = tmp_path / "i.json"
    cli.main(["--clipboard", "--report", "--report-file", str(informe), *extra])
    salida = capsys.readouterr()
    datos = informe.read_text(encoding="utf-8")
    assert not contiene_marca(salida.out, salida.err, datos)
    resumenes = {h["resumen"] for h in json.loads(datos)["resultados"][0]["hallazgos"]}
    assert "OpenAI (oaiusercontent.com)" in resumenes


def test_portapapeles_con_report_sensitive(capsys, portapapeles_simulado):
    _poner_secretos(portapapeles_simulado)
    cli.main(["--clipboard", "--dry-run", "--report-sensitive"])
    assert MARCA in capsys.readouterr().out


# --- Errores: nunca vuelcan contenido, ni con --report-sensitive ---------------

@pytest.mark.parametrize("extra", [[], ["--report-sensitive"]])
def test_error_inesperado_no_vuelca_su_mensaje(tmp_path, capsys, monkeypatch, extra):
    a = archivo_con_secretos(tmp_path)

    def revienta(*_a, **_k):
        raise ValueError(f"contenido: {MARCA}")

    monkeypatch.setattr(texto, "preparar", revienta)
    informe = tmp_path / "i.json"
    assert cli.main([str(a), "--report", "--report-file", str(informe), *extra]) == 1
    salida = capsys.readouterr()
    datos = informe.read_text(encoding="utf-8")
    assert not contiene_marca(salida.out, salida.err, datos)
    assert "error interno (ValueError" in datos


def test_error_de_decodificacion_no_vuelca_bytes(tmp_path, capsys):
    a = tmp_path / "a.txt"
    a.write_bytes(b"\xff\xfe" + MARCA.encode() + b"\x00\xd8")      # BOM UTF-16 con contenido roto
    cli.main([str(a), "--report-sensitive"])
    salida = capsys.readouterr()
    assert not contiene_marca(salida.out, salida.err)


@pytest.mark.parametrize("url,esperado", [
    ("https://files.oaiusercontent.com/x?y=1", "OpenAI (oaiusercontent.com)"),
    ("https://chatgpt.com/c/abc", "ChatGPT (chatgpt.com)"),
    ("https://u123.cdn.example.co.uk/p", "URL de procedencia"),
    ("https://id-9f8e.example.com/descarga?t=1", "URL de procedencia"),
    ("https://mi-blog-personal.es/", "URL de procedencia"),
    ("about:internet", "URL de procedencia (sin dominio)"),
    ("http://intranet/x", "URL de procedencia (sin dominio)"),
    ("http://192.168.1.10/x", "URL de procedencia (dirección IP)"),
    ("http://[2001:db8::1]/x", "URL de procedencia (dirección IP)"),
])
def test_describir_url(url, esperado):
    from aiclean import firmas
    assert firmas.describir_url(url) == esperado


# --- URL que no son de la lista: ni subdominio ni dominio en JSON/stdout/stderr --

SUB, PERSONAL = "u-5521", "dominiopersonal4412"
URL_PERSONAL = f"https://{SUB}.cdn.{PERSONAL}.co.uk/descarga?t=1"
REF_PERSONAL = f"https://www.{PERSONAL}.es/pagina"


def _sin_dominios(*textos):
    return not any(SUB in t or PERSONAL in t for t in textos)


@pytest.mark.skipif(sys.platform != "win32", reason="flujos NTFS")
@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_url_desconocida_en_la_marca_web_no_sale_en_informes(tmp_path, capsys, extra):
    a = tmp_path / "a.txt"
    a.write_bytes(b"texto normal\n")
    with open(f"{a}:Zone.Identifier", "wb") as f:
        f.write(f"[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl={REF_PERSONAL}\r\nHostUrl={URL_PERSONAL}\r\n".encode())
    informe = tmp_path / "i.json"
    cli.main([str(a), "--all", "--report", "--report-file", str(informe), *extra])
    salida = capsys.readouterr()
    datos = informe.read_text(encoding="utf-8")
    assert _sin_dominios(salida.out, salida.err, datos)
    hallazgos = json.loads(datos)["resultados"][0]["hallazgos"]
    assert {h["resumen"] for h in hallazgos} == {"URL de procedencia"}


def test_url_desconocida_del_portapapeles_no_sale_en_informes(tmp_path, capsys, portapapeles_simulado):
    html = windows.escribir_cf_html(windows.HtmlPortapapeles(
        "<html><body><!--StartFragment-->", "<p>hola</p>", "<!--EndFragment--></body></html>", URL_PERSONAL))
    portapapeles_simulado.poner("hola", html, con_url=[("Chromium internal source URL", REF_PERSONAL)])
    informe = tmp_path / "i.json"
    cli.main(["--clipboard", "--all", "--dry-run", "--report", "--report-file", str(informe)])
    salida = capsys.readouterr()
    datos = informe.read_text(encoding="utf-8")
    assert _sin_dominios(salida.out, salida.err, datos)
    assert {h["resumen"] for h in json.loads(datos)["resultados"][0]["hallazgos"]} == {"URL de procedencia"}


def test_url_desconocida_con_report_sensitive_sale_completa(tmp_path, capsys, portapapeles_simulado):
    html = windows.escribir_cf_html(windows.HtmlPortapapeles(
        "<html><body><!--StartFragment-->", "<p>hola</p>", "<!--EndFragment--></body></html>", URL_PERSONAL))
    portapapeles_simulado.poner("hola", html)
    informe = tmp_path / "i.json"
    cli.main(["--clipboard", "--all", "--dry-run", "--report-sensitive", "--report-file", str(informe)])
    assert URL_PERSONAL in capsys.readouterr().out
    hallazgos = json.loads(informe.read_text(encoding="utf-8"))["resultados"][0]["hallazgos"]
    assert [h["valor"] for h in hallazgos] == [URL_PERSONAL]
