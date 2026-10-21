import sys

import pytest

from aiclean import motor, windows
from aiclean.modelos import Estado, Modo


def cf_html(fragmento: str, url: str | None = "https://chatgpt.com/c/123") -> bytes:
    h = windows.HtmlPortapapeles(
        antes="<html><body><!--StartFragment-->", fragmento=fragmento,
        despues="<!--EndFragment--></body></html>", source_url=url,
    )
    return windows.escribir_cf_html(h)


def test_cf_html_ida_y_vuelta():
    datos = cf_html("<p>hola ñandú 😀</p>")
    h = windows.leer_cf_html(datos)
    assert h.fragmento == "<p>hola ñandú 😀</p>"
    assert h.source_url == "https://chatgpt.com/c/123"
    # los desplazamientos de la cabecera son bytes UTF-8 exactos
    cab = datos[:datos.index(b"<html>")].decode()
    campos = dict(l.split(":", 1) for l in cab.strip().split("\r\n"))
    ini, fin = int(campos["StartFragment"]), int(campos["EndFragment"])
    assert datos[ini:fin].decode() == "<p>hola ñandú 😀</p>"
    assert int(campos["EndHTML"]) == len(datos)


def test_cf_html_de_chrome():
    # Formato real que deja Chrome (cabecera sin ceros a la izquierda y con SourceURL)
    html = "<html>\r\n<body>\r\n<!--StartFragment--><p>Texto\u200b</p><!--EndFragment-->\r\n</body>\r\n</html>"
    cab = "Version:0.9\r\nStartHTML:{:08d}\r\nEndHTML:{:08d}\r\nStartFragment:{:08d}\r\nEndFragment:{:08d}\r\nSourceURL:https://chatgpt.com/\r\n"
    largo = len(cab.format(0, 0, 0, 0).encode())
    cuerpo = html.encode()
    ini_f = largo + cuerpo.index(b"<p>")
    fin_f = largo + cuerpo.index(b"<!--EndFragment-->")
    datos = cab.format(largo, largo + len(cuerpo), ini_f, fin_f).encode() + cuerpo
    h = windows.leer_cf_html(datos)
    assert h.fragmento == "<p>Texto\u200b</p>"
    assert h.antes.endswith("<!--StartFragment-->")


def test_cf_html_mal_formado():
    with pytest.raises(ValueError):
        windows.leer_cf_html(b"Version:0.9\r\nStartHTML:5\r\n<html>")


@pytest.mark.skipif(sys.platform != "win32", reason="flujos NTFS")
def test_flujos_ntfs(tmp_path):
    a = tmp_path / "a.txt"
    a.write_bytes(b"x")
    assert windows.flujos_ntfs(a) == ["::$DATA"]
    with open(f"{a}:Zone.Identifier", "wb") as f:
        f.write(b"[ZoneTransfer]\r\nZoneId=3\r\n")
    with open(f"{a}:extra", "wb") as f:
        f.write(b"y")
    assert sorted(windows.flujos_ntfs(a)) == ["::$DATA", ":Zone.Identifier:$DATA", ":extra:$DATA"]
    assert windows.flujos_adicionales(a) == [":extra:$DATA"]


# --- Portapapeles real (guarda y restaura el texto que hubiera) -----------

@pytest.fixture
def portapapeles():
    if sys.platform != "win32":
        pytest.skip("solo Windows")
    anterior = windows.leer_portapapeles()
    yield
    windows.escribir_portapapeles(anterior.texto, anterior.html)


@pytest.mark.portapapeles
def test_portapapeles_real_texto_y_html(portapapeles):
    windows.escribir_portapapeles("Hola\u200b mundo\u00a0IA", cf_html("<p>Hola&#8203; mundo\u200b</p>"))
    opciones = motor.Opciones(modo=Modo.IA, dry_run=True)
    res = motor.procesar_portapapeles(opciones)
    assert res.estado is Estado.ENCONTRADO
    claves = {(h.ubicacion, h.categoria) for h in res.hallazgos}
    assert ("texto", "invisible") in claves and ("HTML", "invisible") in claves
    # valor = URL tal cual (sensible); el servicio va en resumen
    procedencia = [(h.clave, h.valor, h.resumen, h.motivo, h.es_ia) for h in res.hallazgos if h.categoria == "procedencia"]
    assert procedencia == [
        ("SourceURL del HTML copiado", "https://chatgpt.com/c/123", "ChatGPT (chatgpt.com)", "procedencia", True)]
    assert res.accion == "prevista"
    assert windows.leer_portapapeles().texto == "Hola\u200b mundo\u00a0IA"   # dry-run no toca

    res = motor.procesar_portapapeles(motor.Opciones(modo=Modo.IA))
    assert res.estado is Estado.LIMPIO, res.mensaje
    assert res.accion == "realizada"
    pp = windows.leer_portapapeles()
    assert pp.texto == "Hola mundo\u00a0IA"          # sin --normalize el NBSP se conserva
    h = windows.leer_cf_html(pp.html)
    assert h.fragmento == "<p>Hola mundo</p>"
    assert h.source_url is None

    assert motor.procesar_portapapeles(motor.Opciones()).estado is Estado.SIN_CAMBIOS


@pytest.mark.portapapeles
def test_portapapeles_url_normal_solo_con_all(portapapeles):
    windows.escribir_portapapeles("limpio", cf_html("<p>limpio</p>", "https://example.com/"))
    assert motor.procesar_portapapeles(motor.Opciones(dry_run=True)).estado is Estado.SIN_CAMBIOS
    res = motor.procesar_portapapeles(motor.Opciones(modo=Modo.TODO))
    assert res.estado is Estado.LIMPIO
    assert [(h.clave, h.valor, h.resumen, h.motivo, h.es_ia) for h in res.hallazgos] == [
        ("SourceURL del HTML copiado", "https://example.com/", "URL de procedencia", "amplio", False)]
    assert windows.leer_cf_html(windows.leer_portapapeles().html).source_url is None
