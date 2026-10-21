"""Corpus sintético de imágenes para los tests, y lectores independientes del limpiador.

Todo se construye a mano (bytes de segmentos, chunks, TIFF, IIM, XMP) sobre imágenes que
genera Pillow, para saber exactamente qué objetivos hay. Los lectores de aquí no comparten
código con formatos\\imagen.py: sirven para comprobar el resultado sin fiarse de él.
"""

from __future__ import annotations

import io
import struct
import zlib

from PIL import Image

NUL = bytes(1)
C2PA_UUID = bytes.fromhex("6332706100110010800000aa00389b71")
OTRO_UUID = bytes.fromhex("0123456789abcdef0123456789abcdef")


# ---------------------------------------------------------------------------
# Imágenes base (Pillow)

def jpeg_base(modo: str = "RGB", **opciones) -> bytes:
    color = {"RGB": (200, 60, 30), "L": 120, "CMYK": (10, 200, 30, 5)}[modo]
    im = Image.new(modo, (48, 32), color)
    for x in range(48):                                    # algo de detalle para que haya datos
        im.putpixel((x, x % 32), (0, 0, 0) if modo == "RGB" else 0 if modo == "L" else (0, 0, 0, 255))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85, **opciones)
    return b.getvalue()


def png_base(modo: str = "RGB") -> bytes:
    if modo == "P":
        im = Image.new("P", (16, 12), 3)
        im.putpalette([i % 256 for i in range(768)])
        im.info["transparency"] = 0                        # tRNS
    elif modo == "I;16":
        im = Image.new("I;16", (16, 12), 40000)
    else:
        im = Image.new(modo, (16, 12), {"RGB": (10, 20, 30), "RGBA": (10, 20, 30, 128),
                                        "L": 77, "LA": (77, 200)}[modo])
    b = io.BytesIO()
    im.save(b, "PNG", **({"transparency": 0} if modo == "P" else {}))
    return b.getvalue()


def pixeles(datos: bytes) -> tuple:
    im = Image.open(io.BytesIO(datos))
    im.load()
    return im.mode, im.size, im.tobytes()


# ---------------------------------------------------------------------------
# JPEG: segmentos

def segmento(marcador: int, carga: bytes) -> bytes:
    return bytes([0xFF, marcador]) + struct.pack(">H", len(carga) + 2) + carga


def insertar_jpeg(jpeg: bytes, segmentos: list[bytes], tras_app0: bool = True, cola: bytes = b"") -> bytes:
    """Mete segmentos tras SOI (y tras el APP0 JFIF si lo hay) y una cola tras EOI."""
    pos = 2
    if tras_app0 and jpeg[2:4] == bytes([0xFF, 0xE0]):
        pos = 4 + struct.unpack(">H", jpeg[4:6])[0]
    return jpeg[:pos] + b"".join(segmentos) + jpeg[pos:] + cola


def app1_exif(tiff_: bytes) -> bytes:
    return segmento(0xE1, b"Exif" + bytes(2) + tiff_)


def app1_xmp(paquete: str | bytes) -> bytes:
    p = paquete.encode("utf-8") if isinstance(paquete, str) else paquete
    return segmento(0xE1, b"http://ns.adobe.com/xap/1.0/" + NUL + p)


def app1_xmp_extendido(guid: bytes, paquete: bytes) -> bytes:
    cab = b"http://ns.adobe.com/xmp/extension/" + NUL + guid + struct.pack(">II", len(paquete), 0)
    return segmento(0xE1, cab + paquete)


def app1_xmp_extendido_trozos(guid: bytes, paquete: bytes, cortes: list[int]) -> list[bytes]:
    """XMP extendido partido por los desplazamientos de `cortes` (p. ej. [0, 100])."""
    limites = [*cortes, len(paquete)]
    cab = b"http://ns.adobe.com/xmp/extension/" + NUL + guid
    return [segmento(0xE1, cab + struct.pack(">II", len(paquete), a) + paquete[a:b])
            for a, b in zip(limites, limites[1:])]


def extendido_de(jpeg: bytes) -> dict[bytes, bytes]:
    """GUID -> paquete extendido reensamblado (lector propio de los tests; falla si no cuadra)."""
    id_ext = b"http://ns.adobe.com/xmp/extension/" + NUL
    trozos: dict[bytes, list] = {}
    for c in apps(jpeg, 0xE1, id_ext):
        cuerpo = c[len(id_ext):]
        total, desp = struct.unpack(">II", cuerpo[32:40])
        trozos.setdefault(cuerpo[:32], []).append((desp, total, cuerpo[40:]))
    paquetes = {}
    for guid, lista in trozos.items():
        lista.sort()
        datos = b"".join(d for _, _, d in lista)
        assert [d for d, _, _ in lista] == [sum(len(x[2]) for x in lista[:i]) for i in range(len(lista))]
        assert all(t == len(datos) for _, t, _ in lista)
        paquetes[guid] = datos
    return paquetes


def app11_jumbf(contenido: bytes, uuid: bytes = C2PA_UUID, en: int = 1, trozos: int = 1) -> list[bytes]:
    """Un almacén JUMBF (caja jumb con jumd de tipo uuid) partido en `trozos` APP11."""
    jumd = uuid + bytes([0x03]) + b"c2pa" + NUL
    caja_jumd = struct.pack(">I", 8 + len(jumd)) + b"jumd" + jumd
    caja = struct.pack(">I", 8 + len(caja_jumd) + len(contenido)) + b"jumb" + caja_jumd + contenido
    cabecera_caja = caja[:8]
    cuerpo = caja[8:]
    paso = -(-len(cuerpo) // trozos)
    segs = []
    for z in range(trozos):
        parte = cuerpo[z * paso:(z + 1) * paso]
        segs.append(segmento(0xEB, b"JP" + struct.pack(">HI", en, z + 1) + cabecera_caja + parte))
    return segs


def app2_icc(perfil: bytes) -> bytes:
    return segmento(0xE2, b"ICC_PROFILE" + NUL + bytes([1, 1]) + perfil)


def app14_adobe() -> bytes:
    return segmento(0xEE, b"Adobe" + bytes([0, 100, 0, 0, 0, 0, 1]))


def app13_iptc(datasets: list[tuple[int, int, bytes]], otros: list[tuple[int, bytes]] = ()) -> bytes:
    return segmento(0xED, b"Photoshop 3.0" + NUL + irb([(0x0404, iim(datasets)), *otros]))


def com(texto: str) -> bytes:
    return segmento(0xFE, texto.encode("utf-8"))


def iim(datasets: list[tuple[int, int, bytes]]) -> bytes:
    return b"".join(bytes([0x1C, r, d]) + struct.pack(">H", len(v)) + v for r, d, v in datasets)


def irb(recursos: list[tuple[int, bytes]]) -> bytes:
    partes = []
    for ident, datos in recursos:
        partes.append(b"8BIM" + struct.pack(">H", ident) + bytes(2) + struct.pack(">I", len(datos))
                      + datos + (NUL if len(datos) % 2 else b""))
    return b"".join(partes)


# ---------------------------------------------------------------------------
# TIFF/EXIF (escritor propio de los tests)

ASCII, SHORT, LONG, UNDEFINED, BYTE = 2, 3, 4, 7, 1


def tiff(ifd0: list[tuple[int, int, bytes]], exif: list[tuple[int, int, bytes]] = (),
         miniatura: bytes | None = None) -> bytes:
    """TIFF little-endian. Entradas: (tag, tipo, valor en bytes). Con ExifIFD y miniatura (IFD1)."""
    tam = {ASCII: 1, SHORT: 2, LONG: 4, UNDEFINED: 1, BYTE: 1}
    ifd0 = list(ifd0)
    if exif:
        ifd0.append((0x8769, LONG, bytes(4)))
    ifd0.sort()
    datos = bytearray(b"II*" + NUL + struct.pack("<I", 8))

    def bloque(entradas, inicio, siguiente):
        n = len(entradas)
        cuerpo = bytearray(struct.pack("<H", n))
        fuera = inicio + 2 + 12 * n + 4
        valores = bytearray()
        posiciones = {}
        for tag, tipo, v in entradas:
            cuenta = len(v) // tam[tipo]
            if len(v) <= 4:
                cuerpo += struct.pack("<HHI", tag, tipo, cuenta) + v.ljust(4, NUL)
            else:
                posiciones[tag] = fuera + len(valores)
                cuerpo += struct.pack("<HHI", tag, tipo, cuenta) + struct.pack("<I", fuera + len(valores))
                valores += v + (NUL if len(v) % 2 else b"")
        cuerpo += struct.pack("<I", siguiente)
        return cuerpo + valores

    # Se calculan las posiciones en dos pasadas: IFD0, ExifIFD, IFD1, miniatura
    tam0 = len(bloque(ifd0, 8, 0))
    pos_exif = 8 + tam0
    bloque_exif = bloque(sorted(exif), pos_exif, 0) if exif else b""
    pos_ifd1 = pos_exif + len(bloque_exif)
    ifd1 = []
    if miniatura is not None:
        ifd1 = [(0x0103, SHORT, struct.pack("<H", 6)), (0x0201, LONG, bytes(4)), (0x0202, LONG, struct.pack("<I", len(miniatura)))]
        tam1 = len(bloque(ifd1, pos_ifd1, 0))
        ifd1[1] = (0x0201, LONG, struct.pack("<I", pos_ifd1 + tam1))
    ifd0 = [(t, ty, struct.pack("<I", pos_exif) if t == 0x8769 else v) for t, ty, v in ifd0]
    datos += bloque(ifd0, 8, pos_ifd1 if miniatura is not None else 0)
    datos += bloque_exif
    if miniatura is not None:
        datos += bloque(ifd1, pos_ifd1, 0) + miniatura
    return bytes(datos)


def ascii_(texto: str) -> bytes:
    return texto.encode("utf-8") + NUL


def comentario_usuario(texto: str) -> bytes:
    return b"ASCII" + bytes(3) + texto.encode("ascii")


def short(v: int) -> bytes:
    return struct.pack("<H", v)


# ---------------------------------------------------------------------------
# PNG: chunks

def chunk(tipo: str, carga: bytes) -> bytes:
    t = tipo.encode("ascii")
    return struct.pack(">I", len(carga)) + t + carga + struct.pack(">I", zlib.crc32(t + carga))


def insertar_png(png: bytes, antes_de_idat: list[bytes] = (), tras_ihdr: list[bytes] = (),
                 antes_de_iend: list[bytes] = (), cola: bytes = b"") -> bytes:
    ihdr_fin = 8 + 12 + 13
    idat = png.index(b"IDAT") - 4
    iend = png.rindex(b"IEND") - 4
    return (png[:ihdr_fin] + b"".join(tras_ihdr) + png[ihdr_fin:idat] + b"".join(antes_de_idat)
            + png[idat:iend] + b"".join(antes_de_iend) + png[iend:] + cola)


def text(clave: str, texto: str) -> bytes:
    return chunk("tEXt", clave.encode("latin-1") + NUL + texto.encode("latin-1"))


def ztxt(clave: str, texto: str | bytes) -> bytes:
    crudo = texto if isinstance(texto, bytes) else texto.encode("latin-1")
    return chunk("zTXt", clave.encode("latin-1") + NUL + NUL + zlib.compress(crudo))


def itxt(clave: str, texto: str, comprimido: bool = False) -> bytes:
    cuerpo = texto.encode("utf-8")
    if comprimido:
        cuerpo = zlib.compress(cuerpo)
    return chunk("iTXt", clave.encode("latin-1") + NUL + bytes([1 if comprimido else 0, 0]) + NUL + NUL + cuerpo)


def perfil_iptc(datasets: list[tuple[int, int, bytes]]) -> str:
    """Texto de un chunk "Raw profile type iptc" (formato de ImageMagick) con un IRB."""
    crudo = irb([(0x0404, iim(datasets))])
    hexa = crudo.hex()
    lineas = [hexa[i:i + 72] for i in range(0, len(hexa), 72)]
    return "\niptc\n" + f"{len(crudo):8d}" + "\n" + "\n".join(lineas) + "\n"


def cabx(contenido: bytes) -> bytes:
    jumd = C2PA_UUID + bytes([0x03]) + b"c2pa" + NUL
    caja_jumd = struct.pack(">I", 8 + len(jumd)) + b"jumd" + jumd
    return chunk("caBX", struct.pack(">I", 8 + len(caja_jumd) + len(contenido)) + b"jumb" + caja_jumd + contenido)


# ---------------------------------------------------------------------------
# Lectores independientes

def segmentos(jpeg: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """[(marcador, carga)] de los segmentos antes de cada SOS, más los datos de entropía como
    (0, bytes), y la cola tras EOI. Recorrido sencillo, distinto del del limpiador."""
    assert jpeg[:2] == bytes([0xFF, 0xD8])
    i, r = 2, []
    while True:
        assert jpeg[i] == 0xFF
        m = jpeg[i + 1]
        if m == 0xD9:
            return r, jpeg[i + 2:]
        n = struct.unpack(">H", jpeg[i + 2:i + 4])[0]
        r.append((m, jpeg[i + 4:i + 2 + n]))
        i += 2 + n
        if m == 0xDA:
            j = i
            while not (jpeg[j] == 0xFF and jpeg[j + 1] not in (0x00, *range(0xD0, 0xD8))):
                j += 1
            r.append((0, jpeg[i:j]))
            i = j


def nucleo_jpeg(jpeg: bytes) -> list[tuple[int, bytes]]:
    s, _ = segmentos(jpeg)
    return [(m, c) for m, c in s if not (0xE0 <= m <= 0xEF or m == 0xFE)]


def apps(jpeg: bytes, marcador: int, prefijo: bytes = b"") -> list[bytes]:
    s, _ = segmentos(jpeg)
    return [c for m, c in s if m == marcador and c.startswith(prefijo)]


def chunks(png: bytes) -> list[tuple[str, bytes]]:
    assert png[:8] == bytes.fromhex("89504e470d0a1a0a")
    i, r = 8, []
    while i < len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        t = png[i + 4:i + 8].decode("ascii")
        assert zlib.crc32(png[i + 4:i + 8 + n]) == struct.unpack(">I", png[i + 8 + n:i + 12 + n])[0], t
        r.append((t, png[i + 8:i + 8 + n]))
        i += 12 + n
        if t == "IEND":
            break
    return r


def cola_png(png: bytes) -> bytes:
    return png[png.rindex(b"IEND") + 8:]


def claves_texto(png: bytes) -> list[str]:
    return sorted(c.split(NUL, 1)[0].decode("latin-1") for t, c in chunks(png) if t in ("tEXt", "zTXt", "iTXt"))


def xmp_de_png(png: bytes) -> bytes | None:
    for t, c in chunks(png):
        if t in ("iTXt", "tEXt") and c.startswith(b"XML:com.adobe.xmp" + NUL):
            resto = c.split(NUL, 1)[1]
            if t == "tEXt":
                return resto
            comp, resto = resto[0], resto[2:]
            resto = resto.split(NUL, 2)[2]
            return zlib.decompress(resto) if comp else resto
    return None


def iim_de(datos: bytes) -> list[tuple[int, int, bytes]]:
    r, i = [], 0
    while i < len(datos) and datos[i] == 0x1C:
        n = struct.unpack(">H", datos[i + 3:i + 5])[0]
        r.append((datos[i + 1], datos[i + 2], datos[i + 5:i + 5 + n]))
        i += 5 + n
    return r


def iptc_de_app13(carga: bytes) -> list[tuple[int, int, bytes]]:
    i = len(b"Photoshop 3.0") + 1
    while i < len(carga):
        ident = struct.unpack(">H", carga[i + 4:i + 6])[0]
        nombre = carga[i + 6]
        pos = i + 6 + 1 + nombre + ((1 + nombre) % 2)
        n = struct.unpack(">I", carga[pos:pos + 4])[0]
        if ident == 0x0404:
            return iim_de(carga[pos + 4:pos + 4 + n])
        i = pos + 4 + n + (n % 2)
    return []


# ---------------------------------------------------------------------------
# XMP de ejemplo

NS = (' xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/"'
      ' xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"'
      ' xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" xmlns:dcterms="http://purl.org/dc/terms/"'
      ' xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"')
DST = "http://cv.iptc.org/newscodes/digitalsourcetype/"


def paquete_xmp(cuerpo: str, atributos: str = "", xmptk: str | None = "Adobe XMP Core 9.1-c001") -> str:
    tk = f' x:xmptk="{xmptk}"' if xmptk else ""
    return (f'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            f'<x:xmpmeta xmlns:x="adobe:ns:meta/"{tk}>\n'
            f' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
            f'  <rdf:Description rdf:about=""{NS}{atributos}>\n'
            f'{cuerpo}'
            f'  </rdf:Description>\n'
            f' </rdf:RDF>\n'
            f'</x:xmpmeta>\n'
            f'<?xpacket end="w"?>')
