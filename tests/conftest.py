import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))


def pytest_addoption(parser):
    parser.addoption(
        "--portapapeles-real", action="store_true", default=False,
        help="ejecuta también las pruebas que escriben en el portapapeles REAL de Windows",
    )
    parser.addoption(
        "--exe", default=None, metavar="RUTA",
        help="aiclean.exe empaquetado contra el que se pasan los tests de integración (tests\\test_exe.py)",
    )
    parser.addoption(
        "--requiere-herramientas", action="store_true", default=False,
        help="las pruebas que necesitan ExifTool/c2patool fallan (en vez de saltarse) si no están en tools\\",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "portapapeles: usa el portapapeles real (solo con --portapapeles-real)")
    config.addinivalue_line("markers", "exiftool: necesita ExifTool en tools\\ (build.ps1 -SoloHerramientas)")
    config.addinivalue_line("markers", "c2patool: necesita c2patool y las muestras C2PA en tools\\")
    config.addinivalue_line("markers", "exe: prueba aiclean.exe empaquetado (solo con --exe RUTA; lo pasa build.ps1)")


def _falta(marcador: str) -> str | None:
    from aiclean.herramientas import rutas
    tools = rutas.carpeta_tools()
    if marcador == "exiftool" and not (tools / "exiftool" / "exiftool.exe").is_file():
        return "falta ExifTool en tools\\: ejecuta build.ps1 -SoloHerramientas"
    if marcador == "c2patool" and not ((tools / "c2patool" / "c2patool.exe").is_file()
                                       and (tools / "c2pa-muestras").is_dir()):
        return "faltan c2patool o las muestras C2PA en tools\\: ejecuta build.ps1 -SoloHerramientas"
    return None


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--requiere-herramientas"):      # con la opción, fallan en pytest_runtest_setup
        for item in items:
            for marcador in ("exiftool", "c2patool"):
                motivo = marcador in item.keywords and _falta(marcador)
                if motivo:
                    item.add_marker(pytest.mark.skip(reason=motivo))
    if not config.getoption("--exe"):
        sin_exe = pytest.mark.skip(reason="sin --exe RUTA: los tests del exe los pasa build.ps1 al compilar")
        for item in items:
            if "exe" in item.keywords:
                item.add_marker(sin_exe)
    if config.getoption("--portapapeles-real"):
        return
    saltar = pytest.mark.skip(reason="usa el portapapeles real: actívalo con --portapapeles-real")
    for item in items:
        if "portapapeles" in item.keywords:
            item.add_marker(saltar)


def pytest_runtest_setup(item):
    if item.config.getoption("--requiere-herramientas"):
        for marcador in ("exiftool", "c2patool"):
            motivo = marcador in item.keywords and _falta(marcador)
            if motivo:
                pytest.fail(f"--requiere-herramientas: {motivo}", pytrace=False)


class PortapapelesSimulado:
    """Sustituye al portapapeles de Windows: leer/escribir sobre memoria."""

    def __init__(self):
        self.texto = None
        self.html = None
        self.con_url: list[tuple[str, str]] = []
        self.otros: list[str] = []
        self.escrituras = 0

    def poner(self, texto=None, html=None, con_url=(), otros=()):
        self.texto, self.html = texto, html
        self.con_url, self.otros = list(con_url), list(otros)

    def leer(self):
        from aiclean import windows
        return windows.Portapapeles(self.texto, self.html, list(self.con_url), list(self.otros))

    def escribir(self, texto, html):
        self.texto, self.html, self.con_url, self.otros = texto, html, [], []
        self.escrituras += 1


@pytest.fixture
def portapapeles_simulado(monkeypatch):
    from aiclean import windows
    falso = PortapapelesSimulado()
    monkeypatch.setattr(windows, "leer_portapapeles", falso.leer)
    monkeypatch.setattr(windows, "escribir_portapapeles", falso.escribir)
    return falso
