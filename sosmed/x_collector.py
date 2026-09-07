# -*- coding: utf-8 -*-
"""sosmed/x_collector.py — Collector X (Twitter) berbasis browser headless.

Menggantikan ekstensi Chrome \"Camerad X-Scraper\" untuk penarikan OTOMATIS di
latar belakang (server-side), TANPA membuka tab / klik manual. Memakai Playwright
(Chromium headless) yang login sebagai AKUN KEDUA (sesi disimpan sbg storage_state),
membuka URL Advanced Search \"live\" untuk rentang tanggal tertentu, lalu MENYADAP
respons JSON GraphQL X (SearchTimeline / TweetDetail / TweetResultByRestId) —
persis sumber data yang dipakai ekstensi (lihat extension/x-scraper/content_main.js
+ background.js). Node tweet diekstrak rekursif (port dari extractTweets di
background.js) menjadi item ternormalisasi SIAP untuk sosmed.db.ingest_items
(bentuk dict sama dengan ekstensi).

FAIL-SOFT: import Playwright dilakukan LAZY di dalam fungsi, sehingga modul tetap
bisa di-import (& fungsi murni extract_tweets tetap diuji offline) walau Playwright
belum terpasang. Bila belum ada, collect_range balik {ok: False, need_playwright}.

SESI & KREDENSIAL (dibaca dari environment; JANGAN commit):
  SOSMED_X_STATE_FILE   : path storage_state Playwright (default <runs>/x_state.json)
  SOSMED_X_USERNAME      : @handle akun kedua (utk login otomatis)
  SOSMED_X_PASSWORD      : password akun kedua
  SOSMED_X_EMAIL         : email/telepon (opsional; utk langkah verifikasi login)
  SOSMED_X_HEADLESS      : 1 (default) headless; 0 utk tampak jendela (login manual)
  SOSMED_X_TARGET        : target mention (default kring_pajak) -> query \"to:<target>\"
  SOSMED_X_TZ            : zona acuan H-1 & since/until (default Asia/Jakarta)
  SOSMED_X_UNTIL_PLUS1   : 1 (default) until = date_to + 1 hari (operator X eksklusif)
  SOSMED_X_EXPAND_THREADS: 1 (default) buka tiap utas -> sadap TweetDetail (induk + balasan resmi)
  SOSMED_X_MAX_SCROLLS   : batas scroll timeline pencarian (default 40)
  SOSMED_X_MAX_THREADS   : batas utas yang di-expand per-hari (default 60)

CLI:
  python -m sosmed.x_collector            # smoke test offline (parsing)
  SOSMED_X_HEADLESS=0 python -m sosmed.x_collector login   # buat sesi semi-manual
  python -m sosmed.x_collector diag       # cek sesi tersimpan benar-benar login?
  python -m sosmed.x_collector collect 2026-09-06 2026-09-06
"""
import os
import json
import time
import random as _random
import datetime as _dt

_DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_TARGET_OPS = ("SearchTimeline", "TweetDetail", "TweetResultByRestId",
               "TweetResultsByRestIds", "UserTweets")


# ===========================================================================
# Helper ekstraksi (PORT dari extension/x-scraper/background.js) — MURNI, tanpa
# Playwright, sehingga bisa diuji offline.
# ===========================================================================
def _to_iso(created):
    if not created:
        return ""
    try:
        d = _dt.datetime.strptime(str(created), "%a %b %d %H:%M:%S %z %Y")
        return d.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        pass
    try:
        d = _dt.datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        return d.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        return str(created)


def _unwrap_tweet(node):
    if not isinstance(node, dict):
        return None
    if node.get("__typename") == "TweetWithVisibilityResults" and node.get("tweet"):
        return node["tweet"]
    tw = node.get("tweet")
    if isinstance(tw, dict) and tw.get("rest_id") and tw.get("legacy"):
        return tw
    return node


def _user_result(tw):
    try:
        return tw["core"]["user_results"]["result"]
    except Exception:
        return None


def _get_handle(tw):
    u = _user_result(tw)
    if isinstance(u, dict):
        core = u.get("core") or {}
        if core.get("screen_name"):
            return str(core["screen_name"])
        leg = u.get("legacy") or {}
        if leg.get("screen_name"):
            return str(leg["screen_name"])
    leg = tw.get("legacy") or {}
    if leg.get("screen_name"):
        return str(leg["screen_name"])
    return "unknown"


def _get_name(tw):
    u = _user_result(tw)
    if isinstance(u, dict):
        core = u.get("core") or {}
        if core.get("name"):
            return str(core["name"])
        leg = u.get("legacy") or {}
        if leg.get("name"):
            return str(leg["name"])
    return ""


def _get_full_text(tw):
    try:
        note = tw["note_tweet"]["note_tweet_results"]["result"]
        if note.get("text"):
            return str(note["text"])
    except Exception:
        pass
    leg = tw.get("legacy") or {}
    if isinstance(leg.get("full_text"), str):
        return leg["full_text"]
    if isinstance(tw.get("full_text"), str):
        return tw["full_text"]
    return ""


def _get_media(tw):
    out = []
    try:
        leg = tw.get("legacy") or {}
        ent = leg.get("extended_entities") or leg.get("entities") or {}
        for m in (ent.get("media") or []):
            if isinstance(m, dict) and m.get("media_url_https"):
                out.append(m["media_url_https"])
    except Exception:
        pass
    return out


def _is_tweet_node(n):
    if not isinstance(n, dict) or not n.get("rest_id") or not n.get("legacy"):
        return False
    leg = n.get("legacy") or {}
    is_user = n.get("__typename") == "User" or bool(leg.get("screen_name"))
    if is_user:
        return False
    return (n.get("__typename") in ("Tweet", "TweetWithVisibilityResults")
            or isinstance(leg.get("full_text"), str)
            or isinstance(leg.get("conversation_id_str"), str))


def _int0(v):
    try:
        return int(v)
    except Exception:
        return 0


def extract_tweets(obj, official=None, results=None, seen=None, depth=0):
    """Telusuri rekursif struktur JSON GraphQL X; kumpulkan node tweet menjadi
    item ternormalisasi (bentuk sama dgn ekstensi). MURNI (tanpa Playwright)."""
    off = set((h or "").lower().lstrip("@") for h in (official or []))
    if results is None:
        results = []
    if seen is None:
        seen = set()
    if depth > 60 or not isinstance(obj, (dict, list)):
        return results
    if isinstance(obj, dict):
        cand = _unwrap_tweet(obj)
        if isinstance(cand, dict) and _is_tweet_node(cand):
            try:
                leg = cand.get("legacy") or {}
                tid = str(cand.get("rest_id"))
                text = _get_full_text(cand)
                if tid not in seen and (text or leg.get("conversation_id_str")):
                    seen.add(tid)
                    handle = _get_handle(cand)
                    irt = leg.get("in_reply_to_status_id_str")
                    results.append({
                        "platform": "x",
                        "tweet_id": tid,
                        "external_id": tid,
                        "conversation_id": str(leg.get("conversation_id_str") or tid),
                        "in_reply_to_id": str(irt) if irt else None,
                        "permalink": "https://x.com/%s/status/%s" % (handle, tid),
                        "author_handle": handle,
                        "author_name": _get_name(cand),
                        "is_official": handle.lower() in off,
                        "text": text,
                        "created_at": _to_iso(leg.get("created_at")),
                        "created_at_raw": leg.get("created_at") or "",
                        "like_count": _int0(leg.get("favorite_count")),
                        "reply_count": _int0(leg.get("reply_count")),
                        "repost_count": _int0(leg.get("retweet_count")),
                        "media": _get_media(cand),
                        "lang": leg.get("lang") or "",
                        "raw_json": cand,
                    })
            except Exception:
                pass
        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_tweets(v, off, results, seen, depth + 1)
    else:
        for v in obj:
            if isinstance(v, (dict, list)):
                extract_tweets(v, off, results, seen, depth + 1)
    return results


# ===========================================================================
# Konfigurasi / util environment
# ===========================================================================
def _flag(name, default="1"):
    v = (os.environ.get(name, default) or default).strip().lower()
    return v not in ("0", "", "false", "no", "off")


def _int_env(name, default):
    try:
        return int(os.environ.get(name) or default)
    except Exception:
        return int(default)


def _tz_name():
    return os.environ.get("SOSMED_X_TZ", "Asia/Jakarta")


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(_tz_name())
    except Exception:
        return None


def _yesterday():
    tz = _tz()
    now = _dt.datetime.now(tz) if tz else _dt.datetime.now()
    return (now.date() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")


def state_file():
    p = (os.environ.get("SOSMED_X_STATE_FILE") or "").strip()
    if p:
        return p
    base = ""
    try:
        from app_core import CONFIG
        base = CONFIG.get("runs_dir") or ""
    except Exception:
        base = ""
    if not base:
        base = os.path.dirname(os.path.abspath(__file__))
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return os.path.join(base, "x_state.json")


def target_handle():
    return (os.environ.get("SOSMED_X_TARGET", "kring_pajak") or "kring_pajak").strip().lstrip("@")


def build_search_url(date_from, date_to, target=None):
    """URL Advanced Search 'live' utk rentang [date_from, date_to] (inklusif).
    Meniru aturan ekstensi: (to:<target>) until:<...> since:<date_from> &f=live.
    Operator until X bersifat EKSKLUSIF; agar seluruh date_to ikut, default
    until = date_to + 1 hari (SOSMED_X_UNTIL_PLUS1=1). Set 0 utk persis seperti
    contoh manual (until=since=hari yang sama)."""
    import urllib.parse as _up
    target = (target or target_handle()).lstrip("@")
    until = date_to
    if _flag("SOSMED_X_UNTIL_PLUS1", "1"):
        try:
            until = (_dt.datetime.strptime(date_to, "%Y-%m-%d").date()
                     + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            until = date_to
    q = "(to:%s) until:%s since:%s" % (target, until, date_from)
    return "https://x.com/search?q=%s&f=live" % _up.quote(q)


def _sleep(base):
    try:
        time.sleep(max(0.2, base + _random.uniform(-0.4, 0.8)))
    except Exception:
        pass


# ===========================================================================
# Bagian browser (Playwright) — import LAZY
# ===========================================================================
def _new_context(browser):
    sf = state_file()
    kw = dict(user_agent=(os.environ.get("SOSMED_X_UA") or _DEFAULT_UA),
              locale="id-ID", viewport={"width": 1280, "height": 2200})
    tz = _tz_name()
    if tz:
        kw["timezone_id"] = tz
    if os.path.exists(sf):
        try:
            return browser.new_context(storage_state=sf, **kw)
        except Exception:
            pass
    return browser.new_context(**kw)


def _click_text(page, labels):
    for t in labels:
        try:
            loc = page.get_by_role("button", name=t)
            if loc.count() > 0:
                loc.first.click(timeout=6000)
                return True
        except Exception:
            pass
        try:
            loc = page.locator("span:has-text('%s')" % t)
            if loc.count() > 0:
                loc.first.click(timeout=6000)
                return True
        except Exception:
            pass
    return False


def _has_auth_cookie(context):
    """True bila context memiliki cookie 'auth_token' X terisi — sinyal PALING
    andal bahwa sesi benar-benar login (bukan sekadar halaman yang termuat)."""
    try:
        for c in context.cookies():
            if c.get("name") == "auth_token" and (c.get("value") or "").strip():
                return True
    except Exception:
        pass
    return False


def _is_logged_in(page):
    try:
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    _sleep(2.5)
    url = (page.url or "").lower()
    if "login" in url or "/i/flow/" in url or "/logout" in url:
        return False
    # Sinyal paling andal: cookie auth_token (hindari false-positive halaman publik).
    try:
        if _has_auth_cookie(page.context):
            return True
    except Exception:
        pass
    # Cadangan: elemen khusus akun (hanya tampil saat login).
    try:
        if page.locator("a[data-testid='SideNav_NewTweet_Button'], "
                        "[data-testid='SideNav_AccountSwitcher_Button'], "
                        "[data-testid='AppTabBar_Profile_Link'], "
                        "a[href='/compose/post'], a[href='/compose/tweet']").count() > 0:
            return True
    except Exception:
        pass
    return False


def _auto_login(context):
    user = (os.environ.get("SOSMED_X_USERNAME") or "").strip().lstrip("@")
    pw = (os.environ.get("SOSMED_X_PASSWORD") or "").strip()
    email = (os.environ.get("SOSMED_X_EMAIL") or "").strip()
    if not user or not pw:
        return False, ("Kredensial akun kedua belum diset "
                       "(SOSMED_X_USERNAME/SOSMED_X_PASSWORD).")
    page = context.new_page()
    try:
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=60000)
        _sleep(3)
        page.fill("input[autocomplete='username']", user, timeout=30000)
        _click_text(page, ["Next", "Berikutnya", "Lanjut"])
        _sleep(2.5)
        try:
            ver = page.locator("input[data-testid='ocfEnterTextTextInput'], input[name='text']")
            if ver.count() > 0 and email:
                ver.first.fill(email, timeout=8000)
                _click_text(page, ["Next", "Berikutnya", "Lanjut"])
                _sleep(2.5)
        except Exception:
            pass
        page.fill("input[name='password'], input[autocomplete='current-password']", pw, timeout=30000)
        _click_text(page, ["Log in", "Login", "Masuk"])
        _sleep(5)
        if _is_logged_in(page):
            return True, ""
        return False, "Login otomatis gagal (kemungkinan 2FA/captcha). Buat sesi semi-manual."
    except Exception as e:
        return False, "Login otomatis error: %s" % e
    finally:
        try:
            page.close()
        except Exception:
            pass


def save_login_state():
    """Buat/refresh sesi akun kedua. Coba login otomatis dari .env; bila headless
    dimatikan (SOSMED_X_HEADLESS=0) & otomatis gagal, tunggu login manual."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print("Playwright belum terpasang:", e)
        return {"ok": False, "need_playwright": True}
    headless = _flag("SOSMED_X_HEADLESS", "1")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = _new_context(browser)
        page = ctx.new_page()
        err = ""
        ok = _is_logged_in(page)
        if not ok:
            ok, err = _auto_login(ctx)
            if not ok and not headless:
                print("Silakan login manual di jendela browser (tunggu <=180 dtk)...")
                for _ in range(60):
                    _sleep(3)
                    if _is_logged_in(page):
                        ok = True
                        break
            if not ok and headless:
                print("Login otomatis gagal:", err)
                print("Buat sesi SEMI-MANUAL (PowerShell):")
                print('  $env:SOSMED_X_HEADLESS="0"; python -m sosmed.x_collector login')
        # Verifikasi keras: hanya simpan bila cookie auth_token benar-benar ada,
        # supaya sesi ANONIM tidak pernah tersimpan sebagai "ok".
        has_cookie = _has_auth_cookie(ctx)
        if ok and not has_cookie:
            ok = False
            print("Terdeteksi seperti login tetapi cookie auth_token tidak ada; "
                  "kemungkinan belum benar-benar login. Sesi TIDAK disimpan.")
        if ok:
            try:
                ctx.storage_state(path=state_file())
                print("Sesi tersimpan ke", state_file())
            except Exception as e:
                print("Gagal menyimpan sesi:", e)
        else:
            print("Gagal login; sesi tidak tersimpan.")
        try:
            ctx.close()
            browser.close()
        except Exception:
            pass
        return {"ok": ok, "logged_in": bool(has_cookie), "state_file": state_file()}


def diag_session():
    """Cek cepat apakah sesi tersimpan benar-benar login (punya cookie auth_token).
    Pakai: python -m sosmed.x_collector diag"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return {"ok": False, "need_playwright": True, "error": str(e)}
    headless = _flag("SOSMED_X_HEADLESS", "1")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = _new_context(browser)
        page = ctx.new_page()
        li = _is_logged_in(page)
        cookie = _has_auth_cookie(ctx)
        try:
            ctx.close()
            browser.close()
        except Exception:
            pass
        return {"ok": True, "logged_in": bool(li), "auth_cookie": bool(cookie),
                "state_file": state_file(), "state_exists": os.path.exists(state_file())}


def collect_range(date_from=None, date_to=None, official_handles=None,
                  target=None, expand_threads=None, trigger="manual"):
    """Tarik tweet mention X utk rentang tanggal via browser headless.
    Kembalikan (items, info). items = list dict siap sosmed.db.ingest_items."""
    date_from = date_from or _yesterday()
    date_to = date_to or date_from
    off = set((h or "").lower().lstrip("@") for h in (official_handles or []))
    if not off:
        off = {"kring_pajak"}
    if expand_threads is None:
        expand_threads = _flag("SOSMED_X_EXPAND_THREADS", "1")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return [], {"ok": False, "need_playwright": True,
                    "error": ("Playwright belum terpasang. Jalankan: pip install "
                              "playwright && python -m playwright install chromium. (%s)" % e)}

    headless = _flag("SOSMED_X_HEADLESS", "1")
    max_scrolls = _int_env("SOSMED_X_MAX_SCROLLS", 40)
    max_threads = _int_env("SOSMED_X_MAX_THREADS", 60)
    by_id = {}
    url = build_search_url(date_from, date_to, target)

    def _absorb(data):
        try:
            for it in extract_tweets(data, off):
                by_id[it["tweet_id"]] = it
        except Exception:
            pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = _new_context(browser)
        _diag = {"graphql": 0, "search": 0}

        def _on_response(resp):
            try:
                u = resp.url or ""
                if "graphql" not in u:
                    return
                _diag["graphql"] += 1
                if "SearchTimeline" in u:
                    _diag["search"] += 1
                data = None
                try:
                    data = resp.json()
                except Exception:
                    try:
                        data = json.loads(resp.text())
                    except Exception:
                        data = None
                if data is not None:
                    _absorb(data)
            except Exception:
                pass

        ctx.on("response", _on_response)
        page = ctx.new_page()

        if not _is_logged_in(page):
            ok, err = _auto_login(ctx)
            if not ok:
                try:
                    ctx.close()
                    browser.close()
                except Exception:
                    pass
                return [], {"ok": False, "need_login": True, "error": err, "url": url}
            try:
                ctx.storage_state(path=state_file())
            except Exception:
                pass
        logged_in = _has_auth_cookie(ctx)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        _sleep(4)

        stagnant = 0
        last = -1
        for _ in range(max_scrolls):
            n = len(by_id)
            stagnant = stagnant + 1 if n == last else 0
            last = n
            if stagnant >= 3:
                break
            try:
                page.mouse.wheel(0, 3200)
            except Exception:
                pass
            _sleep(2.2)

        if expand_threads:
            conv_ids = []
            seen_c = set()
            for it in list(by_id.values()):
                cid = it.get("conversation_id")
                if cid and cid not in seen_c:
                    seen_c.add(cid)
                    conv_ids.append(cid)
            for cid in conv_ids[:max_threads]:
                try:
                    page.goto("https://x.com/i/status/%s" % cid,
                              wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    continue
                _sleep(2.2)
                try:
                    page.mouse.wheel(0, 2400)
                except Exception:
                    pass
                _sleep(1.6)

        try:
            ctx.storage_state(path=state_file())
        except Exception:
            pass
        try:
            ctx.close()
            browser.close()
        except Exception:
            pass

    items = list(by_id.values())
    info = {"ok": True, "count": len(items),
            "range": "%s s/d %s" % (date_from, date_to),
            "url": url, "expanded": bool(expand_threads),
            "logged_in": bool(logged_in),
            "graphql_seen": _diag["graphql"], "search_seen": _diag["search"]}
    if len(items) == 0:
        if not logged_in:
            info["note"] = ("0 hasil & sesi TIDAK terautentikasi (cookie auth_token "
                            "tidak ada). Buat sesi login dulu: SOSMED_X_HEADLESS=0 "
                            "python -m sosmed.x_collector login")
        elif _diag["search"] == 0:
            info["note"] = ("0 hasil: halaman pencarian tidak mengembalikan SearchTimeline "
                            "(mungkin rate-limit/anti-bot atau layout berubah). Coba "
                            "SOSMED_X_HEADLESS=0 untuk melihat, atau ulangi nanti.")
        else:
            info["note"] = "0 hasil: X tidak mengembalikan mention pada rentang ini."
    return items, info


# ===========================================================================
# Smoke test offline (parsing) — python -m sosmed.x_collector
# ===========================================================================
def _smoke():
    def _mk(rid, screen, name, text, conv, irt=None, created="Wed Aug 05 10:00:00 +0000 2026"):
        return {
            "__typename": "TweetWithVisibilityResults",
            "tweet": {
                "__typename": "Tweet",
                "rest_id": rid,
                "core": {"user_results": {"result": {
                    "__typename": "User",
                    "core": {"screen_name": screen, "name": name}}}},
                "legacy": {
                    "conversation_id_str": conv,
                    "full_text": text,
                    "created_at": created,
                    "favorite_count": 3, "reply_count": 1, "retweet_count": 0,
                    "in_reply_to_status_id_str": irt,
                    "lang": "in",
                },
            },
        }
    payload = {"data": {"search_by_raw_query": {"search_timeline": {"timeline": {
        "instructions": [{"type": "TimelineAddEntries", "entries": [
            {"content": {"itemContent": {"tweet_results": {"result":
                _mk("1001", "wpbingung", "WP Bingung",
                    "@kring_pajak lupa EFIN dong min", "1001")}}}},
            {"content": {"itemContent": {"tweet_results": {"result":
                _mk("1002", "kring_pajak", "Kring Pajak",
                    "Halo, silakan DM ya. Terima kasih", "1001", irt="1001")}}}},
        ]}]}}}}}
    items = extract_tweets(payload, official=["kring_pajak"])
    by = {it["tweet_id"]: it for it in items}
    assert len(by) == 2, items
    q = by["1001"]
    assert q["author_handle"] == "wpbingung" and q["is_official"] is False, q
    assert q["conversation_id"] == "1001" and q["in_reply_to_id"] is None, q
    assert q["external_id"] == "1001" and q["text"].startswith("@kring_pajak"), q
    assert q["permalink"] == "https://x.com/wpbingung/status/1001", q["permalink"]
    assert q["created_at"] == "2026-08-05T10:00:00.000Z", q["created_at"]
    a = by["1002"]
    assert a["is_official"] is True and a["in_reply_to_id"] == "1001", a
    # dedup: payload ulang tidak menambah
    items2 = extract_tweets([payload, payload], official=["kring_pajak"])
    assert len({it["tweet_id"] for it in items2}) == 2, len(items2)
    # URL advanced-search (until +1 default)
    os.environ["SOSMED_X_UNTIL_PLUS1"] = "1"
    u = build_search_url("2026-09-06", "2026-09-06", target="kring_pajak")
    assert "since%3A2026-09-06" in u and "until%3A2026-09-07" in u and "f=live" in u, u
    os.environ["SOSMED_X_UNTIL_PLUS1"] = "0"
    u2 = build_search_url("2026-09-06", "2026-09-06", target="kring_pajak")
    assert "until%3A2026-09-06" in u2, u2
    print("SOSMED_X_COLLECTOR_SMOKE_OK")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        print(json.dumps(save_login_state(), ensure_ascii=False))
    elif cmd in ("diag", "whoami", "status"):
        print(json.dumps(diag_session(), ensure_ascii=False))
    elif cmd == "collect":
        df = sys.argv[2] if len(sys.argv) > 2 else _yesterday()
        dt = sys.argv[3] if len(sys.argv) > 3 else df
        its, info = collect_range(df, dt, official_handles=[target_handle()])
        print(json.dumps({"info": info, "n": len(its)}, ensure_ascii=False))
    else:
        _smoke()
