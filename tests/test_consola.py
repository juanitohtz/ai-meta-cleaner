"""Modo interactivo (consola propia simulada), colores y ayuda.

La detección real de la consola se prueba contra el exe (test_exe.py); aquí se simula con
cli._consola_propia y el teclado se sustituye, así que nada se queda esperando un Enter.
"""

import io
import re
import pytest
from rich.console import Console

from aiclean import __version__, cli, interactivo, motor, salida, windows
from aiclean.modelos import Estado, Resultado
from conftest import RAIZ

ZWSP = chr(0x200B)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
COLOR = re.compile(r"\x1b\[(?:[0-9;]*;)?(?:3[0-7]|9[0-7]|38;)")      # códigos de color (no negrita)


@pytest.fixture
def pausas(monkeypatch):
    llamadas = []
    monkeypatch.setattr("builtins.input", lambda *a: llamadas.append(a) or "")
    return llamadas


@pytest.fixture
def sin_entorno_de_color(monkeypatch):
    """Sin NO_COLOR ni TERM heredados (algunas terminales y herramientas ponen NO_COLOR=1)."""
    for k in ("NO_COLOR", "TERM", "FORCE_COLOR"):
        monkeypatch.delenv(k, raising=False)


def consola_propia(monkeypatch, valor=True):
    monkeypatch.setattr(cli, "_consola_propia", lambda: valor)


def test_bajo_pytest_no_hay_consola_propia():
    assert windows.consola_propia() is False


# --- Modo interactivo (doble clic o archivos arrastrados sobre el exe) ----------------------------

class Teclado:
    """Lo que se «escribe» en la consola, en orden. Si se pide más de lo previsto, la entrada se
    cierra (EOFError) y el test lo detecta con .agotado: nunca se queda esperando."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.preguntas: list[str] = []
        self.agotado = False

    def __call__(self, pregunta=""):
        self.preguntas.append(pregunta)
        if not self.respuestas:
            self.agotado = True
            raise EOFError
        r = self.respuestas.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


@pytest.fixture
def teclado(monkeypatch):
    def preparar(*respuestas):
        consola_propia(monkeypatch)
        t = Teclado(respuestas)
        monkeypatch.setattr("builtins.input", t)
        return t
    return preparar


def sucio(ruta, texto="hola"):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(f"{texto}{ZWSP}\n".encode())
    return ruta


def test_doble_clic_bienvenida_y_enter_vacio_sale(teclado, capsys):
    t = teclado("")
    assert cli.main([]) == 0
    texto = capsys.readouterr().out
    assert f"aiclean {__version__}" in texto and "El original nunca se toca" in texto
    assert texto.count(interactivo.PREGUNTA) == 1 and "Pulsa Enter para salir." not in texto
    assert t.preguntas == ["> "] and not t.agotado


def test_ruta_con_comillas_paso_a_paso_y_vuelve_a_preguntar(tmp_path, teclado, capsys):
    a = sucio(tmp_path / "con espacios y ñ" / "mi nota.txt")
    t = teclado(f'"{a}"', "")
    assert cli.main([]) == 0
    texto = capsys.readouterr().out
    pasos = ["mi nota.txt", "Analizando…", "Encontrado:", "invisible: U+200B ZERO WIDTH SPACE", "carácter oculto",
             "Limpiando…", "Verificando…", "limpio", str(a.with_name("mi nota_clean.txt"))]
    posiciones = [texto.find(p) for p in pasos]
    assert -1 not in posiciones and posiciones == sorted(posiciones), list(zip(pasos, posiciones))
    assert texto.count(interactivo.PREGUNTA) == 2 and "Resumen" not in texto    # un archivo: sin resumen
    assert a.with_name("mi nota_clean.txt").read_bytes() == b"hola\n"
    assert a.read_bytes() == f"hola{ZWSP}\n".encode()
    assert not t.agotado


def test_varias_rutas_sin_comillas_una_inexistente_y_resumen(tmp_path, teclado, capsys):
    a, b = sucio(tmp_path / "a.txt"), sucio(tmp_path / "b.md")
    falta = tmp_path / "no-existe.txt"
    t = teclado(f"{a} {falta} {b}", "")
    assert cli.main([]) == 0
    texto = capsys.readouterr().out
    assert f"No existe: {falta}" in texto
    assert (tmp_path / "a_clean.txt").exists() and (tmp_path / "b_clean.md").exists()
    assert "Resumen" in texto and "2 procesados" in texto and not t.agotado


def test_ruta_inexistente_en_rojo_y_vuelve_a_preguntar(tmp_path, teclado, sin_entorno_de_color, monkeypatch):
    buf = consola_de_prueba(monkeypatch, force_terminal=True, color_system="standard")
    teclado(str(tmp_path / "no-existe.txt"), "")
    assert cli.main([]) == 0
    texto = buf.getvalue()
    assert "\x1b[31mNo existe:" in texto and texto.count(interactivo.PREGUNTA) == 2


def test_carpeta_con_barra_final_entre_comillas_y_sin_subcarpetas_no_pregunta(tmp_path, teclado, capsys):
    carpeta = tmp_path / "mi carpeta"
    sucio(carpeta / "uno.txt")
    t = teclado(f'"{carpeta}\\"', "")
    assert cli.main([]) == 0
    assert (carpeta / "uno_clean.txt").exists()
    assert t.preguntas == ["> ", "> "]                                  # sin pregunta de subcarpetas
    assert "mi carpeta\\uno.txt" in capsys.readouterr().out


@pytest.mark.parametrize("respuesta,incluye", [("s", True), ("Sí", True), ("n", False), ("", False)])
def test_carpeta_con_subcarpetas_pregunta_s_n(tmp_path, teclado, capsys, respuesta, incluye):
    carpeta = tmp_path / "fotos"
    sucio(carpeta / "uno.txt")
    sucio(carpeta / "sub" / "dos.txt")
    t = teclado(str(carpeta), respuesta, "")
    assert cli.main([]) == 0
    assert (carpeta / "uno_clean.txt").exists()
    assert (carpeta / "sub" / "dos_clean.txt").exists() is incluye
    assert t.preguntas[1] == "¿Incluir también las subcarpetas de «fotos»? (s/N) " and not t.agotado


def test_arrastrar_sobre_el_icono_procesa_y_despues_pregunta(tmp_path, teclado, capsys):
    a = sucio(tmp_path / "a.txt")
    carpeta = tmp_path / "carpeta"
    sucio(carpeta / "uno.txt")
    sucio(carpeta / "dentro" / "dos.txt")
    otro = sucio(tmp_path / "otro.txt")
    t = teclado("", str(otro), "")                    # subcarpetas: no; después otra ruta; Enter vacío
    assert cli.main([str(a), str(carpeta)]) == 0
    texto = capsys.readouterr().out
    assert a.read_bytes() == f"hola{ZWSP}\n".encode()                   # nunca --in-place
    assert (tmp_path / "a_clean.txt").read_bytes() == b"hola\n"
    assert (carpeta / "uno_clean.txt").exists() and not (carpeta / "dentro" / "dos_clean.txt").exists()
    assert (tmp_path / "otro_clean.txt").exists()
    assert texto.index("Resumen") < texto.index(interactivo.PREGUNTA)      # primero lo arrastrado
    assert texto.count(interactivo.PREGUNTA) == 2 and not t.agotado


def test_mas_de_20_archivos_una_linea_por_archivo_y_resumen(tmp_path, teclado, capsys):
    carpeta = tmp_path / "muchos"
    for i in range(interactivo.PASO_A_PASO_MAX + 1):
        sucio(carpeta / f"n{i:02}.txt")
    teclado(str(carpeta), "")
    assert cli.main([]) == 0
    texto = capsys.readouterr().out
    assert "Analizando…" not in texto
    assert len(re.findall(r"^limpio ", texto, re.M)) == interactivo.PASO_A_PASO_MAX + 1
    assert "21 procesados" in texto


def test_sin_cambios_y_omitido_con_su_motivo(tmp_path, teclado, capsys):
    limpio = tmp_path / "limpio.txt"
    limpio.write_bytes(b"nada raro\n")
    gif = tmp_path / "anim.gif"
    gif.write_bytes(b"GIF89a" + bytes(20))
    teclado(f"{limpio} {gif}", "")
    assert cli.main([]) == 0
    texto = capsys.readouterr().out
    assert "sin cambios: no se encontró nada en lo inspeccionado" in texto
    assert re.search(r"omitido: formato «\w+» no soportado", texto)
    assert "Limpiando…" not in texto


@pytest.mark.exiftool
def test_imagenes_sin_valores_sensibles_y_aviso_de_synthid_una_vez(tmp_path, teclado, capsys):
    from corpus_imagenes import insertar_png, png_base, text
    secreto = "un gato secreto con sombrero, Steps: 20, Sampler: Euler a"
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    for ruta in (a, b):
        ruta.write_bytes(insertar_png(png_base("RGBA"), antes_de_idat=[text("parameters", secreto)]))
    teclado(f"{a} {b}", "")
    assert cli.main([]) == 0
    texto = capsys.readouterr().out
    assert "secreto" not in texto and "sombrero" not in texto
    assert "procedencia de IA" in texto and f"PNG: texto «parameters» — parámetros de generación ({len(secreto)} caracteres)" in texto
    assert texto.count(interactivo.AVISO_SYNTHID) == 1
    assert (tmp_path / "a_clean.png").exists() and (tmp_path / "b_clean.png").exists()


def test_ctrl_c_en_la_pregunta_o_al_procesar_sale_con_130_sin_pausa(tmp_path, teclado, monkeypatch):
    t = teclado(KeyboardInterrupt())
    assert cli.main([]) == 130 and t.preguntas == ["> "]

    def cancela(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(motor, "procesar_archivo", cancela)
    t = teclado()
    assert cli.main([str(sucio(tmp_path / "a.txt"))]) == 130 and t.preguntas == []


def test_entrada_cerrada_termina_sin_colgarse(teclado):
    t = teclado()
    assert cli.main([]) == 0 and t.agotado


def test_codigo_de_la_sesion_es_el_del_peor_resultado(tmp_path, teclado, monkeypatch):
    def falla(entrada, opciones, progreso=None):
        return Resultado(str(entrada.ruta), "texto", Estado.ERROR, mensaje="sin permiso")

    monkeypatch.setattr(motor, "procesar_archivo", falla)
    teclado(str(sucio(tmp_path / "a.txt")), "")
    assert cli.main([]) == 1


def test_un_fallo_inesperado_se_muestra_y_espera(tmp_path, teclado, capsys, monkeypatch):
    def rompe(*_a, **_k):
        raise RuntimeError("contenido privado del archivo")

    monkeypatch.setattr(motor, "procesar_archivo", rompe)
    t = teclado(str(sucio(tmp_path / "a.txt")), "")
    assert cli.main([]) == 1
    salida_ = capsys.readouterr()
    assert "error interno (RuntimeError" in salida_.err and "privado" not in salida_.out + salida_.err
    assert t.preguntas == ["> ", ""]                                   # la pausa final


@pytest.mark.parametrize("linea,rutas", [
    ('"C:\\Users\\juan\\gato ChatGPT.png"', ["C:\\Users\\juan\\gato ChatGPT.png"]),
    ('C:\\a.txt "D:\\Mis cosas\\b.png"  C:\\c.jpg', ["C:\\a.txt", "D:\\Mis cosas\\b.png", "C:\\c.jpg"]),
    ('"D:\\Mis cosas\\"', ["D:\\Mis cosas\\"]),
    ('"C:\\"', ["C:\\"]),
    ('"C:\\año y ñandú\\x.txt"', ["C:\\año y ñandú\\x.txt"]),
    ("\\\\servidor\\compartida\\x.txt", ["\\\\servidor\\compartida\\x.txt"]),
    ("   ", []),
    ("", []),
])
def test_partir_linea_con_las_reglas_de_windows(linea, rutas):
    assert windows.partir_linea(linea) == rutas


def test_el_motor_anuncia_las_fases(tmp_path):
    fases = []
    progreso = lambda fase, hallazgos: fases.append((fase, len(hallazgos)))
    opciones = motor.Opciones()
    entrada = motor.expandir([str(sucio(tmp_path / "a.txt"))], opciones)[0]
    assert motor.procesar_archivo(entrada, opciones, progreso).estado is Estado.LIMPIO
    assert fases == [(motor.ANALIZANDO, 0), (motor.LIMPIANDO, 1), (motor.VERIFICANDO, 0)]
    fases.clear()
    (tmp_path / "b.txt").write_bytes(b"nada\n")
    entrada = motor.expandir([str(tmp_path / "b.txt")], opciones)[0]
    assert motor.procesar_archivo(entrada, opciones, progreso).estado is Estado.SIN_CAMBIOS
    assert fases == [(motor.ANALIZANDO, 0)]


# --- Consola propia con opciones, y terminal -------------------------------------------------------

def test_con_opciones_y_consola_propia_tambien_espera(tmp_path, monkeypatch, capsys, pausas):
    consola_propia(monkeypatch)
    a = sucio(tmp_path / "a.txt")
    assert cli.main([str(a), "--dry-run"]) == 3
    assert interactivo.PREGUNTA not in capsys.readouterr().out           # con opciones no hay bucle
    assert len(pausas) == 1


@pytest.mark.parametrize("args,codigo", [(["--opcion-que-no-existe"], 2), (["--version"], 0)])
def test_error_de_uso_o_version_con_consola_propia_espera_en_vez_de_cerrarse(monkeypatch, capsys, pausas, args, codigo):
    consola_propia(monkeypatch)
    assert cli.main(args) == codigo
    assert len(pausas) == 1


def test_desde_una_terminal_no_hay_bucle_ni_pausa_ni_cambios(tmp_path, monkeypatch, capsys, pausas):
    consola_propia(monkeypatch, False)
    assert cli.main([]) == 2                                          # como antes: ayuda y código 2
    assert "USO" in capsys.readouterr().err
    a = sucio(tmp_path / "a.txt")
    assert cli.main([str(a)]) == 0
    texto = capsys.readouterr().out
    assert "Resumen" not in texto and "Analizando" not in texto
    with pytest.raises(SystemExit) as e:
        cli.main(["--opcion-que-no-existe"])
    assert e.value.code == 2
    assert pausas == []


# --- Colores --------------------------------------------------------------------

def consola_de_prueba(monkeypatch, **opciones) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(salida, "_consola", Console(file=buf, highlight=False, soft_wrap=True, **opciones))
    return buf


def resultados_variados():
    return [
        Resultado("a.png", "png", Estado.LIMPIO, destino="a_clean.png"),
        Resultado("b.txt", "texto", Estado.SIN_CAMBIOS, mensaje="no se encontró nada en lo inspeccionado"),
        Resultado("c.png", "png", Estado.OMITIDO, mensaje="PNG animado (APNG): no soportado"),
        Resultado("d.html", "html", Estado.PARCIAL, destino="d_clean.html", mensaje="quedan 1 sin tratar"),
        Resultado("e.jpg", "jpeg", Estado.ERROR, mensaje="ExifTool no está disponible"),
    ]


def test_en_una_consola_hay_color_verde_y_rojo_y_poco_mas(monkeypatch, sin_entorno_de_color):
    buf = consola_de_prueba(monkeypatch, force_terminal=True, color_system="standard")
    for r in resultados_variados():
        salida.mostrar_resultado(r, detalle=False)
    from aiclean import informe
    informe.mostrar_resumen(resultados_variados(), dry_run=False)
    texto = buf.getvalue()
    lineas = texto.splitlines()
    assert "\x1b[32mlimpio" in lineas[0] and "→" in lineas[0]
    assert "\x1b[32msin cambios" in lineas[1]
    assert "\x1b[2momitido" in lineas[2]                             # gris
    assert lineas[3].startswith("parcial ")                          # color normal
    assert "\x1b[31merror" in lineas[4]
    usados = set(re.findall(r"\x1b\[([0-9;]+)m", texto))
    assert usados <= {"0", "1", "2", "32", "31", "1;31"}, usados      # sin fondos ni otros colores


@pytest.mark.parametrize("entorno,forzar", [({}, None), ({"NO_COLOR": "1"}, True), ({"TERM": "dumb"}, True)])
def test_sin_color_si_se_redirige_con_no_color_o_term_dumb(monkeypatch, sin_entorno_de_color, entorno, forzar):
    for k, v in entorno.items():
        monkeypatch.setenv(k, v)
    buf = consola_de_prueba(monkeypatch, force_terminal=forzar)
    for r in resultados_variados():
        salida.mostrar_resultado(r, detalle=False)
    texto = buf.getvalue()
    assert not COLOR.search(texto)
    assert "limpio" in texto and "sin cambios" in texto and "error" in texto   # el texto dice lo mismo
    if forzar is None:
        assert "->" in texto and "→" not in texto                    # redirigida: sin caracteres fuera de cp1252


def test_salida_redirigida_real_sin_codigos(tmp_path, capsys):
    a = tmp_path / "a.txt"
    a.write_bytes(f"hola{ZWSP}\n".encode())
    cli.main([str(a), "--report"])
    salida_ = capsys.readouterr()
    assert not ANSI.search(salida_.out + salida_.err)


# --- Ayuda ----------------------------------------------------------------------

def ayuda_plana() -> str:
    buf = io.StringIO()
    salida.mostrar_ayuda(cli.CABECERA, cli.AYUDA, buf)
    return buf.getvalue()


def test_ayuda_plana_ordenada_completa_y_estrecha(capsys):
    assert cli.main(["--help"]) == 0
    texto = capsys.readouterr().out
    assert texto == ayuda_plana()
    assert not ANSI.search(texto)
    titulos = [l for l in texto.splitlines() if l and l == l.upper() and not l.startswith(" ")]
    assert titulos == ["USO", "ENTRADAS", "QUÉ SE HACE", "DÓNDE SE GUARDA", "OTRAS OPCIONES", "EJEMPLOS",
                       "CÓDIGOS DE SALIDA"]
    assert all(len(l) <= salida.ANCHO_AYUDA for l in texto.splitlines())
    opciones = {o for a in cli._crear_parser()._actions for o in a.option_strings}
    for o in opciones:                                                # cada opción, en su propia línea
        assert any(re.match(rf"  (?:\S+, )?{re.escape(o)}\b", l) for l in texto.splitlines()), o
    assert texto.startswith(f"aiclean {__version__} · ")


def test_ayuda_en_consola_con_negrita_y_estrecha(sin_entorno_de_color):
    buf = io.StringIO()
    salida.mostrar_ayuda(cli.CABECERA, cli.AYUDA, buf, terminal=True)
    texto = buf.getvalue()
    assert "\x1b[1mUSO" in texto and "\x1b[1mEJEMPLOS" in texto
    assert all(len(ANSI.sub("", l)) <= salida.ANCHO_AYUDA for l in texto.splitlines())
    assert not COLOR.search(texto.replace("\x1b[2m", ""))            # solo negrita y gris


SOPORTADAS = {"txt", "md", "html", "htm", "xml", "svg", "json", "csv", "png", "jpg", "jpeg", "exe"}
EXTENSION = re.compile(r"[\w*]\.([A-Za-z0-9]{2,5})\b")


def extensiones(lineas) -> set[str]:
    return {e.lower() for l in lineas for e in EXTENSION.findall(l)}


def test_los_ejemplos_de_la_ayuda_solo_usan_formatos_soportados():
    usadas = extensiones(c for c, _ in cli.EJEMPLOS)
    assert usadas and usadas <= SOPORTADAS, usadas - SOPORTADAS


def test_los_ejemplos_del_readme_solo_usan_formatos_soportados():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    bloque = readme.split("### Ejemplos", 1)[1].split("```", 2)[1]
    usadas = extensiones(bloque.splitlines())
    assert usadas and usadas <= SOPORTADAS, usadas - SOPORTADAS
