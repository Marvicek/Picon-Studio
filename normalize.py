# -*- coding: utf-8 -*-
"""
normalize.py
Normalizace jmen kanálů a remap tabulka.
Zachovává stejnou logiku jako původní service.picons.server.
"""

import os
import shutil
import unicodedata


def remove_diacritics(text: str) -> str:
    return str(unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8'))


def normalize_picon_name(picon: str) -> str:
    """Normalizuje jméno kanálu na název souboru piconu."""
    remove_strings  = [' hd', ' ad', ' md 1', ' md 2', ' md 3', ' md 4',
                       ' md 5', ' md 6', ' md 7', ' md 8', ' ', ':', '/', '.', '-']
    replace_strings = [('+', 'plus'), ('&', 'and')]

    picon = remove_diacritics(picon).strip().lower().replace('.png', '')
    for s in remove_strings:
        picon = picon.replace(s, '')
    for old, new in replace_strings:
        picon = picon.replace(old, new)
    return picon


def remap(picon: str, script_dir: str) -> str:
    """Vrátí přemapované jméno piconu podle remap.txt, nebo původní."""
    sample   = os.path.join(script_dir, 'remap.txt.sample')
    filename = os.path.join(script_dir, 'remap.txt')

    if not os.path.exists(filename) and os.path.exists(sample):
        shutil.copyfile(sample, filename)

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for row in f:
                row = row.strip()
                if not row or row.startswith('#'):
                    continue
                parts = row.split('>')
                if len(parts) == 2:
                    if normalize_picon_name(picon) == normalize_picon_name(parts[0]):
                        return parts[1].strip()
    except IOError as e:
        if e.errno != 2:
            print(f'[normalize] Chyba při načtení remap.txt: {e}')
    return picon


def sync_remap_from_sample(script_dir: str) -> None:
    """Přidá do remap.txt nové záznamy ze sample souboru."""
    sample   = os.path.join(script_dir, 'remap.txt.sample')
    filename = os.path.join(script_dir, 'remap.txt')

    if not (os.path.exists(filename) and os.path.exists(sample)):
        return

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            existing = [r for r in f if r.strip() and not r.startswith('#')]
        with open(sample, 'r', encoding='utf-8') as s:
            for row in s:
                if row.strip() and not row.startswith('#') and row not in existing:
                    with open(filename, 'a', encoding='utf-8') as f:
                        f.write(row)
    except IOError as e:
        if e.errno != 2:
            print(f'[normalize] Chyba při sync remap.txt: {e}')
