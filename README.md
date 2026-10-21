<p align="center"><img src="assets/icono.svg" width="96" height="96" alt="Icono de aiclean"></p>

# aiclean (ai-meta-cleaner)

Herramienta de línea de comandos para Windows que elimina metadatos y marcas de procedencia
de IA de texto, JPEG y PNG. Nunca recomprime imágenes y en texto solo quita caracteres
invisibles. No promete conservar en todos los casos la presentación ni el
comportamiento: los límites concretos están en este documento.

> **English.** aiclean is a Windows command-line tool that removes AI provenance metadata and
> hidden marks from files: C2PA manifests, XMP/EXIF/IPTC fields written by AI generators,
> Stable Diffusion/ComfyUI/NovelAI prompts stored in PNG text chunks, and invisible Unicode
> characters in text. It never re-encodes images, edits metadata field by field and verifies every
> output before saving it. Version 1.0 handles text, JPEG and PNG (the final scope of this
> version); double-clicking `aiclean.exe` opens an interactive mode where you drop files or folders.
> It does not remove watermarks embedded in the content itself (SynthID and similar). The
> documentation below is in Spanish.

## Alcance de la 1.0

El alcance de esta versión es **definitivo**: texto, JPEG y PNG.

- **Trata:** texto (archivos, entrada estándar y portapapeles, también HTML, XML y SVG), JPEG y
  PNG.
- **Fuera de alcance:** WebP, GIF, TIFF, HEIF/AVIF, audio, vídeo y documentos (DOCX, XLSX, PPTX,
  ODF y PDF). Se omiten sin tocarlos («omitido: formato … no soportado»).
- **No quita las marcas incrustadas en el contenido** (SynthID y similares): ver
  [Marcas incrustadas en el contenido](#marcas-incrustadas-en-el-contenido).

## Descarga e instalación

1. Descarga `ai-meta-cleaner-v1.0-win64.zip` y `ai-meta-cleaner-v1.0-win64.zip.sha256` desde la
   página de releases del repositorio.
2. Comprueba el hash: `Get-FileHash .\ai-meta-cleaner-v1.0-win64.zip -Algorithm SHA256` tiene que dar
   el valor que hay en el `.sha256`.
3. Descomprime el zip donde quieras, por ejemplo en `%LOCALAPPDATA%\Programs\`. Deja la carpeta
   `ai-meta-cleaner\` entera: `aiclean.exe` necesita a su lado `_internal\` y `tools\exiftool\`.
4. Añade esa carpeta al PATH de tu usuario (Variables de entorno → Path del usuario → Nuevo) y abre
   una terminal nueva. `aiclean --version` tiene que decir `aiclean 1.0`.

No hace falta Python ni instalar nada más: el zip ya trae ExifTool 13.59.

> **El exe no está firmado.** La primera vez, Windows SmartScreen puede avisar («Windows protegió su
> PC») y algún antivirus puede desconfiar de un ejecutable hecho con PyInstaller. Comprueba el
> SHA-256 o, si lo prefieres, compílalo tú desde el código (ver
> [Compilar desde el código](#compilar-desde-el-código)).

## Doble clic: modo interactivo

Con **doble clic en `aiclean.exe`** se abre una ventana que pide rutas:

- **Arrastra a la ventana un archivo o una carpeta** (o escribe la ruta) y pulsa Enter. Puedes
  arrastrar varios a la vez; las rutas valen con comillas o sin ellas. Si una no existe, sale en
  rojo y vuelve a preguntar.
- Cada archivo se muestra **paso a paso**: Analizando…, lo encontrado (tipo, campo y motivo, nunca
  el valor: ni prompts ni URL completas), Limpiando…, Verificando… y el resultado con la ruta de la
  copia limpia (`foto.png` → `foto_clean.png`). **El original no se toca nunca** y no hay
  `--in-place`. Los omitidos y los parciales salen con su motivo.
- Con una carpeta que tiene subcarpetas pregunta «¿Incluir también las subcarpetas? (s/N)»; Enter
  es no. Con más de 20 archivos en una tanda, una línea por archivo en vez del paso a paso. Con
  varios, el resumen al final.
- Vuelve a preguntar hasta que pulses **Enter sin escribir nada**, y la ventana se cierra.
- **Arrastrar archivos o carpetas sobre el icono** de `aiclean.exe` hace lo mismo con ellos y
  después sigue preguntando.
- Siempre en modo IA (lo que delata a una IA); para `--all` u otras opciones, usa la terminal.

```
aiclean 1.0
Quita metadatos y marcas de procedencia de IA de texto, JPEG y PNG,
sin recomprimir y verificando cada archivo. El original nunca se toca:
al lado se crea una copia limpia (nombre_clean.ext).

Arrastra aquí un archivo o carpeta (o escribe la ruta) y pulsa Enter. Enter vacío para salir.
> "C:\Users\yo\Downloads\gato.png"

gato.png
  Analizando…
  Encontrado:
    · C2PA: manifiesto JUMBF — manifiesto de 18342 bytes        procedencia de IA
    · PNG: texto «parameters» — parámetros de generación (412 caracteres)  procedencia de IA
  Limpiando…
  Verificando…
  limpio → C:\Users\yo\Downloads\gato_clean.png
  La marca invisible de los píxeles (SynthID y similares), si la hay, sigue: aiclean no toca los píxeles.

Arrastra aquí un archivo o carpeta (o escribe la ruta) y pulsa Enter. Enter vacío para salir.
>
```

**Desde una terminal, un script o con tuberías** funciona como siempre: sin bucle, sin pausa y con
los mismos códigos de salida. Si la salida se redirige, o con `NO_COLOR` o `TERM=dumb`, no lleva
colores.

## Uso

```
aiclean [RUTA ...] [-r] [--clipboard] [--dry-run] [--all] [--normalize]
        [--in-place | -o CARPETA] [--report] [--report-file ARCHIVO] [--report-sensitive] [-j N]
        [--exiftool RUTA] [-]
```

| Opción | Qué hace |
|---|---|
| `RUTA ...` | Archivos o carpetas. Admite comodines (`*.txt`) aunque PowerShell no los expanda. |
| `-r`, `--recursive` | Entra también en las subcarpetas, pero nunca en junctions ni en enlaces simbólicos de carpeta. |
| `--clipboard` | Limpia el portapapeles: el texto y el HTML. |
| `-` | Lee de la entrada estándar y escribe el texto limpio en la salida estándar. |
| `--dry-run` | Solo muestra lo que encuentra (con línea y columna), sin tocar nada. |
| `--all` | Quita todos los metadatos, no solo los de IA. |
| `--normalize` | Solo en texto plano: cambia los espacios raros por un espacio normal y quita SHY y WORD JOINER. |
| `--in-place` | Sobrescribe el original con un reemplazo atómico, solo si la verificación pasa. |
| `-o`, `--output-dir CARPETA` | Guarda los limpios en otra carpeta, con el mismo árbol de subcarpetas. |
| `--report` | Resumen al final: procesados, qué se eliminó, qué quedó sin tratar y errores. |
| `--report-file ARCHIVO` | Informe completo en JSON. |
| `--report-sensitive` | Autoriza mostrar los valores sensibles en pantalla y en el JSON. |
| `-j`, `--jobs N` | Archivos en paralelo (por defecto 1). Con imágenes, un proceso de ExifTool por hilo. |
| `--exiftool RUTA` | `exiftool.exe` que se usa para las imágenes. Por defecto, el de `tools\`. Nunca se busca en el PATH. |
| `-h`, `--help` | La ayuda, por secciones. Redirigida, en texto plano y sin colores. |
| `--version` | La versión del programa. |

Por defecto **no se sobrescribe nada**: `notas.txt` → `notas_clean.txt`, y si ya existe,
`notas_clean_2.txt`. Si un archivo no tiene nada que quitar, no se crea ninguna copia. La
salida es un archivo nuevo y **no conserva la fecha de modificación** del original, porque
debe reflejar que se ha cambiado.

Al recorrer carpetas se saltan los `*_clean` de pasadas anteriores y nunca se entra en `.git`,
`.hg` ni `.svn`. Los archivos que están **solo en OneDrive** (sin descargar) se saltan con aviso,
para no forzar su descarga.

### Ejemplos

```powershell
aiclean respuesta.md                       # crea respuesta_clean.md
aiclean respuesta.md --dry-run             # qué hay y dónde, sin tocar nada
aiclean C:\Textos -r -o C:\Limpios         # carpeta entera, mismo árbol en otra
aiclean *.txt *.md --in-place --report     # sobrescribe y resume al final
aiclean notas.txt --normalize              # además, espacios raros -> espacio normal
aiclean --clipboard                        # copia de ChatGPT, ejecuta, pega
aiclean imagen.png --dry-run               # C2PA, XMP, EXIF, prompts... sin tocar nada
aiclean C:\Fotos -r --all -o C:\Limpias     # todos los metadatos prescindibles
cmd /c "aiclean - < sucio.txt > limpio.txt"
```

### Códigos de salida

| Código | Significado |
|---|---|
| `0` | Todo bien. |
| `1` | Algún error. |
| `2` | Uso incorrecto. |
| `3` | `--dry-run` encontró algo que quitar. |
| `4` | Resultado **parcial**: quedó algo sin tratar, informado como «no tratado». |
| `130` | Cancelado con Ctrl+C. |

Si se dan varios casos, gana el primero de esta lista: 1, 4, 3, 0.

### Estados de cada archivo

| Estado | Significado |
|---|---|
| `limpio` | Se escribió la salida limpia y pasó la verificación. |
| `parcial` | Queda algo **sin tratar** (dentro de una zona excluida del marcado), informado como «no tratado». Sin `--dry-run`: se quitó lo demás, o no se creó copia si no había nada más. Con `--dry-run`: hay algo que se quitaría y algo que quedaría sin tratar; el mensaje dice cuántos de cada (`se quitarían 1; quedarían 2 sin tratar…`). Código 4. |
| `encontrado` | Solo con `--dry-run`: hay algo que quitar y nada quedaría sin tratar. Código 3. |
| `omitido` | No se procesa, con el motivo (formato no soportado, enlace o flujos NTFS con `--in-place`, solo en OneDrive…). |
| `error` | Falló algo; el original queda como estaba y no se publica ninguna salida. |
| «no se encontró nada en lo inspeccionado» | No significa que el archivo esté libre de marcas: solo que en lo que esta versión sabe inspeccionar no había nada. |

En el JSON, cada resultado lleva además `accion`: `prevista` en `--dry-run` cuando hay algo que
se quitaría, `realizada` si se escribió la salida (archivo, salida estándar o portapapeles) y
`ninguna` en los demás casos (sin nada que quitar, omitido, error o parcial sin nada tratable).
En pantalla, el resumen de `--dry-run` (`--report`) dice «se quitaría» o «quedaría sin tratar»
en la columna Acción; sin `--dry-run`, la columna Tratado dice «sí» o «no».

## Tabla de contrato

| Formato | Modo IA (por defecto) | Con `--all` | Siempre se conserva | Cómo se verifica | En la 1.0 |
|---|---|---|---|---|---|
| **Texto plano** (TXT, MD, código, JSON, CSV, entrada estándar…) | Invisibles: zero-width, tags Unicode, selectores de variación sueltos, BOM sueltos, bidi sin RTL… Con `--normalize`, además los espacios raros pasan a espacio normal y se quitan SHY y WJ. | Lo mismo: en texto no hay otros metadatos. | Lo visible, la codificación, el BOM inicial, los finales de línea, los emojis compuestos, ZWJ/ZWNJ árabe e índico, los VS válidos, las banderas de subdivisión y los selectores CJK. Sin `--normalize`, también NBSP, NNBSP, U+2000–200A, U+205F, SHY y WJ. | Sin lo quitable, el texto es idéntico; lo invisible que queda está en el mismo sitio; misma codificación y BOM; una segunda limpieza no encuentra nada. | Sí |
| **HTML / XML / SVG** | Invisibles literales y referencias (`&#8203;`, `&zwnj;`…) fuera de las zonas excluidas. En los comentarios normales, solo los invisibles literales. | Lo mismo. | Las zonas excluidas, intactas: `<script>`, `<style>`, CDATA, comentarios condicionales `<!--[if …]>` y el interior de las etiquetas `<…>`. Lo que tienen se informa como «no tratado» → PARCIAL. También se conservan espacios especiales y SHY/WJ, literales o como referencia (`--normalize` no se aplica). Nada se reconstruye ni se reformatea. | Alineación con el original: las zonas excluidas, idénticas; fuera de ellas solo desaparecen caracteres quitables; segunda limpieza vacía. | Sí |
| **Portapapeles** | Texto como texto plano y HTML como HTML. `SourceURL` y formatos con URL de una IA. | `SourceURL` y URL de origen siempre. | — Se pierden los demás formatos (RTF, internos del navegador), con aviso. | La misma comprobación que el texto y el HTML, más releer el portapapeles. | Sí |
| **Zone.Identifier** (marca de la web) | `HostUrl` / `ReferrerUrl` de un servicio de IA. | `HostUrl` y `ReferrerUrl` siempre. | La marca nunca se borra entera: `ZoneId` y los demás campos se quedan. | La marca se relee en el temporal y tiene que ser la esperada (si no, o si el destino no es NTFS, error). Si solo cambia la marca, el contenido de la salida tiene que ser idéntico al analizado. | Sí |
| **JPEG / PNG** | Por campo: C2PA (APP11 / `caBX`); XMP `DigitalSourceType` de IA, `CreatorTool` de un generador, entradas de `xmpMM:History` de IA, `dcterms:provenance` y `Credit` de IA; EXIF/IPTC Software, Artist, ImageDescription, UserComment, Credit… con firma o prompt; COM de IA; chunks PNG `parameters`, `prompt`, `workflow`… y `Comment`/`Description`/`Source`/`Software` con valores de IA. | Todo lo prescindible: EXIF (salvo Orientation), XMP, IPTC, COM, APP12/FPXR, textos PNG, `tIME` y datos tras IEND. | Datos de imagen byte a byte; ICC, JFIF, APP14, Orientation; en PNG, IHDR, PLTE, tRNS, IDAT y los chunks de color y presentación (iCCP, sRGB, gAMA, cHRM, cICP, mDCV, cLLI, pHYs, sBIT, bKGD, hIST, sPLT). Lo desconocido no se borra a ciegas. | Recorrido propio: segmentos que no son APPn/COM y datos comprimidos idénticos (JPEG); CRC, estructura y chunks de imagen idénticos (PNG); metadatos = esperados; segundo escaneo vacío. | Sí |

WebP, GIF, TIFF, HEIF/AVIF, audio, vídeo y documentos quedan **fuera de alcance**: se omiten sin tocarlos.

## Texto: detalle

**Se quitan siempre** (caracteres que no se ven y sirven para marcar o esconder datos):

- Zero-width: U+200B, U+200C (ZWNJ), U+200D (ZWJ), U+FEFF suelto (BOM que no está al
  principio), U+2061–2064, U+206A–206F, U+FFF9–FFFB, U+1D173–1D17A, U+034F, U+180E y los
  rellenos Hangul (U+115F, U+1160, U+3164, U+FFA0), salvo en los casos que se conservan (abajo).
- **Tags Unicode** U+E0000–E007F.
- **Selectores de variación** sueltos (U+FE00–FE0F y U+E0100–E01EF).
- Controles **bidi** (U+200E/200F, U+202A–202E, U+2066–2069, U+061C), solo si el texto no
  tiene escritura de derecha a izquierda. Así se cubre el ataque «Trojan Source» en código.

**Se conservan por defecto:** NBSP (U+00A0), NNBSP (U+202F), U+2000–200A, U+205F, SHY (U+00AD)
y WORD JOINER (U+2060). `--normalize` los cambia en texto plano: los espacios pasan a espacio
normal y se quitan SHY y WJ. **Puede cambiar la tipografía** (el ancho de los espacios) **y
dónde se cortan las líneas**: un NBSP o un WJ impiden un salto que después sí puede ocurrir.

**Se conserva** lo que forma parte del texto:

- El ZWJ dentro de emojis compuestos (👨‍👩‍👧, 🏳️‍🌈, 👩🏽‍💻) y los keycaps (#️⃣).
- ZWJ/ZWNJ entre letras de escrituras que los necesitan: persa (می‌خواهم), árabe e índicas
  (devanagari, cingalés…), myanmar, khmer, mongol.
- Un VS15/VS16 detrás de un carácter que lo admite (❤️ / ☺︎, ✅️, ✨️…), según las tablas
  oficiales de Unicode. Si hay más de uno seguido, sobran.
- Las banderas de subdivisión (🏴 de Inglaterra, Escocia, Gales). Sus tags solo se conservan si
  forman un código válido.
- Los selectores de variantes estándar y de ideogramas CJK.
- Los controles bidi si hay hebreo, árabe, etc.
- El espacio ideográfico U+3000.
- La codificación (UTF-8, UTF-16/32 con BOM, Windows-1252), el BOM inicial y los finales de línea.

El `--dry-run` muestra el tipo de cada hallazgo, la cantidad y la posición. El texto que
escondían los tags o una cadena de selectores solo se muestra con `--report-sensitive`.

### HTML, XML y SVG

- Fuera de las zonas excluidas se quitan los invisibles, también los escritos como referencia
  (`&#8203;`, `&#x200B;`, `&zwnj;`…).
- **Zonas excluidas** (no se toca nada): `<script>`, `<style>`, CDATA, los comentarios
  condicionales `<!--[if …]> … <![endif]-->` y el **interior de las etiquetas** `<…>`, incluidos
  los atributos (`alt`, `title`…). En las etiquetas el motivo es la seguridad: quitar un
  invisible de un nombre de etiqueta puede cambiar el documento; por ejemplo, de una etiqueta
  desconocida saldría un `<script>` que se ejecuta.
- Lo que hay en esas zonas **no se oculta**: se informa como «no tratado» (dónde y cuántos, sin
  valores) y el archivo queda en estado **parcial** (código 4). Si solo hay eso, no se crea copia.
- En los comentarios normales `<!-- … -->` sí se quitan los invisibles literales. Allí las
  referencias son texto y se dejan.
- Los espacios especiales y SHY/WJ se conservan siempre, literales o como referencias, y no se
  convierten a entidades. `--normalize` no se aplica a estos formatos: el archivo lo avisa y el
  resto de la limpieza continúa. En un lote mixto, los archivos de texto plano sí se normalizan.
- El documento no se reconstruye ni se reformatea: solo desaparecen los caracteres quitados.

### Portapapeles

Se limpian el texto, con las reglas de texto plano, y el formato HTML, con las de HTML. El HTML
es lo que usa Word al pegar. Además se quita el `SourceURL` (la página de la que se copió, por
ejemplo `chatgpt.com`) si es de una IA, o siempre con `--all`. Esta limpieza es independiente de
las zonas excluidas. Al reescribir el portapapeles se pierden los demás formatos (RTF, formatos
internos del navegador…), y el resultado lo avisa.

### Marca de la web (Zone.Identifier)

Windows guarda en el flujo NTFS `Zone.Identifier` de cada descarga la zona y las URL de origen.
La marca **nunca se borra entera**: se reescribe quitando `HostUrl` y `ReferrerUrl`. En modo IA
se quitan solo si son de un servicio de IA (`chatgpt.com`, `files.oaiusercontent.com`,
`gemini.google.com`, `claude.ai`…); con `--all`, siempre. `ZoneId` y los demás campos se quedan,
para que Windows siga sabiendo que el archivo viene de internet. Si no hay ninguna URL que
quitar, no cuenta como hallazgo.

Después de escribir la marca en el temporal se vuelve a leer y se compara con lo esperado
(reescrita o conservada). Si no coincide o no se puede escribir, por ejemplo porque el destino
no es NTFS (una memoria USB en FAT o exFAT), el archivo da **error** («el destino no admite la
marca de la web; no se publica una copia sin ella») y no queda ni la salida ni el temporal.

## Enlaces

- Sin `--in-place` se puede leer a través de un enlace **indicado expresamente**: la salida es
  siempre un archivo nuevo, y nunca un enlace.
- Con `--in-place` se omiten, con el motivo, los enlaces simbólicos, los archivos con varios
  enlaces duros y las rutas que pasan por una junction, un punto de montaje o un enlace
  simbólico de carpeta. Esto se comprueba sobre la ruta tal cual se indicó, sin resolverla.
  `--dry-run --in-place` aplica las mismas exclusiones y da el mismo `omitido` con el mismo motivo.
- Con `-r` no se entra en junctions ni en enlaces simbólicos de carpeta (así se evitan bucles),
  y los archivos enlazados que aparecen al recorrer o por un comodín no se siguen. Todos salen
  en el informe como omitidos.
- Los accesos directos `.lnk` son archivos normales: nunca se resuelve su destino.

## --in-place y cambios concurrentes

Antes de leer se apuntan la identidad del archivo, su tamaño y su fecha. El análisis y la
transformación usan los mismos bytes leídos. Antes de reemplazar se vuelven a comprobar la
identidad, el tamaño y la fecha, y se compara el SHA-256 del contenido actual con el de los
bytes analizados. Si algo no cuadra, el archivo da error, el original no se toca y solo se
borra el temporal de esa operación. El temporal se escribe a disco (fsync) antes de publicarlo.

Esto **detecta modificaciones que ya se hayan producido**, pero no elimina la ventana entre la
última comprobación y el reemplazo: no hay bloqueo exclusivo.

**Flujos NTFS:** el reemplazo crea un archivo nuevo, que solo lleva el contenido y la marca de la
web. Si el original tiene **otros flujos NTFS** además de `Zone.Identifier` (algunos programas
guardan en ellos datos propios), se omite con el motivo «tiene flujos NTFS adicionales;
--in-place los perdería», también en `--dry-run`. Sin `--in-place` la salida es un archivo nuevo
sin esos flujos y el original los conserva.

**Permisos:** el reemplazo también puede cambiar los **permisos explícitos (ACL)** del archivo,
porque el nuevo hereda los de la carpeta. No se copian ni se comprueban.

## Datos sensibles e informe JSON

**Por defecto**, ni el JSON ni la pantalla muestran valores sensibles: el texto oculto que se
recupere, los prompts, las URL completas o los valores de metadatos que se eliminen. Esto
incluye `--dry-run` y el portapapeles. Sí se muestran la categoría, el campo, la cantidad, la
ubicación, las posiciones, el motivo, el estado, la acción y el resultado de la verificación.
De una URL de un servicio de la lista de IA se da el servicio y el dominio de la lista
(`OpenAI (oaiusercontent.com)`); de cualquier otra, solo **`URL de procedencia`** (o
`URL de procedencia (dirección IP)` / `URL de procedencia (sin dominio)`): ni dominio, ni
subdominios, rutas o parámetros, porque un dominio personal ya identifica a alguien.

Cada hallazgo del JSON lleva `motivo`:

| `motivo` | Qué es |
|---|---|
| `procedencia` | URL y marcas de un servicio de IA (marca de la web, `SourceURL` del portapapeles…). |
| `generador` | Firmas de librerías y programas generadores. Reservado: llega con imágenes y documentos. |
| `unicode` | Higiene de caracteres: invisibles, tags, selectores, bidi, espacios raros, y lo que queda sin tratar en zonas excluidas. |
| `amplio` | Solo se quita por `--all` (por ejemplo, una URL que no es de IA). |

El campo `es_ia` se mantiene por compatibilidad (`true` = se quita en modo IA, `false` = solo
con `--all`). **No prueba que el archivo lo haya hecho una IA**: un invisible o un dominio de la
lista también pueden venir de una persona o de otro programa.

`--report-sensitive` autoriza mostrar los valores en pantalla y en el JSON. Los mensajes de
error **nunca** incluyen contenido del archivo, ni con esa opción: un fallo inesperado sale
como `error interno (Tipo en archivo:línea)`.

El texto que se pide por la salida estándar (`aiclean -`) es el resultado y no se censura.

El JSON lleva las versiones de aiclean, de las reglas de IA, de las tablas Unicode y de
`unicodedata`.

## Imágenes JPEG y PNG

**Qué se trata:** JPEG estático (baseline, secuencial extendido y progresivo, también gris y CMYK
con APP14) y PNG estático (paleta, gris, gris con alfa, RGB, RGBA; 8 y 16 bits).

**Se omiten, con el motivo y sin tocarlos:** MPO/MPF (varias imágenes), Ultra HDR y mapas de
ganancia (XMP `hdrgm`, GContainer o ISO 21496-1; también las fotos HDR de iPhone en JPEG, que
llevan MPF, mapa de ganancia y una segunda imagen tras el final), fotos en movimiento (Motion Photo), JPEG
aritmético, sin pérdida, diferencial o jerárquico, APP11 que no es C2PA (por ejemplo JPEG XT), APNG,
PNG firmados (`dSIG`), PNG con un chunk crítico desconocido o con un CRC inválido, y cualquier
archivo cuya estructura no se pueda recorrer.

**Modo IA, por campo** (nunca grupos enteros): ver la [tabla de contrato](#tabla-de-contrato). El
`motivo` es `procedencia` para C2PA, `DigitalSourceType`, `provenance`, créditos «Made with…» y
prompts, y `generador` para nombres de programas (Software, CreatorTool, softwareAgent, Artist
«Midjourney»…; también Matplotlib, que cuenta como generador). En PNG, `Comment`, `Description`,
`Source` y `Software` solo se quitan si su valor es de IA; `parameters`, `prompt`, `workflow`,
`sd-metadata`, `invokeai_metadata`, `invokeai_graph`, `Dream` y `postprocessing`, siempre. En los
PNG identificados como de Midjourney (firma en Description, generador…), también `Author`, que
guarda el nombre de usuario (motivo `procedencia`); en los demás PNG, `Author` solo con `--all`.

**Con `--all`:** además, todo lo prescindible. El EXIF se sustituye por uno mínimo con solo la
orientación. Lo desconocido (segmentos APPn o chunks auxiliares que no se conocen, JFXX, los datos
tras el final de un JPEG) **no se borra a ciegas**: se conserva y se informa como «no tratado»
(estado `parcial`). En PNG, los datos tras IEND sí se quitan con `--all`, después de validar la
estructura.

**Cómo se escribe:** C2PA, XMP, chunks de texto, COM, EXIF mínimo y datos tras IEND los trata
aiclean directamente. El XMP se edita recortando solo los bytes de cada campo: el resto del paquete
(incluido el `XMPToolkit` original) queda idéntico; si no tenía, no se añade. Los campos se sitúan
con el parser XML (los atributos, leyendo la sintaxis de la etiqueta, nunca buscando texto) y el
resultado se vuelve a leer: tiene que ser XML válido con las mismas instrucciones `<?xpacket?>`, y
si no lo es, el archivo se omite. En PNG el chunk editado se reescribe con su CRC. Los campos EXIF e
IPTC los borra **ExifTool**, sobre una copia de los mismos bytes analizados
(`.<nombre>.aiclean-XXXX.in.jpg`, junto al destino), que se borra siempre. ExifTool necesita la
extensión real, por eso la copia no termina en `.tmp`.

**Verificación (si falla, error y no se publica nada):** recorrido propio de segmentos JPEG (sin
buscar EOI por bytes) y de chunks PNG (longitud, tipo y CRC); los datos de imagen, idénticos byte a
byte y en orden; lo que se conserva, idéntico; los metadatos, exactamente los esperados (los campos
EXIF/IPTC que quedan, iguales uno a uno); segundo escaneo sin nada tratable. Una segunda pasada da
«no se encontró nada».

Detalles que conviene saber:

- Al reescribir el IPTC, ExifTool añade la versión del registro (1:0 `EnvelopeRecordVersion`), un
  campo obligatorio y sin datos del archivo; es lo único nuevo que admite la verificación. Si un
  bloque EXIF o IPTC se queda vacío, ExifTool lo borra entero.
- Un XMP dentro de un chunk `zTXt` se recomprime (el nivel de compresión original no se conoce).
- El XMP extendido de JPEG se edita igual; después se recalcula su GUID (el MD5 del paquete) en
  `xmpNote:HasExtendedXMP` y se vuelve a partir en segmentos APP1 coherentes (longitud total y
  desplazamientos). Si eso no se puede garantizar (el MD5 original no coincide o el XMP principal no
  lo referencia), el archivo se omite; si le faltan trozos, no se toca y se informa como «no tratado».
- Los bloques EXIF duplicados no se editan por campo: si llevan datos de IA, se informan como «no
  tratado».
- Los valores de los campos (prompts, nombres, URL) son sensibles: solo se muestran con
  `--report-sensitive`. Las claves de texto PNG que no son estándar tampoco se muestran (solo su
  longitud), porque también pueden ser datos personales.

### Herramientas externas

`build.ps1 -SoloHerramientas` descarga en `tools\` (fuera de git) lo que fija
`herramientas.lock.json`, y comprueba cada SHA-256 antes de instalarlo:

| Herramienta | Versión | Origen | Uso |
|---|---|---|---|
| ExifTool | 13.59 (Windows 64 bits) | SourceForge, enlazado desde exiftool.org; hash de exiftool.org/checksums.txt | Imágenes |
| c2patool | 0.27.22 | Releases de contentauth/c2pa-rs; hash publicado por GitHub | Solo tests |
| Muestras C2PA | commits fijados | c2pa-org/public-testfiles (CC BY-SA 4.0) y contentauth/c2pa-rs (MIT/Apache-2.0) | Solo tests |

Es el único paso que usa la red: nunca se ejecuta durante una limpieza ni en los tests. Sin
ExifTool, los JPEG y PNG dan un error claro y el texto sigue funcionando. ExifTool se arranca con
ruta absoluta, sin shell, sin archivo de configuración del usuario (`-config ""`), con un tiempo
máximo por petición y con el directorio de trabajo en su propia carpeta.

## Verificación

Nada se da por limpio sin comprobarlo. El resultado se escribe primero en un temporal y solo
se guarda si pasa las comprobaciones. Si falla una, se descarta, el original queda como estaba
y el archivo cuenta como error («verificación fallida»).

- **Texto plano:** quitando lo quitable (y normalizando, si se pidió), el original y el limpio
  son idénticos. Lo invisible que queda ya estaba en el original y en el mismo sitio. La
  codificación y el BOM son los mismos. Una segunda limpieza no encuentra nada.
- **Marcado:** alineando con el original, las zonas excluidas son idénticas y fuera de ellas
  solo desaparecen caracteres quitables.
- **Imágenes:** ver [Imágenes JPEG y PNG](#imágenes-jpeg-y-png).

## Versiones de Unicode

Las tablas de emojis y secuencias de variación son de **Unicode 16.0**. `unicodedata` de
Python 3.12 es **Unicode 15.0**, y solo se usa en dos reglas que miran la categoría del carácter
vecino: ZWJ/ZWNJ entre letras y CGJ. Si `unicodedata` no conoce un carácter (por ejemplo, uno
nuevo de Unicode 16), se trata como letra o marca y el invisible se conserva. Las secuencias
estándar que Unicode 15.0 tenía y 16.0 retiró también se conservan. Las dos versiones se informan
por separado en el JSON.

## PowerShell 5.1 y la entrada estándar

PowerShell 5.1 pasa el texto a los programas en **ASCII** (`$OutputEncoding`), así que en
`Get-Content x.txt | aiclean -` los invisibles llegan convertidos en `?` y ya no se pueden quitar.
Tampoco se puede detectar desde aiclean. Alternativas:

- `aiclean archivo.txt` o `aiclean --clipboard`, que no pasan por la tubería.
- `cmd /c "aiclean - < sucio.txt > limpio.txt"`, que pasa los bytes tal cual.
- En PowerShell, antes de la tubería:
  `$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)`.

## Marcas incrustadas en el contenido

aiclean quita lo que el archivo **declara**: metadatos (XMP, EXIF, IPTC, textos PNG), manifiestos
C2PA, prompts y caracteres invisibles. **No quita las marcas que van dentro del propio contenido**:

- **Imagen, audio y vídeo:** SynthID (Google), TrustMark (Adobe), AudioSeal (Meta), Digimarc y
  similares no son un dato aparte, sino una señal invisible repartida por los píxeles o por el
  sonido. Quitarla obligaría a modificar los píxeles y perder calidad, y aiclean nunca los toca:
  los datos de imagen se copian byte a byte. Por eso, en el modo interactivo, la primera imagen
  con metadatos de IA muestra el aviso «La marca invisible de los píxeles (SynthID y similares), si
  la hay, sigue: aiclean no toca los píxeles».
- Algunas de esas marcas («Durable Content Credentials» de C2PA, con TrustMark o Digimarc) permiten
  a un servicio recuperar el manifiesto C2PA desde la nube aunque el archivo ya no lo lleve.
- **Texto:** SynthID-Text y otras marcas estadísticas están en la elección de las palabras, no en
  caracteres ocultos. **Quitar los invisibles no las elimina.**

## Limitaciones

- Fuera de alcance: WebP, GIF, TIFF, HEIF/AVIF, audio, vídeo y documentos se omiten sin tocarlos.
- Se omiten, sin tocarlos, los JPEG con varias imágenes o mapa de ganancia (MPO/MPF, Ultra HDR,
  fotos HDR de iPhone), las fotos en movimiento y otras variantes que no se pueden verificar.
- No elimina las **marcas incrustadas en el contenido** (SynthID, TrustMark, AudioSeal…): ver
  [Marcas incrustadas en el contenido](#marcas-incrustadas-en-el-contenido).
- No cambia la tipografía visible que suele delatar a una IA (rayas —, comillas tipográficas,
  emojis…): es contenido.
- En **tailandés, jemer, lao y birmano**, el ZWSP (U+200B) marca dónde se puede partir una línea,
  porque esas escrituras no separan palabras con espacios. Quitarlo **cambia dónde se cortan las
  líneas**, aunque las letras sean las mismas.
- En marcado, lo que hay dentro de las zonas excluidas no se limpia: se informa y el archivo
  queda parcial. Fuera de ellas, quitar un invisible puede cambiar detalles de presentación (por
  ejemplo, los cortes de línea), igual que en texto plano.
- La protección de `--in-place` frente a cambios concurrentes no es absoluta (ver arriba), y el
  reemplazo puede cambiar los permisos explícitos (ACL) del archivo.
- Ningún hallazgo prueba autoría: aiclean informa de huellas, no de quién hizo el archivo.
- La salida no conserva la fecha de modificación del original.
- La entrada estándar en PowerShell 5.1: ver la sección anterior.
- En modo IA, en las imágenes solo se quitan los campos que delatan una IA: otros datos del mismo
  archivo (por ejemplo, la fecha de creación de un PNG de Midjourney) se quedan. `--all` los quita.

## Compilar desde el código

```powershell
git clone https://github.com/juanitohtz/ai-meta-cleaner
cd ai-meta-cleaner
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Set-Content -Encoding ascii .venv\Lib\site-packages\aiclean-src.pth "$PWD\src"
.\build.ps1 -SoloHerramientas          # ExifTool, c2patool y muestras C2PA en tools\ (SHA-256 del lock)
.\.venv\Scripts\python -m pytest -q --requiere-herramientas
.\.venv\Scripts\python -m aiclean --help
.\build.ps1                             # exe y zip en dist\
```

`build.ps1` compila siempre desde un árbol limpio (`git archive` del commit, por defecto `HEAD`, y
exige que no haya cambios sin commit): crea un venv nuevo con `requirements-dev.txt`, pasa toda la
batería, empaqueta con PyInstaller (carpeta, consola), pone al lado ExifTool descomprimido del zip
cuyo SHA-256 comprueba contra el lock, genera `THIRD-PARTY-NOTICES.txt` y `BUILD-INFO.txt`, pasa los
tests del exe y crea `dist\ai-meta-cleaner-v<versión>-win64.zip` con su `.sha256`. El exe no lleva
c2patool, ni las muestras, ni nada de los tests.

El icono está en `assets\` (`icono.svg`, `icono-pequeno.svg` para 16–24 px e `icono.ico` con 16,
20, 24, 32, 40, 48, 64, 128 y 256 px). Los tres salen de `assets\generar_icono.py`, sin
dependencias nuevas; `build.ps1` comprueba con `--comprobar` que coinciden byte a byte con el
script antes de compilar.


- Hay tests que **escriben en el portapapeles real** (y pierden lo que hubiera copiado que no sea
  texto o HTML). Por defecto se saltan y el resto usa un portapapeles simulado. Para activarlos:
  `pytest --portapapeles-real`.
- Los tests de enlaces simbólicos necesitan permiso para crearlos (modo desarrollador de Windows o
  una terminal como administrador); sin él, se saltan. Las junctions se prueban siempre.
- Ninguna prueba usa la red.
- Las pruebas de imágenes necesitan ExifTool (y las de C2PA, c2patool y las muestras oficiales) en
  `tools\`: primero `.\build.ps1 -SoloHerramientas`. Sin ellas se saltan con el motivo; con
  `pytest --requiere-herramientas`, fallan. c2patool se ejecuta sin red.
- Medición de rendimiento (no forma parte de pytest):
  `.\.venv\Scripts\python tests\rendimiento\medir.py [--archivos 300] [--repeticiones 5]` (texto) o
  `... medir.py --imagenes [--archivos 60]` (JPEG y PNG: programa completo frente a ExifTool directo
  y tiempos por fase). Genera un corpus con semilla fija en una carpeta temporal y da el mínimo, la
  mediana y el máximo del tiempo, los archivos/s y los MB/s, con los datos del equipo. No fija
  objetivos.

El código está en `src\aiclean\`. Hay un limpiador por tipo en `formatos\`, cada uno con
`preparar(ruta, tipo, modo, normalizar)`, y todo lo que cuenta como IA está en `firmas.py`.

## Licencia

MIT: ver [LICENSE](LICENSE). El zip incluye ExifTool (Phil Harvey, con la misma licencia que Perl),
el intérprete de Python y las librerías que usa el programa, cada uno con su licencia: la lista y
los textos están en `THIRD-PARTY-NOTICES.txt`, dentro del zip.

## Notas de cambios

### 1.0 (primera versión pública)

- Ejecutable para Windows de 64 bits (`aiclean.exe` con ExifTool 13.59 al lado), con icono propio y
  su SHA-256.
- **Doble clic: modo interactivo.** Se arrastran archivos o carpetas a la ventana (o sobre el
  icono) y cada archivo se muestra paso a paso (analizar, lo encontrado, limpiar, verificar), con
  copias `_clean` que nunca tocan el original.
- Texto: caracteres invisibles, espacios raros con `--normalize`, HTML/XML/SVG con zonas que no se
  tocan, entrada estándar, portapapeles y marca de la web (Zone.Identifier).
- JPEG y PNG: C2PA, XMP, EXIF, IPTC, comentarios y prompts, por campo en modo IA y en bloque con
  `--all`, sin recomprimir y verificando cada salida.
- `--help` por secciones y colores sobrios (verde lo correcto, rojo los errores, gris lo
  secundario), sin colores al redirigir, con `NO_COLOR` o con `TERM=dumb`.

### Historial previo a la 1.0

Versiones internas 0.1.0–0.2.0 y builds 1.0 y 1.1 del 21-10-2026, retiradas porque con doble clic no tenían modo interactivo.
