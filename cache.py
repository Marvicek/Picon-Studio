# -*- coding: utf-8 -*-
"""
cache.py
Disk cache (s JSON indexem) + Memory LRU cache pro hotové picony.
"""

import os
import json
import time
from cachetools import LRUCache


class PiconCache:
    def __init__(self, cfg: dict):
        self.disk_dir    = cfg['cache']['disk_dir']
        self.dnu_v_kesi  = int(cfg['cache']['dnu_v_kesi'])
        self.index_file  = os.path.join(self.disk_dir, 'cache.json')
        self._lru        = LRUCache(maxsize=int(cfg['cache']['memory_lru_size']))

        os.makedirs(self.disk_dir, exist_ok=True)

    # ── Memory LRU ────────────────────────────────────────────────────
    def get_memory(self, key: str):
        return self._lru.get(key)

    def set_memory(self, key: str, data: bytes):
        self._lru[key] = data

    # ── Disk cache (loga stažená z internetu) ────────────────────────
    def _load_index(self) -> dict:
        try:
            with open(self.index_file, 'r', encoding='utf-8') as f:
                return json.loads(f.read().strip())
        except Exception:
            return {}

    def _save_index(self, index: dict):
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                f.write(json.dumps(index))
        except Exception as e:
            print(f'[cache] Chyba při ukládání indexu: {e}')

    def get_disk(self, filename: str):
        """Vrátí bytes ze disk cache, nebo None pokud neexistuje."""
        if self.dnu_v_kesi <= 0:
            return None
        path = os.path.join(self.disk_dir, filename)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
        return None

    def set_disk(self, filename: str, data: bytes):
        """Uloží bytes na disk a zapíše do indexu."""
        if self.dnu_v_kesi <= 0:
            return
        path = os.path.join(self.disk_dir, filename)
        try:
            with open(path, 'wb') as f:
                f.write(data)
            index = self._load_index()
            index[filename] = int(time.time())
            self._save_index(index)
        except Exception as e:
            print(f'[cache] Chyba při ukládání na disk: {e}')

    def clear_expired(self):
        """Odstraní expirované soubory z disk cache."""
        if self.dnu_v_kesi <= 0:
            return
        index    = self._load_index()
        ts       = int(time.time())
        ttl      = 60 * 60 * 24 * self.dnu_v_kesi
        changed  = False

        for filename in list(index):
            if int(index[filename]) + ttl < ts:
                path = os.path.join(self.disk_dir, filename)
                if os.path.exists(path):
                    os.remove(path)
                del index[filename]
                changed = True

        # Odstraň PNG soubory které nejsou v indexu
        try:
            for f in os.listdir(self.disk_dir):
                if f.endswith('.png') and f not in index:
                    os.remove(os.path.join(self.disk_dir, f))
                    changed = True
        except Exception:
            pass

        if changed:
            self._save_index(index)
            print('[cache] Expirovaná cache vyčištěna.')
