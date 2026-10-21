import json
import os
import subprocess
import sys

import pytest

from aiclean import cli, windows
from conftest import RAIZ

SUCIO = "Respuesta\u200b generada\u00a0por IA.\r\n"
LIMPIO = "Respuesta generada\u00a0por IA.\r\n"              # el NBSP se conserva por defecto
LIMPIO_NORMALIZADO = "Respuesta generada por IA.\r\n"


def escribir(ruta, texto=SUCIO):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(texto.encode("utf-8"))
    return ruta


def test_crea_clean_y_no_toca_el_original(tmp_path):
    a = escribir(tmp_path / "a.txt")
    assert cli.main([str(a)]) == 0
    assert a.read_bytes() == SUCIO.encode()
    assert (tmp_path / "a_clean.txt").read_bytes() == LIMPIO.encode()


def test_no_sobrescribe_un_clean_existente(tmp_path):
    a = escribir(tmp_path / "a.txt")
    otro = escribir(tmp_path / "a_clean.txt", "mío")
    assert cli.main([str(a)]) == 0
    assert otro.read_text(encoding="utf-8") == "mío"
    assert (tmp_path / "a_clean_2.txt").read_bytes() == LIMPIO.encode()


def test_sin_nada_que_quitar_no_crea_archivo(tmp_path):
    a = escribir(tmp_path / "a.txt", "todo normal\n")
    assert cli.main([str(a)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_dry_run_no_toca_nada_y_devuelve_3(tmp_path, capsys):
    a = escribir(tmp_path / "a.txt")
    assert cli.main([str(a), "--dry-run"]) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]
    salida = capsys.readouterr().out
    assert "U+200B ZERO WIDTH SPACE" in salida
    assert "línea 1, col 10" in salida


def test_in_place(tmp_path):
    a = escribir(tmp_path / "a.txt")
    assert cli.main([str(a), "--in-place"]) == 0
    assert a.read_bytes() == LIMPIO.encode()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_carpeta_recursiva_con_salida_reproduce_el_arbol(tmp_path):
    origen = tmp_path / "origen"
    escribir(origen / "uno.txt")
    escribir(origen / "sub" / "dos.md")
    escribir(origen / "sub" / "tres_clean.txt")            # salida de una pasada anterior: se salta
    escribir(origen / ".git" / "config")                   # nunca se entra en .git
    destino = tmp_path / "destino"
    assert cli.main([str(origen), "-r", "-o", str(destino)]) == 0
    creados = sorted(p.relative_to(destino).as_posix() for p in destino.rglob("*") if p.is_file())
    assert creados == ["sub/dos.md", "uno.txt"]
    assert (destino / "sub" / "dos.md").read_bytes() == LIMPIO.encode()


def test_carpeta_sin_r_no_entra_en_subcarpetas(tmp_path):
    escribir(tmp_path / "uno.txt")
    escribir(tmp_path / "sub" / "dos.txt")
    assert cli.main([str(tmp_path)]) == 0
    assert (tmp_path / "uno_clean.txt").exists()
    assert not (tmp_path / "sub" / "dos_clean.txt").exists()


def test_comodines(tmp_path):
    escribir(tmp_path / "a.txt")
    escribir(tmp_path / "b.txt")
    escribir(tmp_path / "c.md")
    assert cli.main([str(tmp_path / "*.txt")]) == 0
    assert (tmp_path / "a_clean.txt").exists() and (tmp_path / "b_clean.txt").exists()
    assert not (tmp_path / "c_clean.md").exists()


def test_formato_no_soportado_se_omite(tmp_path, capsys):
    gif = tmp_path / "f.gif"                               # GIF llega en la etapa 2b
    gif.write_bytes(b"GIF89a" + b"\x00" * 30)
    assert cli.main([str(gif)]) == 0
    assert "no soportado" in capsys.readouterr().out


def test_archivo_que_no_existe_es_error(tmp_path):
    assert cli.main([str(tmp_path / "no.txt")]) == 1


def test_varios_trabajos(tmp_path):
    for i in range(8):
        escribir(tmp_path / f"f{i}.txt")
    assert cli.main([str(tmp_path), "-j", "4"]) == 0
    assert all((tmp_path / f"f{i}_clean.txt").read_bytes() == LIMPIO.encode() for i in range(8))


def test_report_y_report_file(tmp_path, capsys):
    escribir(tmp_path / "a.txt")
    escribir(tmp_path / "b.txt", "nada\n")
    informe = tmp_path / "informe.json"
    assert cli.main([str(tmp_path / "a.txt"), str(tmp_path / "b.txt"), "--report", "--report-file", str(informe)]) == 0
    salida = capsys.readouterr().out
    assert "Resumen" in salida and "2" in salida
    datos = json.loads(informe.read_text(encoding="utf-8"))
    estados = [r["estado"] for r in datos["resultados"]]
    assert estados == ["limpio", "sin_cambios"]
    assert datos["resultados"][0]["hallazgos"][0]["clave"].startswith("U+200B")


@pytest.mark.parametrize("args", [
    ["--in-place", "-o", "x", "a.txt"],
    ["-", "a.txt"],
    ["--clipboard", "a.txt"],
    ["-", "--in-place"],
    ["-j", "0", "a.txt"],
])
def test_uso_incorrecto(args):
    with pytest.raises(SystemExit) as e:
        cli.main(args)
    assert e.value.code == 2


def test_sin_argumentos_muestra_ayuda():
    assert cli.main([]) == 2


# --- Marca de la web (Zone.Identifier) ------------------------------------

MARCA = (
    b"[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl={ref}\r\nHostUrl={host}\r\n"
    b"LastWriterPackageFamilyName=Microsoft.MicrosoftEdge.Stable_8wekyb3d8bbwe\r\n"
)


def poner_marca(ruta, host, ref="https://example.com/pagina"):
    with open(f"{ruta}:Zone.Identifier", "wb") as f:
        f.write(MARCA.replace(b"{ref}", ref.encode()).replace(b"{host}", host.encode()))


def leer_marca_cruda(ruta):
    with open(f"{ruta}:Zone.Identifier", "rb") as f:
        return f.read()


ntfs = pytest.mark.skipif(sys.platform != "win32", reason="flujos NTFS")


@ntfs
def test_marca_web_de_ia_quita_solo_esa_url_y_conserva_zoneid(tmp_path):
    a = escribir(tmp_path / "a.txt", "texto normal\n")
    poner_marca(a, "https://files.oaiusercontent.com/file-abc")
    assert cli.main([str(a), "--dry-run"]) == 3
    assert cli.main([str(a)]) == 0
    limpio = tmp_path / "a_clean.txt"
    assert limpio.read_bytes() == a.read_bytes()
    assert leer_marca_cruda(limpio) == (
        b"[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://example.com/pagina\r\n"
        b"LastWriterPackageFamilyName=Microsoft.MicrosoftEdge.Stable_8wekyb3d8bbwe\r\n"
    )
    assert b"oaiusercontent" in leer_marca_cruda(a)       # el original no se toca


@ntfs
def test_marca_web_sin_ia_no_es_hallazgo_y_se_copia_igual(tmp_path):
    a = escribir(tmp_path / "a.txt")                      # tiene invisibles, así que se limpia
    poner_marca(a, "https://example.com/descarga")
    assert cli.main([str(a), "--report-file", str(tmp_path / "i.json")]) == 0
    assert leer_marca_cruda(tmp_path / "a_clean.txt") == leer_marca_cruda(a)
    hallazgos = json.loads((tmp_path / "i.json").read_text(encoding="utf-8"))["resultados"][0]["hallazgos"]
    assert all(h["categoria"] != "windows" for h in hallazgos)


@ntfs
def test_marca_web_sin_url_que_quitar_y_texto_limpio_no_crea_copia(tmp_path):
    a = escribir(tmp_path / "a.txt", "texto normal\n")
    poner_marca(a, "https://example.com/descarga")
    assert cli.main([str(a)]) == 0
    assert not (tmp_path / "a_clean.txt").exists()


@ntfs
def test_marca_web_con_all_quita_las_dos_url_y_conserva_zoneid(tmp_path):
    a = escribir(tmp_path / "a.txt")
    poner_marca(a, "https://example.com/descarga")
    assert cli.main([str(a), "--all", "-o", str(tmp_path / "todo")]) == 0
    marca = leer_marca_cruda(tmp_path / "todo" / "a.txt")
    assert marca == b"[ZoneTransfer]\r\nZoneId=3\r\nLastWriterPackageFamilyName=Microsoft.MicrosoftEdge.Stable_8wekyb3d8bbwe\r\n"
    assert windows.leer_marca_web(tmp_path / "todo" / "a.txt").zona == 3


@ntfs
def test_in_place_reescribe_la_marca_sin_borrarla(tmp_path):
    a = escribir(tmp_path / "a.txt")
    poner_marca(a, "https://chatgpt.com/c/123")
    assert cli.main([str(a), "--in-place"]) == 0
    marca = windows.leer_marca_web(a)
    assert marca is not None and marca.zona == 3
    assert marca.host_url is None and marca.referrer_url == "https://example.com/pagina"


# --- Informes: "motivo" de cada hallazgo y "accion" de cada resultado ------

def informe_de(args, tmp_path):
    ruta = tmp_path / "i.json"
    codigo = cli.main([*args, "--report-file", str(ruta)])
    return codigo, json.loads(ruta.read_text(encoding="utf-8"))["resultados"]


def test_json_accion_prevista_realizada_y_ninguna(tmp_path):
    a = escribir(tmp_path / "a.txt")
    b = escribir(tmp_path / "b.txt", "nada\n")
    _, res = informe_de([str(a), str(b), "--dry-run"], tmp_path)
    assert [r["accion"] for r in res] == ["prevista", "ninguna"]
    _, res = informe_de([str(a), str(b)], tmp_path)
    assert [r["accion"] for r in res] == ["realizada", "ninguna"]
    _, res = informe_de([str(tmp_path / "no-existe.txt")], tmp_path)
    assert res[0]["accion"] == "ninguna"


def test_json_motivo_unicode(tmp_path):
    a = escribir(tmp_path / "a.html", "<p>x\u200b</p><script>s='y\u200b'</script>")
    _, res = informe_de([str(a), "--dry-run"], tmp_path)
    assert [(h["categoria"], h["motivo"]) for h in res[0]["hallazgos"]] == [("invisible", "unicode"), ("no_tratado", "unicode")]


@ntfs
def test_json_motivo_procedencia_y_amplio(tmp_path):
    a = escribir(tmp_path / "a.txt", "texto normal\n")
    poner_marca(a, "https://chatgpt.com/c/1", ref="https://example.com/pagina")
    _, res = informe_de([str(a), "--dry-run"], tmp_path)
    assert [(h["clave"], h["motivo"], h["es_ia"]) for h in res[0]["hallazgos"]] == [
        ("Zone.Identifier: HostUrl", "procedencia", True)]
    _, res = informe_de([str(a), "--dry-run", "--all"], tmp_path)
    assert [(h["clave"], h["motivo"], h["es_ia"]) for h in res[0]["hallazgos"]] == [
        ("Zone.Identifier: HostUrl", "procedencia", True), ("Zone.Identifier: ReferrerUrl", "amplio", False)]


def test_json_motivo_y_accion_en_el_portapapeles(tmp_path, portapapeles_simulado):
    html = windows.escribir_cf_html(windows.HtmlPortapapeles(
        "<html><body><!--StartFragment-->", "<p>hola</p>", "<!--EndFragment--></body></html>", "https://chatgpt.com/c/1"))
    portapapeles_simulado.poner("hola\u200b", html)
    _, res = informe_de(["--clipboard", "--dry-run"], tmp_path)
    assert res[0]["accion"] == "prevista"
    assert [(h["categoria"], h["motivo"]) for h in res[0]["hallazgos"]] == [("invisible", "unicode"), ("procedencia", "procedencia")]
    _, res = informe_de(["--clipboard"], tmp_path)
    assert res[0]["accion"] == "realizada"


def test_pantalla_dry_run_dice_se_quitaria(tmp_path, capsys):
    a = escribir(tmp_path / "a.html", "<p>x\u200b</p><script>s='y\u200b'</script>")
    cli.main([str(a), "--dry-run", "--report"])
    salida = capsys.readouterr().out
    resumen = salida[salida.index("Resumen"):]
    assert "se quitaría" in resumen and "quedaría sin tratar" in resumen
    assert "Tratado" not in resumen and "tratado: sí" not in resumen


def test_pantalla_sin_dry_run_dice_tratado(tmp_path, capsys):
    a = escribir(tmp_path / "a.html", "<p>x\u200b</p><script>s='y\u200b'</script>")
    cli.main([str(a), "--report"])
    resumen = capsys.readouterr().out.split("Resumen", 1)[1]
    assert "Tratado" in resumen and "tratado: sí = eliminado" in resumen
    assert "se quitaría" not in resumen


# --- Entrada estándar (proceso real) --------------------------------------

def ejecutar(args, entrada):
    env = dict(os.environ, PYTHONPATH=str(RAIZ / "src"))
    return subprocess.run([sys.executable, "-m", "aiclean", *args], input=entrada, capture_output=True, env=env)


def test_stdin_a_stdout_byte_a_byte():
    r = ejecutar(["-"], SUCIO.encode("utf-8"))
    assert r.returncode == 0
    assert r.stdout == LIMPIO.encode("utf-8")
    assert "limpio" in r.stderr.decode("utf-8", "replace")


def test_stdin_utf16_con_bom():
    r = ejecutar(["-"], b"\xff\xfe" + SUCIO.encode("utf-16-le"))
    assert r.stdout == b"\xff\xfe" + LIMPIO.encode("utf-16-le")


def test_stdin_normalize():
    r = ejecutar(["-", "--normalize"], SUCIO.encode("utf-8"))
    assert r.returncode == 0
    assert r.stdout == LIMPIO_NORMALIZADO.encode("utf-8")


def test_stdin_dry_run_no_escribe_nada():
    r = ejecutar(["-", "--dry-run"], SUCIO.encode())
    assert r.returncode == 3
    assert r.stdout == b""


def test_stdin_sin_cambios_devuelve_lo_mismo():
    r = ejecutar(["-"], b"nada raro\n")
    assert r.returncode == 0 and r.stdout == b"nada raro\n"


def test_stdin_binario_es_error():
    r = ejecutar(["-"], b"\x00\x01\x02")
    assert r.returncode == 1


def test_stdin_json_accion(tmp_path):
    informe = tmp_path / "i.json"
    ejecutar(["-", "--report-file", str(informe)], SUCIO.encode())
    assert json.loads(informe.read_text(encoding="utf-8"))["resultados"][0]["accion"] == "realizada"
    ejecutar(["-", "--dry-run", "--report-file", str(informe)], SUCIO.encode())
    assert json.loads(informe.read_text(encoding="utf-8"))["resultados"][0]["accion"] == "prevista"
