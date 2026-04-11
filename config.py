# -*- coding: utf-8 -*-
"""
config.py
Načítání a validace konfigurace z config.yaml.
"""

import os
import shutil
import yaml

DEFAULT_CONFIG = {
    'server':   {'host': '0.0.0.0', 'port': 8083, 'debug': False},
    'picon':    {'width': 1024, 'height': 1024, 'background': 'transparent'},
    'sources': {
        'logos_dir':  './logos',
        'github_sources': [],
        'remote': {'enabled': False, 'url': ''},
    },
    'own_github': {
        'enabled': False, 'repo': '', 'path': '1024',
        'token': '', 'commit_prefix': 'Picon: ',
    },
    'xbmc_kodi': {
        'username': '', 'password': '', 'cookies_file': '',
    },
    'cache': {'disk_dir': './cache', 'dnu_v_kesi': 7, 'memory_lru_size': 200},
    'services': {
        'skylink':     {'name_patterns': []},
        'sledovanitv': {'name_patterns': []},
        'oneplay':     {'name_patterns': []},
        'playcz':      {'name_patterns': []},
        'radia':       {'name_patterns': []},
        'ivysilani':   {'name_patterns': []},
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(script_dir: str, config_path: str = None) -> dict:
    config_file = config_path or os.path.join(script_dir, 'config.yaml')
    sample_file = os.path.join(script_dir, 'config.yaml.sample')

    if not os.path.exists(config_file) and os.path.exists(sample_file):
        shutil.copyfile(sample_file, config_file)
        print('[config] Vytvořen config.yaml ze sample souboru.')

    cfg = _deep_merge({}, DEFAULT_CONFIG)

    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                user_cfg = yaml.safe_load(f) or {}
            cfg = _deep_merge(cfg, user_cfg)
            print('[config] Konfigurace načtena z config.yaml')
        except Exception as e:
            print(f'[config] Chyba: {e} – používám výchozí hodnoty.')
    else:
        print('[config] config.yaml nenalezen – výchozí hodnoty.')

    # Absolutní cesty
    p = cfg['sources']['logos_dir']
    if not os.path.isabs(p):
        cfg['sources']['logos_dir'] = os.path.join(script_dir, p)

    p = cfg['cache']['disk_dir']
    if not os.path.isabs(p):
        cfg['cache']['disk_dir'] = os.path.join(script_dir, p)

    return cfg
