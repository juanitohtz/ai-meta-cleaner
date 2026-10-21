"""XMP compartido: lectura segura y borrado por campo sin reserializar el paquete.

- Se lee con expat de la biblioteca estándar, rechazando DOCTYPE y entidades (sin entidades
  externas ni bombas de expansión) y con un tamaño máximo.
- Los espacios de nombres se resuelven aquí para conocer los prefijos y la posición exacta en
  bytes de cada propiedad: los elementos, por las posiciones que da el parser; los atributos,
  leyendo la sintaxis de la etiqueta de apertura (nombre = valor entre comillas), nunca buscando
  texto. Borrar un campo es recortar sus bytes: el resto del paquete (incluido x:xmptk, el
  XMPToolkit original, y las instrucciones <?xpacket?>) queda idéntico byte a byte, y el resultado
  se vuelve a leer para comprobar que sigue siendo XML válido con el mismo xpacket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.parsers import expat

from aiclean import firmas

NS_X = "adobe:ns:meta/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_IPTC_EXT = "http://iptc.org/std/Iptc4xmpExt/2008-02-29/"
NS_XMP = "http://ns.adobe.com/xap/1.0/"
NS_XMPMM = "http://ns.adobe.com/xap/1.0/mm/"
NS_STEVT = "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"
NS_DCTERMS = "http://purl.org/dc/terms/"
NS_PHOTOSHOP = "http://ns.adobe.com/photoshop/1.0/"
NS_XMPNOTE = "http://ns.adobe.com/xmp/note/"
NS_HDRGM = "http://ns.adobe.com/hdr-gain-map/1.0/"
NS_GCONTAINER_ITEM = "http://ns.google.com/photos/1.0/container/item/"
NS_GCAMERA = "http://ns.google.com/photos/1.0/camera/"

TAMANO_MAXIMO = 16 * 1024 * 1024

_PREFIJOS = {
    NS_IPTC_EXT: "Iptc4xmpExt", NS_XMP: "xmp", NS_XMPMM: "xmpMM", NS_DCTERMS: "dcterms",
    NS_PHOTOSHOP: "photoshop",
}


class ErrorXMP(Exception):
    pass


@dataclass
class Nodo:
    uri: str
    local: str
    qname: str
    atributos: dict[tuple[str, str], tuple[str, str]]     # (uri, local) -> (qname, valor)
    inicio: int                                             # byte del "<"
    fin_etiqueta: int                                       # byte tras el ">" de la etiqueta de apertura
    fin: int = -1                                           # byte tras el elemento entero
    texto: str = ""
    hijos: list["Nodo"] = field(default_factory=list)
    # (uri, local) -> (inicio con el espacio previo, fin, inicio del valor, fin del valor)
    posiciones: dict[tuple[str, str], tuple[int, int, int, int]] = field(default_factory=dict)
    inicio_cierre: int = -1                                 # byte del "</" (si no es <x/>)


@dataclass
class Documento:
    datos: bytes
    raiz: Nodo
    instrucciones: list[tuple[str, str]] = field(default_factory=list)   # <?xpacket ...?> y demás

    def nodos(self):
        pila = [self.raiz]
        while pila:
            n = pila.pop()
            yield n
            pila.extend(reversed(n.hijos))

    @property
    def xmptk(self) -> str | None:
        for n in self.nodos():
            if (n.uri, n.local) == (NS_X, "xmpmeta"):
                valor = n.atributos.get((NS_X, "xmptk"))
                return valor[1] if valor else None
        return None


@dataclass
class Objetivo:
    """Una propiedad (o entradas de una lista) que se quita, con los bytes que ocupa."""
    clave: str
    valor: str
    resumen: str
    motivo: str
    tramos: list[tuple[int, int]]
    cantidad: int = 1


def analizar(datos: bytes) -> Documento:
    if len(datos) > TAMANO_MAXIMO:
        raise ErrorXMP("XMP demasiado grande")
    if datos[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in datos[:64]:
        raise ErrorXMP("XMP que no está en UTF-8")
    p = expat.ParserCreate()
    p.buffer_text = True
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    raiz_virtual = Nodo("", "", "", {}, 0, 0)
    pila: list[Nodo] = [raiz_virtual]
    instrucciones: list[tuple[str, str]] = []
    espacios: list[dict[str, str]] = [{"xml": "http://www.w3.org/XML/1998/namespace"}]

    def prohibido(*_):
        raise ErrorXMP("XMP con DOCTYPE o entidades")

    def resolver(qname: str, ambito: dict[str, str], atributo: bool) -> tuple[str, str]:
        prefijo, _, local = qname.rpartition(":")
        if not prefijo:
            return ("" if atributo else ambito.get("", "")), local
        if prefijo not in ambito:
            raise ErrorXMP("XMP con un prefijo sin declarar")
        return ambito[prefijo], local

    def inicio(nombre: str, attrs: dict[str, str]):
        ambito = dict(espacios[-1])
        for k, v in attrs.items():
            if k == "xmlns":
                ambito[""] = v
            elif k.startswith("xmlns:"):
                ambito[k[6:]] = v
        espacios.append(ambito)
        pos = p.CurrentByteIndex
        fin_etiqueta = _fin_de_etiqueta(datos, pos)
        uri, local = resolver(nombre, ambito, False)
        atributos = {
            resolver(k, ambito, True): (k, v) for k, v in attrs.items()
            if k != "xmlns" and not k.startswith("xmlns:")
        }
        nodo = Nodo(uri, local, nombre, atributos, pos, fin_etiqueta)
        for qname, (ini, fin_, vi, vf) in _atributos_de_etiqueta(datos, pos, fin_etiqueta).items():
            if qname != "xmlns" and not qname.startswith("xmlns:"):
                nodo.posiciones[resolver(qname, ambito, True)] = (ini, fin_, vi, vf)
        if set(nodo.posiciones) != set(atributos):
            raise ErrorXMP("XMP con atributos que no se pueden situar")
        if datos[fin_etiqueta - 2:fin_etiqueta - 1] == b"/":
            nodo.fin = fin_etiqueta
        pila[-1].hijos.append(nodo)
        pila.append(nodo)

    def fin(_nombre: str):
        nodo = pila.pop()
        espacios.pop()
        if nodo.fin < 0:
            nodo.inicio_cierre = p.CurrentByteIndex
            cierre = datos.find(b">", p.CurrentByteIndex)
            if cierre < 0:
                raise ErrorXMP("XMP truncado")
            nodo.fin = cierre + 1

    def texto(t: str):
        pila[-1].texto += t

    p.StartDoctypeDeclHandler = prohibido
    p.EntityDeclHandler = prohibido
    p.UnparsedEntityDeclHandler = prohibido
    p.ExternalEntityRefHandler = prohibido
    p.StartElementHandler = inicio
    p.EndElementHandler = fin
    p.CharacterDataHandler = texto
    p.ProcessingInstructionHandler = lambda destino, datos_: instrucciones.append((destino, datos_))
    try:
        p.Parse(datos, True)
    except expat.ExpatError as e:
        raise ErrorXMP(f"XMP mal formado (línea {e.lineno})") from None
    if len(raiz_virtual.hijos) != 1:
        raise ErrorXMP("XMP sin un elemento raíz")
    return Documento(datos, raiz_virtual.hijos[0], instrucciones)


_BLANCOS = b" \t\r\n"


def _atributos_de_etiqueta(datos: bytes, pos: int, fin: int) -> dict[str, tuple[int, int, int, int]]:
    """Atributos de la etiqueta de apertura datos[pos:fin], leyendo su sintaxis:
    qname -> (inicio con el espacio previo, fin, inicio del valor, fin del valor)."""
    i = pos + 1
    while i < fin and datos[i] not in _BLANCOS and datos[i] not in b"/>":
        i += 1                                                  # nombre del elemento
    atributos = {}
    while i < fin:
        ini = i
        while i < fin and datos[i] in _BLANCOS:
            i += 1
        if i >= fin or datos[i] in b"/>":
            break
        j = i
        while j < fin and datos[j] not in _BLANCOS and datos[j] != 0x3D:
            j += 1
        nombre = datos[i:j].decode("utf-8")
        while j < fin and datos[j] in _BLANCOS:
            j += 1
        if j >= fin or datos[j] != 0x3D:
            raise ErrorXMP("etiqueta XMP no válida")
        j += 1
        while j < fin and datos[j] in _BLANCOS:
            j += 1
        if j >= fin or datos[j] not in (0x22, 0x27):
            raise ErrorXMP("etiqueta XMP no válida")
        cierre = datos.find(bytes([datos[j]]), j + 1, fin)
        if cierre < 0:
            raise ErrorXMP("etiqueta XMP no válida")
        atributos[nombre] = (ini, cierre + 1, j + 1, cierre)
        i = cierre + 1
    return atributos


def _fin_de_etiqueta(datos: bytes, pos: int) -> int:
    """Byte tras el ">" de la etiqueta que empieza en pos (respetando comillas)."""
    comilla = None
    i = pos + 1
    while i < len(datos):
        c = datos[i]
        if comilla:
            if c == comilla:
                comilla = None
        elif c in (0x22, 0x27):
            comilla = c
        elif c == 0x3E:
            return i + 1
        i += 1
    raise ErrorXMP("XMP truncado")


# ---------------------------------------------------------------------------
# Propiedades

@dataclass
class Propiedad:
    uri: str
    local: str
    valor: str
    tramo: tuple[int, int]
    nodo: Nodo | None            # None si es un atributo de rdf:Description


def propiedades(doc: Documento):
    """Propiedades de primer nivel de cada rdf:Description (atributos y elementos)."""
    for desc in doc.nodos():
        if (desc.uri, desc.local) != (NS_RDF, "Description"):
            continue
        for (uri, local), (qname, valor) in desc.atributos.items():
            if uri in (NS_RDF, "") or uri == "http://www.w3.org/XML/1998/namespace":
                continue
            ini, fin, _, _ = desc.posiciones[(uri, local)]
            yield Propiedad(uri, local, valor, (ini, fin), None)
        for hijo in desc.hijos:
            yield Propiedad(hijo.uri, hijo.local, _valor(hijo), (hijo.inicio, hijo.fin), hijo)


def _valor(nodo: Nodo) -> str:
    recurso = nodo.atributos.get((NS_RDF, "resource"))
    if recurso:
        return recurso[1]
    for contenedor in nodo.hijos:
        if contenedor.uri == NS_RDF and contenedor.local in ("Bag", "Seq", "Alt"):
            return "; ".join(li.texto.strip() for li in contenedor.hijos)
    return nodo.texto.strip()


def _texto_campo(n: Nodo, uri: str, local: str) -> str | None:
    """Valor de un campo de estructura: atributo del nodo o elemento hijo (también dentro de
    un rdf:Description anidado)."""
    attr = n.atributos.get((uri, local))
    if attr:
        return attr[1]
    for h in n.hijos:
        if (h.uri, h.local) == (uri, local):
            return h.texto.strip()
        if (h.uri, h.local) == (NS_RDF, "Description"):
            valor = _texto_campo(h, uri, local)
            if valor is not None:
                return valor
    return None


def objetivos_ia(doc: Documento) -> list[Objetivo]:
    """Lo que se quita en modo IA, por campo."""
    objetivos: list[Objetivo] = []
    for prop in propiedades(doc):
        clave = f"{_PREFIJOS.get(prop.uri, prop.uri)}:{prop.local}"
        if prop.uri == NS_IPTC_EXT and prop.local in ("DigitalSourceType", "DigitalSourceFileType"):
            codigo = firmas.tipo_fuente_ia(prop.valor)
            if codigo:
                objetivos.append(Objetivo(clave, prop.valor, codigo, "procedencia", [prop.tramo]))
        elif (prop.uri, prop.local) == (NS_XMP, "CreatorTool"):
            nombre = firmas.generador(prop.valor)
            if nombre:
                objetivos.append(Objetivo(clave, prop.valor, f"Generador: {nombre}", "generador", [prop.tramo]))
        elif (prop.uri, prop.local) == (NS_DCTERMS, "provenance"):
            objetivos.append(Objetivo(clave, prop.valor, firmas.describir_url(prop.valor) or "procedencia",
                                      "procedencia", [prop.tramo]))
        elif (prop.uri, prop.local) == (NS_PHOTOSHOP, "Credit"):
            nombre = firmas.firma_en_texto(prop.valor) or firmas.generador(prop.valor)
            if nombre:
                objetivos.append(Objetivo(clave, prop.valor, f"Firma: {nombre}", "procedencia", [prop.tramo]))
        elif (prop.uri, prop.local) == (NS_XMPMM, "History") and prop.nodo is not None:
            objetivo = _historia(prop, clave)
            if objetivo:
                objetivos.append(objetivo)
    return objetivos


def _historia(prop: Propiedad, clave: str) -> Objetivo | None:
    """Entradas de xmpMM:History con softwareAgent de IA. Si son todas, la propiedad entera."""
    entradas, de_ia, agentes, generadores = [], [], [], []
    for contenedor in prop.nodo.hijos:
        if contenedor.uri == NS_RDF and contenedor.local in ("Seq", "Bag"):
            for li in contenedor.hijos:
                entradas.append(li)
                agente = _texto_campo(li, NS_STEVT, "softwareAgent")
                nombre = firmas.generador(agente)
                if nombre:
                    de_ia.append(li)
                    agentes.append(agente)
                    if nombre not in generadores:
                        generadores.append(nombre)
    if not de_ia:
        return None
    tramos = [prop.tramo] if len(de_ia) == len(entradas) else [(li.inicio, li.fin) for li in de_ia]
    return Objetivo(f"{clave} (entradas con softwareAgent de IA)", "; ".join(agentes),
                    "Generador: " + ", ".join(generadores), "generador", tramos, cantidad=len(de_ia))


def quitar(datos: bytes, tramos: list[tuple[int, int]], cambios: list[tuple[int, int, bytes]] = ()) -> bytes:
    """El paquete sin esos tramos de bytes y con los cambios (inicio, fin, bytes nuevos).

    Se comprueba que el resultado sigue siendo XML válido, con las mismas instrucciones
    <?xpacket?>; si no, ErrorXMP (y la imagen no se toca)."""
    ediciones = sorted({(i, f, b"") for i, f in tramos} | set(cambios), reverse=True)
    for (i1, _f1, _), (_i2, f2, _) in zip(ediciones, ediciones[1:]):
        if f2 > i1:
            raise ErrorXMP("ediciones de XMP solapadas")
    resultado = datos
    for ini, fin, nuevo in ediciones:
        resultado = resultado[:ini] + nuevo + resultado[fin:]
    if analizar(resultado).instrucciones != analizar(datos).instrucciones:
        raise ErrorXMP("la edición cambió las instrucciones xpacket")
    return resultado


def cambio_de_valor(doc: Documento, uri: str, local: str, nuevo: str) -> tuple[int, int, bytes] | None:
    """Cambio que sustituye el valor simple de una propiedad (atributo o elemento con texto)."""
    for desc in doc.nodos():
        if (desc.uri, desc.local) != (NS_RDF, "Description"):
            continue
        if (uri, local) in desc.posiciones:
            _, _, vi, vf = desc.posiciones[(uri, local)]
            return vi, vf, nuevo.encode("utf-8")
        for h in desc.hijos:
            if (h.uri, h.local) == (uri, local) and not h.hijos and h.inicio_cierre >= 0:
                return h.fin_etiqueta, h.inicio_cierre, nuevo.encode("utf-8")
    return None


# ---------------------------------------------------------------------------
# Variantes que no se tocan

def es_ultra_hdr(doc: Documento) -> bool:
    for n in doc.nodos():
        if n.uri == NS_HDRGM or any(u == NS_HDRGM for u, _ in n.atributos):
            return True
        semantica = n.atributos.get((NS_GCONTAINER_ITEM, "Semantic"))
        if semantica and semantica[1] == "GainMap":
            return True
    return False


def es_foto_en_movimiento(doc: Documento) -> bool:
    nombres = {"MotionPhoto", "MicroVideo", "MotionPhotoVersion", "MicroVideoVersion"}
    for n in doc.nodos():
        if n.uri == NS_GCAMERA and n.local in nombres:
            return True
        if any(u == NS_GCAMERA and l in nombres for u, l in n.atributos):
            return True
    return False


def guid_extendido(doc: Documento) -> str | None:
    for prop in propiedades(doc):
        if (prop.uri, prop.local) == (NS_XMPNOTE, "HasExtendedXMP"):
            return prop.valor
    return None
