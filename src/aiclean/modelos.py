"""Estructuras de datos comunes: modo de limpieza, hallazgos y resultados."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class Modo(Enum):
    IA = "ia"        # solo lo que delata a una IA (por defecto)
    TODO = "todo"    # --all: todos los metadatos


class Estado(Enum):
    LIMPIO = "limpio"            # se escribió la versión limpia
    PARCIAL = "parcial"          # se limpió lo posible, pero queda algo sin tratar (se informa)
    SIN_CAMBIOS = "sin_cambios"  # no se encontró nada en lo inspeccionado
    ENCONTRADO = "encontrado"    # --dry-run: hay algo que quitar
    OMITIDO = "omitido"          # no se procesa (formato no soportado, enlace, solo en OneDrive...)
    ERROR = "error"


TEXTO_SIN_CAMBIOS = "no se encontró nada en lo inspeccionado"

# Por qué se quita un hallazgo (campo "motivo" del informe)
PROCEDENCIA = "procedencia"   # URL y marcas de un servicio de IA
GENERADOR = "generador"       # firmas de librerías y programas generadores (reservado: etapas 2-4)
UNICODE = "unicode"           # higiene de caracteres (invisibles, espacios raros...)
AMPLIO = "amplio"             # solo se quita por --all

# Qué se hizo con un resultado (campo "accion" del informe)
PREVISTA = "prevista"         # --dry-run: habría algo que quitar
REALIZADA = "realizada"       # se escribió la salida limpia
NINGUNA = "ninguna"           # no hay nada que quitar, o se omitió o falló


@dataclass
class Hallazgo:
    categoria: str                 # "invisible", "espacio", "bidi", "etiqueta", "selector", "windows"...
    clave: str                     # campo afectado: "U+200B ZERO WIDTH SPACE", "Zone.Identifier: HostUrl"...
    valor: str = ""                # SENSIBLE: texto oculto descodificado, URL completa... (solo con --report-sensitive)
    cantidad: int = 1
    posiciones: list[str] = field(default_factory=list)   # primeras apariciones ("línea 3, col 7")
    ubicacion: str = ""            # dónde está: "texto", "HTML", "flujo NTFS", "<script>"...
    es_ia: bool = True             # compatibilidad: se quita en modo IA (True) o solo por --all (False); no prueba autoría
    resumen: str = ""              # versión segura del valor: servicio, "URL de procedencia", longitud...
    tratado: bool = True           # False: se ha visto pero no se ha tocado (deja el archivo en PARCIAL)
    motivo: str = UNICODE          # PROCEDENCIA, GENERADOR, UNICODE o AMPLIO

    def a_dict(self, sensible: bool) -> dict:
        datos = asdict(self)
        if not sensible:
            del datos["valor"]
        return datos


@dataclass
class Preparado:
    """Lo que un formato encontró en un archivo y cómo escribir su versión limpia.

    Cada módulo de formatos\\ expone preparar(ruta, tipo, modo, normalizar=False) -> Preparado,
    que lee y analiza una sola vez; escribir(destino) crea el archivo limpio en destino a
    partir de esos mismos bytes.
    """
    hallazgos: list[Hallazgo]
    escribir: Callable[[Path], None]
    datos: bytes | None = None     # bytes exactos analizados (referencia para verificar)
    huella: str | None = None      # SHA-256 de esos bytes (para detectar cambios con --in-place)
    avisos: list[str] = field(default_factory=list)
    contexto: object = None        # lo que el formato necesita para verificar (imágenes: su plan)


class Omitido(Exception):
    """Un formato no puede tratar este archivo (variante no soportada): OMITIDO con el motivo."""


@dataclass
class Resultado:
    fuente: str                    # ruta, "entrada estándar" o "portapapeles"
    tipo: str = ""
    estado: Estado = Estado.SIN_CAMBIOS
    destino: str | None = None
    hallazgos: list[Hallazgo] = field(default_factory=list)
    mensaje: str = ""
    verificacion: str = ""         # "superada", "fallida" o "" si no se escribió nada
    accion: str = NINGUNA          # PREVISTA, REALIZADA o NINGUNA

    def a_dict(self, sensible: bool = False) -> dict:
        return {
            "fuente": self.fuente, "tipo": self.tipo, "estado": self.estado.value, "accion": self.accion,
            "destino": self.destino, "mensaje": self.mensaje, "verificacion": self.verificacion,
            "hallazgos": [h.a_dict(sensible) for h in self.hallazgos],
        }
