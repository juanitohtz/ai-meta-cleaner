"""Medición de rendimiento de aiclean (texto o imágenes). No es un test: pytest no lo recoge.

    .venv\\Scripts\\python tests\\rendimiento\\medir.py [--archivos 300] [--repeticiones 5]
    .venv\\Scripts\\python tests\\rendimiento\\medir.py --imagenes [--archivos 60]

Con --imagenes el corpus son JPEG y PNG con C2PA, XMP, EXIF y texto de IA (necesita Pillow, de
requirements-dev.txt, y ExifTool en tools\\). Se compara el programa completo con ExifTool directo
con la misma selección de cambios (un proceso -stay_open, sin verificación), y se dan los
tiempos por fase dentro del proceso: lectura, análisis, cambios propios, ExifTool y verificación.

Genera un corpus con semilla fija (en una carpeta temporal que se borra al acabar, salvo
--conservar), hace una ejecución de calentamiento y N repeticiones de cada modo, y da
mínimo, mediana y máximo de tiempo total, archivos/s y MB/s.

Cada repetición parte de las mismas condiciones: -o escribe en una carpeta de salida nueva
y --in-place trabaja sobre una copia recién hecha del corpus (la copia no se cronometra).
Se comprueba con el informe JSON que todos los archivos salieron "limpio": si alguno se
omite, falla o no tenía nada, la medición se descarta en vez de dar un número engañoso.

No mide memoria (pospuesto) ni fija objetivos de velocidad.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "src"))

from aiclean import __version__  # noqa: E402

INVISIBLES = [0x200B, 0x200C, 0x200D, 0xFEFF, 0x2062, 0xFE0F, 0x202E]
ESPECIALES = [0x00A0, 0x202F, 0x00AD, 0x2060]
PALABRAS = ("el la de que y en un una por con para los las del se texto modelo respuesta "
            "datos ejemplo línea archivo resumen generado puede también cómo café año").split()


# ---------------------------------------------------------------------------
# Corpus

def _parrafo(rnd: random.Random, palabras: int, densidad: float) -> str:
    partes = []
    for _ in range(palabras):
        p = rnd.choice(PALABRAS)
        if rnd.random() < densidad:
            p += chr(rnd.choice(INVISIBLES))
        if rnd.random() < densidad / 2:
            p += chr(rnd.choice(ESPECIALES))
        partes.append(p)
    if rnd.random() < 0.05:
        partes.append("".join(chr(0xE0000 + ord(c)) for c in "oculto"))
    if rnd.random() < 0.1:
        partes.append("\U0001F468\u200d\U0001F469\u200d\U0001F467 ❤\ufe0f")
    return " ".join(partes)


def generar_corpus(carpeta: Path, archivos: int, semilla: int) -> dict:
    rnd = random.Random(semilla)
    composicion = {"txt": 0, "md": 0, "html": 0}
    total = 0
    for i in range(archivos):
        tipo = rnd.choices(["txt", "md", "html"], weights=[5, 3, 2])[0]
        # Tamaños: la mayoría pequeños, algunos medianos y pocos grandes
        tamano = rnd.choices([2_000, 20_000, 200_000, 1_000_000], weights=[60, 25, 12, 3])[0]
        lineas = []
        largo = 0
        while largo < tamano:
            linea = _parrafo(rnd, rnd.randint(8, 40), densidad=rnd.choice([0.01, 0.05, 0.2]))
            lineas.append(linea)
            largo += len(linea.encode()) + 1
        lineas[0] += "\u200b"                      # todos tienen algo que quitar
        if tipo == "html":
            cuerpo = "\n".join(f"<p>{l}</p>" for l in lineas)
            contenido = f"<!DOCTYPE html>\n<html><body>\n{cuerpo}\n</body></html>\n"
        else:
            contenido = "\n".join(lineas) + "\n"
        sub = carpeta / f"grupo{i % 10}"
        sub.mkdir(parents=True, exist_ok=True)
        datos = contenido.encode("utf-8")
        (sub / f"f{i:04d}.{tipo}").write_bytes(datos)
        composicion[tipo] += 1
        total += len(datos)
    return {"archivos": archivos, "bytes": total, "por_tipo": composicion, "semilla": semilla}


# ---------------------------------------------------------------------------
# Equipo

def _cpu() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        return platform.processor() or "desconocida"


def _ram_gb() -> float | None:
    if sys.platform != "win32":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return round(m.ullTotalPhys / 2**30, 1)


def equipo() -> dict:
    return {
        "equipo": platform.node(), "cpu": _cpu(), "nucleos_logicos": os.cpu_count(), "ram_gb": _ram_gb(),
        "sistema": platform.platform(), "python": platform.python_version(), "aiclean": __version__,
    }


# ---------------------------------------------------------------------------
# Medición

def _ejecutar(args: list[str], informe: Path, esperados: int) -> float:
    env = dict(os.environ, PYTHONPATH=str(RAIZ / "src"))
    inicio = time.perf_counter()
    r = subprocess.run([sys.executable, "-m", "aiclean", *args, "--report-file", str(informe)],
                       capture_output=True, env=env)
    segundos = time.perf_counter() - inicio
    resultados = json.loads(informe.read_text(encoding="utf-8"))["resultados"]
    limpios = sum(1 for x in resultados if x["estado"] == "limpio")
    if r.returncode != 0 or len(resultados) != esperados or limpios != esperados:
        estados = {}
        for x in resultados:
            estados[x["estado"]] = estados.get(x["estado"], 0) + 1
        raise SystemExit(f"medición inválida: código {r.returncode}, estados {estados} (se esperaban {esperados} limpios)")
    return segundos


def medir_salida_nueva(corpus: Path, trabajo: Path, n: int, reps: int, extra: list[str]) -> list[float]:
    tiempos = []
    for k in range(reps + 1):                       # la primera es el calentamiento
        salida = trabajo / f"salida{k}"
        t = _ejecutar([str(corpus), "-r", "-o", str(salida), *extra], trabajo / f"i{k}.json", n)
        shutil.rmtree(salida)
        if k:
            tiempos.append(t)
    return tiempos


def medir_in_place(corpus: Path, trabajo: Path, n: int, reps: int, extra: list[str]) -> list[float]:
    tiempos = []
    for k in range(reps + 1):
        copia = trabajo / f"copia{k}"
        shutil.copytree(corpus, copia)              # fuera del cronómetro
        t = _ejecutar([str(copia), "-r", "--in-place", *extra], trabajo / f"j{k}.json", n)
        shutil.rmtree(copia)
        if k:
            tiempos.append(t)
    return tiempos


def medir_hash(corpus: Path, reps: int) -> list[float]:
    """Coste aislado de la comprobación de contenido de --in-place: leer y hacer SHA-256 de todo."""
    tiempos = []
    archivos = sorted(p for p in corpus.rglob("*") if p.is_file())
    for k in range(reps + 1):
        inicio = time.perf_counter()
        for p in archivos:
            hashlib.sha256(p.read_bytes()).hexdigest()
        if k:
            tiempos.append(time.perf_counter() - inicio)
    return tiempos


# ---------------------------------------------------------------------------
# Imágenes

def generar_corpus_imagenes(carpeta: Path, archivos: int, semilla: int) -> dict:
    import io
    sys.path.insert(0, str(RAIZ / "tests"))
    from PIL import Image
    import corpus_imagenes as c

    rnd = random.Random(semilla)
    composicion = {"jpg": 0, "png": 0}
    total = 0
    carpeta.mkdir(parents=True, exist_ok=True)
    xmp_ia = c.paquete_xmp("", f'\n   Iptc4xmpExt:DigitalSourceType="{c.DST}trainedAlgorithmicMedia"\n   xmp:CreatorTool="Midjourney"')
    exif_ia = c.tiff([(0x0131, c.ASCII, c.ascii_("Midjourney v6")), (0x0112, c.SHORT, c.short(1))])
    for i in range(archivos):
        ancho, alto = rnd.choice([(256, 256), (1024, 768), (2048, 1536)])
        im = Image.effect_noise((ancho, alto), 40).convert("RGB")
        b = io.BytesIO()
        manifiesto = bytes(rnd.getrandbits(8) for _ in range(rnd.choice([4_000, 20_000, 60_000])))
        if i % 2 == 0:
            im.save(b, "JPEG", quality=90)
            datos = c.insertar_jpeg(b.getvalue(), [c.app1_exif(exif_ia), c.app1_xmp(xmp_ia), *c.app11_jumbf(manifiesto, trozos=2)])
            nombre, tipo = f"f{i:04d}.jpg", "jpg"
        else:
            im.save(b, "PNG")
            datos = c.insertar_png(b.getvalue(), antes_de_idat=[
                c.cabx(manifiesto), c.text("parameters", "a cat\nSteps: 20, Sampler: Euler a"),
                c.itxt("XML:com.adobe.xmp", xmp_ia), c.chunk("eXIf", exif_ia)])
            nombre, tipo = f"f{i:04d}.png", "png"
        (carpeta / nombre).write_bytes(datos)
        composicion[tipo] += 1
        total += len(datos)
    return {"archivos": archivos, "bytes": total, "por_tipo": composicion, "semilla": semilla}


def medir_exiftool_directo(corpus: Path, trabajo: Path, reps: int) -> list[float]:
    """ExifTool solo, con la misma selección de cambios, sobre copias (sin verificar)."""
    from aiclean.herramientas import exiftool
    args_jpg = ["-JUMBF:all=", "-XMP-iptcExt:DigitalSourceType=", "-XMP-xmp:CreatorTool=", "-IFD0:Software="]
    args_png = [*args_jpg, "-PNG:Parameters="]
    archivos = sorted(corpus.iterdir())
    tiempos = []
    for k in range(reps + 1):
        salida = trabajo / f"exiftool{k}"
        salida.mkdir()
        inicio = time.perf_counter()
        for p in archivos:
            exiftool.escribir(p, salida / p.name, args_jpg if p.suffix == ".jpg" else args_png)
        if k:
            tiempos.append(time.perf_counter() - inicio)
        shutil.rmtree(salida)
    exiftool.cerrar_todos()
    return tiempos


def medir_fases(corpus: Path, trabajo: Path) -> dict[str, float]:
    """Una pasada dentro del proceso, sumando el tiempo de cada fase."""
    from aiclean import deteccion
    from aiclean.formatos import imagen
    from aiclean.herramientas import exiftool
    from aiclean.modelos import Modo

    fases = dict.fromkeys(["lectura", "análisis", "cambios propios", "ExifTool", "verificación"], 0.0)
    for p in sorted(corpus.iterdir()):
        t0 = time.perf_counter()
        datos = p.read_bytes()
        t1 = time.perf_counter()
        tipo = deteccion.detectar(p)
        plan = imagen.planificar(datos, tipo, Modo.IA)
        t2 = time.perf_counter()
        nuevo = imagen.aplicar(plan)
        t3 = time.perf_counter()
        if plan.exiftool:
            entrada, salida = trabajo / f"in.{p.suffix[1:]}", trabajo / f"out.{p.suffix[1:]}"
            entrada.write_bytes(nuevo)
            exiftool.escribir(entrada, salida, plan.exiftool)
            nuevo = salida.read_bytes()
            entrada.unlink()
            salida.unlink()
        t4 = time.perf_counter()
        imagen.verificar(plan, nuevo)
        t5 = time.perf_counter()
        for nombre, d in zip(fases, (t1 - t0, t2 - t1, t3 - t2, t4 - t3, t5 - t4)):
            fases[nombre] += d
    exiftool.cerrar_todos()
    return fases


def main_imagenes(args) -> int:
    trabajo = Path(tempfile.mkdtemp(prefix="aiclean-rendimiento-img-"))
    try:
        corpus = args.conservar or trabajo / "corpus"
        if corpus.exists() and any(corpus.iterdir()):
            raise SystemExit(f"{corpus} no está vacía: el corpus tiene que generarse desde cero")
        n = args.archivos
        info = generar_corpus_imagenes(corpus, n, args.semilla)
        mb = info["bytes"] / 1e6
        from aiclean.herramientas import exiftool, rutas
        print(json.dumps({"entorno": {**equipo(), "exiftool": str(rutas.exiftool())}, "corpus": info},
                         ensure_ascii=False, indent=2))
        print(f"\n{args.repeticiones} repeticiones tras 1 de calentamiento: mínimo, mediana y máximo\n")
        print(_fila("imágenes: -o (completo)", medir_salida_nueva(corpus, trabajo, n, args.repeticiones, []), n, mb))
        print(_fila("ExifTool directo", medir_exiftool_directo(corpus, trabajo, args.repeticiones), n, mb))
        print(f"\nPor fase (una pasada dentro del proceso; ExifTool {exiftool.version_usada()}):")
        fases = medir_fases(corpus, trabajo)
        total = sum(fases.values())
        for nombre, s in fases.items():
            print(f"  {nombre:<16} {s:7.3f} s  {100 * s / total:5.1f} %  ({1000 * s / n:6.1f} ms/archivo)")
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)
    return 0


def _fila(nombre: str, tiempos: list[float], n: int, mb: float) -> str:
    lo, me, hi = min(tiempos), statistics.median(tiempos), max(tiempos)
    return (f"{nombre:<26} {lo:7.3f} {me:7.3f} {hi:7.3f} s | "
            f"{n / hi:7.1f} {n / me:7.1f} {n / lo:7.1f} arch/s | "
            f"{mb / hi:6.2f} {mb / me:6.2f} {mb / lo:6.2f} MB/s")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--archivos", type=int, default=None, help="por defecto 300 (texto) o 60 (imágenes)")
    p.add_argument("--repeticiones", type=int, default=5)
    p.add_argument("--semilla", type=int, default=20261020)
    p.add_argument("--conservar", type=Path, help="deja el corpus en esta carpeta (fuera de git)")
    p.add_argument("--imagenes", action="store_true", help="corpus de JPEG y PNG en vez de texto")
    args = p.parse_args()
    if args.imagenes:
        args.archivos = args.archivos or 60
        return main_imagenes(args)
    args.archivos = args.archivos or 300

    trabajo = Path(tempfile.mkdtemp(prefix="aiclean-rendimiento-"))
    try:
        corpus = (args.conservar or trabajo / "corpus")
        if corpus.exists() and any(corpus.iterdir()):
            raise SystemExit(f"{corpus} no está vacía: el corpus tiene que generarse desde cero")
        info = generar_corpus(corpus, args.archivos, args.semilla)
        mb = info["bytes"] / 1e6
        print(json.dumps({"entorno": equipo(), "corpus": info}, ensure_ascii=False, indent=2))
        print(f"\n{args.repeticiones} repeticiones tras 1 de calentamiento: mínimo, mediana y máximo")
        print(f"(archivos/s y MB/s en el orden peor, mediana, mejor)\n")
        n = args.archivos
        print(_fila("-o carpeta nueva", medir_salida_nueva(corpus, trabajo, n, args.repeticiones, []), n, mb))
        print(_fila("-o con --normalize", medir_salida_nueva(corpus, trabajo, n, args.repeticiones, ["--normalize"]), n, mb))
        print(_fila("--in-place", medir_in_place(corpus, trabajo, n, args.repeticiones, []), n, mb))
        print(_fila("solo SHA-256 del corpus", medir_hash(corpus, args.repeticiones), n, mb))
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
