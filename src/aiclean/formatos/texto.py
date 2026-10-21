"""Caracteres invisibles Unicode y espacios raros en texto (archivos, stdin y portapapeles).

Quita lo que no se ve (zero-width, BOM sueltos, tags Unicode, selectores de variación
sueltos, controles bidi en texto sin escritura RTL...). Se conserva lo que forma parte del
texto: el ZWJ de los emojis compuestos, ZWJ/ZWNJ entre letras árabes e índicas, un VS15/VS16
tras un carácter que lo admite, las banderas de subdivisión (🏴 + tags) y los selectores de
variantes CJK. Los espacios raros (NBSP, NNBSP...) y SHY/WJ se conservan; solo --normalize,
y solo en texto plano, los cambia por espacio normal o los quita.

En HTML/XML también cuentan las referencias de carácter (&#8203;, &zwj;...). <script>,
<style>, CDATA, comentarios condicionales y el interior de las etiquetas no se tocan: lo que
haya ahí se informa como "no tratado". En los comentarios normales se quitan los invisibles
literales (las referencias allí son texto).
"""

from __future__ import annotations

import hashlib
import html.entities
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from aiclean.modelos import Hallazgo, Modo, Preparado


class ErrorTexto(Exception):
    """El contenido no se puede tratar como texto."""


# ---------------------------------------------------------------------------
# Codificación

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

NOMBRES_CODIFICACION = {
    "utf-8": "UTF-8", "utf-16-le": "UTF-16 LE", "utf-16-be": "UTF-16 BE",
    "utf-32-le": "UTF-32 LE", "utf-32-be": "UTF-32 BE", "cp1252": "Windows-1252",
}


@dataclass(frozen=True)
class Decodificado:
    texto: str
    codificacion: str
    bom: bytes


def decodificar(datos: bytes) -> Decodificado:
    """Texto, codificación y BOM inicial. Nunca traduce los finales de línea."""
    for bom, codec in _BOMS:
        if datos.startswith(bom):
            try:
                return Decodificado(datos[len(bom):].decode(codec), codec, bom)
            except UnicodeDecodeError as e:
                raise ErrorTexto(f"tiene BOM de {NOMBRES_CODIFICACION[codec]} pero no lo es ({e.reason})") from e
    if b"\x00" in datos:
        raise ErrorTexto("parece un archivo binario (tiene bytes nulos)")
    for codec in ("utf-8", "cp1252"):
        try:
            return Decodificado(datos.decode(codec), codec, b"")
        except UnicodeDecodeError:
            continue
    raise ErrorTexto("codificación no reconocida (no es UTF-8, UTF-16/32 con BOM ni Windows-1252)")


def codificar(dec: Decodificado, texto: str) -> bytes:
    return dec.bom + texto.encode(dec.codificacion)


# ---------------------------------------------------------------------------
# Tablas Unicode (generadas de UCD 16.0: emoji-data.txt, emoji-variation-sequences.txt
# y StandardizedVariants.txt; las bases CJK de los selectores se miran por rango).
#
# unicodedata de Python 3.12 es Unicode 15.0. Solo se combina con estas tablas en dos
# sitios (_unida y la regla del CGJ, que miran la categoría general); allí un carácter que
# unicodedata no conoce (Cn) se trata como letra/marca, para conservar en la duda.
# unicodedata.name solo se usa para mostrar nombres.

VERSION_TABLAS_UNICODE = "16.0.0"

class _Rangos:
    def __init__(self, texto: str):
        pares = []
        for parte in texto.split():
            a, _, b = parte.partition("-")
            pares.append((int(a, 16), int(b or a, 16)))
        self._ini = [a for a, _ in pares]
        self._fin = [b for _, b in pares]

    def __contains__(self, cp: int) -> bool:
        i = bisect_right(self._ini, cp) - 1
        return i >= 0 and cp <= self._fin[i]


_PICTOGRAFICOS = _Rangos("""
    0xA9 0xAE 0x203C 0x2049 0x2122 0x2139
    0x2194-0x2199 0x21A9-0x21AA 0x231A-0x231B 0x2328 0x2388 0x23CF
    0x23E9-0x23F3 0x23F8-0x23FA 0x24C2 0x25AA-0x25AB 0x25B6 0x25C0
    0x25FB-0x25FE 0x2600-0x2605 0x2607-0x2612 0x2614-0x2685 0x2690-0x2705 0x2708-0x2712
    0x2714 0x2716 0x271D 0x2721 0x2728 0x2733-0x2734
    0x2744 0x2747 0x274C 0x274E 0x2753-0x2755 0x2757
    0x2763-0x2767 0x2795-0x2797 0x27A1 0x27B0 0x27BF 0x2934-0x2935
    0x2B05-0x2B07 0x2B1B-0x2B1C 0x2B50 0x2B55 0x3030 0x303D
    0x3297 0x3299 0x1F000-0x1F0FF 0x1F10D-0x1F10F 0x1F12F 0x1F16C-0x1F171
    0x1F17E-0x1F17F 0x1F18E 0x1F191-0x1F19A 0x1F1AD-0x1F1E5 0x1F201-0x1F20F 0x1F21A
    0x1F22F 0x1F232-0x1F23A 0x1F23C-0x1F23F 0x1F249-0x1F3FA 0x1F400-0x1F53D 0x1F546-0x1F64F
    0x1F680-0x1F6FF 0x1F774-0x1F77F 0x1F7D5-0x1F7FF 0x1F80C-0x1F80F 0x1F848-0x1F84F 0x1F85A-0x1F85F
    0x1F888-0x1F88F 0x1F8AE-0x1F8FF 0x1F90C-0x1F93A 0x1F93C-0x1F945 0x1F947-0x1FAFF 0x1FC00-0x1FFFD
""")

_BASES_VS_EMOJI = _Rangos("""
    0x23 0x2A 0x30-0x39 0xA9 0xAE 0x203C
    0x2049 0x2122 0x2139 0x2194-0x2199 0x21A9-0x21AA 0x231A-0x231B
    0x2328 0x23CF 0x23E9-0x23F3 0x23F8-0x23FA 0x24C2 0x25AA-0x25AB
    0x25B6 0x25C0 0x25FB-0x25FE 0x2600-0x2604 0x260E 0x2611
    0x2614-0x2615 0x2618 0x261D 0x2620 0x2622-0x2623 0x2626
    0x262A 0x262E-0x262F 0x2638-0x263A 0x2640 0x2642 0x2648-0x2653
    0x265F-0x2660 0x2663 0x2665-0x2666 0x2668 0x267B 0x267E-0x267F
    0x2692-0x2697 0x2699 0x269B-0x269C 0x26A0-0x26A1 0x26A7 0x26AA-0x26AB
    0x26B0-0x26B1 0x26BD-0x26BE 0x26C4-0x26C5 0x26C8 0x26CE-0x26CF 0x26D1
    0x26D3-0x26D4 0x26E9-0x26EA 0x26F0-0x26F5 0x26F7-0x26FA 0x26FD 0x2702
    0x2705 0x2708-0x270D 0x270F 0x2712 0x2714 0x2716
    0x271D 0x2721 0x2728 0x2733-0x2734 0x2744 0x2747
    0x274C 0x274E 0x2753-0x2755 0x2757 0x2763-0x2764 0x2795-0x2797
    0x27A1 0x27B0 0x27BF 0x2934-0x2935 0x2B05-0x2B07 0x2B1B-0x2B1C
    0x2B50 0x2B55 0x3030 0x303D 0x3297 0x3299
    0x1F004 0x1F170-0x1F171 0x1F17E-0x1F17F 0x1F202 0x1F21A 0x1F22F
    0x1F237 0x1F30D-0x1F30F 0x1F315 0x1F31C 0x1F321 0x1F324-0x1F32C
    0x1F336 0x1F378 0x1F37D 0x1F393 0x1F396-0x1F397 0x1F399-0x1F39B
    0x1F39E-0x1F39F 0x1F3A7 0x1F3AC-0x1F3AE 0x1F3C2 0x1F3C4 0x1F3C6
    0x1F3CA-0x1F3CE 0x1F3D4-0x1F3E0 0x1F3ED 0x1F3F3 0x1F3F5 0x1F3F7
    0x1F408 0x1F415 0x1F41F 0x1F426 0x1F43F 0x1F441-0x1F442
    0x1F446-0x1F449 0x1F44D-0x1F44E 0x1F453 0x1F46A 0x1F47D 0x1F4A3
    0x1F4B0 0x1F4B3 0x1F4BB 0x1F4BF 0x1F4CB 0x1F4DA
    0x1F4DF 0x1F4E4-0x1F4E6 0x1F4EA-0x1F4ED 0x1F4F7 0x1F4F9-0x1F4FB 0x1F4FD
    0x1F508 0x1F50D 0x1F512-0x1F513 0x1F549-0x1F54A 0x1F550-0x1F567 0x1F56F-0x1F570
    0x1F573-0x1F579 0x1F587 0x1F58A-0x1F58D 0x1F590 0x1F5A5 0x1F5A8
    0x1F5B1-0x1F5B2 0x1F5BC 0x1F5C2-0x1F5C4 0x1F5D1-0x1F5D3 0x1F5DC-0x1F5DE 0x1F5E1
    0x1F5E3 0x1F5E8 0x1F5EF 0x1F5F3 0x1F5FA 0x1F610
    0x1F687 0x1F68D 0x1F691 0x1F694 0x1F698 0x1F6AD
    0x1F6B2 0x1F6B9-0x1F6BA 0x1F6BC 0x1F6CB 0x1F6CD-0x1F6CF 0x1F6E0-0x1F6E5
    0x1F6E9 0x1F6F0 0x1F6F3
""")

# Bases de FE00-FE0D que no son ideogramas CJK (Myanmar, matemáticas, Phags-pa, jeroglíficos...)
_BASES_VS_ESTANDAR = _Rangos("""
    0x30 0x1000 0x1002 0x1004 0x1010-0x1011 0x1015
    0x1019-0x101A 0x101C-0x101D 0x1022 0x1031 0x1075 0x1078
    0x107A 0x1080 0x2018-0x2019 0x201C-0x201D 0x210B 0x2110
    0x2112 0x211B 0x212C 0x2130-0x2131 0x2133 0x2205
    0x2229-0x222A 0x2268-0x2269 0x2272-0x2273 0x228A-0x228B 0x2293-0x2295 0x2297
    0x229C 0x22DA-0x22DB 0x2A3C-0x2A3D 0x2A9D-0x2A9E 0x2AAC-0x2AAD 0x2ACB-0x2ACC
    0x3001-0x3002 0xA856 0xA85C 0xA85E-0xA860 0xA868 0xAA60-0xAA66
    0xAA6B-0xAA6C 0xAA6F 0xAA7A 0xFF01 0xFF0C 0xFF0E
    0xFF10 0xFF1A-0xFF1B 0xFF1F 0x10AC5-0x10AC6 0x10AD6-0x10AD7 0x10AE1
    0x13091 0x13093 0x1310F 0x13117 0x1311C 0x13121
    0x13127 0x13132 0x13139 0x13183 0x13187 0x131A0
    0x131B1 0x131B8-0x131BA 0x131CB 0x131E0 0x131EE 0x131F8-0x131FA
    0x13216 0x13257 0x1327B 0x1327F 0x13285 0x1328C
    0x132A4 0x132AA 0x132CB 0x132DC 0x132E7 0x132E9
    0x132F8 0x132FD 0x13302-0x13303 0x13307-0x13308 0x13310-0x13314 0x1331B-0x1331C
    0x13321-0x13322 0x13331 0x13338 0x1333C 0x1334A 0x13361
    0x13373 0x13377-0x13378 0x1337D 0x13385 0x13399-0x1339A 0x133AF-0x133B0
    0x133BF 0x133D3 0x133DB 0x133DD 0x133E5 0x133E7-0x133E8
    0x133F2 0x133F5-0x133F6 0x13416 0x13419-0x1341A 0x13423 0x1342C
    0x1342E 0x13443-0x13446 0x1D49C 0x1D49E-0x1D49F 0x1D4A2 0x1D4A5-0x1D4A6
    0x1D4A9-0x1D4AC 0x1D4AE-0x1D4B5
""")

# Secuencias que eran válidas en 15.0 y 16.0 retiró: se siguen conservando
_BASES_VS_ESTANDAR_15 = frozenset([0x13092, 0x130A9, 0x1333B, 0x13403])

_CJK = _Rangos("""
    0x3400-0x4DBF 0x4E00-0x9FFF 0xF900-0xFAFF 0x20000-0x2FA1F 0x30000-0x323AF
""")

# Escrituras que usan ZWJ/ZWNJ entre letras: árabe, siríaco, thaana, n'ko, índicas,
# myanmar, khmer, mongol, formas de presentación árabes y brahmi y familia.
_ESCRITURAS_UNIDAS = _Rangos("""
    0x0600-0x08FF 0x0900-0x0DFF 0x0F00-0x0FFF 0x1000-0x109F 0x1780-0x18AF 0x1CD0-0x1CFF
    0xA840-0xA8FF 0xA980-0xAAFF 0xABC0-0xABFF 0xFB50-0xFDFF 0xFE70-0xFEFE 0x10A00-0x10A5F
    0x11000-0x1137F 0x11400-0x115FF 0x11600-0x11AFF 0x11C00-0x11DAF 0x1E900-0x1E95F
""")

_JAMO = _Rangos("0x1100-0x11FF 0xA960-0xA97F 0xD7B0-0xD7FF")

# Letras y marcas de escrituras de derecha a izquierda (hebreo, árabe, siríaco, thaana...),
# sin contar U+061C (ARABIC LETTER MARK), que es un control bidi
_RTL = re.compile(
    "[\u0590-\u061b\u061d-\u08ff\ufb1d-\ufdff\ufe70-\ufefe\U00010800-\U00010FFF\U0001E800-\U0001EFFF]"
)


# ---------------------------------------------------------------------------
# Clasificación

# Espacios raros y SHY/WJ: se conservan; solo --normalize (y solo en texto plano) los toca
ESPACIOS_RAROS = frozenset([0x00A0, *range(0x2000, 0x200B), 0x202F, 0x205F])
SUAVES = frozenset([0x00AD, 0x2060])          # SOFT HYPHEN, WORD JOINER
_BIDI = frozenset([0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)])
_SIEMPRE = frozenset([
    0x200B, 0x2061, 0x2062, 0x2063, 0x2064, 0xFEFF, 0x3164, 0xFFA0,
    *range(0x206A, 0x2070), *range(0xFFF9, 0xFFFC), *range(0x1D173, 0x1D17B),
])

# Todo lo que esta limpieza puede tocar (quitar o normalizar)
RANGOS_CANDIDATOS = (
    (0x00A0, 0x00A0), (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x180B, 0x180F), (0x2000, 0x200F), (0x202A, 0x202F), (0x205F, 0x2064), (0x2066, 0x206F),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF9, 0xFFFB),
    (0x1D173, 0x1D17A), (0xE0000, 0xE007F), (0xE0100, 0xE01EF),
)


def _regex_de(cps) -> re.Pattern:
    rangos: list[list[int]] = []
    for cp in sorted(cps):
        if rangos and cp == rangos[-1][1] + 1:
            rangos[-1][1] = cp
        else:
            rangos.append([cp, cp])
    return re.compile("[" + "".join(
        re.escape(chr(a)) + (f"-{re.escape(chr(b))}" if b != a else "") for a, b in rangos
    ) + "]")


_TODOS_CANDIDATOS = frozenset(cp for a, b in RANGOS_CANDIDATOS for cp in range(a, b + 1))
_CANDIDATOS = _regex_de(_TODOS_CANDIDATOS)
QUITABLES = _regex_de(_TODOS_CANDIDATOS - ESPACIOS_RAROS - SUAVES)     # lo que puede quitarse siempre
QUITABLES_NORMALIZAR = _regex_de(_TODOS_CANDIDATOS - ESPACIOS_RAROS)   # más SHY/WJ con --normalize
ESPACIOS = _regex_de(ESPACIOS_RAROS)


def _clase(cp: int) -> str | None:
    if cp in ESPACIOS_RAROS:
        return "espacio"
    if cp in SUAVES:
        return "suave"
    if cp in _SIEMPRE:
        return "siempre"
    if cp in _BIDI:
        return "bidi"
    if cp == 0x200D:
        return "zwj"
    if cp == 0x200C:
        return "zwnj"
    if cp in (0xFE0E, 0xFE0F):
        return "vs_emoji"
    if 0xFE00 <= cp <= 0xFE0D:
        return "vs_estandar"
    if 0xE0100 <= cp <= 0xE01EF:
        return "vs_ideografico"
    if 0xE0000 <= cp <= 0xE007F:
        return "tag"
    if cp in (0x180B, 0x180C, 0x180D, 0x180F):
        return "fvs"
    if cp == 0x180E:
        return "mvs"
    if cp == 0x034F:
        return "cgj"
    if cp in (0x115F, 0x1160):
        return "jamo"
    return None


def es_quitable(cp: int, normalizar: bool = False) -> bool:
    """True si la limpieza puede quitar este carácter (SHY/WJ solo al normalizar; los espacios nunca)."""
    clase = _clase(cp)
    if clase == "suave":
        return normalizar
    return clase is not None and clase != "espacio"


_CATEGORIA = {
    "siempre": "invisible", "zwj": "invisible", "zwnj": "invisible", "fvs": "invisible",
    "mvs": "invisible", "cgj": "invisible", "jamo": "invisible", "bidi": "bidi",
    "tag": "etiqueta", "vs_emoji": "selector", "vs_estandar": "selector",
    "vs_ideografico": "selector", "espacio": "espacio", "suave": "formato",
}


# ---------------------------------------------------------------------------
# Referencias de carácter en HTML/XML

ENTIDAD = re.compile(r"&(?:#([0-9]{1,7})|#[xX]([0-9a-fA-F]{1,6})|([A-Za-z][A-Za-z0-9]{1,31}));")

# Zonas del marcado. Las excluidas no se tocan nunca (lo que haya se informa como "no
# tratado"); en los comentarios normales sí se quitan los invisibles literales. El interior
# de las etiquetas (<...>) también se excluye: quitar un invisible de "<scr\u200bipt>"
# crearía un <script> que antes no existía.
_ZONA = re.compile(
    r"(?P<condicional><!--\[if\b.*?<!\[endif\]\s*-->)"
    r"|(?P<comentario><!--.*?-->)"
    r"|(?P<script><script\b.*?</script\s*>)"
    r"|(?P<style><style\b.*?</style\s*>)"
    r"|(?P<cdata><!\[CDATA\[.*?\]\]>)"
    r"|(?P<etiqueta><[/!?]?[A-Za-z][^<>]*>)",
    re.S | re.I,
)
_NOMBRE_ZONA = {
    "condicional": "comentario condicional", "comentario": "comentario", "script": "<script>",
    "style": "<style>", "cdata": "CDATA", "etiqueta": "etiquetas <…>",
}


@dataclass(frozen=True)
class Zona:
    inicio: int
    fin: int
    tipo: str                  # clave de _NOMBRE_ZONA

    @property
    def excluida(self) -> bool:
        return self.tipo != "comentario"


def zonas_marcado(texto: str) -> list[Zona]:
    return [Zona(m.start(), m.end(), m.lastgroup) for m in _ZONA.finditer(texto)]


class _Zonas:
    """Busca en qué zona cae una posición."""

    def __init__(self, zonas: list[Zona]):
        self.zonas = zonas
        self._inicios = [z.inicio for z in zonas]

    def de(self, pos: int) -> Zona | None:
        k = bisect_right(self._inicios, pos) - 1
        if k >= 0 and pos < self.zonas[k].fin:
            return self.zonas[k]
        return None


def cp_de_entidad(m: re.Match, marcado: str) -> int | None:
    """Código del carácter de una referencia (&#8203;, &#x200B;, &zwj;) o None."""
    dec, hexa, nombre = m.groups()
    if dec or hexa:
        cp = int(dec) if dec else int(hexa, 16)
        return cp if cp <= 0x10FFFF and not 0xD800 <= cp <= 0xDFFF else None
    if marcado == "html":
        valor = html.entities.html5.get(nombre + ";")
        if valor and len(valor) == 1:
            return ord(valor)
    return None


class _Secuencia:
    """El texto como unidades: caracteres o, en marcado, referencias de carácter completas."""

    def __init__(self, texto: str, marcado: str | None, con_zonas: bool = True):
        self.texto = texto
        self.cps: list[int] | None = None
        self.ini: list[int] | None = None
        self.zonas = _Zonas(zonas_marcado(texto) if marcado and con_zonas else [])
        if marcado:
            self._trocear_marcado(marcado)
        self.n = len(self.cps) if self.cps is not None else len(texto)

    def _trocear_marcado(self, marcado: str) -> None:
        texto = self.texto
        entidades: dict[int, tuple[int, int]] = {}
        for m in ENTIDAD.finditer(texto):
            if self.zonas.de(m.start()) is not None:
                continue                   # en comentarios y zonas excluidas son texto literal
            cp = cp_de_entidad(m, marcado)
            if cp is not None:
                entidades[m.start()] = (m.end(), cp)
        if not entidades:
            return                         # sin referencias, cada carácter es una unidad: no hace falta trocear
        cps: list[int] = []
        ini: list[int] = []
        i, total = 0, len(texto)
        while i < total:
            ini.append(i)
            ent = entidades.get(i)
            if ent:
                cps.append(ent[1])
                i = ent[0]
            else:
                cps.append(ord(texto[i]))
                i += 1
        ini.append(total)
        self.cps, self.ini = cps, ini

    def cp(self, i: int) -> int:
        return self.cps[i] if self.cps is not None else ord(self.texto[i])

    def span(self, i: int) -> tuple[int, int]:
        if self.ini is None:
            return i, i + 1
        return self.ini[i], self.ini[i + 1]

    def es_literal(self, i: int) -> bool:
        """True si la unidad es el carácter tal cual (no una referencia &...;)."""
        a, b = self.span(i)
        return b - a == 1

    def candidatos(self) -> list[int]:
        if self.cps is None:
            return [m.start() for m in _CANDIDATOS.finditer(self.texto) if not self._excluida(m.start())]
        return [i for i, cp in enumerate(self.cps) if cp in _TODOS_CANDIDATOS and not self._excluida(i)]

    def _excluida(self, i: int) -> bool:
        zona = self.zonas.de(self.span(i)[0])
        return zona is not None and zona.excluida


# ---------------------------------------------------------------------------
# Limpieza

@dataclass(frozen=True)
class Cambio:
    inicio: int        # posición en el texto original
    fin: int
    cp: int
    categoria: str
    nuevo: str         # "" si se quita, " " si es un espacio raro


_ETIQUETA_SUBDIVISION = re.compile(r"[a-z]{2}[a-z0-9]{1,4}")


def limpiar_cadena(texto: str, marcado: str | None = None, normalizar: bool = False) -> tuple[str, list[Cambio]]:
    """Texto limpio y lista de cambios. marcado: None, "html" o "xml".

    normalizar (--normalize) solo vale para texto plano: en marcado se ignora y los
    espacios raros y SHY/WJ se conservan siempre, literales o como referencias.
    """
    return _limpiar(texto, marcado, normalizar and marcado is None, con_zonas=True)


def _limpiar(texto: str, marcado: str | None, normalizar: bool, con_zonas: bool) -> tuple[str, list[Cambio]]:
    if not _CANDIDATOS.search(texto) and not (marcado and "&" in texto):
        return texto, []
    sec = _Secuencia(texto, marcado, con_zonas)
    candidatos = sec.candidatos()
    if not candidatos:
        return texto, []
    clases = {i: _clase(sec.cp(i)) for i in candidatos}
    hay_rtl = any(c == "bidi" for c in clases.values()) and _RTL.search(texto) is not None

    quitar: set[int] = set()
    reemplazar: set[int] = set()
    condicionales: list[int] = []
    for i in candidatos:
        clase = clases[i]
        if clase == "espacio":
            if normalizar:
                reemplazar.add(i)
        elif clase == "suave":
            if normalizar:
                quitar.add(i)
        elif clase == "siempre":
            quitar.add(i)
        elif clase == "bidi":
            if not hay_rtl:
                quitar.add(i)
        elif clase == "tag":
            pass                           # por tramos, abajo
        else:
            condicionales.append(i)
    quitar |= _tags_sobrantes(sec, [i for i in candidatos if clases[i] == "tag"])

    vecinos = _Vecinos(sec, quitar)
    while True:
        cambiado = False
        for i in condicionales:
            if i in quitar:
                continue
            if not _conservar(clases[i], sec, i, vecinos):
                quitar.add(i)
                cambiado = True
        if not cambiado:
            break

    cambios = []
    for i in sorted(quitar | reemplazar):
        a, b = sec.span(i)
        cp = sec.cp(i)
        cambios.append(Cambio(a, b, cp, _CATEGORIA[clases[i]], " " if i in reemplazar else ""))
    partes, pos = [], 0
    for c in cambios:
        partes.append(texto[pos:c.inicio])
        partes.append(c.nuevo)
        pos = c.fin
    partes.append(texto[pos:])
    return "".join(partes), cambios


def _tags_sobrantes(sec: _Secuencia, tags: list[int]) -> set[int]:
    """Tags Unicode que no forman una bandera de subdivisión válida (🏴 + gbeng + cancel)."""
    sobrantes: set[int] = set()
    k = 0
    while k < len(tags):
        inicio = k
        while k + 1 < len(tags) and tags[k + 1] == tags[k] + 1:
            k += 1
        tramo = tags[inicio:k + 1]
        k += 1
        cuerpo = "".join(chr(sec.cp(i) - 0xE0000) for i in tramo[:-1])
        valida = (
            tramo[0] > 0 and sec.cp(tramo[0] - 1) == 0x1F3F4
            and sec.cp(tramo[-1]) == 0xE007F
            and _ETIQUETA_SUBDIVISION.fullmatch(cuerpo) is not None
        )
        if not valida:
            sobrantes.update(tramo)
    return sobrantes


class _Vecinos:
    """Vecino conservado más cercano, saltando lo ya quitado (con atajos, para cadenas largas)."""

    def __init__(self, sec: _Secuencia, quitar: set[int]):
        self.sec, self.quitar = sec, quitar
        self._atras: dict[int, int] = {}
        self._delante: dict[int, int] = {}

    def anterior(self, i: int) -> int | None:
        return self._buscar(i - 1, self._atras, -1)

    def siguiente(self, i: int) -> int | None:
        return self._buscar(i + 1, self._delante, +1)

    def _buscar(self, j: int, atajos: dict[int, int], paso: int) -> int | None:
        recorrido = []
        while 0 <= j < self.sec.n and j in self.quitar:
            recorrido.append(j)
            j = atajos.get(j, j + paso)
        for r in recorrido:
            atajos[r] = j
        return j if 0 <= j < self.sec.n else None


_MODIFICADORES = range(0x1F3FB, 0x1F400)


def _base_emoji(sec: _Secuencia, vecinos: _Vecinos, j: int | None) -> int | None:
    """Salta hacia atrás selectores, tonos de piel, tags y keycap hasta la base del emoji."""
    pasos = 0
    while j is not None and pasos < 16:
        cp = sec.cp(j)
        if cp in (0xFE0E, 0xFE0F, 0x20E3) or cp in _MODIFICADORES or 0xE0020 <= cp <= 0xE007F:
            j = vecinos.anterior(j)
            pasos += 1
            continue
        return cp
    return None


def _categoria(cp: int) -> str:
    """Categoría general; "Cn" (sin asignar para el unicodedata de Python, que puede ser más
    antiguo que las tablas) cuenta como letra o marca, para conservar en la duda."""
    cat = unicodedata.category(chr(cp))
    return "LM" if cat == "Cn" else cat[0]


def _unida(sec: _Secuencia, j: int | None) -> bool:
    if j is None:
        return False
    cp = sec.cp(j)
    return cp in _ESCRITURAS_UNIDAS and _categoria(cp) in ("L", "M", "LM")


def _conservar(clase: str, sec: _Secuencia, i: int, vecinos: _Vecinos) -> bool:
    ant = vecinos.anterior(i)
    cp_ant = sec.cp(ant) if ant is not None else None
    if clase == "vs_emoji":
        return cp_ant is not None and cp_ant in _BASES_VS_EMOJI
    if clase == "vs_estandar":
        return cp_ant is not None and (
            cp_ant in _BASES_VS_ESTANDAR or cp_ant in _BASES_VS_ESTANDAR_15
            or (sec.cp(i) <= 0xFE02 and cp_ant in _CJK)
        )
    if clase == "vs_ideografico":
        return cp_ant is not None and cp_ant in _CJK
    if clase == "fvs":
        return cp_ant is not None and 0x1820 <= cp_ant <= 0x18AA
    sig = vecinos.siguiente(i)
    cp_sig = sec.cp(sig) if sig is not None else None
    if clase == "zwj":
        base = _base_emoji(sec, vecinos, ant)
        if base is not None and base in _PICTOGRAFICOS and cp_sig is not None and cp_sig in _PICTOGRAFICOS:
            return True
        return _unida(sec, ant) and _unida(sec, sig)
    if clase == "zwnj":
        return _unida(sec, ant) and _unida(sec, sig)
    if clase == "mvs":
        return all(c is not None and 0x1820 <= c <= 0x18AA for c in (cp_ant, cp_sig))
    if clase == "cgj":
        return (
            cp_ant is not None and cp_sig is not None
            and _categoria(cp_ant) in ("L", "M", "LM")
            and _categoria(cp_sig) in ("M", "LM")
        )
    if clase == "jamo":
        return (cp_ant is not None and cp_ant in _JAMO) or (cp_sig is not None and cp_sig in _JAMO)
    return False


# ---------------------------------------------------------------------------
# Hallazgos

_MAX_POSICIONES = 3
_MAX_OCULTO = 120


def _posicionador(texto: str):
    saltos = [m.end() for m in re.finditer("\n", texto)]

    def posicion(p: int) -> str:
        linea = bisect_right(saltos, p)
        col = p - (saltos[linea - 1] if linea else 0) + 1
        return f"línea {linea + 1}, col {col}"

    return posicion


def resumir(texto: str, cambios: list[Cambio], ubicacion: str = "") -> list[Hallazgo]:
    """Agrupa los cambios en hallazgos legibles (por carácter; tags y selectores juntos).

    El texto oculto que se recupere va en valor (sensible); resumen solo dice cuánto hay.
    """
    if not cambios:
        return []
    posicion = _posicionador(texto)
    grupos: dict[tuple[str, int | None], list[Cambio]] = {}
    for c in cambios:
        clave = (c.categoria, None if c.categoria in ("etiqueta", "selector") else c.cp)
        grupos.setdefault(clave, []).append(c)

    hallazgos = []
    for (categoria, cp), lista in grupos.items():
        if categoria == "etiqueta":
            clave = "Etiquetas Unicode (U+E0000–E007F)"
            valor, resumen = _oculto_en_tags(lista)
        elif categoria == "selector":
            clave = "Selectores de variación sueltos"
            valor, resumen = _oculto_en_selectores(lista)
        else:
            clave = f"U+{cp:04X} {unicodedata.name(chr(cp), 'SIN NOMBRE')}"
            valor = resumen = "BOM suelto" if cp == 0xFEFF else ""
        hallazgos.append(Hallazgo(
            categoria=categoria, clave=clave, valor=valor, resumen=resumen, cantidad=len(lista),
            posiciones=[posicion(c.inicio) for c in lista[:_MAX_POSICIONES]], ubicacion=ubicacion,
        ))
    return hallazgos


def no_tratados(texto: str, marcado: str | None, ubicacion: str = "") -> list[Hallazgo]:
    """Lo que se quitaría fuera de las zonas excluidas del marcado y dentro se deja tal cual.

    Solo ubicación, cantidad y posiciones: nunca valores.
    """
    if not marcado:
        return []
    posicion = None
    por_tipo: dict[str, list[int]] = {}
    for zona in zonas_marcado(texto):
        if not zona.excluida:
            continue
        fragmento = texto[zona.inicio:zona.fin]
        if not _CANDIDATOS.search(fragmento) and "&" not in fragmento:
            continue
        # En las etiquetas las referencias (&#8203;) sí se interpretan; en lo demás son literales
        _, cambios = _limpiar(fragmento, marcado if zona.tipo == "etiqueta" else None, False, con_zonas=False)
        if cambios:
            por_tipo.setdefault(zona.tipo, []).extend(zona.inicio + c.inicio for c in cambios)
    hallazgos = []
    for tipo, posiciones in por_tipo.items():
        posicion = posicion or _posicionador(texto)
        nombre = _NOMBRE_ZONA[tipo]
        hallazgos.append(Hallazgo(
            categoria="no_tratado", clave="Invisibles en zona excluida",
            cantidad=len(posiciones), posiciones=[posicion(p) for p in posiciones[:_MAX_POSICIONES]],
            ubicacion=f"{ubicacion}: {nombre}" if ubicacion else nombre,
            resumen="no se tocan para no cambiar el comportamiento", tratado=False,
        ))
    return hallazgos


def _tramos(lista: list[Cambio]) -> list[list[Cambio]]:
    tramos: list[list[Cambio]] = []
    for c in lista:
        if tramos and tramos[-1][-1].fin == c.inicio:
            tramos[-1].append(c)
        else:
            tramos.append([c])
    return tramos


def _recortar(s: str) -> str:
    return s if len(s) <= _MAX_OCULTO else s[:_MAX_OCULTO] + "…"


def _oculto_en_tags(lista: list[Cambio]) -> tuple[str, str]:
    """(valor sensible con el texto oculto, resumen seguro con su longitud)."""
    textos = []
    for tramo in _tramos(lista):
        s = "".join(chr(c.cp - 0xE0000) for c in tramo if 0xE0020 <= c.cp <= 0xE007E)
        if s.strip():
            textos.append(s)
    if not textos:
        return "", ""
    largo = sum(len(t) for t in textos)
    return f"texto oculto: «{_recortar(' | '.join(textos))}»", f"texto oculto de {largo} caracteres"


def _oculto_en_selectores(lista: list[Cambio]) -> tuple[str, str]:
    """Descodifica la técnica de esconder bytes en selectores (FE00-FE0F = 0-15, E0100+ = 16-255)."""
    mejores = []
    total = 0
    for tramo in _tramos(lista):
        if len(tramo) < 4:
            continue
        datos = bytes(c.cp - 0xFE00 if c.cp <= 0xFE0F else c.cp - 0xE0100 + 16 for c in tramo)
        mejores.append(datos.decode("utf-8", "replace"))
        total += len(datos)
    if not mejores:
        return "", ""
    return f"posibles datos ocultos: «{_recortar(' | '.join(mejores))}»", f"posibles datos ocultos ({total} bytes)"


# ---------------------------------------------------------------------------
# Archivos

def marcado_de(tipo: str) -> str | None:
    return {"html": "html", "xml": "xml", "svg": "xml"}.get(tipo)


@dataclass(frozen=True)
class Analisis:
    original: Decodificado
    limpio: str
    cambios: list[Cambio]
    hallazgos: list[Hallazgo]


AVISO_NORMALIZAR_MARCADO = "--normalize no se aplica a HTML/XML/SVG: los espacios especiales y SHY/WJ se conservan"


def analizar(datos: bytes, tipo: str, ubicacion: str = "", normalizar: bool = False) -> Analisis:
    dec = decodificar(datos)
    marcado = marcado_de(tipo)
    limpio, cambios = limpiar_cadena(dec.texto, marcado, normalizar)
    hallazgos = resumir(dec.texto, cambios, ubicacion) + no_tratados(dec.texto, marcado, ubicacion)
    return Analisis(dec, limpio, cambios, hallazgos)


def preparar(ruta: Path, tipo: str, modo: Modo, normalizar: bool = False) -> Preparado:
    # En texto los dos modos quitan lo mismo: todo lo invisible es huella posible.
    datos = ruta.read_bytes()
    analisis = analizar(datos, tipo, normalizar=normalizar)
    avisos = [AVISO_NORMALIZAR_MARCADO] if normalizar and marcado_de(tipo) else []

    def escribir(destino: Path) -> None:
        destino.write_bytes(codificar(analisis.original, analisis.limpio))

    return Preparado(
        analisis.hallazgos, escribir, datos=datos, huella=hashlib.sha256(datos).hexdigest(), avisos=avisos,
    )
