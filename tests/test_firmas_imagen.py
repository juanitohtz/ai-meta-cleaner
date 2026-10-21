"""Firmas de imágenes: generadores, prompts y DigitalSourceType, con positivos y negativos."""

import pytest

from aiclean import firmas


@pytest.mark.parametrize("valor,esperado", [
    ("Midjourney v6.1", "Midjourney"),
    ("DALL-E 3", "OpenAI"), ("GPT-Image-1", "OpenAI"), ("ChatGPT", "OpenAI"),
    ("Adobe Firefly", "Adobe Firefly"),
    ("Google Imagen 3", "Google"), ("Made with Google AI", "Google"), ("Gemini 2.5 Flash Image", "Google"),
    ("Microsoft Designer", "Microsoft"), ("Bing Image Creator", "Microsoft"),
    ("Stable Diffusion XL", "Stable Diffusion"), ("SDXL 1.0", "Stable Diffusion"),
    ("AUTOMATIC1111 stable-diffusion-webui", "Stable Diffusion"),
    ("ComfyUI", "ComfyUI"), ("InvokeAI 4.2", "InvokeAI"), ("NovelAI", "NovelAI"),
    ("Leonardo.Ai", "Leonardo AI"), ("Ideogram 2.0", "Ideogram"), ("FLUX.1 [dev]", "FLUX"),
    ("Matplotlib version3.9.0, https://matplotlib.org/", "Matplotlib"),
])
def test_generadores_positivos(valor, esperado):
    assert firmas.generador(valor) == esperado


@pytest.mark.parametrize("valor", [
    "Adobe Photoshop 25.0 (Windows)", "GIMP 2.10", "Canon EOS R5", "Imagen de un gato en la playa",
    "Leonardo da Vinci", "Ana Garcia", "Playground de mi barrio", "Picasa", "darktable 4.6", "",
])
def test_generadores_negativos(valor):
    assert firmas.generador(valor) is None


@pytest.mark.parametrize("valor", [
    "a cat\nNegative prompt: ugly",
    "masterpiece, a cat\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1",
    '{"prompt": "a cat", "steps": 28, "sampler": "k_euler", "seed": 1}',
    '{"3": {"class_type": "KSampler", "inputs": {}}}',
    '{"nodes": [{"id": 1, "widgets_values": [1]}]}',
])
def test_prompts_positivos(valor):
    assert firmas.es_prompt(valor)


@pytest.mark.parametrize("valor", [
    "Steps: 3 para hacer el té", "Mi foto favorita", '{"titulo": "vacaciones"}', "{no es json",
    "Sampler: un muestrario de telas", "",
])
def test_prompts_negativos(valor):
    assert not firmas.es_prompt(valor)


@pytest.mark.parametrize("valor,esperado", [
    ("Made with Google AI", "Google AI"),
    ("a cat Job ID: 0f1e2d3c-4b5a-6978-8091-a2b3c4d5e6f7", "Midjourney"),
    ("Generated with AI", "IA"), ("Creado con inteligencia artificial", "IA"),
    ("Edited with Adobe Firefly", "Adobe Firefly"),
])
def test_firmas_en_texto_positivas(valor, esperado):
    assert firmas.firma_en_texto(valor) == esperado


@pytest.mark.parametrize("valor", [
    "La constelación de Géminis", "Midjourney es un programa", "Fotografía de Ana", "Made with love",
    "Job ID: 1234",
])
def test_firmas_en_texto_negativas(valor):
    assert firmas.firma_en_texto(valor) is None


@pytest.mark.parametrize("valor,esperado", [
    ("http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia", "trainedAlgorithmicMedia"),
    ("http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia", "compositeWithTrainedAlgorithmicMedia"),
    ("http://cv.iptc.org/newscodes/digitalsourcetype/algorithmicMedia", "algorithmicMedia"),
    ("http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture", None),
    ("http://cv.iptc.org/newscodes/digitalsourcetype/compositeCapture", None),
    ("", None),
])
def test_tipo_de_fuente(valor, esperado):
    assert firmas.tipo_fuente_ia(valor) == esperado
