# -*- coding: utf-8 -*-
"""
github.py
GitHub API – listing souborů v repozitáři a push výsledného PNG.
"""

import base64
import hashlib
import time
import threading
from concurrent.futures import ThreadPoolExecutor
import requests

GITHUB_API       = "https://api.github.com"
PAGE_SIZE        = 60
CACHE_TTL        = 60 * 30
PREFETCH_WORKERS = 10

_listing_cache:    dict = {}
_prefetch_progress: dict = {}
_startup_progress: dict = {}   # { source_name: {done, total, running} }
_prefetch_executor = ThreadPoolExecutor(max_workers=PREFETCH_WORKERS, thread_name_prefix='gh-prefetch')
_disk_cache = None

def set_disk_cache(cache):
    global _disk_cache
    _disk_cache = cache


def _fetch_all_logos(repo: str, path: str, token: str = None) -> list:
    """Stáhne kompletní listing z GitHub API (nebo vrátí z cache)."""
    cache_key = f"{repo}/{path}"
    cached    = _listing_cache.get(cache_key)
    if cached and time.time() - cached["ts"] < CACHE_TTL:
        return cached["items"]

    url     = f"{GITHUB_API}/repos/{repo}/contents/{path.strip('/')}" if path.strip('/') else f"{GITHUB_API}/repos/{repo}/contents"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 403:
            print(f"[github] Rate limit nebo špatný token pro {repo}")
            return []
        if resp.status_code != 200:
            print(f"[github] Chyba {resp.status_code} při listingu {repo}/{path}")
            return []
        items = [
            {
                "name":         item["name"],
                "download_url": item["download_url"],
                "sha":          item.get("sha", ""),
                "size":         item.get("size", 0),
            }
            for item in resp.json()
            if item["type"] == "file" and item["name"].lower().endswith(".png")
        ]
        _listing_cache[cache_key] = {"ts": time.time(), "items": items}
        print(f"[github] Listing {repo}/{path}: {len(items)} log (cachováno)")
        return items
    except Exception as e:
        print(f"[github] Výjimka při listingu {repo}/{path}: {e}")
        return []



def _url_to_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest() + '.png'


def _prefetch_one(item: dict, token: str = None):
    if _disk_cache is None:
        return
    key = _url_to_key(item['download_url'])
    if _disk_cache.get_disk(key):
        return
    headers = {}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        r = requests.get(item['download_url'], headers=headers, timeout=10)
        if r.status_code == 200:
            _disk_cache.set_disk(key, r.content)
    except Exception:
        pass


def prefetch_page(items: list, cache_key: str, token: str = None):
    total = len(items)
    _prefetch_progress[cache_key] = {'done': 0, 'total': total}

    def _run():
        done = 0
        futures = [_prefetch_executor.submit(_prefetch_one, item, token) for item in items]
        for fut in futures:
            try:
                fut.result()
            except Exception:
                pass
            done += 1
            _prefetch_progress[cache_key] = {'done': done, 'total': total}

    threading.Thread(target=_run, daemon=True).start()


def get_prefetch_progress(cache_key: str) -> dict:
    return _prefetch_progress.get(cache_key, {})


def list_logos(repo: str, path: str, token: str = None,
               page: int = 0, page_size: int = PAGE_SIZE,
               search: str = "") -> dict:
    """
    Vrátí stránkovaný seznam PNG souborů.
    Vrací: { total, page, page_size, has_more, logos: [...] }
    Každá položka má příznak cached=True pokud je PNG na disku serveru.
    """
    all_items = _fetch_all_logos(repo, path, token)

    # Filtr hledání
    if search:
        q = search.lower()
        all_items = [i for i in all_items if q in i["name"].lower()]

    total      = len(all_items)
    start      = page * page_size
    end        = start + page_size
    page_items = all_items[start:end]

    # Označ položky které jsou v disk cache
    if _disk_cache is not None:
        for item in page_items:
            key = _url_to_key(item["download_url"])
            item["cached"]    = bool(_disk_cache.get_disk(key))
            item["cache_key"] = key   # pro přímý /api/logo/cached/<key> endpoint
    else:
        for item in page_items:
            item["cached"]    = False
            item["cache_key"] = None

    # Prefetch aktualni + next stranky na pozadi (jen nezkešované)
    if _disk_cache is not None:
        uncached = [i for i in page_items if not i.get("cached")]
        if uncached:
            ck = f"{repo}/{path}/p{page}"
            prefetch_page(uncached, ck, token)
        if end < total:
            prefetch_page(all_items[end:end + page_size], f"{repo}/{path}/p{page+1}", token)

    return {
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "has_more":  end < total,
        "logos":     page_items,
    }


def clear_listing_cache(repo: str = None, path: str = None):
    """Vymaže cache listingu pro daný repo/path nebo vše."""
    if repo:
        key = f"{repo}/{path or ''}"
        _listing_cache.pop(key, None)
    else:
        _listing_cache.clear()


def download_logo(download_url: str, token: str = None) -> bytes | None:
    """Stáhne PNG soubor z GitHub raw URL."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(download_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        print(f"[github] Chyba při stahování {download_url}: {e}")
    return None


def push_logo(repo: str, path: str, filename: str, data: bytes,
              token: str, commit_message: str = None) -> dict:
    """
    Pushne PNG soubor na GitHub přes API.
    Pokud soubor již existuje, provede update (potřebuje SHA).

    Vrátí dict: { "ok": True/False, "url": ..., "error": ... }
    """
    if not token:
        return {"ok": False, "error": "Chybí GitHub token"}

    file_path = f"{path.strip('/')}/{filename}"
    url       = f"{GITHUB_API}/repos/{repo}/contents/{file_path}"
    headers   = {
        "Accept":        "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    # Zjisti SHA pokud soubor již existuje (nutné pro update)
    sha = None
    try:
        check = requests.get(url, headers=headers, timeout=10)
        if check.status_code == 200:
            sha = check.json().get("sha")
    except Exception:
        pass

    msg  = commit_message or f"Picon: {filename}"
    body = {
        "message": msg,
        "content": base64.b64encode(data).decode("utf-8"),
    }
    if sha:
        body["sha"] = sha

    try:
        resp = requests.put(url, headers=headers, json=body, timeout=20)
        if resp.status_code in (200, 201):
            html_url = resp.json().get("content", {}).get("html_url", "")
            return {"ok": True, "url": html_url, "updated": sha is not None}
        else:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def startup_prefetch(sources: list):
    """
    Spustí prefetch všech PNG ze všech GitHub zdrojů na pozadí.
    Sleduje progress v _startup_progress.
    sources = [ {name, repo, path, token}, ... ]
    """
    def _run():
        for src in sources:
            name  = src['name']
            repo  = src['repo']
            path  = src.get('path', '')
            token = src.get('token') or None

            _startup_progress[name] = {'done': 0, 'total': 0, 'running': True}
            items = _fetch_all_logos(repo, path, token)
            total = len(items)
            _startup_progress[name]['total'] = total
            if total == 0:
                _startup_progress[name]['running'] = False
                continue

            done = 0
            futures = [_prefetch_executor.submit(_prefetch_one, item, token) for item in items]
            for fut in futures:
                try: fut.result()
                except Exception: pass
                done += 1
                _startup_progress[name]['done'] = done

            _startup_progress[name]['running'] = False
            print(f'[github] Startup prefetch {name}: {done}/{total} hotovo')

    threading.Thread(target=_run, daemon=True).start()


def get_startup_progress() -> dict:
    """Vrátí stav startup prefetche pro všechny zdroje."""
    return dict(_startup_progress)
