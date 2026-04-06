import shutil
# -*- coding: utf-8 -*-
"""
chocholousek.py
Správa Chocholousek picon archivů (7z) z picon.cz.

Workflow:
  1. Přečte id_for_permalinks.log → URL pro každý archiv
  2. Stáhne archiv (7z) → rozbalí PNG do logos/chocholousek/<styl>/
  3. Rozdílový update – porovná ETag/Last-Modified HTTP hlavičky,
     stáhne znovu jen pokud se archiv změnil
  4. Denní automatický scheduler (volá se ze server.py)
"""

import os
import re
import json
import time
import threading
import requests
import py7zr

# URL pro stahování archivů – přesně tak jak to dělá originální plugin
PICON_CZ_BASE = "https://picon.cz/download/{id}/"
PLUGIN_VERSION = "3.1.200720"

# Hlavičky zkopírované z originálního plugin.pyo bytecodu
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:74.0) Gecko/20100101 Firefox/74.0",
    "Referer":    "picon.cz",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_session: requests.Session | None = None

def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(_HEADERS)
        # Navštiv homepage pro session cookies (stejně jako originální plugin)
        try:
            _session.get("https://picon.cz/", timeout=10, allow_redirects=True)
        except Exception:
            pass
    return _session

# Styly co chceme stahovat (ostatní ignorujeme)

PICON_CZ_PERMALINKS_ID = "7337"  # ID souboru id_for_permalinks na picon.cz

def fetch_fresh_permalinks(cache_dir: str, log_file: str) -> bool:
    """
    Stáhne aktuální id_for_permalinks.log z picon.cz (ID 7337).
    Vrátí True pokud se stáhnul nový soubor.
    """
    url = PICON_CZ_BASE.format(id=PICON_CZ_PERMALINKS_ID)
    sess = _get_session()
    try:
        r = sess.get(url, timeout=30, allow_redirects=True)
    except Exception as e:
        print(f'[chocholousek] Chyba stahování permalinks: {e}')
        return False

    if r.status_code != 200:
        print(f'[chocholousek] Permalinks: HTTP {r.status_code}')
        return False

    # Zkontroluj jestli je to textový soubor (ne HTML)
    ct = r.headers.get('Content-Type', '')
    if 'text/html' in ct:
        print(f'[chocholousek] Permalinks: server vrátil HTML (přihlášení vyžadováno?)')
        return False

    # Zkontroluj obsah – musí obsahovat "picon" záznamy
    try:
        text = r.content.decode('utf-8', errors='replace')
    except Exception:
        text = ''

    if 'picon' not in text.lower() or '_by_chocholousek' not in text:
        # Zkus jako 7z archiv (starší verze byly zabalené)
        if r.content[:6] == b'7z\xbc\xaf\'\x1c':
            import tempfile
            tmp = tempfile.mktemp(suffix='.7z', dir=cache_dir)
            try:
                with open(tmp, 'wb') as f:
                    f.write(r.content)
                with py7zr.SevenZipFile(tmp, mode='r') as arc:
                    names = arc.getnames()
                    log_names = [n for n in names if 'permalink' in n.lower() or n.endswith('.log')]
                    if log_names:
                        arc.extract(targets=log_names, path=cache_dir)
                        import glob, shutil
                        found = glob.glob(os.path.join(cache_dir, '**', '*.log'), recursive=True)
                        if found:
                            shutil.copy(found[0], log_file)
                            print(f'[chocholousek] Permalinks staženy (ze 7z): {os.path.basename(found[0])}')
                            return True
            except Exception as e:
                print(f'[chocholousek] Permalinks 7z extrakce chyba: {e}')
            finally:
                try: os.remove(tmp)
                except: pass
        print(f'[chocholousek] Permalinks: obsah nevypadá jako platný log')
        return False

    # Ulož nový log
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(text)
        lines = sum(1 for l in text.splitlines() if l.strip())
        print(f'[chocholousek] Permalinks aktualizovány: {lines} záznamů')
        return True
    except Exception as e:
        print(f'[chocholousek] Chyba uložení permalinks: {e}')
        return False

DEFAULT_STYLES = [
    "piconblack",
    "picontransparent",
    "picontransparentdark",
    "picontransparentwhite",
    "piconwhite",
    "picongray",
    "piconmirrorglass",
    "piconmonochrom",
    "piconsrhd",
]

_lock = threading.Lock()  # jen pro sdílenou session
_style_progress: dict = {}  # { style: { done, total, new, errors } }


# ── Parsování permalinks ───────────────────────────────────────────────────────

def parse_permalinks(log_file: str) -> dict:
    """
    Parsuje id_for_permalinks.log.
    Pro kazdy styl vybere preferovane rozliseni: 220x132, jinak nejvetsi dostupne.
    Vraci: { style: [ { id, filename, sat } ] }
    """
    RES_PRIO = {'220x132': 100, '400x240': 90, '400x170': 80,
                '150x90': 70, '100x60': 60, '96x64': 50,
                '132x46': 40, '50x30': 30}

    pat = re.compile(r'^(\d+)\s+(picon[a-z]+)-(\d+x\d+)-?(.*?)_by_chocholousek\.7z$')
    per_style: dict = {}

    try:
        with open(log_file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                m = pat.match(line)
                if not m:
                    continue
                pid      = m.group(1)
                style    = m.group(2)
                res      = m.group(3)
                sat      = m.group(4)
                filename = line.split(' ', 1)[1]
                per_style.setdefault(style, {}).setdefault(res, []).append(
                    {'id': pid, 'filename': filename, 'sat': sat}
                )
    except Exception as e:
        print(f'[chocholousek] Chyba parsovani permalinks: {e}')
        return {}

    result = {}
    for style, res_map in per_style.items():
        best = max(res_map.keys(), key=lambda r: RES_PRIO.get(r, 0))
        result[style] = res_map[best]
    return result



# ── Stažení a rozbalení archivu ───────────────────────────────────────────────

def _etag_file(cache_dir: str, style: str) -> str:
    return os.path.join(cache_dir, f'chocholousek_{style}.etag')


def _load_etag(cache_dir: str, style: str) -> dict:
    path = _etag_file(cache_dir, style)
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_etag(cache_dir: str, style: str, data: dict):
    with open(_etag_file(cache_dir, style), 'w') as f:
        json.dump(data, f)


_7Z_MAGIC = b'7z\xbc\xaf\x27\x1c'
import shutil


def _download_and_extract(pid: str, filename: str, style: str,
                          out_dir: str, cache_dir: str) -> int:
    """
    Stáhne jeden 7z archiv z picon.cz a rozbalí PNG do out_dir.
    Přesně napodobuje chování originálního Enigma2 pluginu:
    - URL: https://picon.cz/download/{ID}/
    - User-Agent: Firefox 74 (zkopírováno z plugin.pyo)
    - Referer: picon.cz
    - CookieJar session (homepage se navštíví jednou pro cookies)
    """
    url  = PICON_CZ_BASE.format(id=pid)
    etag = _load_etag(cache_dir, f'{style}_{pid}')
    sess = _get_session()

    # Pokud lokální složka neobsahuje žádné PNG, ignoruj etag – stáhni znovu
    if etag:
        try:
            has_pngs = any(f.lower().endswith('.png') for f in os.listdir(out_dir))
        except Exception:
            has_pngs = False
        if not has_pngs:
            etag = None

    headers = {}
    if etag:
        if etag.get('etag'): headers['If-None-Match']     = etag['etag']
        if etag.get('lm'):   headers['If-Modified-Since'] = etag['lm']

    try:
        r = sess.get(url, headers=headers, timeout=60,
                     stream=True, allow_redirects=True)
    except Exception as e:
        print(f'[chocholousek] Chyba připojení {url}: {e}')
        return 0

    if r.status_code == 304:
        return 0  # beze změny

    if r.status_code != 200:
        # Pokud dostaneme HTML (přihlašovací stránka) místo 7z, vypiš varování
        ct = r.headers.get('Content-Type', '')
        if 'text/html' in ct or r.status_code in (301, 302, 403):
            snippet = r.text[:150].replace('\n', ' ')
            print(f'[chocholousek] {filename}: HTTP {r.status_code} – server vrátil HTML místo 7z: {snippet!r}')
        else:
            print(f'[chocholousek] {filename}: HTTP {r.status_code} pro {url}')
        return 0

    # Načti data a zkontroluj magic bytes
    chunks = []
    first_chunk = b''
    for chunk in r.iter_content(65536):
        if not first_chunk:
            first_chunk = chunk[:6]
        chunks.append(chunk)
    data = b''.join(chunks)

    if first_chunk[:6] != _7Z_MAGIC:
        snippet = data[:150].decode('utf-8', errors='replace').replace('\n', ' ')
        print(f'[chocholousek] {filename}: odpověď není 7z archiv – {snippet!r}')
        return 0

    # Ulož do tmp a rozbal
    tmp_path = os.path.join(cache_dir, f'_tmp_{style}_{pid}.7z')
    tmp_dir  = tmp_path + '_extract'
    try:
        with open(tmp_path, 'wb') as fh:
            fh.write(data)

        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(tmp_dir, exist_ok=True)

        with py7zr.SevenZipFile(tmp_path, mode='r') as archive:
            archive.extractall(path=tmp_dir)

        # Přesuň PNG přímo do out_dir (bez podsložek z archivu)
        moved = 0
        for root_d, _, fnames in os.walk(tmp_dir):
            for fn in fnames:
                if fn.lower().endswith('.png'):
                    src = os.path.join(root_d, fn)
                    dst = os.path.join(out_dir, fn)
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.copy2(src, dst)
                    moved += 1

        _save_etag(cache_dir, f'{style}_{pid}', {
            'etag': r.headers.get('ETag', ''),
            'lm':   r.headers.get('Last-Modified', ''),
        })
        print(f'[chocholousek] {filename}: {moved} PNG')
        return moved

    except Exception as e:
        print(f'[chocholousek] Chyba extrakce {filename}: {e}')
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def update_style(style: str, archives: list, logos_dir: str, cache_dir: str,
                 progress_cb=None, cfg: dict = None) -> dict:
    """
    Provede rozdílový update jednoho stylu – sekvenčně archiv po archivu.
    Vrátí: { 'style': style, 'new': int, 'checked': int, 'errors': int }
    """
    total = len(archives)
    if total == 0:
        print(f'[chocholousek] {style}: žádné archivy v permalinks, přeskakuji')
        _style_progress[style] = {'done': 0, 'total': 0, 'new': 0, 'errors': 0}
        return {'style': style, 'new': 0, 'checked': 0, 'errors': 0}



    out_dir = os.path.join(logos_dir, 'chocholousek', style)
    os.makedirs(out_dir, exist_ok=True)

    done      = 0
    total_new = 0
    errors    = 0

    _style_progress[style] = {'done': 0, 'total': total, 'new': 0, 'errors': 0}
    print(f'[chocholousek] Styl {style}: {total} archivů...')

    for arch in archives:
        try:
            n = _download_and_extract(arch['id'], arch['filename'], style, out_dir, cache_dir)
            total_new += n
        except Exception as e:
            errors += 1
            print(f'[chocholousek] {style}/{arch["filename"]} chyba: {e}')
        done += 1
        _style_progress[style] = {'done': done, 'total': total, 'new': total_new, 'errors': errors}
        if progress_cb:
            progress_cb(style, done, total)

    print(f'[chocholousek] {style} hotovo: {total_new} nových, {errors} chyb')
    return {
        'style':   style,
        'new':     total_new,
        'checked': total,
        'errors':  errors,
        'dir':     out_dir,
    }


def update_all_styles(styles: list, permalinks: dict, logos_dir: str,
                      cache_dir: str, progress_cb=None) -> list:
    """Aktualizuje všechny styly – každý styl sekvenčně, archivy uvnitř paralelně."""
    results = []
    for style in styles:
        if style not in permalinks:
            print(f'[chocholousek] Styl "{style}" nenalezen v permalinks')
            continue
        archives = permalinks[style]
        print(f'[chocholousek] Styl {style}: {len(archives)} archivů...')
        r = update_style(style, archives, logos_dir, cache_dir, progress_cb)
        results.append(r)
        print(f'[chocholousek] {style} hotovo: {r["new"]} nových, {r["errors"]} chyb')
    return results


def get_style_progress(style: str) -> dict:
    """Vrátí aktuální průběh stahování stylu."""
    return _style_progress.get(style, {})


# ── Listing lokálních piconů (pro galerii) ────────────────────────────────────

def list_local_logos(logos_dir: str, style: str,
                     page: int = 0, page_size: int = 60,
                     search: str = '') -> dict:
    """Vrátí stránkovaný seznam PNG ze složky stylu."""
    style_dir = os.path.join(logos_dir, 'chocholousek', style)
    if not os.path.exists(style_dir):
        return {'total': 0, 'page': page, 'page_size': page_size,
                'has_more': False, 'logos': [], 'ready': False}

    try:
        # Hledej PNG přímo i rekurzivně (kdyby extrakce zanechala podsložky)
        direct = sorted(f for f in os.listdir(style_dir) if f.lower().endswith('.png'))
        if direct:
            files = direct
        else:
            # Fallback: rekurzivní hledání + přesun do style_dir
            files = []
            for root_d, _, fnames in os.walk(style_dir):
                if root_d == style_dir:
                    continue
                for fn in fnames:
                    if fn.lower().endswith('.png'):
                        src = os.path.join(root_d, fn)
                        dst = os.path.join(style_dir, fn)
                        try:
                            shutil.copy2(src, dst); os.remove(src)
                            files.append(fn)
                        except Exception:
                            files.append(fn)
            files.sort()
    except Exception:
        files = []

    if search:
        q = search.lower()
        files = [f for f in files if q in f.lower()]

    total = len(files)
    start = page * page_size
    logos = [
        {
            'name':         f,
            'download_url': f'/logos/chocholousek/{style}/{f}',
            'local':        True,
        }
        for f in files[start:start + page_size]
    ]

    return {
        'total':     total,
        'page':      page,
        'page_size': page_size,
        'has_more':  start + page_size < total,
        'logos':     logos,
        'ready':     total > 0,
    }


_status_cache: dict = {}   # { style: count } – obnovuje se po update
_status_cache_ts: float = 0.0
_STATUS_TTL = 30  # sekund – jak dlouho cachovat disk scan


def get_styles_status(logos_dir: str, permalinks: dict,
                      force: bool = False) -> list:
    """Vrátí stav každého stylu (počet lokálních piconů). Cache 30s."""
    global _status_cache, _status_cache_ts
    now = time.time()
    if not force and (now - _status_cache_ts) < _STATUS_TTL and _status_cache:
        counts = _status_cache
    else:
        counts = {}
        for style in DEFAULT_STYLES:
            style_dir = os.path.join(logos_dir, 'chocholousek', style)
            try:
                counts[style] = sum(1 for f in os.listdir(style_dir)
                                    if f.lower().endswith('.png')) if os.path.exists(style_dir) else 0
            except Exception:
                counts[style] = 0
        _status_cache    = counts
        _status_cache_ts = now

    return [
        {
            'style':    style,
            'count':    counts.get(style, 0),
            'archives': len(permalinks.get(style, [])),
            'ready':    counts.get(style, 0) > 0,
        }
        for style in DEFAULT_STYLES
    ]


def invalidate_status_cache():
    """Vymaže cache po dokončení update."""
    global _status_cache_ts
    _status_cache_ts = 0.0
