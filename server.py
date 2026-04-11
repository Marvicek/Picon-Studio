# -*- coding: utf-8 -*-
"""
Picon Generator Server v2.0
============================
Bottle HTTP server - galerie zdrojů, editor vrstev, push na GitHub.

Endpointy:
  GET  /picons/<nazev>         - Kodi/TVheadend kompatibilni
  GET  /picons/<c1>/<c2>       - podpora lomitka v nazvu
  GET  /editor                 - GUI (galerie + editor + publish)
  GET  /api/sources            - seznam GitHub zdroju
  GET  /api/gallery/<source>   - listing log z daneho GitHub zdroje
  GET  /api/logo               - stazeni PNG loga z URL (?url=...)
  POST /api/publish            - push hotoveho piconu na vlastni GitHub
  GET  /health                 - health check
"""

import os
import sys
import json
import time
import shutil
# Zajisti UTF-8 vystup bez ohledu na locale terminalu
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import base64
import threading
import argparse
import queue as _queue
import collections as _collections

from bottle import Bottle, run, response, request, static_file
import bottle as _bottle
_bottle.BaseRequest.MEMFILE_MAX = 500 * 1024 * 1024  # 500 MB upload limit

from config    import load_config
from cache     import PiconCache
from resolver  import LogoResolver
from normalize import normalize_picon_name, remap, sync_remap_from_sample
from composer  import compose
from github    import list_logos, download_logo, push_logo, clear_listing_cache, set_disk_cache, get_prefetch_progress, startup_prefetch, get_startup_progress
from chocholousek import DEFAULT_STYLES

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION    = '2.1.0'

cfg         = load_config(SCRIPT_DIR)
cache       = PiconCache(cfg)
resolver    = LogoResolver(cfg, cache)
app         = Bottle()

set_disk_cache(cache)  # propoj disk cache s github prefetchem
_SOURCES_CACHE  = []   # cache pro /api/sources – plnena pri startu, pak lazy


# ── Helpers ────────────────────────────────────────

def _logos_dir() -> str:
    """Vrati absolutni cestu k logos adresari."""
    d = cfg['sources']['logos_dir']
    return d if os.path.isabs(d) else os.path.join(SCRIPT_DIR, d)

def json_response(data, status=200):
    response.content_type = 'application/json; charset=utf-8'
    response.status = status
    return json.dumps(data, ensure_ascii=False, indent=2)


def detect_service(normalized_name):
    for svc_name, svc_cfg in cfg['services'].items():
        for pattern in svc_cfg.get('name_patterns', []):
            if pattern.lower() in normalized_name:
                return svc_name
    return None


def get_picon_bytes(picon_name):
    normalized = normalize_picon_name(picon_name)
    cached     = cache.get_memory(normalized)
    if cached:
        return cached

    remapped  = remap(picon_name, SCRIPT_DIR)
    filename  = normalize_picon_name(remapped) + '.png'
    logo_data = resolver.resolve(filename)

    svc_name  = detect_service(normalized)
    svc_cfg   = cfg['services'].get(svc_name, {}) if svc_name else {}

    result = compose(logo_data, svc_cfg, cfg)
    cache.set_memory(normalized, result)
    return result


# ── /picons – Kodi/TVheadend kompatibilni ────────────────────────────────────────

@app.route('/picons/<p1>/<p2>')
def picons_slash(p1, p2):
    return picons(p1 + '/' + p2)

@app.route('/picons/<picon>')
def picons(picon):
    if picon.lower().endswith('.png'):
        picon = picon[:-4]
    try:
        data = get_picon_bytes(picon)
        response.content_type = 'image/png'
        return data
    except Exception as e:
        print(f'[server] Chyba piconu "{picon}": {e}')
        response.status = 500
        return b''


# ── /api/sources – seznam GitHub zdroju ────────────────────────────────────────

@app.route('/api/sources')
def api_sources():
    """Vrati seznam zdroju – GitHub + local packs + chocho packs."""
    sources = []

    # GitHub zdroje – vždy přidáme, nezávisle na FS
    try:
        for src in cfg['sources'].get('github_sources', []):
            if src.get('enabled', True):
                sources.append({
                    'name': src['name'], 'type': 'github',
                    'repo': src['repo'], 'path': src.get('path', ''),
                })
    except Exception as e:
        print(f'[api/sources] Chyba github_sources: {e}')

    # Vlastni logo balicky (logos/packs/<nazev>/)
    try:
        packs_dir = os.path.join(_logos_dir(), 'packs')
        if os.path.isdir(packs_dir):
            for pack in sorted(os.listdir(packs_dir)):
                pack_path = os.path.join(packs_dir, pack)
                if os.path.isdir(pack_path):
                    count = sum(1 for f in os.listdir(pack_path) if f.lower().endswith('.png'))
                    sources.append({'name': pack, 'type': 'local_pack',
                                    'dir': pack_path, 'count': count})
    except Exception as e:
        print(f'[api/sources] Chyba local packs: {e}')

    # Chocholousek balicky (logos/chocholousek/<nazev>/)
    try:
        chocho_dir = os.path.join(_logos_dir(), 'chocholousek')
        if os.path.isdir(chocho_dir):
            for pack in sorted(os.listdir(chocho_dir)):
                pack_path = os.path.join(chocho_dir, pack)
                if os.path.isdir(pack_path):
                    count = sum(1 for f in os.listdir(pack_path) if f.lower().endswith('.png'))
                    sources.append({'name': pack, 'type': 'chocho_pack',
                                    'dir': pack_path, 'count': count})
    except Exception as e:
        print(f'[api/sources] Chyba chocho packs: {e}')

    # XBMC-Kodi.cz balicky (logos/xbmc-kodi/<nazev>/)
    try:
        xbmc_dir = os.path.join(_logos_dir(), 'xbmc-kodi')
        if os.path.isdir(xbmc_dir):
            for pack in sorted(os.listdir(xbmc_dir)):
                pack_path = os.path.join(xbmc_dir, pack)
                if os.path.isdir(pack_path):
                    count = sum(1 for f in os.listdir(pack_path) if f.lower().endswith('.png'))
                    sources.append({'name': pack, 'type': 'xbmc_pack',
                                    'dir': pack_path, 'count': count})
    except Exception as e:
        print(f'[api/sources] Chyba xbmc packs: {e}')

    print(f'[api/sources] Vracím {len(sources)} zdrojů: {[s["name"] for s in sources]}')
    response.content_type = 'application/json; charset=utf-8'
    response.headers['Cache-Control'] = 'no-cache'
    return json.dumps(sources, ensure_ascii=False)




@app.route('/api/sources/pack/upload', method='POST')
def api_pack_upload():
    """Nahraje PNG soubory do vlastniho balicku logos/packs/<nazev>/."""
    try:
        import re as _re
        pack_name = (request.forms.get('name') or '').strip()
        if not pack_name:
            return json_response({'ok': False, 'error': 'Chybi nazev balicku'}, 400)
        if not _re.match(r'^[\w\- ]+$', pack_name):
            return json_response({'ok': False, 'error': 'Neplatny nazev – pouze pismena, cisla, pomlcky, mezery'}, 400)

        pack_dir = os.path.join(_logos_dir(), 'packs', pack_name)
        os.makedirs(pack_dir, exist_ok=True)

        files = request.files.getall('files')
        if not files:
            return json_response({'ok': False, 'error': 'Zadne soubory – vyber PNG soubory'}, 400)

        added = 0
        errors = 0
        for f in files:
            fname = f.filename or ''
            if not fname.lower().endswith('.png'):
                continue
            safe_name = os.path.basename(fname)
            if not safe_name:
                continue
            dest = os.path.join(pack_dir, safe_name)
            try:
                if os.path.exists(dest):
                    os.remove(dest)
                # Stream primo na disk – necti vse do pameti (2000+ souboru!)
                with open(dest, 'wb') as fh:
                    while True:
                        chunk = f.file.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                added += 1
            except Exception as e:
                errors += 1
                print(f'[packs] {safe_name}: {e}')

        print(f'[packs] Pack "{pack_name}": +{added} PNG, {errors} chyb')
        return json_response({'ok': added > 0 or errors == 0, 'pack': pack_name,
                              'added': added, 'errors': errors})
    except Exception as e:
        print(f'[packs] upload chyba: {e}')
        return json_response({'ok': False, 'error': str(e)}, 500)


@app.route('/api/sources/pack/remove', method='POST')
def api_pack_remove():
    """Odstrani cely balicek."""
    import re, shutil
    data      = request.json or {}
    pack_name = data.get('name', '').strip()
    pack_type = data.get('pack_type', 'local_pack')

    if not pack_name:
        return json_response({'ok': False, 'error': 'Chybi nazev'}, 400)

    if pack_type == 'chocho_pack':
        pack_dir = os.path.join(_logos_dir(), 'chocholousek', pack_name)
    elif pack_type == 'xbmc_pack':
        pack_dir = os.path.join(_logos_dir(), 'xbmc-kodi', pack_name)
    else:
        pack_dir = os.path.join(_logos_dir(), 'packs', pack_name)

    if not os.path.isdir(pack_dir):
        return json_response({'ok': False, 'error': 'Balicek nenalezen'})

    shutil.rmtree(pack_dir, ignore_errors=True)
    print(f'[packs] Odstranen pack: {pack_name}')
    return json_response({'ok': True})


@app.route('/api/sources/chocho/upload', method='POST')
def api_chocho_upload():
    """Nahraje 7z archiv z picon.cz do pojmenovaneho balicku logos/chocholousek/<nazev>/."""
    try:
        import py7zr, shutil, tempfile, re as _re
    except ImportError as e:
        return json_response({'ok': False, 'error': f'Chybi knihovna: {e}. Spust: pip install py7zr'}, 500)

    try:
        pack_name = (request.forms.get('name') or '').strip()
        if not pack_name:
            return json_response({'ok': False, 'error': 'Chybi nazev balicku'}, 400)
        if not _re.match(r'^[\w\- ]+$', pack_name):
            return json_response({'ok': False, 'error': 'Neplatny nazev – pouze pismena, cisla, pomlcky, mezery'}, 400)

        files = request.files.getall('archives')
        if not files:
            return json_response({'ok': False, 'error': 'Zadne archivy – vyber .7z soubory'}, 400)

        pack_dir  = os.path.join(_logos_dir(), 'chocholousek', pack_name)
        cache_dir = cfg['cache']['disk_dir']
        os.makedirs(pack_dir, exist_ok=True)
        os.makedirs(cache_dir, exist_ok=True)

        total_added = 0
        results = []
        for f in files:
            fname = f.filename or 'archive.7z'
            import tempfile as _tf
            tmp_extract = None
            tmp_path    = None
            try:
                # Cti data primo do pameti – vyhni se problemu s f.save() na Windows
                raw = f.file.read()

                # Zapis do docasneho souboru s unikatnim nazvem
                fd, tmp_path = _tf.mkstemp(suffix='.7z', dir=cache_dir)
                with os.fdopen(fd, 'wb') as fh:
                    fh.write(raw)
                del raw  # uvolni pamet

                tmp_extract = tmp_path + '_dir'
                os.makedirs(tmp_extract, exist_ok=True)

                with py7zr.SevenZipFile(tmp_path, mode='r') as arc:
                    arc.extractall(path=tmp_extract)

                moved = 0
                for root_d, _, fnames in os.walk(tmp_extract):
                    for fn in fnames:
                        if fn.lower().endswith('.png'):
                            src = os.path.join(root_d, fn)
                            dst = os.path.join(pack_dir, fn)
                            if os.path.exists(dst):
                                os.remove(dst)
                            shutil.copy2(src, dst)
                            moved += 1

                total_added += moved
                results.append({'file': fname, 'ok': True, 'extracted': moved})
                print(f'[packs] Chocho pack "{pack_name}" – {fname}: {moved} PNG')

            except Exception as e:
                print(f'[packs] Chocho upload chyba {fname}: {e}')
                results.append({'file': fname, 'ok': False, 'error': str(e)})
            finally:
                if tmp_extract: shutil.rmtree(tmp_extract, ignore_errors=True)
                if tmp_path and os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except: pass

        return json_response({'ok': total_added > 0 or len(results) > 0,
                              'pack': pack_name, 'added': total_added, 'results': results})
    except Exception as e:
        print(f'[packs] chocho upload chyba: {e}')
        return json_response({'ok': False, 'error': str(e)}, 500)



@app.route('/api/sources/add', method='POST')
def api_sources_add():
    """Prida novy GitHub zdroj do config.yaml."""
    data  = request.json or {}
    repo  = data.get('repo', '').strip()
    path  = data.get('path', '').strip()
    token = data.get('token', '').strip()
    if not repo or '/' not in repo:
        return json_response({'ok': False, 'error': 'Neplatny repo format'}, 400)

    name = repo + ('/' + path if path else '')
    # Zkontroluj duplicity
    for src in cfg['sources'].get('github_sources', []):
        if src.get('name') == name or src.get('repo') == repo and src.get('path','') == path:
            return json_response({'ok': False, 'error': 'Zdroj jiz existuje'})

    new_src = {'name': name, 'repo': repo, 'path': path, 'enabled': True, 'token': token}
    cfg['sources'].setdefault('github_sources', []).append(new_src)
    _save_config()
    print(f'[sources] Pridan zdroj: {name}')
    return json_response({'ok': True, 'name': name})


@app.route('/api/sources/remove', method='POST')
def api_sources_remove():
    """Odebere GitHub zdroj z config.yaml."""
    name = (request.json or {}).get('name', '').strip()
    if not name:
        return json_response({'ok': False, 'error': 'Chybi name'}, 400)

    sources = cfg['sources'].get('github_sources', [])
    new_sources = [s for s in sources if s.get('name') != name]
    if len(new_sources) == len(sources):
        return json_response({'ok': False, 'error': 'Zdroj nenalezen'})

    cfg['sources']['github_sources'] = new_sources
    clear_listing_cache()
    _save_config()
    print(f'[sources] Odebran zdroj: {name}')
    return json_response({'ok': True})


def _save_config():
    """Ulozi aktualni cfg zpet do config.yaml."""
    import yaml
    config_file = os.path.join(SCRIPT_DIR, 'config.yaml')
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as e:
        print(f'[config] Chyba ukladani: {e}')


# ── /api/gallery – listing log (?source=MarhyCZ/picons) ────────────────────────────────────────

@app.route('/api/gallery')
def api_gallery():
    source_name = request.query.get('source', '').strip()
    page        = int(request.query.get('page', 0))
    search      = request.query.get('q', '').strip()
    page_size   = 60

    # Vlastni pack (logos/packs/<nazev>/)
    if source_name and os.path.isdir(os.path.join(_logos_dir(), 'packs', source_name)):
        pack_dir = os.path.join(_logos_dir(), 'packs', source_name)
        return json_response(_list_local_pack(pack_dir, source_name, 'packs', page, page_size, search))

    # Chocholousek pack (logos/chocholousek/<nazev>/)
    if source_name and os.path.isdir(os.path.join(_logos_dir(), 'chocholousek', source_name)):
        pack_dir = os.path.join(_logos_dir(), 'chocholousek', source_name)
        return json_response(_list_local_pack(pack_dir, source_name, 'chocholousek', page, page_size, search))

    # XBMC-Kodi.cz pack (logos/xbmc-kodi/<nazev>/)
    if source_name and os.path.isdir(os.path.join(_logos_dir(), 'xbmc-kodi', source_name)):
        pack_dir = os.path.join(_logos_dir(), 'xbmc-kodi', source_name)
        return json_response(_list_local_pack(pack_dir, source_name, 'xbmc-kodi', page, page_size, search))

    # Chocholousek stary format (chocholousek/style)
    if source_name.startswith('chocholousek/'):
        style  = source_name.split('/', 1)[1]
        result = list_local_logos(cfg['sources']['logos_dir'], style, page, search=search)
        result['source'] = source_name
        return json_response(result)

    # GitHub zdroj
    source = next(
        (s for s in cfg['sources'].get('github_sources', [])
         if s['name'] == source_name and s.get('enabled', True)),
        None
    )
    if not source:
        return json_response({'error': f'Zdroj "{source_name}" nenalezen'}, 404)

    token  = source.get('token') or None
    result = list_logos(source['repo'], source.get('path', ''), token,
                        page=page, search=search)
    result['source']   = source_name
    result['prefetch'] = get_prefetch_progress(f"{source['repo']}/{source.get('path','')}/p{page}")
    return json_response(result)


def _list_local_pack(pack_dir, pack_name, pack_type, page, page_size, search):
    """Vrati strankovany seznam PNG ze slozky balicku."""
    try:
        files = sorted(f for f in os.listdir(pack_dir) if f.lower().endswith('.png'))
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
            'download_url': f'/logos/{pack_type}/{pack_name}/{f}',
            'local':        True,
            'cached':       False,
        }
        for f in files[start:start + page_size]
    ]
    return {'total': total, 'page': page, 'page_size': page_size,
            'has_more': start + page_size < total, 'logos': logos, 'source': pack_name}



@app.route('/api/gallery/startup-progress')
def api_startup_progress():
    """Vrati stav startup prefetche GitHub zdroju."""
    return json_response(get_startup_progress())



@app.route('/api/gallery/refresh', method='POST')
def api_gallery_refresh():
    """Vymaze listing cache a spusti prefetch vsech PNG pro dany zdroj na pozadi."""
    source_name = (request.json or {}).get('source', '')
    source = next(
        (s for s in cfg['sources'].get('github_sources', [])
         if s['name'] == source_name),
        None
    )
    if not source:
        return json_response({'ok': False, 'error': 'Zdroj nenalezen'}, 404)

    repo  = source['repo']
    path  = source.get('path', '')
    token = source.get('token') or None

    # 1. Smaz listing cache – pristi /api/gallery znovu stahne ze site
    clear_listing_cache(repo, path)

    # 2. Spust uplny prefetch na pozadi
    def _do_prefetch():
        from github import _fetch_all_logos, _prefetch_one
        items = _fetch_all_logos(repo, path, token)
        print(f'[refresh] {source_name}: prefetch {len(items)} souboru...')
        from concurrent.futures import ThreadPoolExecutor, as_completed
        done = 0
        with ThreadPoolExecutor(max_workers=20) as pool:
            futs = [pool.submit(_prefetch_one, item, token) for item in items]
            for _ in as_completed(futs):
                done += 1
        print(f'[refresh] {source_name}: prefetch hotov ({done} souboru)')

    threading.Thread(target=_do_prefetch, daemon=True).start()
    return json_response({'ok': True, 'source': source_name})


# ── XBMC-Kodi.cz refresh – spusti scraper na pozadi ────────────────────────────────────────

_xbmc_progress: dict = {}  # { pack_name: {running, done, total, new, error} }

@app.route('/api/xbmc/credentials')
def api_xbmc_get_credentials():
    """Vrati ulozene XBMC credentials (heslo maskovane)."""
    xbmc_cfg = cfg.get('xbmc_kodi', {})
    return json_response({
        'username': xbmc_cfg.get('username', ''),
        'password': '??????' if xbmc_cfg.get('password') else '',
    })


@app.route('/api/xbmc/credentials', method='POST')
def api_xbmc_save_credentials():
    """Ulozi XBMC credentials do config.yaml."""
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    if not username:
        return json_response({'ok': False, 'error': 'Chybi username'}, 400)
    cfg.setdefault('xbmc_kodi', {})['username'] = username
    if password and password != '??????':
        cfg['xbmc_kodi']['password'] = password
    _save_config()
    print(f'[xbmc] Credentials ulozeny pro uzivatele "{username}"')
    return json_response({'ok': True})



@app.route('/api/xbmc/refresh', method='POST')
def api_xbmc_refresh():
    """Spusti xbmc_kodi_scraper pro dany balicek na pozadi."""
    try:
        data      = request.json or {}
        pack_name = (data.get('pack') or '').strip()
        if not pack_name:
            return json_response({'ok': False, 'error': 'Chybi nazev balicku'})

        if _xbmc_progress.get(pack_name, {}).get('running'):
            return json_response({'ok': False, 'error': 'Aktualizace jiz bezi'})

        xbmc_cfg  = cfg.get('xbmc_kodi', {})
        username  = xbmc_cfg.get('username', '')
        password  = xbmc_cfg.get('password', '')
        cookies_f = xbmc_cfg.get('cookies_file', '')

        if not username and not cookies_f:
            return json_response({'ok': False,
                                  'error': 'Chybi prihlasovaci udaje (xbmc_kodi.username/password v config.yaml)'})
    except Exception as e:
        return json_response({'ok': False, 'error': f'Request error: {e}'})

    out_dir = os.path.join(_logos_dir(), 'xbmc-kodi', pack_name)

    def _run():
        _xbmc_progress[pack_name] = {'running': True, 'done': 0, 'total': 0, 'new': 0, 'error': None}
        try:
            import sys as _sys
            _sys.path.insert(0, SCRIPT_DIR)
            from scripts.xbmc_kodi_scraper import login, load_cookies, download_attachments, load_state, save_state, get_new_attachment_ids
            import requests as _req

            sess = _req.Session()
            sess.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
                'Accept-Language': 'cs,en;q=0.5',
            })

            # Prihlaseni
            if cookies_f and os.path.exists(cookies_f):
                load_cookies(sess, cookies_f)
            else:
                ok = login(sess, username, password)
                if not ok:
                    _xbmc_progress[pack_name]['error'] = 'Prihlaseni selhalo'
                    _xbmc_progress[pack_name]['running'] = False
                    return

            # Inkrementalni sber – pouzije stav z .xbmc_state.json
            print(f'[xbmc] Spoustim aktualizaci balicku "{pack_name}"...')
            state = load_state(out_dir)
            if state.get('max_aid', 0) == 0:
                print(f'[xbmc] Prvni spusteni – stahuji kompletne')
            else:
                print(f'[xbmc] Inkrementalni mod od AID {state["max_aid"]}')

            def _prog(done, total, new):
                _xbmc_progress[pack_name]['done']  = done
                _xbmc_progress[pack_name]['total'] = total
                _xbmc_progress[pack_name]['new']   = new

            attachments, page_count = get_new_attachment_ids(sess, state, _prog)
            state['last_page_count'] = page_count
            _xbmc_progress[pack_name]['total'] = len(attachments)

            if not attachments:
                print(f'[xbmc] Balicek "{pack_name}": zadne nove prilohy ?')
                save_state(out_dir, state)
            else:
                os.makedirs(out_dir, exist_ok=True)
                stats = download_attachments(sess, attachments, out_dir, state, skip_existing=True)
                _xbmc_progress[pack_name]['new']  = stats.get('ok', 0)
                _xbmc_progress[pack_name]['done'] = len(attachments)
                print(f'[xbmc] Balicek "{pack_name}" aktualizovan: +{stats.get("ok",0)} novych')

        except Exception as e:
            print(f'[xbmc] Chyba aktualizace "{pack_name}": {e}')
            _xbmc_progress[pack_name]['error'] = str(e)
        finally:
            _xbmc_progress[pack_name]['running'] = False

    threading.Thread(target=_run, daemon=True).start()
    return json_response({'ok': True, 'pack': pack_name})


@app.route('/api/xbmc/progress')
def api_xbmc_progress():
    """Vrati stav probihajicich XBMC aktualizaci."""
    return json_response(_xbmc_progress)


@app.route('/api/logo/cached/<key>')
def api_logo_cached(key):
    """Primy pristup k disk cache – bez proxy, maximalni rychlost."""
    import re
    if not re.match(r'^[a-f0-9]{32}\.png$', key):
        response.status = 400
        return b''
    data = cache.get_disk(key)
    if data:
        response.content_type = 'image/png'
        response.headers['Cache-Control'] = 'public, max-age=86400'
        return data
    response.status = 404
    return b''


@app.route('/api/logo')
def api_logo():
    url   = request.query.get('url', '').strip()
    token = request.query.get('token', '').strip() or None
    if not url:
        return json_response({'error': 'Chybi parametr url'}, 400)

    import hashlib
    cache_key = hashlib.md5(url.encode()).hexdigest() + '.png'
    cached    = cache.get_disk(cache_key)
    if cached:
        response.content_type = 'image/png'
        response.headers['Cache-Control'] = 'public, max-age=86400'
        return cached

    data = download_logo(url, token)
    if data:
        cache.set_disk(cache_key, data)
        response.content_type = 'image/png'
        response.headers['Cache-Control'] = 'public, max-age=86400'
        return data

    response.status = 404
    return b''


# ── /api/publish – push na vlastni GitHub ────────────────────────────────────────

@app.route('/api/publish', method='POST')
def api_publish():
    try:
        data = request.json or {}

        own = cfg.get('own_github', {})
        token = own.get('token', '')
        if not token:
            return json_response({'error': 'Chybi GitHub token – nastav ho v zalozce GitHub'}, 400)

        # PNG data – base64 z frontendu
        png_b64  = data.get('png_base64', '')
        filename = data.get('filename', '')
        if not png_b64 or not filename:
            return json_response({'error': 'Chybi png_base64 nebo filename'}, 400)

        # Repo a path lze přepsat z frontendu (upload fronta)
        repo = data.get('repo') or own.get('repo', '')
        path = data.get('path') or own.get('path', '')
        if not repo:
            return json_response({'error': 'Chybi cilovy repozitar'}, 400)

        png_bytes = base64.b64decode(png_b64)
        msg       = own.get('commit_prefix', 'Picon: ') + filename

        result = push_logo(
            repo    = repo,
            path    = path,
            filename= filename,
            data    = png_bytes,
            token   = token,
            commit_message = msg,
        )
        return json_response(result)
    except Exception as e:
        return json_response({'error': str(e)}, 500)


# ── /api/github/* – GitHub správce repozitáře ─────────────────────────────────

@app.route('/api/github/creds', method='GET')
def api_github_creds_get():
    own = cfg.get('own_github', {})
    token = own.get('token', '')
    masked = (token[:6] + '…' + token[-4:]) if len(token) > 10 else ('*' * len(token) if token else '')
    return json_response({
        'token_set': bool(token),
        'token_masked': masked,
        'repo':    own.get('repo', ''),
        'path':    own.get('path', ''),
        'enabled': own.get('enabled', False),
    })

@app.route('/api/github/creds', method='POST')
def api_github_creds_set():
    data  = request.json or {}
    token = data.get('token', '').strip()
    repo  = data.get('repo', '').strip()
    path  = data.get('path', '').strip()
    if not cfg.get('own_github'):
        cfg['own_github'] = {}
    if token:
        cfg['own_github']['token'] = token
    cfg['own_github']['repo']    = repo
    cfg['own_github']['path']    = path
    cfg['own_github']['enabled'] = bool(repo and cfg['own_github'].get('token'))
    _save_config()
    return json_response({'ok': True})

@app.route('/api/github/repos', method='GET')
def api_github_repos():
    own   = cfg.get('own_github', {})
    token = own.get('token')
    if not token:
        return json_response({'error': 'Chybi token'}, 400)
    import requests as req
    headers = {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}'}
    try:
        r = req.get('https://api.github.com/user/repos?per_page=100&sort=updated', headers=headers, timeout=15)
        if r.status_code != 200:
            return json_response({'error': f'GitHub API: {r.status_code}'}, r.status_code)
        repos = [{'full_name': i['full_name'], 'private': i['private'],
                  'description': i.get('description',''), 'updated_at': i['updated_at']}
                 for i in r.json()]
        return json_response({'ok': True, 'repos': repos})
    except Exception as e:
        return json_response({'error': str(e)}, 500)

@app.route('/api/github/browse', method='GET')
def api_github_browse():
    own   = cfg.get('own_github', {})
    token = own.get('token')
    repo  = request.query.get('repo') or own.get('repo', '')
    path  = request.query.get('path', '').strip('/')
    if not repo or not token:
        return json_response({'error': 'Chybi repo nebo token'}, 400)
    import requests as req
    url = f"https://api.github.com/repos/{repo}/contents/{path}" if path else f"https://api.github.com/repos/{repo}/contents"
    headers = {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}'}
    try:
        r = req.get(url, headers=headers, timeout=15)
        if r.status_code == 404:
            return json_response({'error': 'Slozka neexistuje'}, 404)
        if r.status_code != 200:
            return json_response({'error': f'GitHub API: {r.status_code}'}, r.status_code)
        items = [{'name': i['name'], 'type': i['type'], 'path': i['path'],
                  'sha': i.get('sha',''), 'size': i.get('size',0),
                  'download_url': i.get('download_url','')}
                 for i in r.json()]
        items.sort(key=lambda x: (0 if x['type']=='dir' else 1, x['name'].lower()))
        return json_response({'ok': True, 'items': items, 'path': path, 'repo': repo})
    except Exception as e:
        return json_response({'error': str(e)}, 500)

@app.route('/api/github/delete', method='POST')
def api_github_delete():
    own   = cfg.get('own_github', {})
    token = own.get('token')
    if not token:
        return json_response({'error': 'Chybi token'}, 400)
    data      = request.json or {}
    path      = data.get('path', '').strip('/')
    sha       = data.get('sha', '')
    item_type = data.get('type', 'file')   # 'file' nebo 'dir'
    repo      = data.get('repo') or own.get('repo', '')
    import requests as req
    headers = {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'}

    def delete_file(fpath, fsha):
        # Pokud nemáme SHA, načteme ho z API
        if not fsha:
            check = req.get(f"https://api.github.com/repos/{repo}/contents/{fpath}",
                            headers=headers, timeout=10)
            if check.status_code != 200:
                return False
            info = check.json()
            fsha = info.get('sha', '') if isinstance(info, dict) else ''
        if not fsha:
            return False
        dr = req.delete(
            f"https://api.github.com/repos/{repo}/contents/{fpath}",
            headers=headers,
            json={'message': 'Picon: smazan ' + fpath.split('/')[-1], 'sha': fsha},
            timeout=15)
        return dr.status_code in (200, 204)

    def delete_recursive(rpath):
        r = req.get(f"https://api.github.com/repos/{repo}/contents/{rpath}",
                    headers=headers, timeout=15)
        if r.status_code != 200:
            return ['Nelze nacist ' + rpath + ': ' + str(r.status_code)]
        items = r.json()
        if not isinstance(items, list):
            return [rpath + ' neni slozka']
        errors = []
        for item in items:
            if item['type'] == 'dir':
                errors.extend(delete_recursive(item['path']))
            elif item['type'] == 'file':
                if not delete_file(item['path'], item.get('sha', '')):
                    errors.append(item['path'])
        return errors

    # Rozlišení: smazat soubor nebo celou složku rekurzivně
    if item_type == 'file':
        if delete_file(path, sha):
            return json_response({'ok': True})
        return json_response({'error': 'Smazani selhalo'}, 500)

    errors = delete_recursive(path)
    return json_response({'ok': len(errors) == 0, 'errors': errors})

@app.route('/api/github/mkdir', method='POST')
def api_github_mkdir():
    own   = cfg.get('own_github', {})
    token = own.get('token')
    if not token:
        return json_response({'error': 'Chybi token'}, 400)
    data   = request.json or {}
    folder = data.get('path', '').strip('/')
    repo   = data.get('repo') or own.get('repo', '')
    if not folder:
        return json_response({'error': 'Chybi nazev slozky'}, 400)
    import requests as req, base64
    url     = f"https://api.github.com/repos/{repo}/contents/{folder}/.gitkeep"
    headers = {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'}
    body    = {'message': f'Picon: vytvorena slozka {folder}',
               'content': base64.b64encode(b'').decode()}
    # Pokud .gitkeep uz existuje, GitHub vyzaduje SHA existujiciho souboru (jinak 422)
    try:
        check = req.get(url, headers=headers, timeout=10)
        if check.status_code == 200:
            existing_sha = check.json().get('sha', '')
            if existing_sha:
                body['sha'] = existing_sha
    except Exception:
        pass
    r = req.put(url, headers=headers, json=body, timeout=15)
    if r.status_code in (200, 201):
        return json_response({'ok': True})
    return json_response({'error': f'HTTP {r.status_code}: {r.text[:200]}'}, r.status_code)


@app.route('/api/github/repo/create', method='POST')
def api_github_repo_create():
    """Vytvoří nový repozitář pro přihlášeného uživatele."""
    own   = cfg.get('own_github', {})
    token = own.get('token')
    if not token:
        return json_response({'error': 'Chybi token'}, 400)
    data        = request.json or {}
    name        = data.get('name', '').strip()
    description = data.get('description', '').strip()
    private     = bool(data.get('private', False))
    if not name:
        return json_response({'error': 'Chybi nazev repozitare'}, 400)
    import requests as req
    headers = {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}',
               'Content-Type': 'application/json'}
    body = {'name': name, 'description': description, 'private': private, 'auto_init': True}
    try:
        r = req.post('https://api.github.com/user/repos', headers=headers, json=body, timeout=15)
        if r.status_code == 201:
            info = r.json()
            return json_response({'ok': True, 'full_name': info['full_name'], 'html_url': info['html_url']})
        return json_response({'error': f'HTTP {r.status_code}: {r.json().get("message", r.text[:200])}'}, r.status_code)
    except Exception as e:
        return json_response({'error': str(e)}, 500)


@app.route('/api/github/repo/delete', method='POST')
def api_github_repo_delete():
    """Smaže repozitář (nevratná operace – vyžaduje delete_repo scope)."""
    own   = cfg.get('own_github', {})
    token = own.get('token')
    if not token:
        return json_response({'error': 'Chybi token'}, 400)
    data = request.json or {}
    repo = data.get('repo', '').strip()
    if not repo:
        return json_response({'error': 'Chybi nazev repozitare'}, 400)
    import requests as req
    headers = {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}'}
    try:
        r = req.delete(f'https://api.github.com/repos/{repo}', headers=headers, timeout=15)
        if r.status_code == 204:
            return json_response({'ok': True})
        msg = ''
        try: msg = r.json().get('message', '')
        except Exception: pass
        return json_response({'error': f'HTTP {r.status_code}: {msg or r.text[:200]}'}, r.status_code)
    except Exception as e:
        return json_response({'error': str(e)}, 500)


# ── /editor – GUI ────────────────────────────────────────

@app.route('/editor')
def editor():
    resp = static_file('editor.html', root=os.path.join(SCRIPT_DIR, 'static'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/static/<filename>')
def static(filename):
    return static_file(filename, root=os.path.join(SCRIPT_DIR, 'static'))

@app.route('/ping')
def ping():
    response.content_type = 'text/plain'
    return 'pong'

@app.route('/health')
def health():
    return json_response({'status': 'ok', 'version': VERSION, 'port': cfg['server']['port']})

@app.route('/api/config')
def api_config():
    return json_response({
        'picon': cfg['picon'],
        'services': list(cfg['services'].keys()),
    })

@app.route('/api/services/add', method='POST')
def api_services_add():
    """Prida noveho poskytovatele do config.yaml."""
    import re as _re
    data = request.json or {}
    name = data.get('name', '').strip()
    if not name or not _re.match(r'^[\w-]+$', name):
        return json_response({'ok': False, 'error': 'Neplatny nazev (pouze pismena, cisla, pomlcky)'}, 400)
    if name in cfg.get('services', {}):
        return json_response({'ok': False, 'error': 'Poskytovatel jiz existuje'})
    cfg.setdefault('services', {})[name] = {'name_patterns': []}
    _save_config()
    print(f'[services] Pridan poskytovatel: {name}')
    return json_response({'ok': True, 'name': name})

@app.route('/api/services/remove', method='POST')
def api_services_remove():
    """Odebere poskytovatele z config.yaml."""
    data = request.json or {}
    name = data.get('name', '').strip()
    if not name:
        return json_response({'ok': False, 'error': 'Chybi name'}, 400)
    if name not in cfg.get('services', {}):
        return json_response({'ok': False, 'error': 'Poskytovatel nenalezen'}, 404)
    del cfg['services'][name]
    _save_config()
    print(f'[services] Odebran poskytovatel: {name}')
    return json_response({'ok': True})

@app.route('/api/config/size', method='POST')
def api_config_size():
    """Ulozi nove rozmery piconu do config.yaml."""
    data = request.json or {}
    w = int(data.get('width', 0))
    h = int(data.get('height', 0))
    if w < 16 or h < 16 or w > 4096 or h > 4096:
        return json_response({'ok': False, 'error': 'Neplatne rozmery (16?4096)'}, 400)
    cfg['picon']['width']  = w
    cfg['picon']['height'] = h
    _save_config()
    print(f'[config] Rozmery piconu zmeneny na {w}x{h}')
    return json_response({'ok': True, 'width': w, 'height': h})

@app.route('/')
def index():
    response.status = 302
    response.set_header('Location', '/editor')
    return ''


# ══════════════════════════════════════════════════════════════════════════════
#  URL GENERATOR  –  /logo/<provider>/<quality>/<channel>
#  + CRUD API pro logo_templates.yaml
# ══════════════════════════════════════════════════════════════════════════════

import yaml as _yaml

_LOGO_TEMPLATES_FILE = os.path.join(SCRIPT_DIR, 'logo_templates.yaml')

def _load_logo_templates() -> dict:
    """Nacte logo_templates.yaml nebo vrati prazdny dict."""
    if os.path.exists(_LOGO_TEMPLATES_FILE):
        try:
            with open(_LOGO_TEMPLATES_FILE, 'r', encoding='utf-8') as f:
                return _yaml.safe_load(f) or {}
        except Exception as e:
            print(f'[logo_templates] Chyba cteni: {e}')
    return {}

def _save_logo_templates(data: dict):
    with open(_LOGO_TEMPLATES_FILE, 'w', encoding='utf-8') as f:
        _yaml.dump(data, f, allow_unicode=True, sort_keys=False)

def _find_logo_file(filename: str) -> bytes | None:
    """Hleda PNG soubor v logos/ a vsech podadresarich (packs, chocholousek, xbmc-kodi)."""
    if not filename:
        return None
    logos = _logos_dir()
    # 1. Prima cesta
    full = os.path.join(logos, filename)
    if os.path.exists(full):
        with open(full, 'rb') as f:
            return f.read()
    # 2. Vsechny podadresare
    for subdir in ['packs', 'chocholousek', 'xbmc-kodi']:
        base = os.path.join(logos, subdir)
        if not os.path.isdir(base):
            continue
        for pack in os.listdir(base):
            p = os.path.join(base, pack, filename)
            if os.path.exists(p):
                with open(p, 'rb') as f:
                    return f.read()
    return None


@app.route('/api/logo/file')
def api_logo_file():
    """Vrati PNG soubor podle jmena souboru z logos/ adresare (pro nahled v URL generatoru)."""
    filename = request.query.get('name', '').strip()
    if not filename or '..' in filename or filename.startswith('/'):
        response.status = 400
        return b''
    data = _find_logo_file(filename)
    if data:
        response.content_type = 'image/png'
        response.headers['Cache-Control'] = 'public, max-age=3600'
        return data
    response.status = 404
    return b''


# ── /logo/<provider>/<quality>/<channel> ─────────────────────────────────────

@app.route('/logo/<provider>/<quality>/<channel>')
def logo_url(provider, quality, channel):
    """
    Dynamicky generuje picon na zaklade URL parametru.
    Priklad: /logo/o2/hd/ceskatelevize1
    Volitelny parametr: ?layers=id1,id2  – aktivni vrstvy z URL generatoru
    """
    templates = _load_logo_templates()
    tmpl = templates.get(provider)
    if not tmpl:
        response.status = 404
        return b''

    # 1. Najdi logo: channel mapping -> default -> resolver (GitHub cache + disk)
    channels  = tmpl.get('channels', {})
    logo_file = channels.get(channel) or tmpl.get('default_logo') or ''
    # Normalizuj priponu
    if logo_file and not logo_file.lower().endswith('.png'):
        logo_file += '.png'
    logo_data = _find_logo_file(logo_file) if logo_file else None
    if not logo_data and logo_file:
        logo_data = resolver.resolve(logo_file)

    # 2. service_cfg
    svc_cfg = cfg['services'].get(provider, {})

    # 5. Extra vrstvy – z ?layers= parametru nebo vsechny aktivni ze sablony
    import urllib.parse as _ul
    layers_param = request.query.get('layers', '').strip()
    active_layer_ids = set(layers_param.split(',')) if layers_param else None
    tmpl_layers = tmpl.get('layers', [])

    extra_layers = []
    for layer in tmpl_layers:
        lid = layer.get('id', '')
        if active_layer_ids is not None:
            if lid not in active_layer_ids:
                continue
        else:
            if not layer.get('active', True):
                continue
        src = layer.get('src', '')
        layer_data = None
        if '/api/logo/file?name=' in src:
            fname = _ul.unquote(src.split('name=', 1)[1])
            layer_data = _find_logo_file(fname)
        elif src.startswith('data:image/'):
            import base64 as _b64
            try:
                header, b64data = src.split(',', 1)
                layer_data = _b64.b64decode(b64data)
            except Exception:
                pass
        elif src and not src.startswith('/') and not src.startswith('http'):
            layer_data = _find_logo_file(src if src.lower().endswith('.png') else src + '.png')
        if layer_data:
            extra_layers.append({
                'data':     layer_data,
                'x':        layer.get('x', 0),
                'y':        layer.get('y', 0),
                'scale':    layer.get('scale', 1.0),
                'opacity':  layer.get('opacity', 1.0),
                'rotation': layer.get('rotation', 0),
            })

    try:
        result = compose(logo_data, svc_cfg, cfg, extra_layers=extra_layers)
        response.content_type = 'image/png'
        response.set_header('Cache-Control', 'no-cache')
        return result
    except Exception as e:
        print(f'[logo_url] Chyba: {e}')
        response.status = 500
        return b''


# ── API: nacti vsechny templaty ───────────────────────────────────────────────

@app.route('/api/logo_templates', method='GET')
def api_logo_templates_get():
    return json_response(_load_logo_templates())


# ── API: uloz cely template pro poskytovatele ─────────────────────────────────

@app.route('/api/logo_templates', method='POST')
def api_logo_templates_save():
    data = request.json or {}
    provider = data.get('provider', '').strip()
    if not provider:
        return json_response({'ok': False, 'error': 'Chybi provider'}, 400)

    templates = _load_logo_templates()
    templates[provider] = {
        'default_logo': data.get('default_logo', ''),
        'channels':     data.get('channels', {}),
        'layers':       data.get('layers', []),
    }
    _save_logo_templates(templates)
    print(f'[logo_templates] Ulozen provider: {provider}')
    return json_response({'ok': True})


# ── API: smazat poskytovatele ─────────────────────────────────────────────────

@app.route('/api/logo_templates/<provider>', method='DELETE')
def api_logo_templates_delete(provider):
    templates = _load_logo_templates()
    if provider not in templates:
        return json_response({'ok': False, 'error': 'Provider nenalezen'}, 404)
    del templates[provider]
    _save_logo_templates(templates)
    print(f'[logo_templates] Odebran provider: {provider}')
    return json_response({'ok': True})


# ── /api/logo/upload – nahrání PNG loga do logos/ adresáře ───────────────────

@app.route('/api/logo/upload', method='POST')
def api_logo_upload():
    """Nahraje PNG soubor do logos/ adresáře. Vrátí jméno souboru."""
    f = request.files.get('file')
    if not f:
        return json_response({'ok': False, 'error': 'Chybí soubor'}, 400)
    fname = os.path.basename(f.filename or 'logo.png')
    if not fname.lower().endswith('.png'):
        fname += '.png'
    # Bezpečnost: jen alfanumerické, pomlčky, tečky
    import re as _re
    fname = _re.sub(r'[^\w\-.]', '_', fname)
    dest = os.path.join(_logos_dir(), fname)
    os.makedirs(_logos_dir(), exist_ok=True)
    f.save(dest, overwrite=True)
    print(f'[logo/upload] Uloženo: {dest}')
    return json_response({'ok': True, 'filename': fname})



@app.route('/api/chocholousek/import', method='POST')
def api_chocho_import():
    """
    Prijme jeden nebo vice .7z archivu (multipart upload),
    rozbali PNG do prislusneho stylu podle jmena souboru.
    Jmeno souboru musi obsahovat styl, napr:
      piconblack-220x132-13.0E_by_chocholousek.7z  – styl piconblack
    """
    import py7zr, shutil, tempfile
    from chocholousek import DEFAULT_STYLES
    logos_dir = _logos_dir()
    cache_dir = cfg['cache']['disk_dir']
    os.makedirs(cache_dir, exist_ok=True)

    uploaded = request.files.getall('archives')
    if not uploaded:
        return json_response({'ok': False, 'error': 'Zadne soubory'}, status=400)

    results = []
    for f in uploaded:
        fname = f.filename or 'unknown.7z'
        style = None
        for s in DEFAULT_STYLES:
            if s in fname.lower():
                style = s
                break
        if style is None:
            results.append({'file': fname, 'ok': False, 'error': 'Neznamy styl v nazvu souboru'})
            continue

        out_dir = os.path.join(logos_dir, 'chocholousek', style)
        os.makedirs(out_dir, exist_ok=True)

        tmp = tempfile.NamedTemporaryFile(suffix='.7z', delete=False, dir=cache_dir)
        try:
            f.save(tmp.name)
            tmp.close()
            with py7zr.SevenZipFile(tmp.name, mode='r') as archive:
                names = [n for n in archive.getnames() if n.lower().endswith('.png')]
                tmp_extract = tmp.name + '_dir'
                os.makedirs(tmp_extract, exist_ok=True)
                try:
                    archive.extract(targets=names, path=tmp_extract)
                    moved = 0
                    for root_d, _, fnames_list in os.walk(tmp_extract):
                        for fn in fnames_list:
                            if fn.lower().endswith('.png'):
                                shutil.copy2(os.path.join(root_d, fn), os.path.join(out_dir, fn)); os.remove(os.path.join(root_d, fn))
                                moved += 1
                finally:
                    shutil.rmtree(tmp_extract, ignore_errors=True)
            print(f'[chocho] Import {fname}: {moved} PNG – {style}')
            results.append({'file': fname, 'ok': True, 'style': style, 'extracted': moved})
        except Exception as e:
            print(f'[chocho] Import {fname} chyba: {e}')
            results.append({'file': fname, 'ok': False, 'error': str(e)})
        finally:
            try: os.remove(tmp.name)
            except: pass

    ok_count = sum(1 for r in results if r.get('ok'))
    return json_response({'ok': ok_count > 0, 'results': results, 'imported': ok_count})



# ── /api/log/stream – SSE live log ────────────────────────────────────────

import sys as _sys

_log_subscribers: list = []
_log_lock   = threading.Lock()
_log_buffer = _collections.deque(maxlen=500)  # kruhovy buffer poslednich 500 zprav

class _LogInterceptor:
    """Zachyti print() vystup a rozesle SSE odberatelum."""
    def __init__(self, original):
        self._orig = original

    def write(self, text):
        try:
            self._orig.write(text)
        except Exception:
            pass
        text = text.rstrip('\n')
        if text.strip():
            with _log_lock:
                _log_buffer.append(text)
                dead = []
                for q in _log_subscribers:
                    try:
                        q.put_nowait(text)
                    except Exception:
                        dead.append(q)
                for q in dead:
                    _log_subscribers.remove(q)

    def flush(self):
        try:
            self._orig.flush()
        except Exception:
            pass

    def fileno(self):
        try:
            return self._orig.fileno()
        except Exception:
            raise AttributeError('fileno')

    def isatty(self):
        try:
            return self._orig.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        # Deleguj vse ostatni na original (encoding, errors, ?)
        return getattr(self._orig, name)

# Interceptor se instaluje az v __main__ bloku (viz nize)
# aby neovlivnil waitress inicializaci


@app.route('/api/log/poll')
def api_log_poll():
    """Polling endpoint misto SSE - kompatibilni s waitress."""
    since = int(request.query.get('since', 0))
    with _log_lock:
        buf = list(_log_buffer)  # deque – list, pak lze slicovat
    msgs = buf[since:]
    return json_response({'msgs': msgs, 'next': since + len(msgs)})


@app.route('/api/log/stream')
def api_log_stream():
    """SSE stream - funguje jen s wsgiref, pro waitress pouzij /api/log/poll."""
    q = _queue.Queue(maxsize=200)
    with _log_lock:
        _log_subscribers.append(q)
        # Posli poslednich N zprav z bufferu
        for msg in list(_log_buffer)[-20:]:
            try: q.put_nowait(msg)
            except: pass

    def generate():
        yield 'data: ping\n\n'
        while True:
            try:
                msg = q.get(timeout=10)
                safe = msg.replace('\n', ' ').replace('\r', '')
                yield f'data: {safe}\n\n'
            except _queue.Empty:
                yield 'data: ping\n\n'
            except GeneratorExit:
                break
        with _log_lock:
            try: _log_subscribers.remove(q)
            except ValueError: pass

    from bottle import response as _resp
    _resp.content_type = 'text/event-stream'
    _resp.headers['Cache-Control']     = 'no-cache'
    _resp.headers['X-Accel-Buffering'] = 'no'
    return generate()


@app.route('/logos/chocholousek/<style>/<filename>')
def serve_chocho_logo(style, filename):
    logos_dir = _logos_dir()
    root = os.path.join(logos_dir, 'chocholousek', style)
    return static_file(filename, root=root)

@app.route('/logos/packs/<pack>/<filename>')
def serve_pack_logo(pack, filename):
    root = os.path.join(_logos_dir(), 'packs', pack)
    return static_file(filename, root=root)

@app.route('/logos/xbmc-kodi/<pack>/<filename>')
def serve_xbmc_logo(pack, filename):
    root = os.path.join(_logos_dir(), 'xbmc-kodi', pack)
    return static_file(filename, root=root)


# Sleduje stav bezicich xbmc scraperu { pack_name: {running, done, total, error} }
_xbmc_scraper_status: dict = {}

@app.route('/api/sources/xbmc/update', method='POST')
def api_xbmc_update():
    """Spusti xbmc_kodi_scraper.py na pozadi pro dany balicek."""
    data      = request.json or {}
    pack_name = data.get('name', '').strip()
    username  = data.get('username', '').strip()
    password  = data.get('password', '').strip()
    cookies_f = data.get('cookies_file', '').strip()

    if not pack_name:
        return json_response({'ok': False, 'error': 'Chybi nazev balicku'}, 400)
    if not username and not cookies_f:
        return json_response({'ok': False, 'error': 'Chybi prihlasovaci udaje (username+password nebo cookies_file)'}, 400)
    if _xbmc_scraper_status.get(pack_name, {}).get('running'):
        return json_response({'ok': False, 'error': 'Scraper jiz bezi'})

    out_dir = os.path.join(_logos_dir(), 'xbmc-kodi', pack_name)
    scraper  = os.path.join(SCRIPT_DIR, 'scripts', 'xbmc_kodi_scraper.py')

    def _run():
        import subprocess
        _xbmc_scraper_status[pack_name] = {'running': True, 'done': 0, 'total': 0, 'error': None}
        cmd = [sys.executable, scraper, '--out', out_dir]
        if cookies_f:
            cmd += ['--cookies', cookies_f]
        else:
            cmd += ['--user', username, '--password', password]

        print(f'[xbmc] Spoustim scraper pro "{pack_name}"...')
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            output = result.stdout + result.stderr
            # Parsuj statistiky z vystupu
            import re
            m_ok  = re.search(r'Stazeno:\s+(\d+)', output)
            m_err = re.search(r'Chyby:\s+(\d+)', output)
            done  = int(m_ok.group(1)) if m_ok else 0
            errs  = int(m_err.group(1)) if m_err else 0
            _xbmc_scraper_status[pack_name] = {
                'running': False, 'done': done, 'errors': errs,
                'error': None if result.returncode == 0 else f'Exit {result.returncode}'
            }
            print(f'[xbmc] "{pack_name}" hotovo: {done} stazeno, {errs} chyb')
        except Exception as e:
            _xbmc_scraper_status[pack_name] = {'running': False, 'done': 0, 'error': str(e)}
            print(f'[xbmc] Chyba scraperu: {e}')

    threading.Thread(target=_run, daemon=True).start()
    return json_response({'ok': True, 'pack': pack_name})


@app.route('/api/sources/xbmc/status')
def api_xbmc_status():
    """Vrati stav bezicich xbmc scraperu."""
    return json_response(_xbmc_scraper_status)



def cache_scheduler():
    next_run_cache = time.time() + 10

    while True:
        now = time.time()

        # Hodinovy ukol – cache + remap
        if now > next_run_cache:
            sync_remap_from_sample(SCRIPT_DIR)
            if cfg['cache']['dnu_v_kesi'] > 0:
                cache.clear_expired()
            next_run_cache = now + 3600

        time.sleep(5)


# ── Start ────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Picon Generator Server')
    parser.add_argument('--port',  type=int,  default=None)
    parser.add_argument('--host',  type=str,  default=None)
    parser.add_argument('--debug', action='store_true', default=False)
    args = parser.parse_args()

    host  = args.host  or cfg['server']['host']
    port  = args.port  or cfg['server']['port']
    debug = args.debug or cfg['server']['debug']

    # Instaluj log interceptor az ted – po inicializaci serveru
    if not isinstance(_sys.stdout, _LogInterceptor):
        _sys.stdout = _LogInterceptor(_sys.stdout)
    if not isinstance(_sys.stderr, _LogInterceptor):
        _sys.stderr = _LogInterceptor(_sys.stderr)

    threading.Thread(target=cache_scheduler, daemon=True).start()

    # Startup prefetch – stahne vsechny GitHub ikony na pozadi
    gh_sources = [s for s in cfg['sources'].get('github_sources', []) if s.get('enabled', True)]
    if gh_sources:
        print(f'[server] Spoustim startup prefetch pro {len(gh_sources)} GitHub zdroju...')
        startup_prefetch(gh_sources)

    # XBMC-Kodi auto-sync pri startu – prime volani scraperu bez HTTP
    xbmc_cfg  = cfg.get('xbmc_kodi', {})
    xbmc_user = xbmc_cfg.get('username', '').strip()
    xbmc_pass = xbmc_cfg.get('password', '').strip()
    xbmc_cook = xbmc_cfg.get('cookies_file', '').strip()

    if xbmc_user or xbmc_cook:
        xbmc_dir = os.path.join(cfg['sources']['logos_dir'], 'xbmc-kodi')
        existing_packs = []
        if os.path.isdir(xbmc_dir):
            existing_packs = [p for p in os.listdir(xbmc_dir)
                              if os.path.isdir(os.path.join(xbmc_dir, p))]
        if not existing_packs:
            existing_packs = ['tv-logo-pack']

        print(f'[server] XBMC-Kodi auto-sync: {existing_packs}')

        def _xbmc_auto(packs=existing_packs, user=xbmc_user, pw=xbmc_pass,
                       cook=xbmc_cook, logos_dir=cfg['sources']['logos_dir']):
            import time as _t
            _t.sleep(5)
            try:
                import sys as _s; _s.path.insert(0, SCRIPT_DIR)
                from scripts.xbmc_kodi_scraper import login, load_cookies, \
                    download_attachments, load_state, save_state, get_new_attachment_ids
                import requests as _req
                sess = _req.Session()
                sess.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'})
                if cook and os.path.exists(cook):
                    load_cookies(sess, cook)
                else:
                    if not login(sess, user, pw):
                        print('[xbmc] Auto-sync: prihlaseni selhalo')
                        return
                for pack_name in packs:
                    out = os.path.join(logos_dir, 'xbmc-kodi', pack_name)
                    state = load_state(out)
                    _xbmc_progress[pack_name] = {'running': True, 'done': 0,
                                                  'total': 0, 'new': 0, 'error': None}
                    attachments, page_count = get_new_attachment_ids(sess, state)
                    state['last_page_count'] = page_count
                    _xbmc_progress[pack_name]['total'] = len(attachments)
                    if attachments:
                        stats = download_attachments(sess, attachments, out, state, skip_existing=True)
                        _xbmc_progress[pack_name]['new'] = stats.get('ok', 0)
                        save_state(out, state)
                    _xbmc_progress[pack_name].update({'running': False,
                                                       'done': len(attachments)})
                    print(f'[xbmc] Auto-sync "{pack_name}": +{_xbmc_progress[pack_name]["new"]} novych')
            except Exception as e:
                print(f'[xbmc] Auto-sync chyba: {e}')

        threading.Thread(target=_xbmc_auto, daemon=True).start()
    else:
        print('[server] XBMC-Kodi: zadne credentials – dopln username/password do config.yaml')

    print(f'\n  Picon Generator Server v{VERSION}')
    print(f'  ================================')
    print(f'  http://{host}:{port}/editor      – GUI editor + galerie')
    print(f'  http://{host}:{port}/picons/<kanal>')
    print(f'  http://{host}:{port}/health\n')

    # Pouzij vicevaknovy server - WSGIRefServer je jednovlaknovy a blokuje
    server = 'waitress'
    try:
        import waitress as _w
    except ImportError:
        try:
            import cheroot as _c
            server = 'cheroot'
        except ImportError:
            try:
                import paste as _p
                server = 'paste'
            except ImportError:
                server = 'wsgiref'   # posledni zachrana

    print(f'[server] HTTP server: {server}')
    try:
        if server == 'waitress':
            import logging
            logging.basicConfig(level=logging.INFO,
                format='[waitress] %(message)s',
                stream=sys.__stdout__)  # primo na puvodni stdout
            from waitress import serve as waitress_serve
            print(f'[server] Nasloucham na http://{host}:{port}/', flush=True)
            waitress_serve(app, host=host, port=port,
                           threads=16,
                           connection_limit=100,
                           channel_timeout=600,      # 10 minut pro velke uploady
                           recv_bytes=10*1024*1024,  # 10MB recv buffer
                           clear_untrusted_proxy_headers=True)
        elif server in ('cheroot', 'paste'):
            run(app, host=host, port=port, debug=False, reloader=False,
                server=server)
        else:
            # wsgiref – obnov puvodni stdout/stderr aby wsgiref fungoval
            _sys.stdout = _sys.stdout._orig if isinstance(_sys.stdout, _LogInterceptor) else _sys.stdout
            _sys.stderr = _sys.stderr._orig if isinstance(_sys.stderr, _LogInterceptor) else _sys.stderr
            run(app, host=host, port=port, debug=False, reloader=False,
                server='wsgiref')
    except KeyboardInterrupt:
        print('\n[server] Ukonceno.')
        sys.exit(0)
