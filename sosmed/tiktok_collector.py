# -*- coding: utf-8 -*-
"""sosmed/tiktok_collector.py — Collector TikTok berbasis browser (Fase 2).

Meniru pola sosmed/x_collector.py & ig_collector.py: Playwright (Chromium) login
sebagai AKUN KEDUA (storage_state / profil Chrome), membuka profil akun RESMI di
TikTok, membuka beberapa video terbaru, lalu MENYADAP respons JSON web-API TikTok
(`api/comment/list/` + `api/comment/list/reply/`) untuk memanen KOMENTAR (pertanyaan
warga) beserta BALASAN akun resmi. Item ternormalisasi SIAP untuk
sosmed.db.ingest_items (pairing Q&A sadar-thread otomatis: conversation_id = aweme_id
video, in_reply_to_id = cid komentar induk, is_official).

Seperti IG, TikTok tak punya pencarian per-tanggal publik; v1 memanen komentar pada
N video TERBARU akun resmi. Penjadwal harian + dedup + pull_log menjaga komentar
baru terkumpul tiap hari tanpa dobel.

FAIL-SOFT: import Playwright LAZY; fungsi murni extract_tt_comments teruji offline.

ENV:
  SOSMED_TT_STATE_FILE   : path storage_state (default <runs>/tiktok_state.json)
  SOSMED_TT_USERNAME     : @handle akun kedua (login otomatis)
  SOSMED_TT_PASSWORD     : password akun kedua
  SOSMED_TT_TARGET       : @handle akun RESMI dipantau (default SOSMED_X_TARGET / 'kring_pajak')
  SOSMED_TT_HEADLESS     : 1 (default) headless; 0 utk login manual
  SOSMED_TT_MAX_VIDEOS   : jumlah video terbaru dibuka (default 10)
  SOSMED_TT_MAX_SCROLLS  : batas scroll komentar per-video (default 8)
  SOSMED_TT_TZ           : zona acuan H-1 (default Asia/Jakarta)
  SOSMED_TT_USER_DATA_DIR: folder \"User Data\" Chrome utk profil yg sudah login
  SOSMED_TT_PROFILE_DIR  : subfolder profil (default 'Default')
  SOSMED_TT_CHANNEL      : channel browser (mis. 'chrome')

CLI:
  python -m sosmed.tiktok_collector                 # smoke test offline (parsing)
  SOSMED_TT_HEADLESS=0 python -m sosmed.tiktok_collector login
  python -m sosmed.tiktok_collector diag
  python -m sosmed.tiktok_collector collect 2026-09-06 2026-09-06
  python -m sosmed.tiktok_collector dump 2026-09-06 2026-09-06 hasil_tt.csv
"""
import os
import json
import time
import random as _random
import datetime as _dt

_DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# ===========================================================================
# Helper ekstraksi (MURNI, tanpa Playwright) — bisa diuji offline.
# ===========================================================================
def _epoch_to_iso(v):
    try:
        ts = int(v)
    except Exception:
        return ""
    if ts <= 0:
        return ""
    try:
        return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        return ""


def _tt_user(u):
    if not isinstance(u, dict):
        return "", "", ""
    return (str(u.get("unique_id") or u.get("uniqueId") or "").lstrip("@"),
            str(u.get("nickname") or ""),
            str(u.get("uid") or u.get("id") or ""))


def _is_tt_comment(n):
    """True bila node terlihat seperti komentar TikTok (punya cid + text + user)."""
    if not isinstance(n, dict):
        return False
    if not n.get("cid"):
        return False
    if n.get("text") is None:
        return False
    u = n.get("user")
    return isinstance(u, dict) and bool(u.get("unique_id") or u.get("uniqueId"))


def _mk_tt_item(n, official):
    cid = str(n.get("cid") or "")
    if not cid:
        return None
    handle, name, uid = _tt_user(n.get("user"))
    aweme = str(n.get("aweme_id") or "")
    # reply_id / reply_to_reply_id != '0' menandakan ini balasan komentar.
    parent = None
    for k in ("reply_to_reply_id", "reply_id"):
        v = str(n.get(k) or "0")
        if v and v != "0":
            parent = v
            break
    conv = aweme or (parent or cid)
    text = str(n.get("text") or "")
    permalink = ""
    def _i(*keys):
        for k in keys:
            try:
                return int(n.get(k) or 0)
            except Exception:
                pass
        return 0
    return {
        "platform": "tiktok",
        "external_id": cid,
        "comment_id": cid,
        "conversation_id": conv,
        "in_reply_to_id": parent,
        "parent_id": parent,
        "permalink": permalink,
        "author_handle": handle or "unknown",
        "author_name": name,
        "author_id": uid,
        "is_official": (handle or "").lower() in official,
        "text": text,
        "created_at": _epoch_to_iso(n.get("create_time") or n.get("createTime")),
        "created_at_raw": n.get("create_time") or n.get("createTime") or "",
        "like_count": _i("digg_count"),
        "reply_count": _i("reply_comment_total", "reply_comment_count"),
        "repost_count": 0,
        "media": [],
        "lang": "",
        "raw_json": n,
    }


def extract_tt_comments(obj, official=None, results=None, seen=None, depth=0):
    """Telusuri rekursif payload JSON komentar TikTok; kumpulkan node komentar
    (termasuk balasan) menjadi item ternormalisasi. MURNI (tanpa Playwright)."""
    off = set((h or "").lower().lstrip("@") for h in (official or []))
    if results is None:
        results = []
    if seen is None:
        seen = set()
    if depth > 40 or not isinstance(obj, (dict, list)):
        return results
    if isinstance(obj, dict):
        if _is_tt_comment(obj):
            it = _mk_tt_item(obj, off)
            if it and it["external_id"] not in seen and it["text"]:
                seen.add(it["external_id"])
                results.append(it)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_tt_comments(v, official, results, seen, depth + 1)
    else:
        for v in obj:
            if isinstance(v, (dict, list)):
                extract_tt_comments(v, official, results, seen, depth + 1)
    return results


def extract_aweme_ids(obj, out=None, depth=0):
    """Panen daftar {aweme_id -> create_time} dari payload item_list utk memilih
    video terbaru yang akan dibuka."""
    if out is None:
        out = {}
    if depth > 40 or not isinstance(obj, (dict, list)):
        return out
    if isinstance(obj, dict):
        aid = obj.get("aweme_id") or obj.get("id")
        if aid and (obj.get("desc") is not None or obj.get("create_time")
                    or obj.get("createTime") or obj.get("video")):
            try:
                ct = int(obj.get("create_time") or obj.get("createTime") or 0)
            except Exception:
                ct = 0
            out[str(aid)] = {"create_time": ct}
        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_aweme_ids(v, out, depth + 1)
    else:
        for v in obj:
            if isinstance(v, (dict, list)):
                extract_aweme_ids(v, out, depth + 1)
    return out


def _write_dump(path, items, target=None):
    import csv as _csv
    cols = ["comment_id", "author_handle", "author_name", "is_official",
            "conversation_id", "in_reply_to_id", "created_at", "like_count",
            "reply_count", "permalink", "text"]
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for it in (items or []):
                w.writerow({
                    "comment_id": it.get("external_id", ""),
                    "author_handle": it.get("author_handle", ""),
                    "author_name": it.get("author_name", ""),
                    "is_official": "YES" if it.get("is_official") else "NO",
                    "conversation_id": it.get("conversation_id", ""),
                    "in_reply_to_id": it.get("in_reply_to_id") or "",
                    "created_at": it.get("created_at", ""),
                    "like_count": it.get("like_count", 0),
                    "reply_count": it.get("reply_count", 0),
                    "permalink": it.get("permalink", ""),
                    "text": (it.get("text") or "").replace("\r", " ").replace("\n", " "),
                })
        return True
    except Exception as e:
        print("Gagal menulis dump:", e)
        return False


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
    return os.environ.get("SOSMED_TT_TZ", os.environ.get("SOSMED_X_TZ", "Asia/Jakarta"))


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
    p = (os.environ.get("SOSMED_TT_STATE_FILE") or "").strip()
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
    return os.path.join(base, "tiktok_state.json")


def target_handle():
    return (os.environ.get("SOSMED_TT_TARGET")
            or os.environ.get("SOSMED_X_TARGET", "kring_pajak")
            or "kring_pajak").strip().lstrip("@")


def profile_url(target=None):
    return "https://www.tiktok.com/@%s" % (target or target_handle())


def _sleep(base):
    try:
        time.sleep(max(0.2, base + _random.uniform(-0.4, 0.8)))
    except Exception:
        pass


# ===========================================================================
# Bagian browser (Playwright) — import LAZY
# ===========================================================================
def _context_kwargs():
    kw = dict(user_agent=(os.environ.get("SOSMED_TT_UA") or _DEFAULT_UA),
              locale="id-ID", viewport={"width": 1280, "height": 2200})
    tz = _tz_name()
    if tz:
        kw["timezone_id"] = tz
    return kw


def _persistent_dir():
    return (os.environ.get("SOSMED_TT_USER_DATA_DIR") or "").strip()


def _launch(pw, headless):
    args = ["--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"]
    channel = (os.environ.get("SOSMED_TT_CHANNEL") or "").strip()
    udd = _persistent_dir()
    if udd:
        prof = (os.environ.get("SOSMED_TT_PROFILE_DIR") or "Default").strip()
        if prof:
            args.append("--profile-directory=%s" % prof)
        launch_kw = dict(headless=headless, args=args, channel=(channel or "chrome"))
        launch_kw.update(_context_kwargs())
        ctx = pw.chromium.launch_persistent_context(udd, **launch_kw)
        return None, ctx, True
    launch_kw = dict(headless=headless, args=args)
    if channel:
        launch_kw["channel"] = channel
    browser = pw.chromium.launch(**launch_kw)
    sf = state_file()
    kw = _context_kwargs()
    if os.path.exists(sf):
        try:
            return browser, browser.new_context(storage_state=sf, **kw), False
        except Exception:
            pass
    return browser, browser.new_context(**kw), False


def _close(browser, ctx):
    try:
        ctx.close()
    except Exception:
        pass
    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass


def _has_auth_cookie(context):
    """True bila context punya cookie 'sessionid' TikTok terisi (sinyal login)."""
    try:
        for c in context.cookies():
            if c.get("name") in ("sessionid", "sessionid_ss") and (c.get("value") or "").strip():
                return True
    except Exception:
        pass
    return False


def _is_logged_in(page):
    try:
        page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    _sleep(2.5)
    url = (page.url or "").lower()
    if "/login" in url:
        return False
    try:
        if _has_auth_cookie(page.context):
            return True
    except Exception:
        pass
    return False


def _auto_login(context):
    """TikTok kerap butuh captcha/checkpoint; login otomatis by password sering
    gagal. Fungsi ini upaya terbaik; utamakan profil Chrome yang sudah login atau
    sesi semi-manual (SOSMED_TT_HEADLESS=0)."""
    user = (os.environ.get("SOSMED_TT_USERNAME") or "").strip().lstrip("@")
    pw = (os.environ.get("SOSMED_TT_PASSWORD") or "").strip()
    if not user or not pw:
        return False, ("Kredensial akun kedua TikTok belum diset "
                       "(SOSMED_TT_USERNAME/SOSMED_TT_PASSWORD). Disarankan pakai "
                       "profil Chrome yg sudah login (SOSMED_TT_USER_DATA_DIR) "
                       "atau sesi semi-manual (SOSMED_TT_HEADLESS=0 ... login).")
    page = context.new_page()
    try:
        page.goto("https://www.tiktok.com/login/phone-or-email/email",
                  wait_until="domcontentloaded", timeout=60000)
        _sleep(3)
        try:
            page.fill("input[name='username']", user, timeout=20000)
            page.fill("input[type='password']", pw, timeout=20000)
            page.keyboard.press("Enter")
            _sleep(6)
        except Exception:
            pass
        if _is_logged_in(page):
            return True, ""
        return False, "Login TikTok otomatis gagal (kemungkinan captcha/2FA). Buat sesi semi-manual."
    except Exception as e:
        return False, "Login TikTok otomatis error: %s" % e
    finally:
        try:
            page.close()
        except Exception:
            pass


def save_login_state():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print("Playwright belum terpasang:", e)
        return {"ok": False, "need_playwright": True}
    headless = _flag("SOSMED_TT_HEADLESS", "1")
    with sync_playwright() as pw:
        browser, ctx, persistent = _launch(pw, headless)
        page = ctx.new_page()
        ok = _is_logged_in(page)
        err = ""
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
                print("Login TikTok otomatis gagal:", err)
                print("Buat sesi SEMI-MANUAL (PowerShell):")
                print('  $env:SOSMED_TT_HEADLESS="0"; python -m sosmed.tiktok_collector login')
        has_cookie = _has_auth_cookie(ctx)
        if ok and not has_cookie:
            ok = False
            print("Terdeteksi seperti login tetapi cookie sessionid tidak ada; sesi TIDAK disimpan.")
        if ok:
            if persistent:
                print("Login diambil dari profil Chrome yang ada; tersimpan di profil itu.")
            else:
                try:
                    ctx.storage_state(path=state_file())
                    print("Sesi tersimpan ke", state_file())
                except Exception as e:
                    print("Gagal menyimpan sesi:", e)
        else:
            print("Gagal login; sesi tidak tersimpan.")
        _close(browser, ctx)
        return {"ok": ok, "logged_in": bool(has_cookie),
                "persistent": persistent, "state_file": state_file()}


def diag_session():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return {"ok": False, "need_playwright": True, "error": str(e)}
    headless = _flag("SOSMED_TT_HEADLESS", "1")
    with sync_playwright() as pw:
        browser, ctx, persistent = _launch(pw, headless)
        page = ctx.new_page()
        li = _is_logged_in(page)
        cookie = _has_auth_cookie(ctx)
        _close(browser, ctx)
        return {"ok": True, "logged_in": bool(li), "auth_cookie": bool(cookie),
                "persistent": persistent, "profile": _persistent_dir() or None,
                "state_file": state_file(), "state_exists": os.path.exists(state_file())}


def collect_range(date_from=None, date_to=None, official_handles=None,
                  target=None, trigger="manual", dump_path=None, **_kw):
    """Tarik komentar pada N video terbaru akun resmi TikTok via browser.
    Kembalikan (items, info). Lihat catatan tanggal di docstring modul."""
    date_from = date_from or _yesterday()
    date_to = date_to or date_from
    off = set((h or "").lower().lstrip("@") for h in (official_handles or []))
    tgt = (target or target_handle()).lstrip("@")
    off.add(tgt.lower())
    url = profile_url(tgt)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return [], {"ok": False, "need_playwright": True,
                    "error": ("Playwright belum terpasang. Jalankan: pip install "
                              "playwright && python -m playwright install chromium. (%s)" % e)}
    headless = _flag("SOSMED_TT_HEADLESS", "1")
    max_videos = _int_env("SOSMED_TT_MAX_VIDEOS", 10)
    max_scrolls = _int_env("SOSMED_TT_MAX_SCROLLS", 8)
    by_id = {}
    awemes = {}
    _diag = {"comments": 0, "items": 0}

    with sync_playwright() as pw:
        browser, ctx, persistent = _launch(pw, headless)

        def _on_response(resp):
            try:
                u = resp.url or ""
                if "tiktok.com" not in u:
                    return
                if "/api/comment/list" in u:
                    try:
                        data = resp.json()
                    except Exception:
                        return
                    _diag["comments"] += 1
                    for it in extract_tt_comments(data, off):
                        by_id[it["external_id"]] = it
                elif "/api/post/item_list" in u or "/api/user/detail" in u:
                    try:
                        data = resp.json()
                    except Exception:
                        return
                    _diag["items"] += 1
                    extract_aweme_ids(data, awemes)
            except Exception:
                pass

        ctx.on("response", _on_response)
        page = ctx.new_page()

        if not _is_logged_in(page):
            ok, err = _auto_login(ctx)
            if not ok:
                _close(browser, ctx)
                return [], {"ok": False, "need_login": True, "error": err, "url": url}
            if not persistent:
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
        for _ in range(4):
            try:
                page.mouse.wheel(0, 2600)
            except Exception:
                pass
            _sleep(2.0)

        vids = sorted(awemes.items(), key=lambda kv: kv[1].get("create_time") or 0,
                      reverse=True)
        vids = [aid for aid, _ in vids][:max_videos]
        for aid in vids:
            try:
                page.goto("https://www.tiktok.com/@%s/video/%s" % (tgt, aid),
                          wait_until="domcontentloaded", timeout=45000)
            except Exception:
                continue
            _sleep(2.8)
            stagnant = 0
            last = -1
            for _ in range(max_scrolls):
                n = len(by_id)
                stagnant = stagnant + 1 if n == last else 0
                last = n
                if stagnant >= 3:
                    break
                try:
                    page.mouse.wheel(0, 2200)
                except Exception:
                    pass
                _sleep(1.6)

        if not persistent:
            try:
                ctx.storage_state(path=state_file())
            except Exception:
                pass
        _close(browser, ctx)

    # Lengkapi permalink & conversation_id yang kosong (mis. balasan tanpa aweme_id
    # eksplisit) memakai video yang sedang dibuka bila memungkinkan.
    items = list(by_id.values())
    for it in items:
        if it.get("conversation_id") and not it.get("permalink"):
            it["permalink"] = "https://www.tiktok.com/@%s/video/%s" % (tgt, it["conversation_id"])
    dumped = False
    if dump_path:
        dumped = _write_dump(dump_path, items, tgt)
    info = {"ok": True, "count": len(items),
            "range": "%s s/d %s" % (date_from, date_to), "url": url,
            "target": tgt, "logged_in": bool(logged_in),
            "videos_opened": len(vids), "videos_seen": len(awemes),
            "comments_seen": _diag["comments"], "items_seen": _diag["items"]}
    if dump_path:
        info["dump_path"] = dump_path
        info["dumped"] = bool(dumped)
    if len(items) == 0:
        if not logged_in:
            info["note"] = ("0 hasil & sesi TIDAK terautentikasi (cookie sessionid "
                            "tidak ada). Pakai profil Chrome yg sudah login lewat "
                            "SOSMED_TT_USER_DATA_DIR, atau: SOSMED_TT_HEADLESS=0 "
                            "python -m sosmed.tiktok_collector login")
        elif _diag["comments"] == 0:
            info["note"] = ("0 hasil: TikTok tidak mengembalikan respons komentar "
                            "(mungkin captcha/rate-limit atau layout berubah). "
                            "Coba SOSMED_TT_HEADLESS=0 untuk melihat.")
        else:
            info["note"] = "0 hasil: tidak ada komentar terpanen pada video terbaru."
    return items, info


# ===========================================================================
# Smoke test offline (parsing) — python -m sosmed.tiktok_collector
# ===========================================================================
def _smoke():
    payload = {"comments": [
        {"cid": "c1", "text": "min lupa EFIN gimana resetnya?", "create_time": 1757116800,
         "aweme_id": "vid9", "reply_id": "0", "digg_count": 4,
         "user": {"unique_id": "wargapajak", "nickname": "Warga", "uid": "9001"},
         "reply_comment": [
             {"cid": "c2", "text": "Halo Kak, hubungi 1500200 ya. Terima kasih",
              "create_time": 1757118600, "aweme_id": "vid9", "reply_id": "c1",
              "reply_to_reply_id": "c1",
              "user": {"unique_id": "kring_pajak", "nickname": "Kring Pajak", "uid": "1"}}]},
        {"cid": "c3", "text": "cara ganti email terdaftar dong", "create_time": 1757120000,
         "aweme_id": "vid9", "reply_id": "0",
         "user": {"unique_id": "netizen2", "nickname": "Netizen", "uid": "9002"}},
    ]}
    items = extract_tt_comments(payload, official=["kring_pajak"])
    by = {it["external_id"]: it for it in items}
    assert len(by) == 3, items
    q = by["c1"]
    assert q["author_handle"] == "wargapajak" and q["is_official"] is False, q
    assert q["conversation_id"] == "vid9" and q["in_reply_to_id"] is None, q
    assert q["created_at"] == "2025-09-06T00:00:00.000Z", q["created_at"]
    a = by["c2"]
    assert a["is_official"] is True and a["in_reply_to_id"] == "c1", a
    assert a["conversation_id"] == "vid9", a
    # dedup
    items2 = extract_tt_comments([payload, payload], official=["kring_pajak"])
    assert len({it["external_id"] for it in items2}) == 3, len(items2)
    # aweme ids
    il = {"itemList": [{"id": "vid9", "desc": "info pajak", "createTime": 1757100000}]}
    aw = extract_aweme_ids(il)
    assert "vid9" in aw, aw
    # ingest end-to-end (pairing Q&A): c1 terjawab oleh c2
    try:
        import tempfile
        import sosmed.db as sdb
        os.environ["SOSMED_DB_FILE"] = os.path.join(tempfile.mkdtemp(), "tt_test.db")
        os.environ["SOSMED_OFFICIAL_HANDLES"] = "kring_pajak"
        c = sdb.init_db(sdb.connect())
        r = sdb.ingest_items(c, items, default_platform="tiktok", source="test")
        assert r["ok"] and r["n_new"] == 3, r
        rowq = c.execute("SELECT item_type,status,answered_by FROM sosmed_items "
                         "WHERE external_id='c1'").fetchone()
        assert rowq[0] == "pertanyaan" and rowq[1] == "terjawab", tuple(rowq)
        assert rowq[2] == "kring_pajak", tuple(rowq)
        c.close()
    except ImportError:
        pass
    print("SOSMED_TIKTOK_COLLECTOR_SMOKE_OK")


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
    elif cmd == "dump":
        df = sys.argv[2] if len(sys.argv) > 2 else _yesterday()
        dt = sys.argv[3] if len(sys.argv) > 3 else df
        out = sys.argv[4] if len(sys.argv) > 4 else ("tt_dump_%s_%s.csv" % (df, dt))
        its, info = collect_range(df, dt, official_handles=[target_handle()], dump_path=out)
        print(json.dumps({"info": info, "n": len(its)}, ensure_ascii=False))
    else:
        _smoke()
