<#
.SYNOPSIS
  -SoloHerramientas: prepara tools\ (fuera de git) segun herramientas.lock.json: ExifTool
  (ejecucion), c2patool y las muestras oficiales C2PA (solo tests). Cada descarga se comprueba con
  su SHA-256 (las muestras, ademas, con su hash de git) y los zip verificados se guardan en
  tools\_descargas\. Es el unico paso que descarga herramientas.

  Sin parametros: compila aiclean.exe desde un arbol limpio (git archive del commit, por defecto
  HEAD; exige que no haya cambios sin commit). Crea un venv nuevo con requirements-dev.txt, pasa
  toda la bateria (--requiere-herramientas), empaqueta con PyInstaller (carpeta, consola), pone al
  lado ExifTool descomprimido del zip verificado, genera THIRD-PARTY-NOTICES.txt y BUILD-INFO.txt,
  pasa los tests del exe y deja en dist\ la carpeta ai-meta-cleaner\ y el zip con su .sha256.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\build.ps1 -SoloHerramientas
  powershell -ExecutionPolicy Bypass -File .\build.ps1
#>
param([switch] $SoloHerramientas, [string] $Commit = 'HEAD')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'      # Invoke-WebRequest va mucho mas rapido sin la barra

$lock = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot 'herramientas.lock.json') | ConvertFrom-Json
$tools = Join-Path $PSScriptRoot 'tools'
$descargas = Join-Path $tools '_descargas'
$temporal = Join-Path $env:TEMP ('aiclean-build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $tools, $descargas, $temporal | Out-Null

# Binarios que puede llevar _internal\ (cualquier otro hace fallar la compilacion: asi la lista de
# licencias de terceros no se queda corta sin que nadie se entere)
$BINARIOS_PERMITIDOS = @(
    # Python y su biblioteca estandar (licencias en el LICENSE.txt de Python: PSF, OpenSSL, libffi,
    # expat, bzip2, xz, mpdecimal) y el runtime de Visual C++ que Python distribuye con el
    '_internal\python312.dll', '_internal\VCRUNTIME140.dll', '_internal\VCRUNTIME140_1.dll',
    '_internal\libcrypto-3.dll', '_internal\libssl-3.dll', '_internal\libffi-8.dll',
    '_internal\_bz2.pyd', '_internal\_ctypes.pyd', '_internal\_decimal.pyd', '_internal\_elementtree.pyd',
    '_internal\_hashlib.pyd', '_internal\_lzma.pyd', '_internal\_multiprocessing.pyd', '_internal\_queue.pyd',
    '_internal\_socket.pyd', '_internal\_ssl.pyd', '_internal\_wmi.pyd', '_internal\pyexpat.pyd',
    '_internal\select.pyd', '_internal\unicodedata.pyd'
)

function Invocar([string] $Que, [scriptblock] $Bloque) {
    Write-Host "== $Que" -ForegroundColor Cyan
    & $Bloque
    if ($LASTEXITCODE -ne 0) { throw "$Que fallo (codigo $LASTEXITCODE)" }
}

function Descargar([string] $Url, [string] $Destino) {
    Invoke-WebRequest -Uri $Url -OutFile $Destino -UseBasicParsing -MaximumRedirection 10 -UserAgent 'aiclean-build'
}

function Sha256([string] $Ruta) {
    (Get-FileHash -Algorithm SHA256 $Ruta).Hash.ToLowerInvariant()
}

function BlobGit([string] $Ruta) {
    # Hash de git de un archivo: SHA-1 de "blob <tamano>\0" + contenido
    $datos = [IO.File]::ReadAllBytes($Ruta)
    $cabecera = [Text.Encoding]::ASCII.GetBytes("blob $($datos.Length)`0")
    $sha1 = [Security.Cryptography.SHA1]::Create()
    $todo = New-Object byte[] ($cabecera.Length + $datos.Length)
    [Array]::Copy($cabecera, $todo, $cabecera.Length)
    [Array]::Copy($datos, 0, $todo, $cabecera.Length, $datos.Length)
    -join ($sha1.ComputeHash($todo) | ForEach-Object { $_.ToString('x2') })
}

function Zip-Verificado($h) {
    # El zip de la herramienta en tools\_descargas\, descargado si falta, siempre con su SHA-256 comprobado
    $zip = Join-Path $descargas $h.archivo
    if (-not (Test-Path $zip) -or (Sha256 $zip) -ne $h.sha256) {
        Write-Host "== $($h.nombre) $($h.version): descargando" -ForegroundColor Cyan
        $tmp = Join-Path $temporal $h.archivo
        Descargar $h.url $tmp
        $hash = Sha256 $tmp
        if ($hash -ne $h.sha256) {
            throw "$($h.nombre): SHA-256 distinto del lock (esperado $($h.sha256), descargado $hash). No se instala."
        }
        Move-Item -Force -LiteralPath $tmp -Destination $zip
    }
    if ((Sha256 $zip) -ne $h.sha256) { throw "$($h.nombre): el zip de tools\_descargas no cuadra con el lock" }
    return $zip
}

function Extraer-Herramienta($h, [string] $Destino) {
    # Descomprime el zip verificado en $Destino (ExifTool trae su exe como "exiftool(-k).exe")
    $zip = Zip-Verificado $h
    $extraido = Join-Path $temporal ('x-' + [guid]::NewGuid().ToString('N'))
    Expand-Archive -Path $zip -DestinationPath $extraido
    $nombreZip = if ($h.nombre -eq 'exiftool') { 'exiftool(-k).exe' } else { $h.ejecutable }
    $exe = @(Get-ChildItem -LiteralPath $extraido -Recurse -File -Filter $nombreZip)
    if ($exe.Count -ne 1) { throw "$($h.nombre): no se encuentra $nombreZip en el zip" }
    if (Test-Path $Destino) { Remove-Item -Recurse -Force $Destino }
    New-Item -ItemType Directory -Force (Split-Path $Destino) | Out-Null
    Move-Item -LiteralPath $exe[0].Directory.FullName -Destination $Destino
    if ($nombreZip -ne $h.ejecutable) {
        Rename-Item -LiteralPath (Join-Path $Destino $nombreZip) -NewName $h.ejecutable
    }
}

function Instalar-Herramienta($h) {
    $destino = Join-Path $tools $h.destino
    $marca = Join-Path $destino '.instalado'
    $zip = Zip-Verificado $h
    if ((Test-Path $marca) -and ((Get-Content -Raw $marca).Trim() -eq "$($h.version) $($h.sha256)")) {
        Write-Host "== $($h.nombre) $($h.version): ya instalado" -ForegroundColor Green
        return
    }
    Extraer-Herramienta $h $destino
    [IO.File]::WriteAllText($marca, "$($h.version) $($h.sha256)")
    Write-Host "   instalado en $destino (SHA-256 comprobado)" -ForegroundColor Green
}

function Instalar-Muestras($m) {
    $destino = Join-Path $tools 'c2pa-muestras'
    New-Item -ItemType Directory -Force $destino | Out-Null
    $fuentes = @()
    foreach ($f in $m.fuentes) {
        foreach ($a in $f.archivos) {
            $nombre = Split-Path -Leaf $a.ruta
            $final = Join-Path $destino $nombre
            if ((Test-Path $final) -and (BlobGit $final) -eq $a.git_blob -and ($a.sha256 -eq '' -or (Sha256 $final) -eq $a.sha256)) {
                Write-Host "== muestra ${nombre}: ya instalada" -ForegroundColor Green
            } else {
                Write-Host "== muestra ${nombre}: descargando" -ForegroundColor Cyan
                $tmp = Join-Path $temporal $nombre
                Descargar "https://raw.githubusercontent.com/$($f.repo)/$($f.commit)/$($a.ruta)" $tmp
                $blob = BlobGit $tmp
                if ($blob -ne $a.git_blob) { throw "${nombre}: hash de git distinto del lock ($blob). No se instala." }
                $hash = Sha256 $tmp
                if ($a.sha256 -eq '') {
                    Write-Warning "${nombre}: el lock no tiene SHA-256; el de esta descarga (hash de git correcto) es $hash"
                } elseif ($hash -ne $a.sha256) {
                    throw "${nombre}: SHA-256 distinto del lock ($hash). No se instala."
                }
                Move-Item -Force -LiteralPath $tmp -Destination $final
            }
            $fuentes += "$nombre`t$($f.repo)@$($f.commit)`t$($a.ruta)`t$($f.licencia)"
        }
    }
    $texto = "Muestras oficiales C2PA para los tests (no se distribuyen). Licencia y origen de cada una:`r`n" + ($fuentes -join "`r`n") + "`r`n"
    Set-Content -Encoding ascii (Join-Path $destino 'FUENTES.txt') $texto
}

function Preparar-Herramientas {
    foreach ($h in $lock.herramientas) { Instalar-Herramienta $h }
    Instalar-Muestras $lock.muestras_c2pa
}

# ---------------------------------------------------------------------------
# Compilacion

$AVISOS_PY = @'
"""THIRD-PARTY-NOTICES.txt: licencias de lo que va dentro del zip, sacadas de lo instalado."""
import importlib.metadata as md, pathlib, sys

from PyInstaller.archive.readers import ZlibArchiveReader

destino, pyz, exiftool = map(pathlib.Path, sys.argv[1:4])
modulos = ZlibArchiveReader(str(pyz)).toc.keys()          # lo que PyInstaller metio en el exe
mapa = md.packages_distributions()
usadas = set()
for nombre in modulos:
    raiz = nombre.split(".")[0]
    if raiz in sys.stdlib_module_names or raiz == "aiclean" or raiz.startswith("_"):
        continue
    for d in mapa.get(raiz, []):
        usadas.add(d)
esperadas = {"rich", "markdown-it-py", "mdurl", "Pygments"}
if {u.lower() for u in usadas} != {e.lower() for e in esperadas}:
    sys.exit(f"dependencias empaquetadas inesperadas: {sorted(usadas)} (se esperaban {sorted(esperadas)})")

def licencias(dist):
    textos = []
    for f in dist.files or []:
        n = f.name.upper()
        if n.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")) or "/licenses/" in str(f).replace("\\", "/"):
            textos.append(pathlib.Path(f.locate()).read_text(encoding="utf-8", errors="replace"))
    return "\n".join(textos).strip()

linea = "=" * 78
partes = ["Avisos y licencias de terceros incluidos en ai-meta-cleaner (aiclean.exe).",
          "El codigo de aiclean es MIT (ver LICENSE).", ""]

def bloque(titulo, licencia, texto):
    partes.extend([linea, titulo, f"Licencia: {licencia}", linea, texto.strip(), ""])

ex = exiftool / "exiftool_files"
bloque("ExifTool (Phil Harvey) - tools\\exiftool\\", "la misma que Perl (Artistic License o GPL)",
       (ex / "LICENSE").read_text(encoding="utf-8", errors="replace")
       + "\n\nEl paquete de Windows de ExifTool incluye Strawberry Perl y librerias de MinGW; sus licencias"
         " van tal cual en tools\\exiftool\\exiftool_files\\Licenses_Strawberry_Perl.zip.")
bloque(f"Python {sys.version.split()[0]} (interprete y biblioteca estandar, en _internal\\)",
       "PSF License Agreement y las licencias de las librerias que incluye (OpenSSL, libffi, expat, zlib, bzip2, xz...)",
       (pathlib.Path(sys.base_prefix) / "LICENSE.txt").read_text(encoding="utf-8", errors="replace")
       + "\n\nVCRUNTIME140.dll y VCRUNTIME140_1.dll: Microsoft Visual C++ Redistributable, que Python para Windows"
         " distribuye con el interprete segun los terminos de redistribucion de Microsoft.")
pyi = md.distribution("pyinstaller")
bloque(f"PyInstaller {pyi.version} (cargador de aiclean.exe)", "GPL-2.0-or-later con la excepcion del bootloader",
       licencias(pyi))
for nombre in sorted(usadas, key=str.lower):
    d = md.distribution(nombre)
    clasificadores = [c.split("::")[-1].strip() for c in d.metadata.get_all("Classifier") or [] if c.startswith("License ::")]
    lic = d.metadata.get("License-Expression") or d.metadata.get("License") or ", ".join(clasificadores) or "ver texto"
    bloque(f"{d.metadata['Name']} {d.version}", lic.splitlines()[0], licencias(d))
destino.write_text("\n".join(partes), encoding="utf-8")
print("dependencias:", ", ".join(sorted(usadas, key=str.lower)))
'@

$ZIP_PY = @'
"""Zip de la distribucion (barras normales, carpeta raiz ai-meta-cleaner/) y su .sha256."""
import hashlib, pathlib, sys, zipfile

carpeta, zip_ = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
with zipfile.ZipFile(zip_, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in sorted(carpeta.rglob("*")):
        if p.is_file():
            z.write(p, (pathlib.Path(carpeta.name) / p.relative_to(carpeta)).as_posix())
h = hashlib.sha256(zip_.read_bytes()).hexdigest()
pathlib.Path(str(zip_) + ".sha256").write_text(f"{h}  {zip_.name}\n", encoding="ascii", newline="\n")
print(h)
'@

function Compilar {
    if (git status --porcelain) { throw 'Hay cambios sin commit: la compilacion usa un arbol limpio. Haz commit primero.' }
    $sha = (git rev-parse --verify "$Commit^{commit}").Trim()
    $arbol = (git rev-parse "$sha^{tree}").Trim()
    Write-Host "== Compilando el commit $sha (arbol $arbol)" -ForegroundColor Cyan

    $fuente = Join-Path $temporal 'fuente'
    Invocar 'git archive' { git archive --format=zip -o (Join-Path $temporal 'fuente.zip') $sha }
    Expand-Archive -Path (Join-Path $temporal 'fuente.zip') -DestinationPath $fuente

    Preparar-Herramientas
    New-Item -ItemType Directory -Force (Join-Path $fuente 'tools') | Out-Null
    foreach ($d in 'exiftool', 'c2patool', 'c2pa-muestras') {
        Copy-Item -Recurse (Join-Path $tools $d) (Join-Path $fuente "tools\$d")
    }

    # venv nuevo con las versiones fijadas (el de la carpeta de trabajo apunta a otro src\)
    $base = (Get-Content (Join-Path $PSScriptRoot '.venv\pyvenv.cfg') | Where-Object { $_ -match '^executable\s*=' }) -replace '^executable\s*=\s*', ''
    $venv = Join-Path $temporal 'venv'
    Invocar 'Crear venv' { & $base -m venv $venv }
    $py = Join-Path $venv 'Scripts\python.exe'
    Invocar 'Dependencias (requirements-dev.txt)' { & $py -m pip install -q --disable-pip-version-check -r (Join-Path $fuente 'requirements-dev.txt') }

    Push-Location $fuente
    try {
        Invocar 'Tests del codigo' { & $py -m pytest -q -p no:cacheprovider --requiere-herramientas --basetemp (Join-Path $temporal 'pt') }
        # El SVG y el .ico del commit tienen que ser los que genera el script, byte a byte
        Invocar 'Icono (assets\generar_icono.py --comprobar)' { & $py (Join-Path $fuente 'assets\generar_icono.py') --comprobar }
    } finally { Pop-Location }

    $version = ((Get-Content (Join-Path $fuente 'src\aiclean\__init__.py') | Where-Object { $_ -match '^__version__' }) -replace '.*"(.+)".*', '$1')
    if ($version -notmatch '^\d+\.\d+$') { throw "version no valida: $version (formato MAYOR.MENOR)" }
    $partes = $version.Split('.')
    $tupla = "($($partes[0]), $($partes[1]), 0, 0)"
    $fichero = Join-Path $temporal 'version.txt'
    @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=$tupla, prodvers=$tupla, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'juanitohtz'),
      StringStruct('FileDescription', 'aiclean - removes AI provenance metadata'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'aiclean'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 juanitohtz. MIT License'),
      StringStruct('OriginalFilename', 'aiclean.exe'),
      StringStruct('ProductName', 'ai-meta-cleaner'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding ascii $fichero

    $pyiDist = Join-Path $temporal 'pyi-dist'
    Invocar 'PyInstaller' {
        & $py -m PyInstaller --noconfirm --clean --onedir --console --name aiclean `
            --distpath $pyiDist --workpath (Join-Path $temporal 'pyi-build') --specpath (Join-Path $temporal 'pyi-build') `
            --paths (Join-Path $fuente 'src') --version-file $fichero --icon (Join-Path $fuente 'assets\icono.ico') --log-level WARN `
            --exclude-module PIL --exclude-module tkinter `
            (Join-Path $fuente 'src\aiclean\__main__.py')
    }
    $dist = Join-Path $temporal 'ai-meta-cleaner'
    Move-Item -LiteralPath (Join-Path $pyiDist 'aiclean') -Destination $dist

    $binarios = @(Get-ChildItem -LiteralPath (Join-Path $dist '_internal') -Recurse -File |
                  Where-Object { $_.Extension -in '.dll', '.pyd' } |
                  ForEach-Object { $_.FullName.Substring($dist.Length + 1) } | Sort-Object)
    $raros = @($binarios | Where-Object { $BINARIOS_PERMITIDOS -notcontains $_ })
    if ($raros) { throw "binarios no previstos en _internal (revisar licencias y la lista): $($raros -join ', ')" }

    $exiftool = $lock.herramientas | Where-Object nombre -eq 'exiftool'
    Extraer-Herramienta $exiftool (Join-Path $dist 'tools\exiftool')
    $avisos = Join-Path $temporal 'avisos.py'
    Set-Content -Encoding utf8 $avisos $AVISOS_PY
    $pyz = Join-Path $temporal 'pyi-build\aiclean\PYZ-00.pyz'
    Invocar 'THIRD-PARTY-NOTICES.txt' { & $py $avisos (Join-Path $dist 'THIRD-PARTY-NOTICES.txt') $pyz (Join-Path $dist 'tools\exiftool') }
    Copy-Item (Join-Path $fuente 'LICENSE'), (Join-Path $fuente 'README.md') $dist
    $pyiVersion = (& $py -m PyInstaller --version).Trim()
    @(
        "ai-meta-cleaner $version"
        "commit: $sha"
        "arbol: $arbol"
        "compilado (UTC): $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm'))"
        "Python: $((& $py -c 'import platform; print(platform.python_version())').Trim())"
        "PyInstaller: $pyiVersion"
        "ExifTool: $($exiftool.version) (zip SHA-256 $($exiftool.sha256))"
    ) | Set-Content -Encoding ascii (Join-Path $dist 'BUILD-INFO.txt')

    Push-Location $fuente
    try {
        Invocar 'Tests del exe' { & $py -m pytest -q -p no:cacheprovider --requiere-herramientas --basetemp (Join-Path $temporal 'pt-exe') tests\test_exe.py --exe (Join-Path $dist 'aiclean.exe') }
    } finally { Pop-Location }

    $nombreZip = "ai-meta-cleaner-v$version-win64.zip"
    $zip = Join-Path $temporal $nombreZip
    $empaquetar = Join-Path $temporal 'zip.py'
    Set-Content -Encoding utf8 $empaquetar $ZIP_PY
    Invocar 'Zip' { & $py $empaquetar $dist $zip }

    $salida = Join-Path $PSScriptRoot 'dist'
    New-Item -ItemType Directory -Force $salida | Out-Null
    foreach ($viejo in (Join-Path $salida 'ai-meta-cleaner'), (Join-Path $salida $nombreZip), (Join-Path $salida "$nombreZip.sha256")) {
        if (Test-Path $viejo) { Remove-Item -Recurse -Force -LiteralPath $viejo }    # compilacion anterior
    }
    Move-Item -LiteralPath $dist -Destination $salida
    Move-Item -LiteralPath $zip, "$zip.sha256" -Destination $salida
    Write-Host "== Listo: $(Join-Path $salida $nombreZip)" -ForegroundColor Green
    Get-Content (Join-Path $salida "$nombreZip.sha256")
}

try {
    if ($SoloHerramientas) {
        Preparar-Herramientas
        $ver = & (Join-Path $tools 'exiftool\exiftool.exe') -ver
        if ($LASTEXITCODE -ne 0) { throw "exiftool -ver fallo (codigo $LASTEXITCODE)" }
        Write-Host "== Listo. ExifTool $ver en $tools" -ForegroundColor Green
    } else {
        Compilar
    }
} finally {
    Remove-Item -Recurse -Force -LiteralPath $temporal -ErrorAction SilentlyContinue
}
