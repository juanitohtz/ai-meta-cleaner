"""Todo lo que cuenta como IA: servicios, dominios, generadores, prompts y valores de metadatos."""

from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlsplit

# Cambia cuando cambia lo que cuenta como IA (va en el informe JSON)
VERSION_REGLAS = "2026.10.21-1"

# Dominio -> servicio. Cuenta también cualquier subdominio (files.oaiusercontent.com...).
DOMINIOS_IA = {
    "chatgpt.com": "ChatGPT",
    "chat.openai.com": "ChatGPT",
    "openai.com": "OpenAI",
    "oaiusercontent.com": "OpenAI",
    "oaistatic.com": "OpenAI",
    "sora.com": "Sora",
    "gemini.google.com": "Gemini",
    "bard.google.com": "Gemini",
    "aistudio.google.com": "Google AI Studio",
    "labs.google": "Google Labs",
    "notebooklm.google.com": "NotebookLM",
    "claude.ai": "Claude",
    "anthropic.com": "Anthropic",
    "claudeusercontent.com": "Claude",
    "copilot.microsoft.com": "Copilot",
    "designer.microsoft.com": "Microsoft Designer",
    "midjourney.com": "Midjourney",
    "firefly.adobe.com": "Adobe Firefly",
    "leonardo.ai": "Leonardo",
    "ideogram.ai": "Ideogram",
    "krea.ai": "Krea",
    "playground.com": "Playground",
    "perplexity.ai": "Perplexity",
    "grok.com": "Grok",
    "x.ai": "xAI",
    "meta.ai": "Meta AI",
    "deepseek.com": "DeepSeek",
    "mistral.ai": "Mistral",
    "poe.com": "Poe",
    "character.ai": "Character.AI",
    "runwayml.com": "Runway",
    "pika.art": "Pika",
    "klingai.com": "Kling",
    "lumalabs.ai": "Luma",
    "suno.com": "Suno",
    "suno.ai": "Suno",
    "udio.com": "Udio",
    "elevenlabs.io": "ElevenLabs",
    "stability.ai": "Stability AI",
    "dreamstudio.ai": "DreamStudio",
    "civitai.com": "Civitai",
    "nightcafe.studio": "NightCafe",
    "replicate.com": "Replicate",
    "replicate.delivery": "Replicate",
    "huggingface.co": "Hugging Face",
}


def _host(url: str) -> str:
    url = url.strip()
    try:
        host = (urlsplit(url).hostname if "//" in url else url.split("/")[0]) or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def _dominio_ia(host: str) -> str | None:
    for dominio in DOMINIOS_IA:
        if host == dominio or host.endswith("." + dominio):
            return dominio
    return None


def servicio_ia(url: str | None) -> str | None:
    """Nombre del servicio de IA si la URL (o el host) es de uno conocido."""
    if not url:
        return None
    dominio = _dominio_ia(_host(url))
    return DOMINIOS_IA[dominio] if dominio else None


URL_GENERICA = "URL de procedencia"


def describir_url(url: str | None) -> str:
    """Versión segura de una URL para informes. De un servicio de la lista, "Servicio (dominio
    de la lista)"; de cualquier otra, solo "URL de procedencia" (con "dirección IP" o "sin
    dominio" si aplica): ni dominio, ni subdominios, ni ruta, ni consulta, que pueden
    identificar a alguien."""
    if not url:
        return ""
    host = _host(url)
    dominio = _dominio_ia(host)
    if dominio:
        return f"{DOMINIOS_IA[dominio]} ({dominio})"
    try:
        ipaddress.ip_address(host)
        return f"{URL_GENERICA} (dirección IP)"
    except ValueError:
        pass
    if not host or "." not in host:
        return f"{URL_GENERICA} (sin dominio)"
    return URL_GENERICA


# ---------------------------------------------------------------------------
# Generadores (imágenes; documentos en la etapa 4)
#
# Se buscan en campos de programa (Software, CreatorTool, softwareAgent, OriginatingProgram,
# Source/Software de PNG, Artist). Los nombres ambiguos se exigen con su forma completa
# ("Leonardo AI", no "Leonardo"; "Google Imagen", no "imagen", que en español es "image").

_GENERADORES = [(re.compile(p, re.I), nombre) for p, nombre in (
    (r"\bmidjourney\b", "Midjourney"),
    (r"\bdall[-· ]?e\b|\bgpt[- ]?image\b|\bgpt-4o\b|\bchatgpt\b|\bopenai\b", "OpenAI"),
    (r"\bfirefly\b", "Adobe Firefly"),
    (r"\bgoogle\s+(ai|imagen|gemini)\b|\bimagen\s*\d|\bgemini\b|\bnano\s+banana\b", "Google"),
    (r"\bmicrosoft\s+designer\b|\bbing\s+image\s+creator\b|\bcopilot\b", "Microsoft"),
    (r"\bstable[- ]?diffusion\b|\bsdxl\b|\bsd\s?3(\.5)?\b", "Stable Diffusion"),
    (r"\bautomatic1111\b|\ba1111\b|\bsd[- ]webui\b", "Stable Diffusion WebUI"),
    (r"\bcomfyui\b", "ComfyUI"),
    (r"\binvoke\s?ai\b", "InvokeAI"),
    (r"\bfooocus\b", "Fooocus"),
    (r"\bnovel\s?ai\b", "NovelAI"),
    (r"\bleonardo[ .]?ai\b", "Leonardo AI"),
    (r"\bideogram\b", "Ideogram"),
    (r"\bflux\.?1\b|\bblack\s+forest\s+labs\b", "FLUX"),
    (r"\bkrea(\.ai)?\b", "Krea"),
    (r"\bplayground\s?(ai|v\d)\b", "Playground"),
    (r"\bmeta\s+ai\b|\bimagine\s+with\s+meta\b", "Meta AI"),
    (r"\bgrok\b|\bxai\s+aurora\b", "Grok"),
    (r"\bdreamstudio\b", "DreamStudio"),
    (r"\bnightcafe\b", "NightCafe"),
    # Librerías de generación de documentos y gráficos: cuentan como generador
    (r"\bmatplotlib\b", "Matplotlib"),
)]


def generador(texto: str | None) -> str | None:
    """Nombre del generador si el texto de un campo de programa es de uno conocido."""
    if not texto:
        return None
    for patron, nombre in _GENERADORES:
        if patron.search(texto):
            return nombre
    return None


# Firmas inequívocas en texto libre (descripción, comentario, crédito): no basta un nombre
# suelto, que puede aparecer en una descripción normal ("la constelación de Géminis").
_FIRMAS_TEXTO = [(re.compile(p, re.I), nombre) for p, nombre in (
    (r"\bmade\s+with\s+google\s+ai\b", "Google AI"),
    (r"\bjob\s+id:\s*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "Midjourney"),
    (r"\b(made|generated|created|edited)\s+(with|by|using)\s+(adobe\s+)?firefly\b", "Adobe Firefly"),
    (r"\b(made|generated|created)\s+(with|by|using)\s+(an?\s+)?(ai|a\.i\.|artificial\s+intelligence)\b", "IA"),
    (r"\b(generad[oa]|cread[oa]|hech[oa])\s+(con|por|mediante)\s+(una?\s+)?(ia|inteligencia\s+artificial)\b", "IA"),
    (r"\bimagined\s+with\s+ai\b", "Meta AI"),
)]


def firma_en_texto(texto: str | None) -> str | None:
    """Generador o "IA" si un texto libre lleva una firma inequívoca de IA."""
    if not texto:
        return None
    for patron, nombre in _FIRMAS_TEXTO:
        if patron.search(texto):
            return nombre
    return None


# Prompts y parámetros de generación
_A1111_PASOS = re.compile(r"(^|[\n,]\s*)Steps:\s*\d+", re.I)
_A1111_OTROS = re.compile(r"\b(Sampler|CFG scale|Seed|Model hash|Schedule type):\s*\S", re.I)
_NEGATIVO = re.compile(r"(^|\n)Negative prompt:", re.I)
_CLAVES_NOVELAI = {"prompt", "steps", "sampler", "seed"}
_MAX_JSON = 32 * 1024 * 1024


def es_prompt(texto: str | None) -> bool:
    """El texto son parámetros de generación (A1111/Forge, ComfyUI, NovelAI, InvokeAI)."""
    if not texto:
        return False
    if _NEGATIVO.search(texto) or (_A1111_PASOS.search(texto) and _A1111_OTROS.search(texto)):
        return True
    t = texto.lstrip()
    if t[:1] in "{[" and len(t) <= _MAX_JSON:
        try:
            datos = json.loads(t)
        except ValueError:
            return False
        return _json_de_generacion(datos)
    return False


def _json_de_generacion(datos) -> bool:
    if isinstance(datos, dict):
        if _CLAVES_NOVELAI <= set(datos):
            return True                                          # NovelAI (Comment)
        nodos = list(datos.values())
        if nodos and all(isinstance(n, dict) and "class_type" in n for n in nodos):
            return True                                          # ComfyUI (prompt)
        if isinstance(datos.get("nodes"), list) and any(
                isinstance(n, dict) and "widgets_values" in n for n in datos["nodes"]):
            return True                                          # ComfyUI (workflow)
        if "model_weights" in datos or ("app_version" in datos and "image" in datos):
            return True                                          # InvokeAI (sd-metadata)
    return False


# IPTC Digital Source Type (http://cv.iptc.org/newscodes/digitalsourcetype/...)
DST_IA = {"trainedalgorithmicmedia", "compositewithtrainedalgorithmicmedia", "algorithmicmedia"}


def tipo_fuente_ia(valor: str | None) -> str | None:
    """El código de DigitalSourceType si es de IA ("trainedAlgorithmicMedia"...)."""
    if not valor:
        return None
    codigo = valor.strip().rstrip("/").rsplit("/", 1)[-1]
    return codigo if codigo.lower() in DST_IA else None


# Chunks de texto PNG: claves que solo escriben herramientas de generación (siempre se quitan
# en modo IA) y claves genéricas que solo se quitan si su valor es de IA
CLAVES_PNG_IA = {
    "parameters", "prompt", "workflow", "sd-metadata", "invokeai_metadata", "invokeai_graph",
    "dream", "postprocessing",
}
CLAVES_PNG_CONDICIONALES = {"comment", "description", "source", "software"}
