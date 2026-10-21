"""Motor: enlaces, --in-place con cambios concurrentes, fallos, idempotencia, PARCIAL."""

import os
import subprocess
import sys

import pytest

from aiclean import cli, motor, verificar, windows
from aiclean.formatos import texto
from aiclean.modelos import TEXTO_SIN_CAMBIOS, Estado

ZWSP, NBSP = chr(0x200B), chr(0x00A0)
SUCIO = f"hola{ZWSP} mundo{NBSP}IA\n".encode()
LIMPIO = f"hola mundo{NBSP}IA\n".encode()

windows_solo = pytest.mark.skipif(sys.platform != "win32", reason="enlaces de Windows")


def escribir(ruta, datos=SUCIO):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    return ruta


def junction(enlace, destino):
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(enlace), str(destino)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("no se pudo crear la junction")


def symlink(enlace, destino, carpeta=False):
    try:
        os.symlink(destino, enlace, target_is_directory=carpeta)
    except OSError:
        pytest.skip("crear enlaces simbólicos necesita privilegios o modo desarrollador")


def resultados(args):
    """Ejecuta la CLI y devuelve (código, resultados) leyendo el informe JSON."""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        informe = os.path.join(d, "i.json")
        codigo = cli.main([*args, "--report-file", informe])
        with open(informe, encoding="utf-8") as f:
            return codigo, json.load(f)["resultados"]


def sobrantes(carpeta):
    """Temporales y _clean que hayan quedado."""
    return sorted(p.name for p in carpeta.rglob("*") if ".aiclean-" in p.name or "_clean" in p.name)


# --- Enlaces ---------------------------------------------------------------

@windows_solo
def test_in_place_rechaza_enlace_simbolico_y_no_toca_el_destino(tmp_path):
    real = escribir(tmp_path / "real.txt")
    enlace = tmp_path / "enlace.txt"
    symlink(enlace, real)
    codigo, res = resultados([str(enlace), "--in-place"])
    assert res[0]["estado"] == "omitido"
    assert res[0]["mensaje"] == "es un enlace simbólico; --in-place no lo sigue"
    assert real.read_bytes() == SUCIO and enlace.is_symlink()


@windows_solo
def test_sin_in_place_se_lee_a_traves_del_enlace_indicado(tmp_path):
    real = escribir(tmp_path / "real.txt")
    enlace = tmp_path / "enlace.txt"
    symlink(enlace, real)
    assert cli.main([str(enlace)]) == 0
    salida = tmp_path / "enlace_clean.txt"
    assert salida.read_bytes() == LIMPIO and not salida.is_symlink()
    assert real.read_bytes() == SUCIO


def test_in_place_rechaza_varios_enlaces_duros(tmp_path):
    a = escribir(tmp_path / "a.txt")
    os.link(a, tmp_path / "b.txt")
    codigo, res = resultados([str(a), "--in-place"])
    assert res[0]["estado"] == "omitido"
    assert res[0]["mensaje"] == "tiene 2 enlaces duros; --in-place rompería el enlace"
    assert a.read_bytes() == SUCIO
    assert cli.main([str(a)]) == 0                     # sin --in-place sí: la salida es nueva
    assert (tmp_path / "a_clean.txt").read_bytes() == LIMPIO


@windows_solo
def test_in_place_rechaza_ruta_que_pasa_por_una_junction(tmp_path):
    real = tmp_path / "real"
    a = escribir(real / "a.txt")
    junction(tmp_path / "j", real)
    codigo, res = resultados([str(tmp_path / "j" / "a.txt"), "--in-place"])
    assert res[0]["estado"] == "omitido"
    assert "la ruta pasa por una junction" in res[0]["mensaje"] and "--in-place no la sigue" in res[0]["mensaje"]
    assert a.read_bytes() == SUCIO


@windows_solo
def test_in_place_rechaza_ruta_que_pasa_por_enlace_simbolico_de_carpeta(tmp_path):
    real = tmp_path / "real"
    a = escribir(real / "a.txt")
    symlink(tmp_path / "s", real, carpeta=True)
    codigo, res = resultados([str(tmp_path / "s" / "a.txt"), "--in-place"])
    assert res[0]["estado"] == "omitido"
    assert "enlace simbólico de carpeta" in res[0]["mensaje"]
    assert a.read_bytes() == SUCIO


@windows_solo
def test_recursivo_no_entra_en_junctions_ni_hace_bucles(tmp_path):
    raiz = tmp_path / "raiz"
    escribir(raiz / "sub" / "a.txt")
    junction(raiz / "sub" / "bucle", raiz)                 # apunta hacia arriba: sería un bucle
    junction(raiz / "atajo", raiz / "sub")
    codigo, res = resultados([str(raiz), "-r", "--dry-run"])
    por_fuente = {os.path.relpath(r["fuente"], raiz): r for r in res}
    assert por_fuente[os.path.join("sub", "a.txt")]["estado"] == "encontrado"
    assert por_fuente["atajo"]["estado"] == "omitido"
    assert por_fuente[os.path.join("sub", "bucle")]["mensaje"].startswith("es una junction")
    assert len(res) == 3                                   # a.txt una sola vez


@windows_solo
def test_recursivo_no_sigue_archivos_enlazados(tmp_path):
    escribir(tmp_path / "fuera" / "x.txt")
    carpeta = tmp_path / "carpeta"
    carpeta.mkdir()
    symlink(carpeta / "x.txt", tmp_path / "fuera" / "x.txt")
    codigo, res = resultados([str(carpeta), "-r"])
    assert res[0]["estado"] == "omitido" and "enlace simbólico" in res[0]["mensaje"]
    assert not (tmp_path / "fuera" / "x_clean.txt").exists() and not (carpeta / "x_clean.txt").exists()


def test_lnk_es_un_archivo_normal_y_no_se_resuelve(tmp_path):
    destino = escribir(tmp_path / "destino.txt")
    lnk = tmp_path / "acceso.lnk"
    # Cabecera de un acceso directo de Windows (ShellLinkHeader) + ruta: binario, no texto
    lnk.write_bytes(b"\x4c\x00\x00\x00\x01\x14\x02\x00" + b"\x00" * 68 + str(destino).encode("utf-16-le"))
    codigo, res = resultados([str(lnk), "--in-place"])
    assert res[0]["estado"] == "omitido" and res[0]["mensaje"] == "formato no reconocido"
    assert destino.read_bytes() == SUCIO


# --- --in-place y cambios concurrentes ------------------------------------

def _tras_verificar(monkeypatch, accion):
    """Ejecuta accion(ruta_original) justo después de verificar el temporal."""
    real = verificar.POR_TIPO["texto"]

    def envoltura(preparado, limpio, tipo, modo, normalizar=False):
        real(preparado, limpio, tipo, modo, normalizar)
        accion()

    monkeypatch.setitem(verificar.POR_TIPO, "texto", envoltura)


def test_in_place_detecta_cambio_de_contenido(tmp_path, monkeypatch):
    a = escribir(tmp_path / "a.txt")
    _tras_verificar(monkeypatch, lambda: a.write_bytes(SUCIO + b"otro proceso\n"))
    codigo, res = resultados([str(a), "--in-place"])
    assert codigo == 1 and res[0]["estado"] == "error"
    assert "cambió mientras se procesaba" in res[0]["mensaje"]
    assert a.read_bytes() == SUCIO + b"otro proceso\n"         # no se ha pisado
    assert sobrantes(tmp_path) == []


def test_in_place_detecta_mismo_tamano_y_fecha_con_otro_contenido(tmp_path, monkeypatch):
    a = escribir(tmp_path / "a.txt")
    st = os.stat(a)

    def cambiar():
        a.write_bytes(SUCIO.replace(b"hola", b"HOLA"))           # mismo tamaño
        os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns))         # misma fecha

    _tras_verificar(monkeypatch, cambiar)
    codigo, res = resultados([str(a), "--in-place"])
    assert res[0]["estado"] == "error"
    assert res[0]["mensaje"] == "el contenido del original cambió mientras se procesaba; no se ha tocado"
    assert a.read_bytes().startswith(b"HOLA")


def test_in_place_detecta_otro_archivo_con_el_mismo_nombre(tmp_path, monkeypatch):
    a = escribir(tmp_path / "a.txt")
    st = os.stat(a)

    def sustituir():
        nuevo = escribir(tmp_path / "nuevo.txt")                 # mismo contenido y fecha, otro archivo
        os.utime(nuevo, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(nuevo, a)

    _tras_verificar(monkeypatch, sustituir)
    codigo, res = resultados([str(a), "--in-place"])
    assert res[0]["estado"] == "error"
    assert res[0]["mensaje"] == "el original cambió mientras se procesaba; no se ha tocado"


def test_fsync_del_temporal_antes_de_publicar(tmp_path, monkeypatch):
    a = escribir(tmp_path / "a.txt")
    orden = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (orden.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda s, d: (orden.append("replace"), real_replace(s, d))[1])
    assert cli.main([str(a), "--in-place"]) == 0
    assert orden == ["fsync", "replace"]
    assert a.read_bytes() == LIMPIO


# --- Fallos: no queda ni el temporal ni el _clean; el original intacto ------

def _escribir_que_falla(monkeypatch, excepcion):
    real = texto.preparar

    def preparar(ruta, tipo, modo, normalizar=False):
        p = real(ruta, tipo, modo, normalizar)

        def escribir_mal(destino):
            destino.write_bytes(b"a medias")
            raise excepcion

        p.escribir = escribir_mal
        return p

    monkeypatch.setattr(texto, "preparar", preparar)


@pytest.mark.parametrize("modo", [[], ["--in-place"], ["-o", "SALIDA"]])
def test_error_de_escritura(tmp_path, monkeypatch, modo):
    a = escribir(tmp_path / "a.txt")
    _escribir_que_falla(monkeypatch, OSError(28, "No queda espacio en el disco"))
    args = [str(a), *[str(tmp_path / "out") if m == "SALIDA" else m for m in modo]]
    codigo, res = resultados(args)
    assert codigo == 1 and res[0]["estado"] == "error"
    assert a.read_bytes() == SUCIO
    assert sobrantes(tmp_path) == []
    salida = tmp_path / "out"
    assert not salida.exists() or list(salida.iterdir()) == []


@pytest.mark.parametrize("modo", [[], ["--in-place"]])
def test_fallo_de_verificacion(tmp_path, monkeypatch, modo):
    a = escribir(tmp_path / "a.txt")

    def falla(*_):
        raise verificar.FalloVerificacion("el texto visible cambió (línea 1)")

    monkeypatch.setitem(verificar.POR_TIPO, "texto", falla)
    codigo, res = resultados([str(a), *modo])
    assert res[0]["estado"] == "error" and res[0]["verificacion"] == "fallida"
    assert a.read_bytes() == SUCIO and sobrantes(tmp_path) == []


@pytest.mark.parametrize("modo", [[], ["--in-place"]])
def test_ctrl_c_a_mitad_de_escribir(tmp_path, monkeypatch, modo):
    a = escribir(tmp_path / "a.txt")
    _escribir_que_falla(monkeypatch, KeyboardInterrupt())
    assert cli.main([str(a), *modo]) == 130
    assert a.read_bytes() == SUCIO and sobrantes(tmp_path) == []


# --- Idempotencia ----------------------------------------------------------

@pytest.mark.parametrize("nombre,contenido", [
    ("a.txt", SUCIO),
    ("a.html", f"<!DOCTYPE html><p>ho{ZWSP}la&#8203;{NBSP}</p>".encode()),
])
def test_limpiar_lo_limpio_no_encuentra_nada(tmp_path, nombre, contenido):
    a = escribir(tmp_path / nombre, contenido)
    assert cli.main([str(a)]) == 0
    limpio = next(p for p in tmp_path.iterdir() if "_clean" in p.name)
    codigo, res = resultados([str(limpio)])
    assert codigo == 0 and res[0]["estado"] == "sin_cambios"
    assert res[0]["mensaje"] == TEXTO_SIN_CAMBIOS


def test_portapapeles_simulado_idempotente(portapapeles_simulado):
    portapapeles_simulado.poner(f"Hola{ZWSP} mundo{NBSP}")
    assert motor.procesar_portapapeles(motor.Opciones()).estado is Estado.LIMPIO
    assert portapapeles_simulado.texto == f"Hola mundo{NBSP}"
    res = motor.procesar_portapapeles(motor.Opciones())
    assert res.estado is Estado.SIN_CAMBIOS and res.mensaje == TEXTO_SIN_CAMBIOS
    assert portapapeles_simulado.escrituras == 1


def test_portapapeles_simulado_con_html_y_normalize(portapapeles_simulado):
    html = windows.escribir_cf_html(windows.HtmlPortapapeles(
        "<html><body><!--StartFragment-->", f"<p>a{ZWSP}{NBSP}b</p>", "<!--EndFragment--></body></html>", None))
    portapapeles_simulado.poner(f"a{ZWSP}{NBSP}b", html)
    res = motor.procesar_portapapeles(motor.Opciones(normalizar=True))
    assert res.estado is Estado.LIMPIO
    assert "--normalize no se aplica al HTML" in res.mensaje
    assert portapapeles_simulado.texto == "a b"                              # texto plano: normalizado
    assert windows.leer_cf_html(portapapeles_simulado.html).fragmento == f"<p>a{NBSP}b</p>"   # HTML: conservado


# --- --normalize en lotes mixtos y PARCIAL --------------------------------

def test_normalize_en_lote_mixto(tmp_path):
    escribir(tmp_path / "t.txt", f"a{ZWSP}{NBSP}b\n".encode())
    escribir(tmp_path / "p.html", f"<p>a{ZWSP}{NBSP}b</p>".encode())
    codigo, res = resultados([str(tmp_path), "--normalize"])
    assert codigo == 0
    assert (tmp_path / "t_clean.txt").read_bytes() == b"a b\n"
    assert (tmp_path / "p_clean.html").read_bytes() == f"<p>a{NBSP}b</p>".encode()
    html = next(r for r in res if r["fuente"].endswith("p.html"))
    assert html["estado"] == "limpio" and "--normalize no se aplica a HTML/XML/SVG" in html["mensaje"]


def test_html_con_invisibles_en_script_queda_parcial(tmp_path):
    a = escribir(tmp_path / "a.html", f"<p>x{ZWSP}</p><script>var s='y{ZWSP}';</script>".encode())
    codigo, res = resultados([str(a)])
    assert codigo == 4 and res[0]["estado"] == "parcial"
    assert (tmp_path / "a_clean.html").read_bytes() == f"<p>x</p><script>var s='y{ZWSP}';</script>".encode()
    pendiente = [h for h in res[0]["hallazgos"] if not h["tratado"]]
    assert pendiente[0]["cantidad"] == 1 and pendiente[0]["ubicacion"] == "<script>"
    assert "valor" not in pendiente[0]


@pytest.mark.parametrize("atributos", [
    f'alt="foto{ZWSP}" title="t{ZWSP}"',
    'alt="foto&#8203;" title="t&#x200B;"',                  # también como referencias
])
def test_invisible_en_atributos_queda_no_tratado_y_parcial(tmp_path, atributos):
    original = f'<!DOCTYPE html><p>texto</p><img src="a.png" {atributos}>'.encode()
    a = escribir(tmp_path / "a.html", original)
    codigo, res = resultados([str(a)])
    assert codigo == 4 and res[0]["estado"] == "parcial"
    pendientes = [h for h in res[0]["hallazgos"] if not h["tratado"]]
    assert len(pendientes) == 1 and pendientes[0]["categoria"] == "no_tratado"
    assert pendientes[0]["ubicacion"] == "etiquetas <…>" and pendientes[0]["cantidad"] == 2
    assert "valor" not in pendientes[0]
    assert not (tmp_path / "a_clean.html").exists() and a.read_bytes() == original


def test_invisible_en_atributo_y_en_texto_limpia_el_texto_y_queda_parcial(tmp_path):
    a = escribir(tmp_path / "a.html", f'<p title="t{ZWSP}">x{ZWSP}</p>'.encode())
    codigo, res = resultados([str(a)])
    assert codigo == 4 and res[0]["estado"] == "parcial"
    assert (tmp_path / "a_clean.html").read_bytes() == f'<p title="t{ZWSP}">x</p>'.encode()


def test_html_con_invisibles_solo_en_script_queda_parcial_sin_crear_copia(tmp_path):
    a = escribir(tmp_path / "a.html", f"<p>x</p><script>var s='y{ZWSP}';</script>".encode())
    codigo, res = resultados([str(a)])
    assert codigo == 4 and res[0]["estado"] == "parcial"
    assert not (tmp_path / "a_clean.html").exists()


def test_sin_cambios_dice_lo_inspeccionado(tmp_path):
    a = escribir(tmp_path / "a.txt", b"nada\n")
    codigo, res = resultados([str(a)])
    assert res[0]["estado"] == "sin_cambios" and res[0]["mensaje"] == TEXTO_SIN_CAMBIOS


# --- --dry-run: tratables + no tratados = PARCIAL (código 4) -----------------

HTML_MIXTO = f"<!DOCTYPE html><p>x{ZWSP}</p><script>var s='y{ZWSP}{ZWSP}';</script>"


def _por_tratado(hallazgos):
    tratados = [(h["ubicacion"], h["clave"], h["cantidad"]) for h in hallazgos if h["tratado"]]
    pendientes = [(h["ubicacion"], h["categoria"], h["cantidad"]) for h in hallazgos if not h["tratado"]]
    return tratados, pendientes


def test_dry_run_archivo_con_tratables_y_no_tratados_es_parcial(tmp_path):
    a = escribir(tmp_path / "a.html", HTML_MIXTO.encode())
    codigo, res = resultados([str(a), "--dry-run"])
    assert codigo == 4 and res[0]["estado"] == "parcial"
    assert _por_tratado(res[0]["hallazgos"]) == (
        [("", "U+200B ZERO WIDTH SPACE", 1)],
        [("<script>", "no_tratado", 2)],
    )
    assert res[0]["mensaje"] == "se quitarían 1; quedarían 2 sin tratar en zonas que no se tocan"
    assert a.read_bytes() == HTML_MIXTO.encode() and sobrantes(tmp_path) == []


def test_dry_run_stdin_con_tratables_y_no_tratados_es_parcial(tmp_path):
    from conftest import RAIZ
    informe = tmp_path / "i.json"
    env = dict(os.environ, PYTHONPATH=str(RAIZ / "src"))
    r = subprocess.run(
        [sys.executable, "-m", "aiclean", "-", "--dry-run", "--report-file", str(informe)],
        input=HTML_MIXTO.encode(), capture_output=True, env=env,
    )
    assert r.returncode == 4 and r.stdout == b""
    import json
    res = json.loads(informe.read_text(encoding="utf-8"))["resultados"][0]
    assert res["estado"] == "parcial"
    assert _por_tratado(res["hallazgos"]) == (
        [("", "U+200B ZERO WIDTH SPACE", 1)],
        [("<script>", "no_tratado", 2)],
    )
    assert res["mensaje"] == "se quitarían 1; quedarían 2 sin tratar en zonas que no se tocan"
    assert "se quitarían 1; quedarían 2 sin tratar" in r.stderr.decode("utf-8", "replace")


def test_dry_run_portapapeles_con_tratables_y_no_tratados_es_parcial(portapapeles_simulado):
    html = windows.escribir_cf_html(windows.HtmlPortapapeles(
        "<html><body><!--StartFragment-->", f"<p>x{ZWSP}</p><script>s='y{ZWSP}{ZWSP}'</script>",
        "<!--EndFragment--></body></html>", None))
    portapapeles_simulado.poner(f"x{ZWSP}", html)
    codigo, res = resultados(["--clipboard", "--dry-run"])
    assert codigo == 4 and res[0]["estado"] == "parcial"
    assert _por_tratado(res[0]["hallazgos"]) == (
        [("texto", "U+200B ZERO WIDTH SPACE", 1), ("HTML", "U+200B ZERO WIDTH SPACE", 1)],
        [("HTML: <script>", "no_tratado", 2)],
    )
    assert res[0]["mensaje"] == "se quitarían 2; quedarían 2 sin tratar en zonas que no se tocan"
    assert portapapeles_simulado.escrituras == 0


# --- --dry-run con --in-place: las mismas exclusiones que la operación real --

def test_dry_run_in_place_omite_varios_enlaces_duros(tmp_path):
    a = escribir(tmp_path / "a.txt")
    os.link(a, tmp_path / "b.txt")
    codigo, res = resultados([str(a), "--in-place", "--dry-run"])
    assert codigo == 0 and res[0]["estado"] == "omitido"
    assert res[0]["mensaje"] == "tiene 2 enlaces duros; --in-place rompería el enlace"
    assert a.read_bytes() == SUCIO


@windows_solo
def test_dry_run_in_place_omite_ruta_por_junction(tmp_path):
    real = tmp_path / "real"
    a = escribir(real / "a.txt")
    junction(tmp_path / "j", real)
    codigo, res = resultados([str(tmp_path / "j" / "a.txt"), "--in-place", "--dry-run"])
    assert codigo == 0 and res[0]["estado"] == "omitido"
    assert res[0]["mensaje"] == f"la ruta pasa por una junction (o punto de montaje) ({tmp_path / 'j'}); --in-place no la sigue"
    assert a.read_bytes() == SUCIO


@windows_solo
def test_dry_run_in_place_omite_enlace_simbolico(tmp_path):
    real = escribir(tmp_path / "real.txt")
    enlace = tmp_path / "enlace.txt"
    symlink(enlace, real)
    codigo, res = resultados([str(enlace), "--in-place", "--dry-run"])
    assert codigo == 0 and res[0]["estado"] == "omitido"
    assert res[0]["mensaje"] == "es un enlace simbólico; --in-place no lo sigue"


# --- Zone.Identifier: se relee en el temporal; flujos extra con --in-place ---

ZONA_IA = b"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=https://chatgpt.com/c/1\r\n"
NO_ADMITE = "el destino no admite la marca de la web; no se publica una copia sin ella"


def _poner_flujo(ruta, nombre, datos):
    with open(f"{ruta}:{nombre}", "wb") as f:
        f.write(datos)


def _leer_flujo(ruta, nombre):
    with open(f"{ruta}:{nombre}", "rb") as f:
        return f.read()


@windows_solo
@pytest.mark.parametrize("modo", [[], ["--in-place"], ["-o", "SALIDA"]])
def test_marca_web_que_no_se_puede_escribir_da_error_sin_salida(tmp_path, monkeypatch, modo):
    a = escribir(tmp_path / "a.txt")
    _poner_flujo(a, "Zone.Identifier", ZONA_IA)

    def no_ntfs(contenido, destino):
        raise OSError(22, "Función incorrecta", None, 1)    # ERROR_INVALID_FUNCTION, como en FAT/exFAT

    monkeypatch.setattr(windows, "escribir_marca_web", no_ntfs)
    args = [str(a), *[str(tmp_path / "out") if m == "SALIDA" else m for m in modo]]
    codigo, res = resultados(args)
    assert codigo == 1 and res[0]["estado"] == "error" and res[0]["mensaje"] == NO_ADMITE
    assert a.read_bytes() == SUCIO and _leer_flujo(a, "Zone.Identifier") == ZONA_IA
    assert sobrantes(tmp_path) == []
    salida = tmp_path / "out"
    assert not salida.exists() or list(salida.iterdir()) == []


@windows_solo
def test_marca_web_que_no_queda_igual_da_error_sin_salida(tmp_path, monkeypatch):
    a = escribir(tmp_path / "a.txt")
    _poner_flujo(a, "Zone.Identifier", ZONA_IA)
    real = windows.escribir_marca_web
    monkeypatch.setattr(windows, "escribir_marca_web", lambda contenido, destino: real(contenido[:10], destino))
    codigo, res = resultados([str(a)])
    assert codigo == 1 and res[0]["estado"] == "error" and res[0]["mensaje"] == NO_ADMITE
    assert sobrantes(tmp_path) == []


@windows_solo
@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_in_place_con_flujos_ntfs_adicionales_se_omite(tmp_path, extra):
    a = escribir(tmp_path / "a.txt")
    _poner_flujo(a, "Zone.Identifier", ZONA_IA)
    _poner_flujo(a, "resumen", b"datos de otro programa")
    codigo, res = resultados([str(a), "--in-place", *extra])
    assert codigo == 0 and res[0]["estado"] == "omitido"
    assert res[0]["mensaje"] == "tiene flujos NTFS adicionales; --in-place los perdería"
    assert a.read_bytes() == SUCIO
    assert _leer_flujo(a, "resumen") == b"datos de otro programa"
    assert _leer_flujo(a, "Zone.Identifier") == ZONA_IA


@windows_solo
def test_sin_in_place_un_flujo_adicional_no_impide_la_copia(tmp_path):
    a = escribir(tmp_path / "a.txt")
    _poner_flujo(a, "resumen", b"datos de otro programa")
    assert cli.main([str(a)]) == 0
    assert (tmp_path / "a_clean.txt").read_bytes() == LIMPIO
    assert _leer_flujo(a, "resumen") == b"datos de otro programa"
