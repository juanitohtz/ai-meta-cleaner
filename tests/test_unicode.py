"""Versiones Unicode: tablas de UCD 16.0 y unicodedata de Python (15.0 en 3.12).

Regresiones del intento de bajar las tablas a 15.0 (1.1): esas secuencias son válidas en
16.0, ya se conservaban en 0.1.0 y se tienen que seguir conservando.
"""

import unicodedata

import pytest

from aiclean import informe
from aiclean.formatos import texto
from aiclean.formatos.texto import limpiar_cadena

VS15, VS16, VS1 = chr(0xFE0E), chr(0xFE0F), chr(0xFE00)
ZWJ, ZWNJ, CGJ = chr(0x200D), chr(0x200C), chr(0x034F)

# Bases con secuencia VS15/VS16 en emoji-variation-sequences 16.0 que no estaban en 15.0
BASES_VS_EMOJI_16 = [
    0x23EB, 0x23EC, 0x23F0, 0x26CE, 0x2705, 0x270A, 0x270B, 0x2728, 0x274C,
    0x274E, 0x2754, 0x2755, 0x2795, 0x2796, 0x2797, 0x27B0, 0x27BF,
]
# StandardizedVariants 16.0 (FE00) que no estaban en 15.0: comillas y jeroglíficos
BASES_VS_ESTANDAR_16 = [0x2018, 0x2019, 0x201C, 0x201D, 0x13132, 0x131E0, 0x13338, 0x133DB, 0x133E5, 0x133E7, 0x133E8]
# Y las que 15.0 tenía y 16.0 retiró: se conservan igualmente
BASES_VS_ESTANDAR_SOLO_15 = [0x13092, 0x130A9, 0x1333B, 0x13403]


def limpio(s):
    return limpiar_cadena(s)[0]


@pytest.mark.parametrize("base", BASES_VS_EMOJI_16)
@pytest.mark.parametrize("vs", [VS15, VS16])
def test_vs_emoji_de_16_se_conserva(base, vs):
    s = f"ok {chr(base)}{vs} ok"
    assert limpio(s) == s


@pytest.mark.parametrize("base", BASES_VS_ESTANDAR_16 + BASES_VS_ESTANDAR_SOLO_15)
def test_variantes_estandar_se_conservan(base):
    s = f"{chr(base)}{VS1}"
    assert limpio(s) == s


def test_vs16_repetido_tras_base_de_16_deja_solo_uno():
    assert limpio(f"{chr(0x2705)}{VS16}{VS16}") == f"{chr(0x2705)}{VS16}"


def test_vs_tras_base_que_no_lo_admite_se_quita():
    # U+1F600 tiene presentación emoji por defecto y no tiene secuencia VS en ninguna versión
    assert limpio(f"{chr(0x1F600)}{VS16}") == chr(0x1F600)


# --- Dónde se combinan las tablas con unicodedata --------------------------

def test_versiones_se_informan_por_separado():
    v = informe.versiones()
    assert v["tablas_unicode"] == texto.VERSION_TABLAS_UNICODE == "16.0.0"
    assert v["unicodedata"] == unicodedata.unidata_version


# U+0897 ARABIC PEPET: marca combinante nueva en Unicode 16.0; para unicodedata 15.0 es Cn
PEPET = chr(0x0897)


@pytest.mark.skipif(unicodedata.category(PEPET) != "Cn", reason="este Python ya conoce U+0897")
def test_caracter_desconocido_para_unicodedata_se_trata_como_letra_o_marca():
    beh = chr(0x0628)
    for s in (f"{beh}{ZWJ}{PEPET}", f"{beh}{ZWNJ}{PEPET}", f"{beh}{CGJ}{PEPET}"):
        assert limpio(s) == s       # en la duda se conserva


def test_con_categorias_conocidas_las_reglas_no_cambian():
    beh, coma_arabe = chr(0x0628), chr(0x060C)
    assert limpio(f"{beh}{ZWNJ}{coma_arabe}") == f"{beh}{coma_arabe}"     # puntuación: se quita
    assert limpio(f"a{CGJ}b") == "ab"                                       # CGJ sin marca detrás
