# -*- coding: utf-8 -*-
"""
composer.py
Kompozice výsledného picon PNG:
  - Logo stanice (na pozadí)
  - Extra vrstvy (custom PNG z URL generátoru)
"""

import os
import io
from PIL import Image, ImageDraw, ImageFont


def _apply_layer(canvas: Image.Image, layer_img: Image.Image, props: dict):
    """Aplikuje vrstvu na canvas s transformacemi (scale, rotation, opacity)."""
    scale    = float(props.get('scale', 1.0))
    rotation = float(props.get('rotation', 0))
    opacity  = float(props.get('opacity', 1.0))
    x        = int(props.get('x', 0))
    y        = int(props.get('y', 0))

    new_w = max(1, int(layer_img.width  * scale))
    new_h = max(1, int(layer_img.height * scale))
    layer = layer_img.resize((new_w, new_h), Image.LANCZOS)

    if rotation != 0:
        layer = layer.rotate(-rotation, expand=True, resample=Image.BICUBIC)

    if opacity < 1.0:
        r, g, b, a = layer.split()
        a = a.point(lambda v: int(v * opacity))
        layer = Image.merge('RGBA', (r, g, b, a))

    canvas.paste(layer, (x, y), layer)


def compose(logo_data: bytes | None, service_cfg: dict,
            cfg: dict, extra_layers: list | None = None) -> bytes:
    """
    Složí výsledný picon PNG.

    logo_data    – bytes PNG loga (nebo None → prázdné pozadí)
    service_cfg  – konfigurace služby (zatím nevyužívána)
    cfg          – celá konfigurace (pro rozměry a pozadí)
    extra_layers – seznam vrstev [{data, x, y, scale, opacity, rotation}]
    """
    w  = cfg['picon']['width']
    h  = cfg['picon']['height']
    bg = cfg['picon']['background']

    # Pozadí
    if bg == 'transparent':
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    elif bg == 'black':
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 255))
    elif bg == 'white':
        canvas = Image.new('RGBA', (w, h), (255, 255, 255, 255))
    else:
        try:
            hex_color = bg.lstrip('#')
            r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
            canvas = Image.new('RGBA', (w, h), (r, g, b, 255))
        except Exception:
            canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))

    # Logo
    if logo_data:
        try:
            logo = Image.open(io.BytesIO(logo_data)).convert('RGBA')
            logo = logo.resize((w, h), Image.LANCZOS)
            canvas.paste(logo, (0, 0), logo)
        except Exception as e:
            print(f'[composer] Chyba při načtení loga: {e}')

    # Extra vrstvy (custom PNG z URL generátoru)
    if extra_layers:
        for el in extra_layers:
            try:
                el_img = Image.open(io.BytesIO(el['data'])).convert('RGBA')
                _apply_layer(canvas, el_img, el)
            except Exception as e:
                print(f'[composer] Chyba extra vrstvy: {e}')

    out = io.BytesIO()
    canvas.save(out, format='PNG')
    return out.getvalue()


def generate_placeholder_logo(text: str, width: int = 220, height: int = 132) -> bytes:
    """Vygeneruje placeholder logo s textem (pro testování bez obrázků)."""
    colors = {
        'A': (200,30,30),  'B': (30,100,200), 'C': (20,160,60),
        'D': (150,0,200),  'E': (200,120,0),  'F': (0,150,150),
    }
    first = text.strip().upper()[:1] if text.strip() else '?'
    color = colors.get(first, (80, 80, 80))

    img  = Image.new('RGBA', (width, height), color)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    except Exception:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 36)
        except Exception:
            font = ImageFont.load_default()

    display = text.upper()[:8] if text else '?'
    bbox = draw.textbbox((0, 0), display, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((width - tw) // 2, (height - th) // 2), display, fill=(255,255,255,255), font=font)

    out = io.BytesIO()
    img.save(out, format='PNG')
    return out.getvalue()
