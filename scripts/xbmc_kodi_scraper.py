#!/usr/bin/env python3
import json, os, re, sys, time

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Chybi zavislosti: pip install requests beautifulsoup4")
    sys.exit(1)

BASE_URL   = "https://www.xbmc-kodi.cz"
THREAD_URL = BASE_URL + "/prispevek-tv-logo-pack"
LOGIN_URL  = BASE_URL + "/member.php"
ATTACH_URL = BASE_URL + "/attachment.php"
STATE_FILE = ".xbmc_state.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "cs,en;q=0.5",
    "Connection": "keep-alive",
    "Referer": BASE_URL,
}

def load_state(out_dir):
    p = os.path.join(out_dir, STATE_FILE)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"max_aid": 0, "last_page_count": 0, "downloaded": []}

def save_state(out_dir, state):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def login(session, username, password):
    print("[xbmc-kodi] Prihlasuji jako %s..." % username)
    r = session.get(LOGIN_URL, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    post_key = ""
    ki = soup.find("input", {"name": "my_post_key"})
    if ki:
        post_key = ki.get("value", "")
    data = {
        "action": "do_login", "url": "/", "quick_login": "1",
        "my_post_key": post_key, "quick_username": username,
        "quick_password": password, "quick_remember": "yes", "submit": "Login",
    }
    r2 = session.post(LOGIN_URL, data=data,
                      headers=dict(HEADERS, Referer=LOGIN_URL),
                      timeout=15, allow_redirects=True)
    if "mybbuser" in session.cookies:
        print("[xbmc-kodi] Prihlaseni uspesne")
        return True
    if username.lower() in r2.text.lower() and "odhlasit" in r2.text.lower():
        print("[xbmc-kodi] Prihlaseni uspesne")
        return True
    print("[xbmc-kodi] Prihlaseni selhalo")
    return False

def load_cookies(session, cookies_file):
    with open(cookies_file, encoding="utf-8") as f:
        cookies = json.load(f)
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", "www.xbmc-kodi.cz"))
    print("[xbmc-kodi] Nacteno %d cookies" % len(cookies))

def save_cookies(session, cookies_file):
    cookies = [{"name": c.name, "value": c.value, "domain": c.domain} for c in session.cookies]
    with open(cookies_file, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)
    print("[xbmc-kodi] Cookies ulozeny do %s" % cookies_file)

def get_page_count(session):
    r = session.get(THREAD_URL, headers=HEADERS, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    max_page = 1
    for a in soup.find_all("a", href=re.compile(r"page=\d+")):
        m = re.search(r"page=(\d+)", a["href"])
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page

def _parse_page(soup):
    found = []
    seen = set()
    for a in soup.find_all("a", href=re.compile(r"attachment\.php\?aid=(\d+)")):
        m = re.search(r"aid=(\d+)", a["href"])
        if not m:
            continue
        aid = int(m.group(1))
        if aid in seen:
            continue
        seen.add(aid)
        name = a.get_text(strip=True) or ("logo_%d.png" % aid)
        found.append((aid, name))
    return found

def get_new_attachment_ids(session, state, progress_cb=None):
    current_pages = get_page_count(session)
    last_pages    = state.get("last_page_count", 0)
    max_aid       = state.get("max_aid", 0)
    already_dl    = set(state.get("downloaded", []))
    print("[xbmc-kodi] Forum ma %d stranek (posledni: %d)" % (current_pages, last_pages))
    attachments = []
    seen_aids   = set()

    if max_aid == 0:
        print("[xbmc-kodi] Prvni spusteni - stahuji vse...")
        for page in range(1, current_pages + 1):
            url = THREAD_URL if page == 1 else "%s?page=%d" % (THREAD_URL, page)
            print("[xbmc-kodi]   Stranka %d/%d" % (page, current_pages))
            try:
                r = session.get(url, headers=HEADERS, timeout=20)
                if r.status_code == 200:
                    for aid, name in _parse_page(BeautifulSoup(r.text, "html.parser")):
                        if aid not in seen_aids:
                            attachments.append((aid, name))
                            seen_aids.add(aid)
                if progress_cb:
                    progress_cb(page, current_pages, len(attachments))
                time.sleep(0.8)
            except Exception as e:
                print("[xbmc-kodi]   Chyba: %s" % e)
    else:
        pages_to_check = max(3, (current_pages - last_pages) + 2)
        start_page = max(1, current_pages - pages_to_check + 1)
        print("[xbmc-kodi] Inkrementalni - stranky %d-%d" % (start_page, current_pages))
        stop = False
        for page in range(current_pages, start_page - 1, -1):
            if stop:
                break
            url = THREAD_URL if page == 1 else "%s?page=%d" % (THREAD_URL, page)
            print("[xbmc-kodi]   Stranka %d/%d" % (page, current_pages))
            try:
                r = session.get(url, headers=HEADERS, timeout=20)
                if r.status_code != 200:
                    continue
                for aid, name in _parse_page(BeautifulSoup(r.text, "html.parser")):
                    if aid <= max_aid and aid in already_dl:
                        print("[xbmc-kodi]   AID %d uz stazeno - stop" % aid)
                        stop = True
                        break
                    if aid not in seen_aids and aid not in already_dl:
                        attachments.append((aid, name))
                        seen_aids.add(aid)
                if progress_cb:
                    progress_cb(current_pages - page + 1, pages_to_check, len(attachments))
                time.sleep(0.8)
            except Exception as e:
                print("[xbmc-kodi]   Chyba: %s" % e)

    print("[xbmc-kodi] Novych priloh: %d" % len(attachments))
    return attachments, current_pages

def download_attachments(session, attachments, out_dir, state,
                          skip_existing=True, progress_cb=None):
    os.makedirs(out_dir, exist_ok=True)
    stats = {"ok": 0, "skipped": 0, "error": 0, "not_png": 0}
    already_dl = set(state.get("downloaded", []))
    total = len(attachments)

    for i, (aid, orig_name) in enumerate(attachments, 1):
        safe_name = re.sub(r'[^\w\-.]', '_', orig_name)
        if not safe_name.lower().endswith('.png'):
            safe_name += '.png'
        out_path = os.path.join(out_dir, safe_name)

        if skip_existing and os.path.exists(out_path):
            stats["skipped"] += 1
            already_dl.add(aid)
            continue

        url = "%s?aid=%d" % (ATTACH_URL, aid)
        try:
            r = session.get(url, headers=dict(HEADERS, Referer=THREAD_URL),
                            timeout=20, stream=True)
            if r.status_code == 403:
                print("[xbmc-kodi] [%d/%d] AID %d: 403 Forbidden" % (i, total, aid))
                stats["error"] += 1
                continue
            if r.status_code != 200:
                print("[xbmc-kodi] [%d/%d] AID %d: HTTP %d" % (i, total, aid, r.status_code))
                stats["error"] += 1
                continue
            ct = r.headers.get("Content-Type", "")
            if "image/png" not in ct and "image/jpeg" not in ct and "octet-stream" not in ct:
                stats["not_png"] += 1
                continue
            cd = r.headers.get("Content-Disposition", "")
            m = re.search(r'filename=["\']?([^"\';\s]+)', cd)
            if m:
                out_path = os.path.join(out_dir, re.sub(r'[^\w\-.]', '_', m.group(1)))
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            print("[xbmc-kodi] [%d/%d] OK %s" % (i, total, os.path.basename(out_path)))
            stats["ok"] += 1
            already_dl.add(aid)
            state["max_aid"]    = max(state.get("max_aid", 0), aid)
            state["downloaded"] = list(already_dl)
            save_state(out_dir, state)
        except Exception as e:
            print("[xbmc-kodi] [%d/%d] AID %d: %s" % (i, total, aid, e))
            stats["error"] += 1

        if progress_cb:
            progress_cb(i, total, stats["ok"])
        time.sleep(0.3)

    return stats

def get_all_attachment_ids(session, out_dir=None, progress_cb=None):
    state = load_state(out_dir) if out_dir else {"max_aid": 0, "last_page_count": 0, "downloaded": []}
    attachments, _ = get_new_attachment_ids(session, state, progress_cb)
    return attachments

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", "-u")
    parser.add_argument("--password", "-p")
    parser.add_argument("--cookies", "-c")
    parser.add_argument("--out", "-o", default="./logos/xbmc-kodi/tv-logo-pack")
    parser.add_argument("--save-cookies")
    parser.add_argument("--no-skip", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()

    if not args.user and not args.cookies:
        print("Chyba: zadej --user + --password nebo --cookies")
        sys.exit(1)

    session = requests.Session()
    session.headers.update(HEADERS)

    if args.cookies:
        load_cookies(session, args.cookies)
    else:
        if not login(session, args.user, args.password):
            sys.exit(1)
        if args.save_cookies:
            save_cookies(session, args.save_cookies)

    out_dir = args.out
    state   = {"max_aid": 0, "last_page_count": 0, "downloaded": []} if args.full \
              else load_state(out_dir)

    attachments, page_count = get_new_attachment_ids(session, state)
    state["last_page_count"] = page_count

    if not attachments:
        print("[xbmc-kodi] Zadne nove prilohy - vse aktualni")
        save_state(out_dir, state)
        return

    print("[xbmc-kodi] Stahuji %d priloh..." % len(attachments))
    stats = download_attachments(session, attachments, out_dir, state,
                                  skip_existing=not args.no_skip)
    print("Hotovo: OK=%d Preskoceno=%d Chyby=%d" % (
        stats["ok"], stats["skipped"], stats["error"]))

if __name__ == "__main__":
    main()
