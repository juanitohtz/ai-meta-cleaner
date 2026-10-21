"""Integración de la CLI contra aiclean.exe empaquetado (pytest --exe RUTA; lo pasa build.ps1).

Cada caso se ejecuta dos veces sobre copias idénticas del corpus sintético: con el exe y con el
código fuente. Tienen que coincidir el código de salida, el informe JSON (sin las rutas) y los
bytes de las salidas. El exe se copia a una carpeta con espacios y «ñ» y se ejecuta sin Python en
el PATH y desde otro directorio de trabajo.
"""

import ctypes
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aiclean import cli, salida
from conftest import RAIZ
from corpus_imagenes import (
    ASCII, DST, NUL, app1_exif, app1_xmp, app11_jumbf, ascii_, cabx, chunk, insertar_jpeg,
    insertar_png, itxt, jpeg_base, paquete_xmp, png_base, segmento, text, tiff,
)

pytestmark = [pytest.mark.exe, pytest.mark.exiftool]

ZWSP = chr(0x200B)
NO_HAY_EXIFTOOL = "ExifTool no está disponible: ejecuta build.ps1 -SoloHerramientas o usa --exiftool RUTA"


def corpus() -> dict[str, bytes]:
    return {
        "texto.txt": f"Respuesta{ZWSP} generada por IA.\n".encode(),
        "pagina.html": f"<p>x{ZWSP}</p><script>s='y{ZWSP}'</script>".encode(),
        "foto.jpg": insertar_jpeg(jpeg_base(), [
            *app11_jumbf(b"m" * 200),
            app1_exif(tiff([(0x010F, ASCII, ascii_("Canon")), (0x0131, ASCII, ascii_("Midjourney v6"))])),
            app1_xmp(paquete_xmp("", f' Iptc4xmpExt:DigitalSourceType="{DST}trainedAlgorithmicMedia"'))]),
        "imagen.png": insertar_png(png_base("RGBA"), antes_de_idat=[
            cabx(b"m" * 50), text("parameters", "a cat\nSteps: 20, Sampler: Euler a"), text("Title", "Gato"),
            itxt("XML:com.adobe.xmp", paquete_xmp("", f' Iptc4xmpExt:DigitalSourceType="{DST}trainedAlgorithmicMedia"'))]),
        "animado.png": insertar_png(png_base(), tras_ihdr=[chunk("acTL", bytes(8))]),
        "hdr.jpg": insertar_jpeg(jpeg_base(), [segmento(0xE2, b"MPF" + NUL + bytes(20)),
                                               segmento(0xE2, b"urn:iso:std:iso:ts:21496:-1" + NUL + b"x")]),
        "desconocido.png": insertar_png(png_base(), antes_de_idat=[chunk("vpAg", bytes(9)), text("Comment", "hola")]),
    }


@pytest.fixture
def exe(request, tmp_path) -> Path:
    """Copia de la distribución en una carpeta con espacios y «ñ»."""
    origen = Path(request.config.getoption("--exe")).resolve()
    destino = tmp_path / "carpeta con espacios y ñ" / "ai-meta-cleaner"
    shutil.copytree(origen.parent, destino)
    return destino / "aiclean.exe"


def entorno_sin_python(extra_path: Path | None = None) -> dict:
    sistema = os.environ.get("SystemRoot", r"C:\Windows")
    env = {k: v for k, v in os.environ.items() if k.upper() in (
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE",
        "HOMEPATH", "COMPUTERNAME", "USERNAME", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS")}
    env["PATH"] = os.pathsep.join([*(str(extra_path),) * (extra_path is not None), rf"{sistema}\System32", sistema])
    return env


def con_exe(exe: Path, args: list[str], cwd: Path, entrada: bytes | None = None, extra_path=None):
    return subprocess.run([str(exe), *args], cwd=cwd, input=entrada, capture_output=True,
                          env=entorno_sin_python(extra_path), timeout=300)


def con_fuente(args: list[str], cwd: Path, entrada: bytes | None = None):
    env = dict(os.environ, PYTHONPATH=str(RAIZ / "src"))
    return subprocess.run([sys.executable, "-m", "aiclean", *args], cwd=cwd, input=entrada,
                          capture_output=True, env=env, timeout=300)


def normalizar(informe: Path, base: Path) -> list:
    """Resultados del JSON sin rutas absolutas ni la fecha."""
    datos = json.loads(informe.read_text(encoding="utf-8"))
    salida = []
    for r in datos["resultados"]:
        relativo = lambda ruta: None if ruta is None else os.path.relpath(ruta, base) if os.path.isabs(ruta) else ruta
        salida.append({**r, "fuente": relativo(r["fuente"]), "destino": relativo(r["destino"])})
    return sorted(salida, key=lambda r: r["fuente"])


def archivos(carpeta: Path) -> dict[str, bytes]:
    return {p.relative_to(carpeta).as_posix(): p.read_bytes() for p in carpeta.rglob("*") if p.is_file()}


def preparar(base: Path) -> Path:
    entrada = base / "entrada ñ"
    entrada.mkdir(parents=True)
    for nombre, datos in corpus().items():
        (entrada / nombre).write_bytes(datos)
    return entrada


@pytest.mark.parametrize("extra", [[], ["--all"], ["--dry-run"]])
def test_exe_y_codigo_dan_lo_mismo(tmp_path, exe, extra):
    resultados = {}
    for quien in ("exe", "fuente"):
        base = tmp_path / quien
        entrada = preparar(base)
        args = [str(entrada), "-o", str(base / "salida"), "--report-file", str(base / "informe.json"), *extra]
        r = con_exe(exe, args, cwd=tmp_path) if quien == "exe" else con_fuente(args, cwd=tmp_path)
        resultados[quien] = (r.returncode, normalizar(base / "informe.json", base),
                             archivos(base / "salida") if (base / "salida").exists() else {})
    assert resultados["exe"] == resultados["fuente"]
    codigo, informe, salidas = resultados["exe"]
    estados = {r["fuente"]: r["estado"] for r in informe}
    assert estados[os.path.join("entrada ñ", "animado.png")] == "omitido"
    assert estados[os.path.join("entrada ñ", "hdr.jpg")] == "omitido"
    assert estados[os.path.join("entrada ñ", "pagina.html")] == "parcial"
    if extra == ["--all"]:
        assert estados[os.path.join("entrada ñ", "desconocido.png")] == "parcial"
    if extra != ["--dry-run"]:
        assert estados[os.path.join("entrada ñ", "foto.jpg")] == "limpio" and b"Midjourney" not in salidas["foto.jpg"]
    assert codigo == 4


def test_exe_entrada_estandar(tmp_path, exe):
    datos = f"hola{ZWSP} mundo\n".encode()
    a = con_exe(exe, ["-"], cwd=tmp_path, entrada=datos)
    b = con_fuente(["-"], cwd=tmp_path, entrada=datos)
    assert (a.returncode, a.stdout) == (b.returncode, b.stdout) == (0, b"hola mundo\n")


def test_exe_version(tmp_path, exe):
    from aiclean import __version__
    import re
    r = con_exe(exe, ["--version"], cwd=tmp_path)
    salida = r.stdout.decode().strip()
    assert r.returncode == 0 and salida == f"aiclean {__version__}" and re.fullmatch(r"aiclean \d+\.\d+", salida)


def test_exe_sin_exiftool_da_error_claro_y_no_usa_el_path(tmp_path, exe):
    shutil.rmtree(exe.parent / "tools" / "exiftool")
    falso = tmp_path / "en el path"
    falso.mkdir()
    for carpeta in (falso, tmp_path):                      # un exiftool.exe en el PATH y en el directorio actual
        (carpeta / "exiftool.exe").write_bytes(b"MZ no es un ejecutable")
    entrada = preparar(tmp_path / "datos")
    informe = tmp_path / "informe.json"
    r = con_exe(exe, [str(entrada / "foto.jpg"), str(entrada / "texto.txt"), "-o", str(tmp_path / "salida"),
                      "--report-file", str(informe)], cwd=tmp_path, extra_path=falso)
    assert r.returncode == 1
    res = {Path(x["fuente"]).name: x for x in json.loads(informe.read_text(encoding="utf-8"))["resultados"]}
    assert res["foto.jpg"]["estado"] == "error" and res["foto.jpg"]["mensaje"] == NO_HAY_EXIFTOOL
    assert res["texto.txt"]["estado"] == "limpio"


def test_la_distribucion_no_lleva_lo_que_no_debe(exe):
    carpeta = exe.parent
    nombres = [p.relative_to(carpeta).as_posix().lower() for p in carpeta.rglob("*")]
    prohibidos = ("c2patool", "c2pa-muestras", "muestras", "tests/", "pytest", "/pil/", "pillow", "corpus_imagenes")
    assert not [n for n in nombres if any(x in n for x in prohibidos)]
    for necesario in ("aiclean.exe", "tools/exiftool/exiftool.exe", "tools/exiftool/exiftool_files/license",
                      "license", "third-party-notices.txt", "build-info.txt", "readme.md"):
        assert necesario in nombres, necesario
    assert not (carpeta / "tools" / "exiftool" / ".instalado").exists()


# --- Modo interactivo, salida redirigida e icono -------------------------------------------------
# Para el doble clic y el arrastre, el exe se abre en una consola nueva (oculta) sin redirigir nada,
# como desde el Explorador; consola_windows.py lee esa pantalla, escribe rutas y pulsa Enter.

AYUDANTE = Path(__file__).with_name("consola_windows.py")


def en_consola_nueva(exe: Path, args: list[str], cwd: Path) -> subprocess.Popen:
    oculta = subprocess.STARTUPINFO()
    oculta.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    oculta.wShowWindow = 0                                 # SW_HIDE
    return subprocess.Popen([str(exe), *args], cwd=cwd, env=entorno_sin_python(),
                            creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=oculta)


PREGUNTA = "Enter vacío para salir."


def consola(pid: int, accion: str, *texto: str) -> str:
    r = subprocess.run([sys.executable, str(AYUDANTE), str(pid), accion, *texto], capture_output=True, timeout=30,
                       creationflags=subprocess.DETACHED_PROCESS)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    return r.stdout.decode("utf-8")


def esperar_pantalla(proceso: subprocess.Popen, texto: str, veces: int = 1, plazo: float = 120) -> str:
    fin = time.monotonic() + plazo
    while True:
        assert proceso.poll() is None, f"el exe terminó sin esperar (código {proceso.returncode})"
        pantalla = consola(proceso.pid, "leer")
        if pantalla.count(texto) >= veces:
            return pantalla
        assert time.monotonic() < fin, pantalla
        time.sleep(0.3)


def pulsar_enter_y_esperar(proceso: subprocess.Popen) -> int:
    time.sleep(1)
    assert proceso.poll() is None                         # sigue esperando a Enter
    consola(proceso.pid, "enter")
    return proceso.wait(timeout=30)


def test_exe_doble_clic_muestra_bienvenida_y_sale_con_enter_vacio(tmp_path, exe):
    from aiclean import __version__
    proceso = en_consola_nueva(exe, [], tmp_path)
    try:
        pantalla = esperar_pantalla(proceso, PREGUNTA)
        assert f"aiclean {__version__}" in pantalla and "El original nunca se toca" in pantalla
        assert pulsar_enter_y_esperar(proceso) == 0
    finally:
        if proceso.poll() is None:
            proceso.kill()


def test_exe_bucle_interactivo_con_rutas_escritas(tmp_path, exe):
    entrada = preparar(tmp_path / "datos con espacios")
    falta = "no-existe.txt"              # relativa al directorio de trabajo y corta: la consola parte las líneas largas
    proceso = en_consola_nueva(exe, [], tmp_path)
    try:
        esperar_pantalla(proceso, PREGUNTA)
        consola(proceso.pid, "escribir", f'"{entrada / "texto.txt"}" {falta} "{entrada / "foto.jpg"}"')
        pantalla = esperar_pantalla(proceso, PREGUNTA, veces=2)
        for paso in ("texto.txt", "Analizando…", "Encontrado:", "Limpiando…", "Verificando…", "limpio",
                     "foto.jpg", "Resumen", "2 procesados", f"No existe: {falta}", "SynthID"):
            assert paso in pantalla, paso
        assert pantalla.count("SynthID") == 1
        assert pulsar_enter_y_esperar(proceso) == 0
    finally:
        if proceso.poll() is None:
            proceso.kill()
    assert (entrada / "texto_clean.txt").exists() and b"Midjourney" not in (entrada / "foto_clean.jpg").read_bytes()


def test_exe_arrastrar_archivos_y_carpetas(tmp_path, exe):
    entrada = preparar(tmp_path / "datos")
    carpeta = entrada / "carpeta"
    (carpeta / "sub").mkdir(parents=True)
    sucio = f"x{ZWSP}\n".encode()
    for archivo in (carpeta / "uno.txt", carpeta / "sub" / "dos.txt"):
        archivo.write_bytes(sucio)
    originales = archivos(entrada)
    proceso = en_consola_nueva(exe, [str(entrada / "texto.txt"), str(entrada / "foto.jpg"), str(carpeta)], exe.parent)
    try:
        esperar_pantalla(proceso, "¿Incluir también las subcarpetas de «carpeta»? (s/N)")
        consola(proceso.pid, "enter")                     # Enter = no
        pantalla = esperar_pantalla(proceso, PREGUNTA)
        assert "Resumen" in pantalla and "4 procesados" not in pantalla and "3 procesados" in pantalla
        assert pulsar_enter_y_esperar(proceso) == 0
    finally:
        if proceso.poll() is None:
            proceso.kill()
    ahora = archivos(entrada)
    assert {k: v for k, v in ahora.items() if k in originales} == originales          # nada sobrescrito
    assert sorted(set(ahora) - set(originales)) == ["carpeta/uno_clean.txt", "foto_clean.jpg", "texto_clean.txt"]
    assert ahora["carpeta/uno_clean.txt"] == b"x\n" and b"Midjourney" not in ahora["foto_clean.jpg"]


def test_exe_redirigido_no_espera_ni_lleva_colores(tmp_path, exe):
    entrada = preparar(tmp_path / "datos")
    r = con_exe(exe, [str(entrada), "-o", str(tmp_path / "salida"), "--report"], cwd=tmp_path)
    assert r.returncode == 4 and b"Resumen" in r.stdout
    assert b"\x1b" not in r.stdout + r.stderr
    sin_nada = con_exe(exe, [], cwd=tmp_path)             # sin argumentos y sin consola propia: como antes
    assert sin_nada.returncode == 2 and b"USO" in sin_nada.stderr and b"Enter" not in sin_nada.stdout
    ayuda = con_exe(exe, ["--help"], cwd=tmp_path)
    assert ayuda.returncode == 0 and b"\x1b" not in ayuda.stdout
    # Redirigida, la salida va en la página de códigos ANSI del sistema (la del exe no lleva PYTHONUTF8)
    ansi = f"cp{ctypes.windll.kernel32.GetACP()}"
    assert ayuda.stdout.decode(ansi).replace("\r\n", "\n") == salida.texto_ayuda(cli.CABECERA, cli.AYUDA)


def test_exe_lleva_el_icono(exe):
    ico = (RAIZ / "assets" / "icono.ico").read_bytes()
    reservado, tipo, n = struct.unpack_from("<HHH", ico, 0)
    assert (reservado, tipo, n) == (0, 1, 9)
    binario = exe.read_bytes()
    for i in range(n):
        ancho, _alto, _colores, _r, _planos, _bits, tam, desp = struct.unpack_from("<BBBBHHII", ico, 6 + 16 * i)
        assert ico[desp:desp + tam] in binario, f"falta la imagen de {ancho or 256} px"
