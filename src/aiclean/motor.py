"""Flujo: detectar → escanear → limpiar (en un temporal) → verificar → guardar.

Nada se sobrescribe salvo con --in-place, y entonces solo con un reemplazo atómico
del temporal ya verificado. Si algo falla, el original queda como estaba y solo se
borra el temporal de esta operación.

--in-place y cambios concurrentes: antes de reemplazar se comprueba que el original sigue
siendo el mismo archivo, con el mismo tamaño y fecha, y que su contenido (SHA-256) es el que
se analizó. Eso detecta modificaciones que ya se hayan producido, pero no cierra la ventana
entre esa última comprobación y el reemplazo: no hay bloqueo exclusivo.
"""

from __future__ import annotations

import glob
import os
import re
import secrets
import sys
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from aiclean import deteccion, firmas, verificar, windows
from aiclean.formatos import imagen, texto
from aiclean.herramientas import exiftool, rutas
from aiclean.modelos import (
    AMPLIO, PREVISTA, PROCEDENCIA, REALIZADA, TEXTO_SIN_CAMBIOS, Estado, Hallazgo, Modo, Omitido, Resultado,
)

# tipo -> limpiador (las siguientes etapas añaden audio/vídeo y documentos)
FORMATOS = {tipo: texto for tipo in deteccion.TIPOS_TEXTO}
FORMATOS.update({tipo: imagen for tipo in imagen.TIPOS})

_CARPETAS_PROHIBIDAS = {".git", ".hg", ".svn"}

# Fases que procesar_archivo anuncia a quien lo pida (el modo interactivo las muestra)
ANALIZANDO, LIMPIANDO, VERIFICANDO = "analizando", "limpiando", "verificando"
Progreso = Callable[[str, list[Hallazgo]], None]      # (fase, hallazgos: solo en LIMPIANDO)
_SALIDA_PREVIA = re.compile(r"_clean(_\d+)?$")
_COMODINES = re.compile(r"[*?\[]")


@dataclass(frozen=True)
class Opciones:
    modo: Modo = Modo.IA
    dry_run: bool = False
    in_place: bool = False
    carpeta_salida: Path | None = None
    recursivo: bool = False
    trabajos: int = 1
    normalizar: bool = False


@dataclass(frozen=True)
class Entrada:
    ruta: Path
    base: Path                  # para reproducir el árbol con -o
    error: str = ""
    omitir: str = ""            # motivo para no procesarla (enlace encontrado al recorrer...)


class CambioConcurrente(Exception):
    """El original cambió mientras se procesaba (--in-place)."""


class MarcaNoAdmitida(Exception):
    """La marca de la web no se pudo escribir en el temporal, o no quedó igual."""


NO_ADMITE_MARCA = "el destino no admite la marca de la web; no se publica una copia sin ella"
FLUJOS_ADICIONALES = "tiene flujos NTFS adicionales; --in-place los perdería"


# ---------------------------------------------------------------------------
# Rutas

def hay_comodines(texto_ruta: str) -> bool:
    return bool(_COMODINES.search(texto_ruta))


def expandir(rutas: list[str], opciones: Opciones) -> list[Entrada]:
    """Archivos a procesar, en orden y sin repetir. PowerShell no expande comodines: se hace aquí."""
    entradas: list[Entrada] = []
    vistos: set[Path] = set()
    salida = Path(os.path.abspath(opciones.carpeta_salida)) if opciones.carpeta_salida else None

    def agregar(ruta: Path, base: Path, omitir: str = "") -> None:
        clave = Path(os.path.abspath(ruta)) if omitir else ruta.resolve()
        if clave not in vistos:
            vistos.add(clave)
            entradas.append(Entrada(ruta, base, omitir=omitir))

    for texto_ruta in rutas:
        ruta = Path(texto_ruta)
        if not ruta.exists() and _COMODINES.search(texto_ruta):
            coincidencias = sorted(glob.glob(texto_ruta, recursive=opciones.recursivo))
            base_fija = _base_de_comodin(texto_ruta)
            archivos = [Path(c) for c in coincidencias if Path(c).is_file()]
            if not archivos:
                entradas.append(Entrada(ruta, ruta.parent, "no hay archivos que coincidan"))
            for a in archivos:
                agregar(a, a.parent, _enlace_bajo(a, base_fija))
        elif ruta.is_dir():
            for a, omitir in _recorrer(ruta, opciones.recursivo, salida):
                agregar(a, ruta, omitir)
        elif ruta.is_file():
            agregar(ruta, ruta.parent)       # indicado expresamente: se puede leer aunque sea un enlace
        else:
            entradas.append(Entrada(ruta, ruta.parent, "no existe"))
    return entradas


_NO_SE_SIGUE = "enlace simbólico encontrado al recorrer; solo se leen enlaces indicados expresamente"


def _recorrer(carpeta: Path, recursivo: bool, salida: Path | None):
    """(ruta, motivo para omitirla) de cada archivo. No entra en junctions ni enlaces de carpeta."""
    for raiz, dirs, archivos in os.walk(carpeta, followlinks=False):
        visitar = []
        for d in sorted(dirs):
            sub = Path(raiz) / d
            if d in _CARPETAS_PROHIBIDAS or (salida and Path(os.path.abspath(sub)) == salida):
                continue
            tipo = _tipo_enlace(sub)
            if tipo:
                if recursivo:
                    yield sub, f"es {tipo}; -r no entra en ella"
                continue
            visitar.append(d)
        dirs[:] = visitar
        for nombre in sorted(archivos):
            ruta = Path(raiz) / nombre
            if _SALIDA_PREVIA.search(ruta.stem) or _es_temporal(nombre):
                continue
            yield ruta, (_NO_SE_SIGUE if ruta.is_symlink() else "")
        if not recursivo:
            break


def _tipo_enlace(ruta: Path) -> str | None:
    """"una junction" / "un enlace simbólico de carpeta" / None. No resuelve nada."""
    if os.path.isjunction(ruta):
        return "una junction (o punto de montaje)"
    if ruta.is_symlink():
        return "un enlace simbólico de carpeta" if ruta.is_dir() else "un enlace simbólico"
    return None


def _base_de_comodin(patron: str) -> Path:
    partes = Path(patron).parts
    fijas = []
    for p in partes:
        if _COMODINES.search(p):
            break
        fijas.append(p)
    return Path(os.path.abspath(Path(*fijas) if fijas else Path(".")))


def _enlace_bajo(ruta: Path, base: Path) -> str:
    """Motivo si, por debajo de la parte fija del comodín, hay algún enlace (no indicado expresamente)."""
    absoluta = Path(os.path.abspath(ruta))
    if absoluta.is_symlink():
        return _NO_SE_SIGUE
    for padre in absoluta.parents:
        if padre == base or base not in padre.parents:
            break
        if _tipo_enlace(padre):
            return f"la ruta pasa por {_tipo_enlace(padre)}; los comodines no los siguen"
    return ""


def motivo_enlace_in_place(ruta: Path) -> str:
    """Por qué --in-place no puede tocar esta ruta ("" si puede). Mira la ruta tal cual se indicó,
    sin resolverla, para que los enlaces no queden ocultos."""
    if ruta.is_symlink():
        return "es un enlace simbólico; --in-place no lo sigue"
    absoluta = Path(os.path.abspath(ruta))           # abspath no resuelve enlaces
    for padre in absoluta.parents:
        tipo = _tipo_enlace(padre)
        if tipo:
            return f"la ruta pasa por {tipo} ({padre}); --in-place no la sigue"
    enlaces = os.stat(ruta, follow_symlinks=False).st_nlink
    if enlaces > 1:
        return f"tiene {enlaces} enlaces duros; --in-place rompería el enlace"
    return ""


def motivo_in_place(ruta: Path) -> str:
    """Por qué --in-place (también con --dry-run) no puede tocar esta ruta ("" si puede)."""
    motivo = motivo_enlace_in_place(ruta)
    if not motivo and sys.platform == "win32" and windows.flujos_adicionales(ruta):
        motivo = FLUJOS_ADICIONALES
    return motivo


_TEMPORAL_IMAGEN = re.compile(r"\.aiclean-[0-9a-f]{8}\.(in|out)\.[a-z0-9]+$")


def _es_temporal(nombre: str) -> bool:
    """El temporal (.<nombre>.aiclean-XXXX.tmp) y las copias para ExifTool (...aiclean-XXXX.in.jpg)."""
    return nombre.startswith(".") and ".aiclean-" in nombre and (
        nombre.endswith(".tmp") or bool(_TEMPORAL_IMAGEN.search(nombre)))


def _reservar_destino(entrada: Entrada, opciones: Opciones) -> Path:
    """Crea vacío el archivo de salida con un nombre libre (así dos hilos no eligen el mismo)."""
    ruta = entrada.ruta
    if opciones.carpeta_salida:
        try:
            relativa = ruta.relative_to(entrada.base)
        except ValueError:
            relativa = Path(ruta.name)
        candidato = opciones.carpeta_salida / relativa
        candidato.parent.mkdir(parents=True, exist_ok=True)
        stem, sufijo_extra = candidato.stem, ""
    else:
        candidato = ruta.with_name(f"{ruta.stem}_clean{ruta.suffix}")
        stem, sufijo_extra = ruta.stem, "_clean"
    n = 1
    while True:
        try:
            with open(candidato, "xb"):
                return candidato
        except FileExistsError:
            n += 1
            candidato = candidato.with_name(f"{stem}{sufijo_extra}_{n}{ruta.suffix}")


def _temporal_junto_a(destino: Path) -> Path:
    return destino.with_name(f".{destino.name}.aiclean-{secrets.token_hex(4)}.tmp")


def _copiar_bytes(origen: Path, destino: Path) -> None:
    """Copia solo el contenido (sin flujos NTFS como Zone.Identifier)."""
    with open(origen, "rb") as fo, open(destino, "wb") as fd:
        for bloque in iter(lambda: fo.read(1 << 20), b""):
            fd.write(bloque)


# ---------------------------------------------------------------------------
# Archivos

def procesar(entradas: list[Entrada], opciones: Opciones) -> list[Resultado]:
    try:
        if opciones.trabajos <= 1 or len(entradas) <= 1:
            return [procesar_archivo(e, opciones) for e in entradas]
        with ThreadPoolExecutor(max_workers=opciones.trabajos) as pool:
            return list(pool.map(lambda e: procesar_archivo(e, opciones), entradas))
    finally:
        exiftool.cerrar_todos()                          # un proceso por hilo: ninguno queda abierto


def procesar_archivo(entrada: Entrada, opciones: Opciones, progreso: Progreso | None = None) -> Resultado:
    ruta = entrada.ruta
    res = Resultado(fuente=str(ruta))
    if entrada.error:
        return replace(res, estado=Estado.ERROR, mensaje=entrada.error)
    if entrada.omitir:
        return replace(res, estado=Estado.OMITIDO, mensaje=entrada.omitir)
    try:
        return _procesar(ruta, entrada, opciones, res, progreso or _sin_progreso)
    except PermissionError as e:
        return replace(res, estado=Estado.ERROR, mensaje=f"sin permiso ({e.strerror or 'acceso denegado'})")
    except verificar.FalloVerificacion as e:
        return replace(res, estado=Estado.ERROR, mensaje=str(e), verificacion="fallida")
    except Omitido as e:
        return replace(res, estado=Estado.OMITIDO, mensaje=str(e))
    except (texto.ErrorTexto, CambioConcurrente, MarcaNoAdmitida, rutas.HerramientaNoDisponible,
            exiftool.ErrorExifTool) as e:
        return replace(res, estado=Estado.ERROR, mensaje=str(e))
    except OSError as e:
        return replace(res, estado=Estado.ERROR, mensaje=e.strerror or type(e).__name__)
    except Exception as e:                               # noqa: BLE001 - un fallo inesperado no para el lote
        return replace(res, estado=Estado.ERROR, mensaje=error_interno(e))


def error_interno(e: BaseException) -> str:
    """Describe un fallo inesperado sin su mensaje (podría llevar contenido del archivo)."""
    marco = traceback.extract_tb(e.__traceback__)[-1] if e.__traceback__ else None
    donde = f" en {Path(marco.filename).name}:{marco.lineno}" if marco else ""
    return f"error interno ({type(e).__name__}{donde})"


def _sin_progreso(fase: str, hallazgos: list[Hallazgo]) -> None:
    pass


def _procesar(ruta: Path, entrada: Entrada, opciones: Opciones, res: Resultado, progreso: Progreso) -> Resultado:
    if opciones.in_place:                                # también en --dry-run: mismo resultado que la operación real
        motivo = motivo_in_place(ruta)
        if motivo:
            return replace(res, estado=Estado.OMITIDO, mensaje=motivo)
    progreso(ANALIZANDO, [])
    if windows.solo_en_la_nube(ruta):
        return replace(res, estado=Estado.OMITIDO, mensaje="está solo en OneDrive (no se descarga)")
    tipo = deteccion.detectar(ruta)
    res = replace(res, tipo=tipo)
    formato = FORMATOS.get(tipo)
    if formato is None:
        motivo = ("formato no reconocido" if tipo == "desconocido"
                  else f"formato «{tipo}» no soportado (aiclean trata texto, JPEG y PNG)")
        return replace(res, estado=Estado.OMITIDO, mensaje=motivo)

    identidad = _identidad(ruta)                         # antes de la primera lectura
    preparado = formato.preparar(ruta, tipo, opciones.modo, normalizar=opciones.normalizar)
    marca = windows.leer_marca_web(ruta)
    h_marca, marca_nueva = _marca_web(marca, opciones.modo)
    hallazgos = preparado.hallazgos + h_marca
    res = replace(res, hallazgos=hallazgos, mensaje="; ".join(preparado.avisos))
    tratables = [h for h in hallazgos if h.tratado]
    parcial = len(tratables) != len(hallazgos)
    if not hallazgos:
        return replace(res, estado=Estado.SIN_CAMBIOS, mensaje=res.mensaje or TEXTO_SIN_CAMBIOS)
    if not tratables:
        return replace(res, estado=Estado.PARCIAL, mensaje=_mensaje_parcial(res.mensaje, hallazgos))
    if opciones.dry_run:
        return _previsto(res, hallazgos)

    progreso(LIMPIANDO, hallazgos)
    destino = ruta if opciones.in_place else _reservar_destino(entrada, opciones)
    temporal = _temporal_junto_a(destino)
    try:
        if tratables == h_marca:
            _escribir_copia(preparado, ruta, temporal)     # solo cambia la marca de la web
            progreso(VERIFICANDO, [])
            verificar.verificar_copia(preparado, temporal)
        else:
            preparado.escribir(temporal)
            progreso(VERIFICANDO, [])
            verificar.POR_TIPO[tipo](preparado, temporal, tipo, opciones.modo, opciones.normalizar)
        if marca:
            # La marca nunca se borra entera: se conserva o se reescribe sin las URL
            _escribir_marca(marca_nueva if marca_nueva is not None else marca.crudo, temporal)
        _fsync(temporal)
        if opciones.in_place:
            _comprobar_sin_cambios(ruta, identidad, preparado.huella)
        os.replace(temporal, destino)
    except BaseException:
        temporal.unlink(missing_ok=True)
        if destino != ruta:
            destino.unlink(missing_ok=True)
        raise
    estado = Estado.PARCIAL if parcial else Estado.LIMPIO
    mensaje = _mensaje_parcial(res.mensaje, hallazgos) if parcial else res.mensaje
    return replace(
        res, estado=estado, destino=str(destino), mensaje=mensaje, verificacion="superada", accion=REALIZADA,
    )


def _mensaje_parcial(mensaje: str, hallazgos: list[Hallazgo]) -> str:
    sin_tratar = sum(h.cantidad for h in hallazgos if not h.tratado)
    texto_ = f"quedan {sin_tratar} sin tratar en zonas que no se tocan"
    return f"{mensaje}; {texto_}" if mensaje else texto_


def _previsto(res: Resultado, hallazgos: list[Hallazgo]) -> Resultado:
    """--dry-run con algo que quitar: ENCONTRADO, o PARCIAL si además queda algo sin tratar."""
    sin_tratar = sum(h.cantidad for h in hallazgos if not h.tratado)
    if not sin_tratar:
        return replace(res, estado=Estado.ENCONTRADO, accion=PREVISTA)
    quitar = sum(h.cantidad for h in hallazgos if h.tratado)
    texto_ = f"se quitarían {quitar}; quedarían {sin_tratar} sin tratar en zonas que no se tocan"
    mensaje = f"{res.mensaje}; {texto_}" if res.mensaje else texto_
    return replace(res, estado=Estado.PARCIAL, mensaje=mensaje, accion=PREVISTA)


def _escribir_marca(contenido: bytes, temporal: Path) -> None:
    """Escribe la marca de la web en el temporal y la relee: si no queda igual (o el destino no
    admite flujos NTFS, como FAT o exFAT) no se publica una copia sin ella."""
    try:
        windows.escribir_marca_web(contenido, temporal)
        escrita = windows.leer_marca_web(temporal)
    except OSError as e:
        raise MarcaNoAdmitida(NO_ADMITE_MARCA) from e
    if escrita is None or escrita.crudo != contenido:
        raise MarcaNoAdmitida(NO_ADMITE_MARCA)


def _identidad(ruta: Path) -> tuple[int, int, int, int]:
    st = os.stat(ruta)
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _comprobar_sin_cambios(ruta: Path, identidad: tuple[int, int, int, int], huella: str | None) -> None:
    """--in-place: el original sigue siendo el que se analizó (archivo, tamaño, fecha y contenido)."""
    try:
        if _identidad(ruta) != identidad:
            raise CambioConcurrente("el original cambió mientras se procesaba; no se ha tocado")
        if huella is not None and verificar.sha256_de(ruta) != huella:
            raise CambioConcurrente("el contenido del original cambió mientras se procesaba; no se ha tocado")
    except OSError as e:
        raise CambioConcurrente(f"no se pudo comprobar el original ({e.strerror or type(e).__name__}); no se ha tocado") from e


def _fsync(ruta: Path) -> None:
    with open(ruta, "r+b") as f:
        os.fsync(f.fileno())


def _escribir_copia(preparado, ruta: Path, temporal: Path) -> None:
    if preparado.datos is not None:
        temporal.write_bytes(preparado.datos)            # los mismos bytes que se analizaron
    else:
        _copiar_bytes(ruta, temporal)


def _marca_web(marca: windows.MarcaWeb | None, modo: Modo) -> tuple[list[Hallazgo], bytes | None]:
    """Hallazgos de la marca de la web y su contenido reescrito (None si no cambia).

    Solo se quitan HostUrl y ReferrerUrl: en modo IA si son de un servicio de IA; con --all,
    siempre. ZoneId y lo demás se conservan. Sin URL que quitar, no hay hallazgo.
    """
    if marca is None:
        return [], None
    hallazgos = []
    quitar = set()
    for campo, url in (("HostUrl", marca.host_url), ("ReferrerUrl", marca.referrer_url)):
        if not url:
            continue
        servicio = firmas.servicio_ia(url)
        if servicio or modo is Modo.TODO:
            quitar.add(campo.lower())
            hallazgos.append(Hallazgo(
                categoria="windows", clave=f"Zone.Identifier: {campo}", valor=url,
                resumen=firmas.describir_url(url), ubicacion="flujo NTFS", es_ia=bool(servicio),
                motivo=PROCEDENCIA if servicio else AMPLIO,
            ))
    return hallazgos, (windows.reescribir_marca_web(marca, quitar) if quitar else None)


# ---------------------------------------------------------------------------
# Entrada estándar y portapapeles

def procesar_bytes_texto(datos: bytes, opciones: Opciones, fuente: str) -> tuple[Resultado, bytes]:
    """Para la entrada estándar: devuelve el resultado y los bytes de salida (los originales en --dry-run)."""
    res = Resultado(fuente=fuente)
    tipo = deteccion.tipo_texto(datos[:deteccion.CABECERA])
    if tipo is None:
        return replace(res, estado=Estado.ERROR, mensaje="la entrada no es texto"), datos
    marcado = texto.marcado_de(tipo)
    avisos = [texto.AVISO_NORMALIZAR_MARCADO] if opciones.normalizar and marcado else []
    res = replace(res, tipo=tipo, mensaje="; ".join(avisos))
    try:
        analisis = texto.analizar(datos, tipo, normalizar=opciones.normalizar)
    except texto.ErrorTexto as e:
        return replace(res, estado=Estado.ERROR, mensaje=str(e)), datos
    res = replace(res, hallazgos=analisis.hallazgos)
    parcial = any(not h.tratado for h in analisis.hallazgos)
    if not analisis.hallazgos:
        return replace(res, estado=Estado.SIN_CAMBIOS, mensaje=res.mensaje or TEXTO_SIN_CAMBIOS), datos
    if not analisis.cambios:
        return replace(res, estado=Estado.PARCIAL, mensaje=_mensaje_parcial(res.mensaje, analisis.hallazgos)), datos
    if opciones.dry_run:
        return _previsto(res, analisis.hallazgos), datos
    try:
        verificar.verificar_cadena(analisis.original.texto, analisis.limpio, marcado, opciones.normalizar)
        verificar.sin_restos(analisis.limpio, marcado, opciones.normalizar)
    except verificar.FalloVerificacion as e:
        return replace(res, estado=Estado.ERROR, mensaje=str(e), verificacion="fallida"), datos
    salida = texto.codificar(analisis.original, analisis.limpio)
    estado = Estado.PARCIAL if parcial else Estado.LIMPIO
    mensaje = _mensaje_parcial(res.mensaje, analisis.hallazgos) if parcial else res.mensaje
    return replace(
        res, estado=estado, destino="salida estándar", mensaje=mensaje, verificacion="superada", accion=REALIZADA,
    ), salida


def procesar_portapapeles(opciones: Opciones) -> Resultado:
    res = Resultado(fuente="portapapeles", tipo="texto")
    try:
        pp = windows.leer_portapapeles()
    except windows.ErrorPortapapeles as e:
        return replace(res, estado=Estado.ERROR, mensaje=str(e))
    if pp.texto is None and pp.html is None:
        return replace(res, estado=Estado.OMITIDO, mensaje="el portapapeles no contiene texto")

    hallazgos: list[Hallazgo] = []
    texto_limpio = None
    if pp.texto is not None:
        texto_limpio, cambios = texto.limpiar_cadena(pp.texto, None, opciones.normalizar)
        hallazgos += texto.resumir(pp.texto, cambios, "texto")

    html_nuevo = None
    avisos = []
    html_original = None
    if pp.html is not None:
        try:
            html_original = windows.leer_cf_html(pp.html)
        except (ValueError, UnicodeDecodeError):
            avisos.append("el HTML del portapapeles estaba mal formado y se descarta")
            hallazgos.append(Hallazgo("portapapeles", "HTML mal formado", ubicacion="HTML"))
    if html_original is not None:
        if opciones.normalizar:
            avisos.append("--normalize no se aplica al HTML del portapapeles")
        partes = {}
        for nombre in ("antes", "fragmento", "despues"):
            original = getattr(html_original, nombre)
            limpio, cambios = texto.limpiar_cadena(original, "html")
            partes[nombre] = limpio
            ubicacion = f"HTML ({nombre})" if nombre != "fragmento" else "HTML"
            hallazgos += texto.resumir(original, cambios, ubicacion) + texto.no_tratados(original, "html", ubicacion)
        url = html_original.source_url
        servicio = firmas.servicio_ia(url)
        quitar_url = bool(url) and (servicio is not None or opciones.modo is Modo.TODO)
        if quitar_url:
            hallazgos.append(Hallazgo(
                "procedencia", "SourceURL del HTML copiado", valor=url, resumen=firmas.describir_url(url),
                ubicacion="HTML", es_ia=bool(servicio), motivo=PROCEDENCIA if servicio else AMPLIO,
            ))
        html_nuevo = replace(html_original, **partes, source_url=None if quitar_url else url)

    for formato, url in pp.con_url:
        servicio = firmas.servicio_ia(url)
        if servicio or opciones.modo is Modo.TODO:
            hallazgos.append(Hallazgo(
                "procedencia", f"Formato «{formato}» con la URL de origen", valor=url,
                resumen=firmas.describir_url(url), ubicacion="portapapeles", es_ia=bool(servicio),
                motivo=PROCEDENCIA if servicio else AMPLIO,
            ))

    res = replace(res, hallazgos=hallazgos, mensaje="; ".join(avisos))
    parcial = any(not h.tratado for h in hallazgos)
    if not hallazgos:
        return replace(res, estado=Estado.SIN_CAMBIOS, mensaje=res.mensaje or TEXTO_SIN_CAMBIOS)
    if all(not h.tratado for h in hallazgos):
        return replace(res, estado=Estado.PARCIAL, mensaje=_mensaje_parcial(res.mensaje, hallazgos))
    if opciones.dry_run:
        return _previsto(res, hallazgos)

    try:
        if pp.texto is not None:
            verificar.verificar_cadena(pp.texto, texto_limpio, None, opciones.normalizar)
            verificar.sin_restos(texto_limpio, None, opciones.normalizar)
        if html_nuevo is not None:
            for nombre in ("antes", "fragmento", "despues"):
                verificar.verificar_cadena(getattr(html_original, nombre), getattr(html_nuevo, nombre), "html")
                verificar.sin_restos(getattr(html_nuevo, nombre), "html")
        html_bytes = windows.escribir_cf_html(html_nuevo) if html_nuevo is not None else None
        if html_bytes is not None:
            releido = windows.leer_cf_html(html_bytes)
            if releido.fragmento != html_nuevo.fragmento:
                raise verificar.FalloVerificacion("el HTML reconstruido no cuadra")
        windows.escribir_portapapeles(texto_limpio, html_bytes)
        comprobado = windows.leer_portapapeles()
        if comprobado.texto != texto_limpio:
            raise verificar.FalloVerificacion("el portapapeles no quedó con el texto limpio")
    except verificar.FalloVerificacion as e:
        return replace(res, estado=Estado.ERROR, mensaje=str(e), verificacion="fallida")
    except windows.ErrorPortapapeles as e:
        return replace(res, estado=Estado.ERROR, mensaje=str(e))

    if pp.otros:
        avisos.append("también se quitaron del portapapeles: " + ", ".join(sorted(set(pp.otros))))
    mensaje = "; ".join(avisos)
    if parcial:
        mensaje = _mensaje_parcial(mensaje, hallazgos)
    return replace(
        res, estado=Estado.PARCIAL if parcial else Estado.LIMPIO, destino="portapapeles",
        mensaje=mensaje, verificacion="superada", accion=REALIZADA,
    )
