"""Imágenes JPEG y PNG: objetivos por campo, lo que se conserva, variantes omitidas y verificación.

Los esperados se escriben a mano (bytes exactos cuando el limpiador no pasa por ExifTool) y se
comprueban con los lectores de corpus_imagenes.py y con Pillow, no con el escáner que limpia.
"""

import json
import struct
import sys

import pytest

from aiclean import cli
from corpus_imagenes import (
    ASCII, DST, NUL, OTRO_UUID, SHORT, UNDEFINED, app1_exif, app1_xmp, app1_xmp_extendido,
    app1_xmp_extendido_trozos, extendido_de, app2_icc, app11_jumbf, app13_iptc, app14_adobe, apps, ascii_, cabx, chunk, chunks, com,
    comentario_usuario, iim_de, insertar_jpeg, insertar_png, iptc_de_app13, itxt, jpeg_base,
    nucleo_jpeg, paquete_xmp, perfil_iptc, pixeles, png_base, segmento, short, text, tiff, xmp_de_png, ztxt,
)

pytestmark = pytest.mark.exiftool        # preparar() de imágenes exige ExifTool en tools\

ID_XMP = b"http://ns.adobe.com/xap/1.0/" + NUL
ID_EXIF = b"Exif" + bytes(2)


def limpiar(tmp_path, nombre, datos, *args):
    """(código, resultado JSON, bytes de la salida o None). La salida va a tmp_path\\out."""
    entrada = tmp_path / nombre
    entrada.write_bytes(datos)
    informe = tmp_path / "i.json"
    codigo = cli.main([str(entrada), "-o", str(tmp_path / "out"), *args, "--report-file", str(informe)])
    res = json.loads(informe.read_text(encoding="utf-8"))["resultados"][0]
    salida = tmp_path / "out" / nombre
    return codigo, res, salida.read_bytes() if salida.exists() else None


def resumen(res):
    return [(h["categoria"], h["clave"], h["motivo"]) for h in res["hallazgos"]]


def sobrantes(carpeta):
    return sorted(p.name for p in carpeta.rglob("*") if ".aiclean-" in p.name)


# ---------------------------------------------------------------------------
# JPEG

ICC = app2_icc(b"perfil-icc-de-prueba" * 10)


@pytest.mark.parametrize("modo,opciones", [
    ("RGB", {}), ("RGB", {"progressive": True}), ("RGB", {"restart_marker_blocks": 1}),
    ("L", {}), ("CMYK", {}),
])
def test_jpeg_c2pa_se_quita_y_lo_demas_queda_byte_a_byte(tmp_path, modo, opciones):
    base = jpeg_base(modo, **opciones)
    original = insertar_jpeg(base, [*app11_jumbf(b"manifiesto" * 300, trozos=2), ICC, app14_adobe()])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio" and res["verificacion"] == "superada"
    assert resumen(res) == [("c2pa", "manifiesto JUMBF", "procedencia")]
    assert salida == insertar_jpeg(base, [ICC, app14_adobe()])          # el original sin los dos APP11
    assert apps(salida, 0xEB) == [] and nucleo_jpeg(salida) == nucleo_jpeg(original)
    assert pixeles(salida) == pixeles(original)


CUERPO_IA = (
    '   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Paisaje</rdf:li></rdf:Alt></dc:title>\n'
    '   <photoshop:Credit>Made with Google AI</photoshop:Credit>\n'
    '   <dcterms:provenance>https://cai-manifests.example.org/m/123</dcterms:provenance>\n'
    '   <xmpMM:History>\n'
    '    <rdf:Seq>\n'
    '     <rdf:li stEvt:action="created" stEvt:softwareAgent="Adobe Photoshop 25.0 (Windows)"/>\n'
    '     <rdf:li stEvt:action="edited" stEvt:softwareAgent="Adobe Firefly"/>\n'
    '    </rdf:Seq>\n'
    '   </xmpMM:History>\n'
)
ATRIBUTOS_IA = (f'\n   Iptc4xmpExt:DigitalSourceType="{DST}trainedAlgorithmicMedia"'
                '\n   xmp:CreatorTool="Adobe Firefly 3"\n   photoshop:City="Madrid"')
# A mano: sin los dos atributos de IA, sin Credit ni provenance, y sin la entrada de Firefly
CUERPO_LIMPIO = (
    '   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Paisaje</rdf:li></rdf:Alt></dc:title>\n'
    '   \n'
    '   \n'
    '   <xmpMM:History>\n'
    '    <rdf:Seq>\n'
    '     <rdf:li stEvt:action="created" stEvt:softwareAgent="Adobe Photoshop 25.0 (Windows)"/>\n'
    '     \n'
    '    </rdf:Seq>\n'
    '   </xmpMM:History>\n'
)
ATRIBUTOS_LIMPIOS = '\n   photoshop:City="Madrid"'
HALLAZGOS_XMP = [
    ("xmp", "Iptc4xmpExt:DigitalSourceType", "procedencia"),
    ("xmp", "xmp:CreatorTool", "generador"),
    ("xmp", "photoshop:Credit", "procedencia"),
    ("xmp", "dcterms:provenance", "procedencia"),
    ("xmp", "xmpMM:History (entradas con softwareAgent de IA)", "generador"),
]


def test_jpeg_xmp_por_campo_byte_a_byte(tmp_path):
    base = jpeg_base()
    original = insertar_jpeg(base, [app1_xmp(paquete_xmp(CUERPO_IA, ATRIBUTOS_IA))])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio"
    assert resumen(res) == HALLAZGOS_XMP
    assert [h["resumen"] for h in res["hallazgos"]] == [
        "trainedAlgorithmicMedia", "Generador: Adobe Firefly", "Firma: Google AI", "URL de procedencia",
        "Generador: Adobe Firefly"]
    assert salida == insertar_jpeg(base, [app1_xmp(paquete_xmp(CUERPO_LIMPIO, ATRIBUTOS_LIMPIOS))])


EXIF_IA = tiff([(0x010F, ASCII, ascii_("Canon")), (0x0131, ASCII, ascii_("Midjourney v6"))])


@pytest.mark.parametrize("xmptk", ["Adobe XMP Core 9.1-c001", None])
def test_xmptk_original_se_conserva_y_exiftool_no_anade_el_suyo(tmp_path, xmptk):
    """El EXIF obliga a pasar por ExifTool: el XMP tiene que salir exactamente como se editó."""
    base = jpeg_base()
    original = insertar_jpeg(base, [app1_exif(EXIF_IA), app1_xmp(paquete_xmp(CUERPO_IA, ATRIBUTOS_IA, xmptk))])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio"
    assert ("exif", "Software", "generador") in resumen(res)
    assert apps(salida, 0xE1, ID_XMP) == [ID_XMP + paquete_xmp(CUERPO_LIMPIO, ATRIBUTOS_LIMPIOS, xmptk).encode()]
    if xmptk is None:
        assert b"xmptk" not in salida and b"XMPToolkit" not in salida and b"ExifTool" not in salida


def test_xmp_sin_ia_queda_identico_aunque_pase_por_exiftool(tmp_path):
    paquete = paquete_xmp('   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Gato</rdf:li></rdf:Alt></dc:title>\n')
    original = insertar_jpeg(jpeg_base(), [app1_exif(EXIF_IA), app1_xmp(paquete)])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert res["estado"] == "limpio" and resumen(res) == [("exif", "Software", "generador")]
    assert apps(salida, 0xE1, ID_XMP) == [ID_XMP + paquete.encode()]


def test_all_quita_el_xmp_entero_con_su_xmptk(tmp_path):
    original = insertar_jpeg(jpeg_base(), [app1_xmp(paquete_xmp(CUERPO_IA, ATRIBUTOS_IA))])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original, "--all")
    assert res["estado"] == "limpio"
    assert apps(salida, 0xE1, ID_XMP) == [] and b"xmptk" not in salida
    assert ("xmp", "resto de propiedades", "amplio") in resumen(res)


MINIATURA = jpeg_base("L")
PROMPT = "a cat\nNegative prompt: ugly\nSteps: 20, Sampler: Euler a"
EXIF_COMPLETO = tiff(
    [(0x010F, ASCII, ascii_("Canon")), (0x0131, ASCII, ascii_("Midjourney v6")),
     (0x013B, ASCII, ascii_("Ana Garcia")), (0x010E, ASCII, ascii_(PROMPT)), (0x0112, SHORT, short(6))],
    exif=[(0x9286, UNDEFINED, comentario_usuario("Steps: 30, Sampler: DPM++ 2M, CFG scale: 7, Seed: 1")),
          (0x9003, ASCII, ascii_("2026:10:21 10:00:00"))],
    miniatura=MINIATURA,
)


def test_jpeg_exif_por_campo(tmp_path):
    from PIL import Image
    import io
    original = insertar_jpeg(jpeg_base(), [app1_exif(EXIF_COMPLETO)])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio"
    assert [(h["clave"], h["motivo"], h["resumen"]) for h in res["hallazgos"]] == [
        ("Software", "generador", "Generador: Midjourney"),
        ("ImageDescription", "procedencia", "prompt de 55 caracteres"),
        ("UserComment", "procedencia", "prompt de 51 caracteres"),
    ]
    exif = Image.open(io.BytesIO(salida)).getexif()
    assert {k: v for k, v in exif.items() if k != 0x8769} == {0x010F: "Canon", 0x013B: "Ana Garcia", 0x0112: 6}
    assert dict(exif.get_ifd(0x8769)) == {0x9003: "2026:10:21 10:00:00"}
    assert MINIATURA in apps(salida, 0xE1, ID_EXIF)[0]                   # la miniatura se queda
    assert pixeles(salida) == pixeles(original) and nucleo_jpeg(salida) == nucleo_jpeg(original)


def test_jpeg_exif_que_queda_vacio_desaparece_entero(tmp_path):
    base = jpeg_base()
    original = insertar_jpeg(base, [app1_exif(tiff([(0x0131, ASCII, ascii_("Midjourney"))])), ICC])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio" and res["verificacion"] == "superada"
    assert apps(salida, 0xE1, ID_EXIF) == [] and apps(salida, 0xE2) == apps(original, 0xE2)
    assert nucleo_jpeg(salida) == nucleo_jpeg(original)


def test_jpeg_all_deja_solo_orientation_y_lo_que_se_conserva(tmp_path):
    base = jpeg_base()
    original = insertar_jpeg(base, [app1_exif(EXIF_COMPLETO), app1_xmp(paquete_xmp(CUERPO_IA)), ICC,
                                    app13_iptc([(2, 25, b"gato")]), com("hola"), app14_adobe()])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original, "--all")
    assert codigo == 0 and res["estado"] == "limpio"
    minimo = (b"II*" + NUL + struct.pack("<I", 8) + struct.pack("<H", 1)
              + struct.pack("<HHI", 0x0112, 3, 1) + short(6) + bytes(2) + struct.pack("<I", 0))
    assert salida == insertar_jpeg(base, [app1_exif(minimo), ICC, app14_adobe()])


IPTC = [(1, 90, bytes([0x1B, 0x25, 0x47])), (2, 0, bytes([0, 4])), (2, 65, b"Midjourney"),
        (2, 80, b"Ana Garcia"), (2, 110, b"Made with Google AI"), (2, 120, b"Steps: 20, Sampler: Euler a, Seed: 5"),
        (2, 25, b"paisaje")]
RESOLUCION = (0x03ED, bytes(range(16)))


def test_jpeg_iptc_por_campo(tmp_path):
    original = insertar_jpeg(jpeg_base(), [app13_iptc(IPTC, [RESOLUCION])])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio"
    assert [(h["clave"], h["motivo"]) for h in res["hallazgos"]] == [
        ("OriginatingProgram", "generador"), ("Credit", "procedencia"), ("Caption-Abstract", "procedencia")]
    quedan = iptc_de_app13(apps(salida, 0xED)[0])
    # ExifTool añade 1:0 (EnvelopeRecordVersion = 4) al reescribir el IPTC: obligatorio en IIM y sin
    # datos del archivo; es la única adición que admite la verificación (junto con 2:0)
    assert sorted(quedan) == sorted([(1, 0, bytes([0, 4])), (1, 90, bytes([0x1B, 0x25, 0x47])),
                                     (2, 0, bytes([0, 4])), (2, 80, b"Ana Garcia"), (2, 25, b"paisaje")])
    assert bytes(range(16)) in apps(salida, 0xED)[0]                     # el otro recurso se queda


def test_jpeg_iptc_que_queda_vacio(tmp_path):
    original = insertar_jpeg(jpeg_base(), [app13_iptc([(2, 110, b"Made with Google AI")])])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio" and res["verificacion"] == "superada"
    assert all(b"Google" not in c for c in apps(salida, 0xED))
    assert nucleo_jpeg(salida) == nucleo_jpeg(original)


def test_png_iptc_que_queda_vacio(tmp_path):
    original = insertar_png(png_base(), antes_de_idat=[ztxt("Raw profile type iptc", perfil_iptc([(2, 110, b"Made with Google AI")]))])
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert codigo == 0 and res["estado"] == "limpio" and res["verificacion"] == "superada"
    assert b"Google" not in salida and pixeles(salida) == pixeles(original)


def test_jpeg_com_de_ia_se_quita_y_el_normal_se_queda(tmp_path):
    base = jpeg_base()
    original = insertar_jpeg(base, [com("Generated with AI by my bot"), com("Foto de mis vacaciones")])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert resumen(res) == [("comentario", "Comentario (COM)", "procedencia")]
    assert salida == insertar_jpeg(base, [com("Foto de mis vacaciones")])
    codigo, res, salida = limpiar(tmp_path, "b.jpg", original, "--all")
    assert salida == base


def test_jpeg_cola_se_conserva_y_con_all_queda_parcial(tmp_path):
    base = jpeg_base()
    original = insertar_jpeg(base, [com("Made with AI")], cola=b"DATOS-TRAS-EOI")
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio"
    assert salida == insertar_jpeg(base, [], cola=b"DATOS-TRAS-EOI")
    codigo, res, salida = limpiar(tmp_path, "b.jpg", original, "--all")
    assert codigo == 4 and res["estado"] == "parcial"
    assert [h["clave"] for h in res["hallazgos"] if not h["tratado"]] == ["Datos tras el final de la imagen (EOI)"]
    assert salida == insertar_jpeg(base, [], cola=b"DATOS-TRAS-EOI")


def _sof(base: bytes, marcador: int) -> bytes:
    i = base.index(bytes([0xFF, 0xC0]))
    return base[:i + 1] + bytes([marcador]) + base[i + 2:]


HDRGM = paquete_xmp("", ' xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" hdrgm:Version="1.0"')
MOTION = paquete_xmp("", ' xmlns:GCamera="http://ns.google.com/photos/1.0/camera/" GCamera:MotionPhoto="1"')


@pytest.mark.parametrize("nombre,construir,mensaje", [
    ("mpf", lambda b: insertar_jpeg(b, [segmento(0xE2, b"MPF" + NUL + bytes(20))]),
     "JPEG con varias imágenes (MPF/MPO): no soportado"),
    ("mpf_y_gainmap", lambda b: insertar_jpeg(b, [segmento(0xE2, b"MPF" + NUL + bytes(20)),        # HDR de iPhone
                                                  segmento(0xE2, b"urn:iso:std:iso:ts:21496:-1" + NUL + b"x")]),
     "JPEG Ultra HDR (mapa de ganancia ISO 21496-1): no soportado"),
    ("gainmap", lambda b: insertar_jpeg(b, [segmento(0xE2, b"urn:iso:std:iso:ts:21496:-1" + NUL + b"x")]),
     "JPEG Ultra HDR (mapa de ganancia ISO 21496-1): no soportado"),
    ("hdrgm", lambda b: insertar_jpeg(b, [app1_xmp(HDRGM)]), "JPEG Ultra HDR (mapa de ganancia): no soportado"),
    ("motion", lambda b: insertar_jpeg(b, [app1_xmp(MOTION)]), "foto en movimiento (Motion Photo): no soportado"),
    ("aritmetico", lambda b: _sof(b, 0xC9), "JPEG aritmético: variante que el verificador no cubre"),
    ("sin_perdida", lambda b: _sof(b, 0xC3), "JPEG sin pérdida: variante que el verificador no cubre"),
    ("jpeg_xt", lambda b: insertar_jpeg(b, app11_jumbf(b"capa hdr", uuid=OTRO_UUID, en=2)),
     "APP11 (JUMBF) que no es C2PA, p. ej. JPEG XT: no soportado"),
    ("jumbf_ambiguo", lambda b: insertar_jpeg(b, app11_jumbf(b"capa hdr", uuid=OTRO_UUID, en=1)),
     "APP11 con cajas JUMBF ambiguas (mismo número de caja): no se modifica"),
    ("truncado", lambda b: b[:-40], "estructura JPEG no válida (datos de imagen sin final): no se modifica"),
    ("longitud", lambda b: insertar_jpeg(b, [bytes([0xFF, 0xE5]) + struct.pack(">H", 60000) + b"x"]),
     "estructura JPEG no válida (longitud de segmento imposible): no se modifica"),
])
def test_jpeg_variantes_que_se_omiten(tmp_path, nombre, construir, mensaje):
    datos = construir(insertar_jpeg(jpeg_base(), app11_jumbf(b"m" * 50)))
    codigo, res, salida = limpiar(tmp_path, f"{nombre}.jpg", datos)
    assert codigo == 0 and res["estado"] == "omitido" and res["mensaje"] == mensaje
    assert salida is None and (tmp_path / f"{nombre}.jpg").read_bytes() == datos


def _md5(datos: bytes) -> bytes:
    import hashlib
    return hashlib.md5(datos).hexdigest().upper().encode()


def _principal(guid: bytes, extra: str = "") -> str:
    return paquete_xmp("", f' xmlns:xmpNote="http://ns.adobe.com/xmp/note/" xmpNote:HasExtendedXMP="{guid.decode()}"{extra}')


EXT_IA = paquete_xmp(CUERPO_IA, ATRIBUTOS_IA, None).encode()
EXT_LIMPIO = paquete_xmp(CUERPO_LIMPIO, ATRIBUTOS_LIMPIOS, None).encode()      # a mano, como en el XMP normal


def test_jpeg_xmp_extendido_con_ia_se_edita_y_se_recalcula_el_guid(tmp_path):
    base = jpeg_base()
    viejo, nuevo = _md5(EXT_IA), _md5(EXT_LIMPIO)
    original = insertar_jpeg(base, [app1_xmp(_principal(viejo)), app1_xmp_extendido(viejo, EXT_IA)])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 0 and res["estado"] == "limpio" and res["verificacion"] == "superada"
    assert [(c, k, m) for c, k, m in resumen(res)] == HALLAZGOS_XMP
    assert all(h["ubicacion"] == "XMP extendido (APP1)" for h in res["hallazgos"])
    assert salida == insertar_jpeg(base, [app1_xmp(_principal(nuevo)), app1_xmp_extendido(nuevo, EXT_LIMPIO)])
    assert extendido_de(salida) == {nuevo: EXT_LIMPIO} and _md5(EXT_LIMPIO) == nuevo


def test_jpeg_xmp_extendido_grande_se_parte_en_trozos_coherentes(tmp_path):
    relleno = "   <dc:description><rdf:Alt><rdf:li xml:lang=\"x-default\">" + "texto largo " * 12000 + "</rdf:li></rdf:Alt></dc:description>\n"
    ext = paquete_xmp(relleno + CUERPO_IA, ATRIBUTOS_IA, None).encode()
    esperado = paquete_xmp(relleno + CUERPO_LIMPIO, ATRIBUTOS_LIMPIOS, None).encode()
    viejo = _md5(ext)
    original = insertar_jpeg(jpeg_base(), [app1_xmp(_principal(viejo)),
                                           *app1_xmp_extendido_trozos(viejo, ext, [0, 60000, 120000])])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert res["estado"] == "limpio" and res["verificacion"] == "superada"
    assert extendido_de(salida) == {_md5(esperado): esperado}
    assert len(apps(salida, 0xE1, b"http://ns.adobe.com/xmp/extension/")) == 3        # 144 KB en trozos de APP1
    principal = apps(salida, 0xE1, ID_XMP)[0]
    assert b'HasExtendedXMP="' + _md5(esperado) + b'"' in principal


def test_jpeg_xmp_extendido_sin_ia_queda_igual_aunque_se_edite_el_principal(tmp_path):
    ext = paquete_xmp('   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Gato</rdf:li></rdf:Alt></dc:title>\n', "", None).encode()
    guid = _md5(ext)
    principal_ia = _principal(guid, f' Iptc4xmpExt:DigitalSourceType="{DST}trainedAlgorithmicMedia"')
    base = jpeg_base()
    original = insertar_jpeg(base, [app1_xmp(principal_ia), app1_xmp_extendido(guid, ext)])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert res["estado"] == "limpio"
    assert salida == insertar_jpeg(base, [app1_xmp(_principal(guid)), app1_xmp_extendido(guid, ext)])


@pytest.mark.parametrize("nombre,construir,mensaje", [
    ("md5", lambda: [app1_xmp(_principal(b"0" * 32)), app1_xmp_extendido(b"0" * 32, EXT_IA)],
     "XMP extendido incoherente (su MD5 no coincide con el GUID): no se modifica"),
    ("sin_referencia", lambda: [app1_xmp(paquete_xmp("")), app1_xmp_extendido(_md5(EXT_IA), EXT_IA)],
     "XMP extendido sin referencia en el XMP principal: no se modifica"),
])
def test_jpeg_xmp_extendido_que_no_se_puede_garantizar_se_omite(tmp_path, nombre, construir, mensaje):
    datos = insertar_jpeg(jpeg_base(), construir())
    codigo, res, salida = limpiar(tmp_path, f"{nombre}.jpg", datos)
    assert res["estado"] == "omitido" and res["mensaje"] == mensaje and salida is None


def test_jpeg_xmp_extendido_incompleto_no_se_toca_y_se_informa(tmp_path):
    guid = _md5(EXT_IA)
    trozos = app1_xmp_extendido_trozos(guid, EXT_IA, [0, 100, 200])
    original = insertar_jpeg(jpeg_base(), [app1_xmp(_principal(guid)), trozos[0], trozos[2]])   # falta un trozo
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 4 and res["estado"] == "parcial" and salida is None
    assert [(h["clave"], h["tratado"]) for h in res["hallazgos"]] == [("XMP extendido ilegible", False)]


def test_xmp_atributos_se_situan_leyendo_la_etiqueta(tmp_path):
    """Un valor que contiene el texto de otro atributo no confunde al lector."""
    trampa = f" photoshop:Instructions='nota Iptc4xmpExt:DigitalSourceType=\"{DST}trainedAlgorithmicMedia\" fin'"
    real = f'\n   Iptc4xmpExt:DigitalSourceType="{DST}trainedAlgorithmicMedia"'
    base = png_base()
    original = insertar_png(base, antes_de_idat=[itxt("XML:com.adobe.xmp", paquete_xmp("", trampa + real))])
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert res["estado"] == "limpio" and resumen(res) == [("xmp", "Iptc4xmpExt:DigitalSourceType", "procedencia")]
    assert salida == insertar_png(base, antes_de_idat=[itxt("XML:com.adobe.xmp", paquete_xmp("", trampa))])


def test_xmp_editado_es_xml_valido_con_su_xpacket_y_crc(tmp_path):
    import xml.etree.ElementTree as ET
    import zlib
    original = insertar_png(png_base(), antes_de_idat=[itxt("XML:com.adobe.xmp", paquete_xmp(CUERPO_IA, ATRIBUTOS_IA))])
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert res["estado"] == "limpio"
    paquete = xmp_de_png(salida).decode("utf-8")
    assert paquete.startswith('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>') and paquete.endswith('<?xpacket end="w"?>')
    ET.fromstring(paquete.split("?>", 1)[1].rsplit("<?xpacket", 1)[0])           # XML bien formado
    i = salida.index(b"iTXt")
    n = struct.unpack(">I", salida[i - 4:i])[0]
    assert struct.unpack(">I", salida[i + 4 + n:i + 8 + n])[0] == zlib.crc32(salida[i:i + 4 + n])   # CRC recalculado


def test_jpeg_exif_duplicado_con_ia_queda_no_tratado(tmp_path):
    otro = tiff([(0x0131, ASCII, ascii_("Stable Diffusion"))])
    original = insertar_jpeg(jpeg_base(), [app1_exif(tiff([(0x010F, ASCII, ascii_("Canon"))])), app1_exif(otro)])
    codigo, res, salida = limpiar(tmp_path, "a.jpg", original)
    assert codigo == 4 and res["estado"] == "parcial" and salida is None
    assert [h["clave"] for h in res["hallazgos"]] == ["EXIF duplicado con datos de IA"]


# ---------------------------------------------------------------------------
# PNG

XMP_PNG_IA = paquete_xmp("", f'\n   Iptc4xmpExt:DigitalSourceType="{DST}trainedAlgorithmicMedia"')


@pytest.mark.parametrize("modo", ["P", "L", "LA", "RGB", "RGBA", "I;16"])
def test_png_variantes_c2pa_texto_y_xmp_byte_a_byte(tmp_path, modo):
    base = png_base(modo)
    original = insertar_png(base, antes_de_idat=[
        cabx(b"m" * 500), text("parameters", "a cat\nSteps: 20, Sampler: Euler a"), itxt("XML:com.adobe.xmp", XMP_PNG_IA)])
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert codigo == 0 and res["estado"] == "limpio" and res["verificacion"] == "superada"
    assert resumen(res) == [("c2pa", "manifiesto JUMBF", "procedencia"),
                            ("png", "texto «parameters»", "procedencia"),
                            ("xmp", "Iptc4xmpExt:DigitalSourceType", "procedencia")]
    assert salida == insertar_png(base, antes_de_idat=[itxt("XML:com.adobe.xmp", paquete_xmp("", ""))])
    assert pixeles(salida) == pixeles(original)


COMFY_PROMPT = '{"3": {"class_type": "KSampler", "inputs": {"seed": 1}}, "4": {"class_type": "CheckpointLoaderSimple", "inputs": {}}}'
COMFY_WORKFLOW = '{"last_node_id": 4, "nodes": [{"id": 3, "type": "KSampler", "widgets_values": [1, 20]}]}'
NOVELAI = '{"prompt": "a cat", "steps": 28, "sampler": "k_euler", "seed": 1, "uc": "bad"}'
MJ = "a cat on a sofa Job ID: 0f1e2d3c-4b5a-6978-8091-a2b3c4d5e6f7"


def test_png_textos_por_clave_y_valor(tmp_path):
    base = png_base()
    se_quedan = [text("Comment", "Hecho con GIMP"), text("Description", "Atardecer en la playa"),
                 text("Software", "GIMP 2.10"), text("Title", "Gato"), text("Copyright", "Ana 2026")]
    se_van = [text("Comment", NOVELAI), text("Description", MJ), text("Source", "NovelAI Diffusion V4 5AA3D9F7"),
              text("Software", "NovelAI"), ztxt("workflow", COMFY_WORKFLOW), ztxt("parameters", PROMPT),
              itxt("prompt", COMFY_PROMPT, comprimido=True), text("parameters", "x" * 65536 + "\nSteps: 20, Sampler: Euler a"),
              text("Software", "Matplotlib version3.9.0, https://matplotlib.org/")]
    original = insertar_png(base, antes_de_idat=[se_quedan[0], se_van[0], se_quedan[1], se_van[1], se_van[2],
                                                 se_quedan[2], se_van[3], se_quedan[3], *se_van[4:], se_quedan[4]])
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert codigo == 0 and res["estado"] == "limpio"
    assert [(h["clave"], h["motivo"], h["resumen"]) for h in res["hallazgos"]] == [
        ("texto «Comment»", "procedencia", f"prompt de {len(NOVELAI)} caracteres"),
        ("texto «Description»", "procedencia", "Firma: Midjourney"),
        ("texto «Source»", "generador", "Generador: NovelAI"),
        ("texto «Software»", "generador", "Generador: NovelAI"),
        ("texto «workflow»", "procedencia", f"parámetros de generación ({len(COMFY_WORKFLOW)} caracteres)"),
        ("texto «parameters»", "procedencia", f"parámetros de generación ({len(PROMPT)} caracteres)"),
        ("texto «prompt»", "procedencia", f"parámetros de generación ({len(COMFY_PROMPT)} caracteres)"),
        ("texto «parameters»", "procedencia", "parámetros de generación (65564 caracteres)"),
        ("texto «Software»", "generador", "Generador: Matplotlib"),
    ]
    assert salida == insertar_png(base, antes_de_idat=se_quedan)


SBIT, BKGD, GAMA = chunk("sBIT", bytes([8, 8, 8])), chunk("bKGD", bytes(6)), chunk("gAMA", struct.pack(">I", 45455))
PHYS = chunk("pHYs", struct.pack(">IIB", 3780, 3780, 1))
CHRM = chunk("cHRM", bytes(32))
SRGB = chunk("sRGB", bytes([0]))
ICCP = chunk("iCCP", b"perfil" + NUL + NUL + bytes.fromhex("789c4b4e4e060000025b0130"))
VPAG = chunk("vpAg", bytes(9))


def test_png_all_quita_lo_prescindible_y_conserva_color_y_desconocidos(tmp_path):
    base = png_base()
    exif = tiff([(0x0131, ASCII, ascii_("GIMP")), (0x0112, SHORT, short(3))])
    original = insertar_png(base, antes_de_idat=[PHYS, SBIT, BKGD, GAMA, CHRM, SRGB, ICCP, chunk("tIME", bytes(7)),
                                                 chunk("eXIf", exif), text("Title", "Gato"), VPAG],
                            cola=b"DATOS-TRAS-IEND")
    codigo, res, salida = limpiar(tmp_path, "a.png", original, "--all")
    assert codigo == 4 and res["estado"] == "parcial"                    # el chunk desconocido se informa
    assert [(h["clave"], h["tratado"]) for h in res["hallazgos"] if not h["tratado"]] == [
        ("Chunk auxiliar desconocido (vpAg)", False)]
    minimo = (b"II*" + NUL + struct.pack("<I", 8) + struct.pack("<H", 1)
              + struct.pack("<HHI", 0x0112, 3, 1) + short(3) + bytes(2) + struct.pack("<I", 0))
    assert salida == insertar_png(base, antes_de_idat=[PHYS, SBIT, BKGD, GAMA, CHRM, SRGB, ICCP, chunk("eXIf", minimo), VPAG])


def test_png_cola_se_conserva_en_modo_ia(tmp_path):
    base = png_base()
    original = insertar_png(base, antes_de_idat=[cabx(b"m")], cola=b"COLA")
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert res["estado"] == "limpio" and salida == base + b"COLA"


def _idat_partido(base: bytes, entre: bytes) -> bytes:
    c = dict(chunks(base))
    idat = c["IDAT"]
    i = base.index(b"IDAT") - 4
    fin = i + 12 + len(idat)
    return base[:i] + chunk("IDAT", idat[:3]) + entre + chunk("IDAT", idat[3:]) + base[fin:]


def _crc_roto(base: bytes) -> bytes:
    i = base.index(b"IDAT") - 4
    n = struct.unpack(">I", base[i:i + 4])[0]
    j = i + 8 + n
    return base[:j] + bytes([base[j] ^ 0xFF]) + base[j + 1:]


@pytest.mark.parametrize("nombre,construir,mensaje", [
    ("apng", lambda b: insertar_png(b, tras_ihdr=[chunk("acTL", struct.pack(">II", 1, 0))]),
     "PNG animado (APNG): no soportado"),
    ("dsig", lambda b: insertar_png(b, antes_de_iend=[chunk("dSIG", b"firma")]), "PNG firmado (dSIG): no se modifica"),
    ("crc", _crc_roto, "PNG con CRC inválido (chunk IDAT): no se modifica"),
    ("critico", lambda b: insertar_png(b, antes_de_idat=[chunk("QQQQ", b"x")]),
     "PNG con un chunk crítico desconocido (QQQQ): no se modifica"),
    ("idat", lambda b: _idat_partido(b, text("Comment", "x")), "estructura PNG no válida (IDAT)"),
    ("truncado", lambda b: b[:-12], "estructura PNG no válida (falta IEND): no se modifica"),
])
def test_png_variantes_que_se_omiten(tmp_path, nombre, construir, mensaje):
    datos = construir(insertar_png(png_base(), antes_de_idat=[cabx(b"m")]))
    codigo, res, salida = limpiar(tmp_path, f"{nombre}.png", datos)
    assert codigo == 0 and res["estado"] == "omitido" and res["mensaje"] == mensaje
    assert salida is None and (tmp_path / f"{nombre}.png").read_bytes() == datos


def test_png_bomba_de_compresion_no_se_descomprime(tmp_path):
    bomba = ztxt("Comment", bytes(65 * 1024 * 1024))
    original = insertar_png(png_base(), antes_de_idat=[bomba])
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert codigo == 4 and res["estado"] == "parcial" and salida is None
    assert [(h["clave"], h["resumen"]) for h in res["hallazgos"]] == [("texto «Comment»", "comprimido dañado o demasiado grande")]
    codigo, res, salida = limpiar(tmp_path, "b.png", original, "--all")
    assert res["estado"] == "limpio" and salida == png_base()


def test_png_exif_por_campo_con_exiftool(tmp_path):
    from PIL import Image
    import io
    exif = tiff([(0x0131, ASCII, ascii_("Midjourney")), (0x0112, SHORT, short(3)), (0x010F, ASCII, ascii_("Canon"))])
    original = insertar_png(png_base(), antes_de_idat=[chunk("eXIf", exif)])
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert res["estado"] == "limpio" and resumen(res) == [("exif", "Software", "generador")]
    assert dict(Image.open(io.BytesIO(salida)).getexif()) == {0x0112: 3, 0x010F: "Canon"}
    assert pixeles(salida) == pixeles(original)


def test_png_iptc_en_perfil_de_texto(tmp_path):
    perfil = perfil_iptc([(2, 110, b"Made with Google AI"), (2, 25, b"gato")])
    original = insertar_png(png_base(), antes_de_idat=[ztxt("Raw profile type iptc", perfil)])
    codigo, res, salida = limpiar(tmp_path, "a.png", original)
    assert res["estado"] == "limpio" and [(h["clave"], h["motivo"]) for h in res["hallazgos"]] == [("Credit", "procedencia")]
    textos = [c for t, c in chunks(salida) if t == "zTXt" and c.startswith(b"Raw profile type iptc")]
    import zlib
    cuerpo = zlib.decompress(textos[0].split(NUL, 1)[1][1:]).decode("latin-1").split("\n")
    crudo = bytes.fromhex("".join(cuerpo[3:]))
    assert (2, 25, b"gato") in iim_de(crudo[crudo.index(bytes([0x1C])):])
    assert b"Made with Google AI" not in crudo


def test_png_xmp_en_text_y_ztxt_se_edita(tmp_path):
    import zlib
    base = png_base()
    original = insertar_png(base, antes_de_idat=[text("XML:com.adobe.xmp", XMP_PNG_IA)])   # así lo guarda Midjourney
    codigo, res, salida = limpiar(tmp_path, "t.png", original)
    assert res["estado"] == "limpio"
    assert salida == insertar_png(base, antes_de_idat=[text("XML:com.adobe.xmp", paquete_xmp("", ""))])
    original = insertar_png(base, antes_de_idat=[ztxt("XML:com.adobe.xmp", XMP_PNG_IA)])
    codigo, res, salida = limpiar(tmp_path, "z.png", original)
    assert res["estado"] == "limpio"
    # Se recomprime (el nivel original no se conoce): se compara el texto descomprimido
    resto = [(t, c) for t, c in chunks(salida) if t != "zTXt"]
    assert resto == [(t, c) for t, c in chunks(base)]
    [z] = [c for t, c in chunks(salida) if t == "zTXt"]
    clave, cuerpo = z.split(NUL, 1)
    assert clave == b"XML:com.adobe.xmp" and cuerpo[:1] == NUL
    assert zlib.decompress(cuerpo[1:]) == paquete_xmp("", "").encode("latin-1")


# ---------------------------------------------------------------------------
# Comunes: idempotencia, dry-run, privacidad, --in-place, nombres, -j

def _corpus():
    jpeg = insertar_jpeg(jpeg_base(), [*app11_jumbf(b"m" * 100), app1_exif(EXIF_COMPLETO),
                                       app1_xmp(paquete_xmp(CUERPO_IA, ATRIBUTOS_IA)), app13_iptc(IPTC), com("Made with AI"), ICC])
    png = insertar_png(png_base("RGBA"), antes_de_idat=[cabx(b"m"), text("parameters", PROMPT), itxt("XML:com.adobe.xmp", XMP_PNG_IA),
                                                        chunk("eXIf", EXIF_IA), text("Comment", "normal")], cola=b"COLA")
    return {"a.jpg": jpeg, "b.png": png}


@pytest.mark.parametrize("extra", [[], ["--all"]])
def test_segunda_pasada_no_encuentra_nada(tmp_path, extra):
    for nombre, datos in _corpus().items():
        codigo, res, salida = limpiar(tmp_path, nombre, datos, *extra)
        assert res["estado"] == "limpio", (nombre, res["mensaje"])
        (tmp_path / "otra").mkdir(exist_ok=True)
        codigo, res2, salida2 = limpiar(tmp_path / "otra", nombre, salida, *extra)
        assert res2["estado"] == "sin_cambios" and salida2 is None, (nombre, res2)


def test_dry_run_no_escribe_nada(tmp_path):
    for nombre, datos in _corpus().items():
        codigo, res, salida = limpiar(tmp_path, nombre, datos, "--dry-run")
        assert codigo == 3 and res["estado"] == "encontrado" and res["accion"] == "prevista" and salida is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.jpg", "b.png", "i.json"]


MARCAS = ("PROMPT-SECRETO-4471", "ARTISTA-SECRETO-4471", "dominio-secreto-4471")


def _con_secretos():
    exif = tiff([(0x013B, ASCII, ascii_("Midjourney " + MARCAS[1])), (0x010E, ASCII, ascii_(f"{MARCAS[0]}\nSteps: 20, Sampler: Euler a"))])
    xmp_ = paquete_xmp(f'   <dcterms:provenance>https://{MARCAS[2]}.example.org/x</dcterms:provenance>\n')
    return insertar_jpeg(jpeg_base(), [app1_exif(exif), app1_xmp(xmp_)])


@pytest.mark.parametrize("extra", [[], ["--dry-run"], ["--all"]])
def test_privacidad_sin_report_sensitive(tmp_path, capsys, extra):
    codigo, res, _ = limpiar(tmp_path, "a.jpg", _con_secretos(), "--report", *extra)
    salida = capsys.readouterr()
    informe = (tmp_path / "i.json").read_text(encoding="utf-8")
    for marca in MARCAS:
        assert marca not in salida.out and marca not in salida.err and marca not in informe


def test_privacidad_con_report_sensitive(tmp_path, capsys):
    limpiar(tmp_path, "a.jpg", _con_secretos(), "--dry-run", "--report-sensitive")
    salida = capsys.readouterr().out
    assert all(marca in salida for marca in MARCAS)


ntfs = pytest.mark.skipif(sys.platform != "win32", reason="flujos NTFS")


@ntfs
def test_in_place_con_imagen_y_marca_de_la_web(tmp_path):
    base = jpeg_base()
    a = tmp_path / "a.jpg"
    a.write_bytes(insertar_jpeg(base, app11_jumbf(b"m" * 100)))
    with open(f"{a}:Zone.Identifier", "wb") as f:
        f.write(b"[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=https://chatgpt.com/x\r\n")
    assert cli.main([str(a), "--in-place"]) == 0
    assert a.read_bytes() == base
    with open(f"{a}:Zone.Identifier", "rb") as f:
        assert f.read() == b"[ZoneTransfer]\r\nZoneId=3\r\n"
    assert sobrantes(tmp_path) == []


@ntfs
def test_in_place_imagen_con_flujo_extra_se_omite(tmp_path):
    a = tmp_path / "a.png"
    datos = insertar_png(png_base(), antes_de_idat=[cabx(b"m")])
    a.write_bytes(datos)
    with open(f"{a}:extra", "wb") as f:
        f.write(b"x")
    assert cli.main([str(a), "--in-place"]) == 0
    assert a.read_bytes() == datos


def test_nombre_con_caracteres_no_ascii_pasa_por_exiftool(tmp_path):
    nombre = "ñandú " + chr(0x1F600) + ".jpg"
    codigo, res, salida = limpiar(tmp_path, nombre, insertar_jpeg(jpeg_base(), [app1_exif(EXIF_IA)]))
    assert codigo == 0 and res["estado"] == "limpio" and b"Midjourney" not in salida
    assert sobrantes(tmp_path) == []


def test_varios_hilos_cierran_todos_los_procesos(tmp_path, monkeypatch):
    from aiclean.herramientas import exiftool
    creados = []
    original_init = exiftool._Proceso.__init__

    def init(self, *a, **k):
        original_init(self, *a, **k)
        creados.append(self)

    monkeypatch.setattr(exiftool._Proceso, "__init__", init)
    for i in range(8):
        (tmp_path / f"f{i}.jpg").write_bytes(insertar_jpeg(jpeg_base(), [app1_exif(EXIF_IA)]))
    assert cli.main([str(tmp_path), "-j", "4"]) == 0
    assert creados and all(p.p.poll() is not None for p in creados)
    assert len({id(p) for p in creados}) <= 4
    assert all(not (tmp_path / f"f{i}_clean.jpg").read_bytes().count(b"Midjourney") for i in range(8))



# --- Author de Midjourney -----------------------------------------------------

def test_author_se_quita_en_modo_ia_solo_si_el_png_es_de_midjourney(tmp_path, capsys):
    base = png_base()
    original = insertar_png(base, antes_de_idat=[text("Author", "usuario4471"), text("Description", MJ),
                                                 text("Title", "Gato")])
    codigo, res, salida = limpiar(tmp_path, "mj.png", original)
    assert res["estado"] == "limpio"
    assert [(h["clave"], h["motivo"], h["resumen"]) for h in res["hallazgos"]] == [
        ("texto «Description»", "procedencia", "Firma: Midjourney"),
        ("texto «Author»", "procedencia", "usuario del servicio (11 caracteres)")]
    assert salida == insertar_png(base, antes_de_idat=[text("Title", "Gato")])
    informe = (tmp_path / "i.json").read_text(encoding="utf-8")
    assert "usuario4471" not in informe and "usuario4471" not in capsys.readouterr().out
    codigo, res, salida = limpiar(tmp_path, "mj2.png", original, "--all")
    assert ("png", "texto «Author»", "procedencia") in resumen(res) and ("png", "texto «Author»", "amplio") not in resumen(res)
    assert salida == base


def test_author_de_otros_png_solo_con_all(tmp_path):
    base = png_base()
    original = insertar_png(base, antes_de_idat=[text("Author", "Ana"), text("parameters", PROMPT)])
    codigo, res, salida = limpiar(tmp_path, "sd.png", original)
    assert res["estado"] == "limpio" and [h["clave"] for h in res["hallazgos"]] == ["texto «parameters»"]
    assert salida == insertar_png(base, antes_de_idat=[text("Author", "Ana")])
    codigo, res, salida = limpiar(tmp_path, "sd2.png", original, "--all")
    assert ("png", "texto «Author»", "amplio") in resumen(res) and salida == base
