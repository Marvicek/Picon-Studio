# -*- coding: utf-8 -*-
"""
resolver.py
Fallback řetězec zdrojů log:
  1. Disk (lokální složka logos/)
  2. GitHub zdroje v pořadí dle config (github_sources[])
  3. Vlastní vzdálené úložiště (remote)
  4. Fallback default.png
"""

import os
import requests
from cache import PiconCache


class LogoResolver:
    def __init__(self, cfg: dict, cache: PiconCache):
        self.logos_dir      = cfg['sources']['logos_dir']
        self.github_sources = cfg['sources'].get('github_sources', [])
        self.remote_cfg     = cfg['sources'].get('remote', {})
        self.cache          = cache

    def resolve(self, filename: str) -> bytes | None:
        # 1. Disk
        data = self._from_disk(filename)
        if data:
            return data

        # 2. GitHub zdroje v pořadí
        for source in self.github_sources:
            if not source.get('enabled', True):
                continue
            base_url = f"https://raw.githubusercontent.com/{source['repo']}/master/{source.get('path','').strip('/')}/"
            token    = source.get('token') or None
            data     = self._from_url(base_url, filename, token)
            if data:
                return data

        # 3. Remote
        if self.remote_cfg.get('enabled') and self.remote_cfg.get('url'):
            data = self._from_url(self.remote_cfg['url'], filename)
            if data:
                return data

        # 4. Default
        return self._default_logo()

    def _from_disk(self, filename: str) -> bytes | None:
        path = os.path.join(self.logos_dir, filename)
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
        return None

    def _from_url(self, base_url: str, filename: str, token: str = None) -> bytes | None:
        cache_key = filename
        cached    = self.cache.get_disk(cache_key)
        if cached:
            return cached

        url     = base_url.rstrip('/') + '/' + filename
        headers = {}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        try:
            print(f'[resolver] Stahuji: {url}')
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                self.cache.set_disk(cache_key, resp.content)
                return resp.content
        except Exception as e:
            print(f'[resolver] Chyba {url}: {e}')
        return None

    def _default_logo(self) -> bytes | None:
        path = os.path.join(self.logos_dir, 'default.png')
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
        return None
