#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prefetch_github.py
==================
Spusť PŘED zabalením do ZIPu – stáhne všechna loga z GitHub zdrojů
do lokální disk cache, takže po nainstalování serveru jsou ikony
k dispozici okamžitě bez čekání.

Použití:
    cd picon-server
    python3 scripts/prefetch_github.py

Nebo s vlastním config.yaml:
    python3 scripts/prefetch_github.py --config /cesta/config.yaml
"""

import os
import sys
import time
import json
import hashlib
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Přidej rodičovský adresář do path
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

try:
    import requests
except ImportError:
    print("Chyba: pip install requests")
    sys.exit(1)

try:
    from config import load_config
except ImportError:
    print(f"Chyba: nelze importovat config.py z {SCRIPT_DIR}")
    sys.exit(1)


GITHUB_API   = "https://api.github.com"
WORKERS      = 20
_print_lock  = threading.Lock()


def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def fetch_listing(repo: str, path: str, token: str = None) -> list:
    """Stáhne kompletní listing PNG souborů z GitHub repozitáře."""
    url = (f"{GITHUB_API}/repos/{repo}/contents/{path.strip('/')}"
           if path.strip('/') else f"{GITHUB_API}/repos/{repo}/contents")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 403:
            log(f"  ✗ Rate limit nebo špatný token pro {repo} (HTTP 403)")
            return []
        if r.status_code == 404:
            log(f"  ✗ Repozitář nebo cesta nenalezena: {repo}/{path} (HTTP 404)")
            return []
        if r.status_code != 200:
            log(f"  ✗ HTTP {r.status_code} pro {repo}/{path}")
            return []
        items = [
            {"name": i["name"], "download_url": i["download_url"]}
            for i in r.json()
            if i["type"] == "file" and i["name"].lower().endswith(".png")
        ]
        return items
    except Exception as e:
        log(f"  ✗ Chyba při listingu {repo}/{path}: {e}")
        return []


def download_one(item: dict, cache_dir: str, token: str = None) -> bool:
    """Stáhne jedno PNG do cache. Přeskočí pokud už existuje."""
    url      = item["download_url"]
    key      = hashlib.md5(url.encode()).hexdigest() + ".png"
    filepath = os.path.join(cache_dir, key)

    if os.path.exists(filepath):
        return False  # již v cache

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(r.content)
            return True
    except Exception:
        pass
    return False


def update_cache_index(cache_dir: str):
    """Aktualizuje cache.json index se všemi PNG soubory."""
    index_file = os.path.join(cache_dir, "cache.json")
    try:
        with open(index_file, "r") as f:
            index = json.loads(f.read().strip())
    except Exception:
        index = {}

    now = int(time.time())
    changed = False
    for fname in os.listdir(cache_dir):
        if fname.endswith(".png") and fname not in index:
            index[fname] = now
            changed = True

    if changed:
        with open(index_file, "w") as f:
            f.write(json.dumps(index))


def prefetch_source(src: dict, cache_dir: str) -> dict:
    """Stáhne všechna loga jednoho GitHub zdroje do cache."""
    name  = src["name"]
    repo  = src["repo"]
    path  = src.get("path", "")
    token = src.get("token") or None

    log(f"\n→ {name}  ({repo}/{path})")
    items = fetch_listing(repo, path, token)
    if not items:
        log(f"  Prázdný listing, přeskakuji.")
        return {"name": name, "total": 0, "new": 0, "skipped": 0}

    log(f"  Nalezeno {len(items)} PNG souborů, stahuji...")

    new     = 0
    skipped = 0
    errors  = 0
    done    = 0
    start   = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(download_one, item, cache_dir, token): item for item in items}
        for fut in as_completed(futures):
            done += 1
            try:
                result = fut.result()
                if result:
                    new += 1
                else:
                    skipped += 1
            except Exception:
                errors += 1

            # Progress každých 50 souborů
            if done % 50 == 0 or done == len(items):
                elapsed = time.time() - start
                log(f"  [{done}/{len(items)}] nových: {new}, v cache: {skipped}  ({elapsed:.1f}s)")

    update_cache_index(cache_dir)
    log(f"  ✓ {name}: {new} nových, {skipped} z cache, {errors} chyb")
    return {"name": name, "total": len(items), "new": new, "skipped": skipped, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="Prefetch GitHub log do cache")
    parser.add_argument("--config", default=os.path.join(SCRIPT_DIR, "config.yaml"),
                        help="Cesta k config.yaml")
    parser.add_argument("--source", default=None,
                        help="Stáhni jen jeden zdroj (podle name v configu)")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        # Zkus sample
        sample = args.config + ".sample"
        if os.path.exists(sample):
            args.config = sample
            log(f"config.yaml nenalezen, používám {sample}")
        else:
            log(f"Chyba: config soubor nenalezen: {args.config}")
            sys.exit(1)

    cfg       = load_config(SCRIPT_DIR, config_path=args.config)
    cache_dir = cfg["cache"]["disk_dir"]
    os.makedirs(cache_dir, exist_ok=True)

    sources = [s for s in cfg["sources"].get("github_sources", []) if s.get("enabled", True)]
    if args.source:
        sources = [s for s in sources if s["name"] == args.source]
        if not sources:
            log(f"Zdroj '{args.source}' nenalezen v configu.")
            sys.exit(1)

    if not sources:
        log("Žádné aktivní GitHub zdroje v configu.")
        sys.exit(0)

    log(f"Cache adresář: {cache_dir}")
    log(f"Zdrojů ke stažení: {len(sources)}")

    total_start = time.time()
    results = []
    for src in sources:
        results.append(prefetch_source(src, cache_dir))

    elapsed = time.time() - total_start
    total_new  = sum(r["new"] for r in results)
    total_all  = sum(r["total"] for r in results)
    log(f"\n{'='*50}")
    log(f"Hotovo za {elapsed:.1f}s – celkem {total_all} souborů, {total_new} nově staženo")
    log(f"Cache: {cache_dir}")
    log("ZIP teď zabalí i cache – server naběhne okamžitě s ikonami.")


if __name__ == "__main__":
    main()
