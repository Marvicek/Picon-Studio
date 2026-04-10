# -*- coding: utf-8 -*-
"""
composer.py
Kompozice výsledného picon PNG:
  - Logo stanice (na pozadí)
  - Příznakové ikony (badges) dle konfigurace služby a layout.json
"""

import os
import io
import json
from PIL import Image, ImageDraw, ImageFont

BADGE_TYPES = ['dvbs2', 'dvbt2', 'iptv', 'radio', 'enc', 'hd', '4k']


def _load_layout(badges_dir: str, badge_defaults: dict) -> dict:
    """Načte layout.json ze složky služby, nebo vrátí výchozí pozice."""
    layout_file = os.path.join(badges_dir, 'layout.json')
    if os.path.exists(layout_file):
        try:
            with open(layout_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f'[composer] Chyba při načtení layout.json: {e}')
    return badge_defaults


def _load_badge_image(badge_type: str, badges_dir: str, shared_dir: str) -> Image.Image | None:
    """Načte PNG ikonku příznaku – nejdřív ze složky služby, pak ze shared/."""
    for directory in [badges_dir, shared_dir]:
        path = os.path.join(directory, f'{badge_type}.png')
        if os.path.exists(path):
            try:
                return Image.open(path).convert('RGBA')
            except Exception as e:
                print(f'[composer] Chyba při načtení badge {path}: {e}')
    return None


def _apply_badge(canvas: Image.Image, badge_img: Image.Image, props: dict):
    """Aplikuje badge na canvas s transformacemi (scale, rotation, opacity)."""
    scale    = float(props.get('scale', 1.0))
    rotation = float(props.get('rotation', 0))
    opacity  = float(props.get('opacity', 1.0))
    x        = int(props.get('x', 0))
    y        = int(props.get('y', 0))

    # Škálování
    new_w = max(1, int(badge_img.width  * scale))
    new_h = max(1, int(badge_img.height * scale))
    badge = badge_img.resize((new_w, new_h), Image.LANCZOS)

    # Otočení
    if rotation != 0:
        badge = badge.rotate(-rotation, expand=True, resample=Image.BICUBIC)

    # Průhlednost
    if opacity < 1.0:
        r, g, b, a = badge.split()
        a = a.point(lambda v: int(v * opacity))
        badge = Image.merge('RGBA', (r, g, b, a))

    # Vložení na canvas
    canvas.paste(badge, (x, y), badge)


def compose(logo_data: bytes | None, service_cfg: dict, active_badges: dict,
            cfg: dict, extra_layers: list | None = None) -> bytes:
    """
    Složí výsledný picon PNG.

    logo_data     – bytes PNG loga (nebo None → prázdné pozadí)
    service_cfg   – konfigurace služby z config.yaml
    active_badges – dict {badge_type: True/False} které příznaky zobrazit
    cfg           – celá konfigurace (pro rozměry, cesty, badge_defaults)
    """
    w = cfg['picon']['width']
    h = cfg['picon']['height']
    bg = cfg['picon']['background']
    badges_dir  = service_cfg.get('badges_dir', '')
    shared_dir  = os.path.join(cfg['sources']['badges_dir'], 'shared')
    badge_defs  = cfg['badge_defaults']

    # Pozadí
    if bg == 'transparent':
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    elif bg == 'black':
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 255))
    elif bg == 'white':
        canvas = Image.new('RGBA', (w, h), (255, 255, 255, 255))
    else:
        # hex barva #rrggbb
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

    # Načti layout – _layout_override z URL generátoru má přednost
    layout_override = service_cfg.get('_layout_override')
    if layout_override:
        layout = {**_load_layout(badges_dir, badge_defs), **layout_override}
    else:
        layout = _load_layout(badges_dir, badge_defs)

    # Vykresli aktivní badges
    for badge_type in BADGE_TYPES:
        if not active_badges.get(badge_type):
            continue
        badge_img = _load_badge_image(badge_type, badges_dir, shared_dir)
        if badge_img is None:
            print(f'[composer] Badge {badge_type} nenalezen (ani v shared/)')
            continue
        props = layout.get(badge_type, badge_defs.get(badge_type, {}))
        _apply_badge(canvas, badge_img, props)

    # Vykresli extra vrstvy (custom PNG z URL generatoru)
    if extra_layers:
        for el in extra_layers:
            try:
                el_img = Image.open(io.BytesIO(el['data'])).convert('RGBA')
                _apply_badge(canvas, el_img, el)
            except Exception as e:
                print(f'[composer] Chyba extra vrstvy: {e}')

    # Export jako PNG bytes
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
