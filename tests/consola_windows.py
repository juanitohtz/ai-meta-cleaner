"""Ayudante de test_exe.py: se engancha a la consola de otro proceso para leer lo que hay en
pantalla, escribir o pulsar Enter, como haría alguien delante de la ventana.

Va en un proceso aparte (python consola_windows.py PID leer|enter|escribir TEXTO), así la consola
de pytest no se toca. «leer» escribe en stdout, en UTF-8, las líneas de la pantalla hasta el
cursor; «escribir» teclea TEXTO y pulsa Enter.
"""

import ctypes
import sys
from ctypes import wintypes

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING = 1, 2, 3
KEY_EVENT, VK_RETURN, SCAN_RETURN = 1, 0x0D, 0x1C


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT), ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD), ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]


class _EVENTO(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_relleno", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENTO)]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
k32.ReadConsoleOutputCharacterW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, COORD,
                                            ctypes.POINTER(wintypes.DWORD)]
k32.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(INPUT_RECORD), wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD)]


def comprobar(ok, que: str) -> None:
    if not ok:
        sys.exit(f"{que}: error {ctypes.get_last_error()}")


def abrir(nombre: str) -> int:
    h = k32.CreateFileW(nombre, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                        OPEN_EXISTING, 0, None)
    comprobar(h and h != wintypes.HANDLE(-1).value, f"CreateFile {nombre}")
    return h


def leer() -> str:
    h = abrir("CONOUT$")
    info = CONSOLE_SCREEN_BUFFER_INFO()
    comprobar(k32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)), "GetConsoleScreenBufferInfo")
    ancho = info.dwSize.X
    lineas = []
    for fila in range(info.dwCursorPosition.Y + 1):          # fila a fila: cada una es una línea de pantalla
        buf = ctypes.create_unicode_buffer(ancho)
        leidos = wintypes.DWORD()
        comprobar(k32.ReadConsoleOutputCharacterW(h, buf, ancho, COORD(0, fila), ctypes.byref(leidos)),
                  "ReadConsoleOutputCharacter")
        lineas.append(buf[:leidos.value].rstrip())
    return "\n".join(lineas)


def enter(texto: str = "") -> None:
    """Teclea texto (cada carácter, pulsado y soltado) y después Enter."""
    h = abrir("CONIN$")
    pulsaciones = [(0, 0, c) for c in texto] + [(VK_RETURN, SCAN_RETURN, "\r")]
    teclas = (INPUT_RECORD * (2 * len(pulsaciones)))()
    for i, (vk, scan, caracter) in enumerate(pulsaciones):
        for j, pulsada in enumerate((True, False)):
            tecla = teclas[2 * i + j]
            tecla.EventType = KEY_EVENT
            tecla.Event.KeyEvent = KEY_EVENT_RECORD(pulsada, 1, vk, scan, caracter, 0)
    escritas = wintypes.DWORD()
    comprobar(k32.WriteConsoleInputW(h, teclas, len(teclas), ctypes.byref(escritas)) and escritas.value == len(teclas),
              "WriteConsoleInput")


if __name__ == "__main__":
    pid, accion = int(sys.argv[1]), sys.argv[2]
    k32.FreeConsole()
    comprobar(k32.AttachConsole(pid), f"AttachConsole {pid}")
    if accion == "leer":
        sys.stdout.buffer.write(leer().encode("utf-8"))
    elif accion == "escribir":
        enter(sys.argv[3])
    else:
        enter()
