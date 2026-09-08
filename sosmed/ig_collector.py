# -*- coding: utf-8 -*-
"""sosmed/ig_collector.py — Collector Instagram berbasis browser (Fase 2).

Meniru pola sosmed/x_collector.py: memakai Playwright (Chromium) yang login sebagai
AKUN KEDUA (storage_state ATAU profil Chrome yang sudah ada), membuka profil akun
RESMI di Instagram, membuka beberapa postingan terbaru, lalu MENYADAP respons JSON
private-API IG (`/api/v1/media/<id>/comments/` + `/child_comments/`) untuk memanen
KOMENTAR (pertanyaan warga) beserta BALASAN akun resmi. Item ternormalisasi SIAP
untuk sosmed.db.ingest_items (pairing Q&A sadar-thread berjalan otomatis lewat
conversation_id = id media, in_reply_to_id = parent_comment_id, is_official).

Berbeda dgn X: Instagram tidak punya pencarian per-tanggal untuk publik, sehingga
strategi v1 adalah \"komentar pada N postingan TERBARU akun resmi\". Penjadwal harian
+ dedup (UNIQUE platform+external_id) memastikan komentar baru tiap hari terkumpul
tanpa dobel; pull_log tetap menandai hari itu \"sudah ditarik\" (lihat autopull.py).

FAIL-SOFT: import Playwright LAZY sehingga fungsi murni extract_ig_comments tetap
bisa diuji offline walau Playwright belum terpasang.

ENV (JANGAN commit kredensial):
  SOSMED_IG_STATE_FILE   : path storage_state (default <runs>/ig_state.json)
  SOSMED_IG_USERNAME     : @handle akun kedua (login otomatis)
  SOSMED_IG_PASSWORD     : password akun kedua
  SOSMED_IG_TARGET       : @handle akun RESMI yang dipantau (default dari
                           SOSMED_X_TARGET, atau 'kring_pajak')
  SOSMED_IG_HEADLESS     : 1 (default) headless; 0 utk login manual tampak jendela
  SOSMED_IG_MAX_POSTS    : jumlah postingan terbaru yang dibuka (default 12;
                           set 0 = SEMUA postingan target, 1 = hanya terbaru, dst.)
  SOSMED_IG_MAX_SCROLLS  : batas scroll komentar per-postingan (default 8)
  SOSMED_IG_DEBUG        : 1 utk mencetak trace URL/keys respons (diagnosa)
  SOSMED_IG_TZ           : zona acuan H-1 (default Asia/Jakarta)
  SOSMED_IG_USER_DATA_DIR: folder \"User Data\" Chrome utk pakai profil yg sudah login
  SOSMED_IG_PROFILE_DIR  : subfolder profil (default 'Default')
  SOSMED_IG_CHANNEL      : channel browser (mis. 'chrome'); default Chromium bawaan

CLI:
  python -m sosmed.ig_collector                         # smoke test offline (parsing)
  SOSMED_IG_HEADLESS=0 python -m sosmed.ig_collector login
  python -m sosmed.ig_collector diag
  python -m sosmed.ig_collector collect 2026-09-06 2026-09-06
  python -m sosmed.ig_collector dump 2026-09-06 2026-09-06 hasil_ig.csv
"""
import os
import re
import json
import time
import random as _random
import datetime as _dt

_DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_MEDIA_RE = re.compile(r"/media/(\d+)/(?:comments|child_comments)")


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


def _ig_user(u):
    if not isinstance(u, dict):
        return "", "", ""
    return (str(u.get("username") or "").lstrip("@"),
            str(u.get("full_name") or ""),
            str(u.get("pk") or u.get("id") or ""))


def _is_ig_comment(n):
    """True bila node terlihat seperti komentar IG (punya text + user.username + pk)."""
    if not isinstance(n, dict):
        return False
    if n.get("text") is None:
        return False
    # Node media/postingan (punya caption/kode) BUKAN komentar.
    if any(k in n for k in ("media_type", "product_type", "shortcode",
                            "carousel_media", "code")):
        return False
    u = n.get("user")
    if not isinstance(u, dict) or not u.get("username"):
        return False
    return bool(n.get("pk") or n.get("id") or n.get("comment_id"))


def _mk_ig_item(n, media_id, code, off):
    pk = str(n.get("pk") or n.get("id") or n.get("comment_id") or "").split("_")[0]
    if not pk:
        return None
    handle, name, uid = _ig_user(n.get("user"))
    parent = n.get("parent_comment_id")
    parent = str(parent).split("_")[0] if parent else None
    conv = str(media_id or n.get("media_id") or pk).split("_")[0]
    text = str(n.get("text") or "")
    permalink = ("https://www.instagram.com/p/%s/c/%s/" % (code, pk)) if code else ""
    def _i(*keys):
        for k in keys:
            try:
                return int(n.get(k) or 0)
            except Exception:
                pass
        return 0
    return {
        "platform": "ig",
        "external_id": pk,
        "comment_id": pk,
        "conversation_id": conv,
        "in_reply_to_id": parent,
        "parent_id": parent,
        "permalink": permalink,
        "author_handle": handle or "unknown",
        "author_name": name,
        "author_id": uid,
        "is_official": (handle or "").lower() in off,
        "text": text,
        "created_at": _epoch_to_iso(n.get("created_at") or n.get("created_at_utc")),
        "created_at_raw": n.get("created_at") or n.get("created_at_utc") or "",
        "like_count": _i("comment_like_count", "like_count"),
        "reply_count": _i("child_comment_count"),
        "repost_count": 0,
        "media": [],
        "lang": "",
        "raw_json": n,
    }


def extract_ig_comments(obj, media_id=None, code=None, official=None,
                        results=None, seen=None, depth=0):
    """Telusuri rekursif payload JSON komentar IG; kumpulkan node komentar (termasuk
    balasan berjenjang) menjadi item ternormalisasi. MURNI (tanpa Playwright)."""
    off = set((h or "").lower().lstrip("@") for h in (official or []))
    if results is None:
        results = []
    if seen is None:
        seen = set()
    if depth > 40 or not isinstance(obj, (dict, list)):
        return results
    if isinstance(obj, dict):
        if _is_ig_comment(obj):
            it = _mk_ig_item(obj, media_id, code, off)
            if it and it["external_id"] not in seen and it["text"]:
                seen.add(it["external_id"])
                results.append(it)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_ig_comments(v, media_id, code, official, results, seen, depth + 1)
    else:
        for v in obj:
            if isinstance(v, (dict, list)):
                extract_ig_comments(v, media_id, code, official, results, seen, depth + 1)
    return results


def extract_media_codes(obj, out=None, depth=0):
    """Panen pasangan (media_pk -> shortcode) & daftar (pk, code, taken_at) dari
    payload feed/profil IG, utk menentukan postingan yang akan dibuka."""
    if out is None:
        out = {}
    if depth > 40 or not isinstance(obj, (dict, list)):
        return out
    if isinstance(obj, dict):
        code = obj.get("code") or obj.get("shortcode")
        pk = obj.get("pk") or obj.get("id")
        if code and pk and (obj.get("taken_at") or obj.get("caption") is not None
                            or obj.get("media_type")):
            _own = obj.get("user") or obj.get("owner") or {}
            _oh = (str(_own.get("username") or "").lstrip("@").lower()
                   if isinstance(_own, dict) else "")
            out[str(pk).split("_")[0]] = {"code": str(code),
                                          "taken_at": obj.get("taken_at") or 0,
                                          "owner": _oh}
        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_media_codes(v, out, depth + 1)
    else:
        for v in obj:
            if isinstance(v, (dict, list)):
                extract_media_codes(v, out, depth + 1)
    return out


def _thread_sort(items):
    """Urutkan komentar ala thread: komentar UTAMA lalu BALASAN-nya (berjenjang),
    dikelompokkan per postingan (conversation_id) & urut waktu. Memudahkan dibaca
    di CSV: utama | balasan | balasan ..."""
    by_id = {}
    for it in (items or []):
        cid = it.get("comment_id") or it.get("external_id")
        if cid:
            by_id[cid] = it
    children = {}
    roots = []
    for it in (items or []):
        p = it.get("in_reply_to_id")
        if p and p in by_id:
            children.setdefault(p, []).append(it)
        else:
            roots.append(it)
    def _ck(x):
        return x.get("created_at") or ""
    roots.sort(key=lambda x: ((x.get("conversation_id") or ""), _ck(x)))
    out, seen = [], set()
    def _emit(it, level):
        cid = it.get("comment_id") or it.get("external_id")
        if not cid or cid in seen:
            return
        seen.add(cid)
        it["_level"] = level
        out.append(it)
        for ch in sorted(children.get(cid, []), key=_ck):
            _emit(ch, level + 1)
    for r in roots:
        _emit(r, 0)
    for it in (items or []):
        cid = it.get("comment_id") or it.get("external_id")
        if cid and cid not in seen:
            it["_level"] = 0
            out.append(it)
            seen.add(cid)
    return out


def _write_dump(path, items, target=None):
    import csv as _csv
    cols = ["tipe", "level", "comment_id", "author_handle", "author_name",
            "is_official", "conversation_id", "in_reply_to_id", "created_at",
            "like_count", "reply_count", "permalink", "text"]
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for it in _thread_sort(items):
                w.writerow({
                    "tipe": "balasan" if it.get("in_reply_to_id") else "utama",
                    "level": int(it.get("_level") or 0),
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
    return os.environ.get("SOSMED_IG_TZ", os.environ.get("SOSMED_X_TZ", "Asia/Jakarta"))


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
    p = (os.environ.get("SOSMED_IG_STATE_FILE") or "").strip()
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
    return os.path.join(base, "ig_state.json")


def target_handle():
    return (os.environ.get("SOSMED_IG_TARGET")
            or os.environ.get("SOSMED_X_TARGET", "kring_pajak")
            or "kring_pajak").strip().lstrip("@")


def profile_url(target=None):
    return "https://www.instagram.com/%s/" % (target or target_handle())


def _sleep(base):
    try:
        time.sleep(max(0.2, base + _random.uniform(-0.4, 0.8)))
    except Exception:
        pass


# ===========================================================================
# Bagian browser (Playwright) — import LAZY
# ===========================================================================
def _context_kwargs():
    kw = dict(user_agent=(os.environ.get("SOSMED_IG_UA") or _DEFAULT_UA),
              locale="id-ID", viewport={"width": 1280, "height": 2200})
    tz = _tz_name()
    if tz:
        kw["timezone_id"] = tz
    return kw


def _persistent_dir():
    return (os.environ.get("SOSMED_IG_USER_DATA_DIR") or "").strip()


def _launch(pw, headless):
    args = ["--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"]
    channel = (os.environ.get("SOSMED_IG_CHANNEL") or "").strip()
    udd = _persistent_dir()
    if udd:
        prof = (os.environ.get("SOSMED_IG_PROFILE_DIR") or "Default").strip()
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
    """True bila context punya cookie 'sessionid' IG terisi (sinyal login andal)."""
    try:
        for c in context.cookies():
            if c.get("name") == "sessionid" and (c.get("value") or "").strip():
                return True
    except Exception:
        pass
    return False


def _is_logged_in(page):
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    _sleep(2.5)
    url = (page.url or "").lower()
    if "/accounts/login" in url or "/accounts/emailsignup" in url:
        return False
    try:
        if _has_auth_cookie(page.context):
            return True
    except Exception:
        pass
    return False


def _click_text(page, labels):
    for t in labels:
        try:
            loc = page.get_by_role("button", name=t)
            if loc.count() > 0:
                loc.first.click(timeout=6000)
                return True
        except Exception:
            pass
    return False


def _auto_login(context):
    user = (os.environ.get("SOSMED_IG_USERNAME") or "").strip().lstrip("@")
    pw = (os.environ.get("SOSMED_IG_PASSWORD") or "").strip()
    if not user or not pw:
        return False, ("Kredensial akun kedua IG belum diset "
                       "(SOSMED_IG_USERNAME/SOSMED_IG_PASSWORD).")
    page = context.new_page()
    try:
        page.goto("https://www.instagram.com/accounts/login/",
                  wait_until="domcontentloaded", timeout=60000)
        _sleep(3)
        try:
            _click_text(page, ["Allow all cookies", "Izinkan semua cookie",
                               "Only allow essential cookies"])
        except Exception:
            pass
        page.fill("input[name='username']", user, timeout=30000)
        page.fill("input[name='password']", pw, timeout=30000)
        _click_text(page, ["Log in", "Log In", "Masuk"])
        _sleep(6)
        if _is_logged_in(page):
            return True, ""
        return False, "Login IG otomatis gagal (kemungkinan 2FA/checkpoint). Buat sesi semi-manual."
    except Exception as e:
        return False, "Login IG otomatis error: %s" % e
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
    headless = _flag("SOSMED_IG_HEADLESS", "1")
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
                print("Login IG otomatis gagal:", err)
                print("Buat sesi SEMI-MANUAL (PowerShell):")
                print('  $env:SOSMED_IG_HEADLESS=\"0\"; python -m sosmed.ig_collector login')
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
    headless = _flag("SOSMED_IG_HEADLESS", "1")
    with sync_playwright() as pw:
        browser, ctx, persistent = _launch(pw, headless)
        page = ctx.new_page()
        li = _is_logged_in(page)
        cookie = _has_auth_cookie(ctx)
        _close(browser, ctx)
        return {"ok": True, "logged_in": bool(li), "auth_cookie": bool(cookie),
                "persistent": persistent, "profile": _persistent_dir() or None,
                "state_file": state_file(), "state_exists": os.path.exists(state_file())}


_MORE_COMMENTS_RE = re.compile(
    r"(view\s+(more|all|previous)?\s*comments?|"
    r"(lihat|muat)\s+komentar(\s+(lain|lainnya|sebelumnya))?)", re.I)
_MORE_REPLIES_RE = re.compile(
    r"(view\s+(all\s+)?(\d[\d.,]*\s+)?repl(y|ies)|"
    r"(lihat|muat)\s+(\d[\d.,]*\s+)?balasan(\s+lainnya)?|"
    r"balas(an)?\s+lainnya)", re.I)

_SCROLL_JS = """
() => {
  const els = Array.from(document.querySelectorAll('div,ul,section'));
  let best = null, bestH = 0;
  for (const el of els) {
    const over = el.scrollHeight - el.clientHeight;
    if (over > 200 && el.clientHeight > 150) {
      const oy = getComputedStyle(el).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > bestH) {
        best = el; bestH = el.scrollHeight;
      }
    }
  }
  if (best) { best.scrollTop = best.scrollHeight; return best.scrollHeight; }
  window.scrollTo(0, document.body.scrollHeight);
  return 0;
}
"""


def _click_all(page, rx, limit=40):
    """Klik semua elemen yang teksnya cocok regex rx (mis. 'lihat balasan').
    Kembalikan jumlah klik sukses. Best-effort (fail-soft)."""
    try:
        els = page.get_by_text(rx).all()
    except Exception:
        els = []
    n = 0
    for b in els[:limit]:
        try:
            b.scroll_into_view_if_needed(timeout=800)
        except Exception:
            pass
        try:
            b.click(timeout=1200)
            n += 1
        except Exception:
            pass
    return n


def _load_comments(page, max_scrolls):
    """Muat komentar + BALASAN berjenjang: klik 'muat komentar', lalu buka SEMUA
    toggle 'lihat balasan' (child comments dimuat lewat graphql & ikut disadap),
    scroll kontainer komentar via JS, ulangi. Best-effort (fail-soft)."""
    for _ in range(max(1, int(max_scrolls or 1))):
        _click_all(page, _MORE_COMMENTS_RE, limit=8)
        # Buka balasan berjenjang: ulang beberapa kali karena toggle baru
        # bermunculan setelah yang sebelumnya diklik.
        for _r in range(3):
            if not _click_all(page, _MORE_REPLIES_RE, limit=40):
                break
            _sleep(0.8)
        try:
            page.evaluate(_SCROLL_JS)
        except Exception:
            pass
        try:
            page.mouse.wheel(0, 2400)
        except Exception:
            pass
        _sleep(1.5)
    # Sapuan akhir: pastikan seluruh balasan sudah diperluas.
    for _r in range(5):
        if not _click_all(page, _MORE_REPLIES_RE, limit=60):
            break
        _sleep(1.0)


def collect_range(date_from=None, date_to=None, official_handles=None,
                  target=None, trigger="manual", dump_path=None, **_kw):
    """Tarik komentar pada N postingan terbaru akun resmi IG via browser.
    Kembalikan (items, info). items siap sosmed.db.ingest_items.

    Catatan: IG tak punya pencarian per-tanggal publik, jadi date_from/date_to
    hanya dicatat; v1 menarik komentar pada postingan TERBARU (dedup + pull_log
    menjaga agar komentar baru tiap hari terkumpul tanpa dobel)."""
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
    headless = _flag("SOSMED_IG_HEADLESS", "1")
    max_posts = _int_env("SOSMED_IG_MAX_POSTS", 12)
    max_scrolls = _int_env("SOSMED_IG_MAX_SCROLLS", 8)
    by_id = {}
    media_codes = {}
    _diag = {"comments": 0, "feed": 0, "json": 0}
    _debug = _flag("SOSMED_IG_DEBUG", "0")
    _trace = []

    with sync_playwright() as pw:
        browser, ctx, persistent = _launch(pw, headless)

        def _on_response(resp):
            try:
                u = resp.url or ""
                if "instagram.com" not in u:
                    return
                is_api = ("/api/" in u or "/graphql" in u
                          or "/comments/" in u or "/child_comments/" in u)
                if not is_api:
                    return
                try:
                    data = resp.json()
                except Exception:
                    return
                _diag["json"] += 1
                # 1) Panen komentar dari respons komentar ATAU graphql —
                #    IG 2026 kerap mengirim komentar lewat /graphql/query.
                got = 0
                if ("/comments/" in u or "/child_comments/" in u
                        or "/graphql" in u):
                    m = _MEDIA_RE.search(u)
                    mid = m.group(1) if m else None
                    code = (media_codes.get(str(mid)) or {}).get("code") if mid else None
                    before = len(by_id)
                    for it in extract_ig_comments(data, mid, code, off):
                        by_id[it["external_id"]] = it
                    got = len(by_id) - before
                    if got:
                        _diag["comments"] += 1
                # 2) Panen kode media (postingan) utk menentukan yg dibuka.
                if ("/feed/user/" in u or "web_profile_info" in u
                        or "/graphql" in u or "/api/v1/users/" in u):
                    extract_media_codes(data, media_codes)
                    _diag["feed"] += 1
                if _debug:
                    _trace.append({
                        "url": u[:200],
                        "keys": (list(data.keys())[:14]
                                 if isinstance(data, dict) else "list"),
                        "new_comments": got,
                    })
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

        # Urutkan postingan terbaru (taken_at desc) lalu buka satu per satu.
        _tgt_l = tgt.lower()
        _codes_all = sorted(media_codes.values(),
                            key=lambda d: d.get("taken_at") or 0, reverse=True)
        # Hanya buka postingan milik target (owner cocok / tak diketahui);
        # buang postingan sugesti/explore dari akun lain.
        codes = [d["code"] for d in _codes_all
                 if d.get("code") and (not d.get("owner")
                                       or d.get("owner") == _tgt_l)]
        if not codes:
            codes = [d["code"] for d in _codes_all if d.get("code")]
        if max_posts and max_posts > 0:
            codes = codes[:max_posts]  # 0 / negatif = SEMUA postingan target
        for code in codes:
            try:
                page.goto("https://www.instagram.com/p/%s/" % code,
                          wait_until="domcontentloaded", timeout=45000)
            except Exception:
                continue
            _sleep(2.6)
            # Scroll panel komentar (JS) + klik \"muat lebih / lihat balasan\".
            _load_comments(page, max_scrolls)

        if not persistent:
            try:
                ctx.storage_state(path=state_file())
            except Exception:
                pass
        _close(browser, ctx)

    items = list(by_id.values())
    dumped = False
    if dump_path:
        dumped = _write_dump(dump_path, items, tgt)
    info = {"ok": True, "count": len(items),
            "range": "%s s/d %s" % (date_from, date_to), "url": url,
            "target": tgt, "logged_in": bool(logged_in),
            "posts_opened": len(codes), "media_seen": len(media_codes),
            "comments_seen": _diag["comments"], "feed_seen": _diag["feed"],
            "json_seen": _diag["json"]}
    if _debug:
        info["trace"] = _trace[:80]
    if dump_path:
        info["dump_path"] = dump_path
        info["dumped"] = bool(dumped)
    if len(items) == 0:
        if not logged_in:
            info["note"] = ("0 hasil & sesi TIDAK terautentikasi (cookie sessionid "
                            "tidak ada). Pakai profil Chrome yg sudah login lewat "
                            "SOSMED_IG_USER_DATA_DIR, atau: SOSMED_IG_HEADLESS=0 "
                            "python -m sosmed.ig_collector login")
        elif _diag["comments"] == 0:
            info["note"] = ("0 hasil: IG tidak mengembalikan respons komentar "
                            "(mungkin rate-limit/checkpoint atau layout berubah). "
                            "Coba SOSMED_IG_HEADLESS=0 untuk melihat.")
        else:
            info["note"] = "0 hasil: tidak ada komentar terpanen pada postingan terbaru."
    return items, info


# ===========================================================================
# Smoke test offline (parsing) — python -m sosmed.ig_collector
# ===========================================================================
def _smoke():
    payload = {"comments": [
        {"pk": "111", "text": "min lupa EFIN gimana resetnya?", "created_at": 1757116800,
         "user": {"username": "wargapajak", "full_name": "Warga", "pk": "9001"},
         "comment_like_count": 2, "child_comment_count": 1,
         "preview_child_comments": [
             {"pk": "112", "text": "Halo Kak, silakan hubungi 1500200. Terima kasih",
              "created_at": 1757118600, "parent_comment_id": "111",
              "user": {"username": "kring_pajak", "full_name": "Kring Pajak", "pk": "1"}}]},
        {"pk": "113", "text": "cara ganti email terdaftar dong", "created_at": 1757120000,
         "user": {"username": "netizen2", "full_name": "Netizen", "pk": "9002"}},
    ]}
    items = extract_ig_comments(payload, media_id="555", code="ABCcode",
                                official=["kring_pajak"])
    by = {it["external_id"]: it for it in items}
    assert len(by) == 3, items
    q = by["111"]
    assert q["author_handle"] == "wargapajak" and q["is_official"] is False, q
    assert q["conversation_id"] == "555" and q["in_reply_to_id"] is None, q
    assert q["created_at"] == "2025-09-06T00:00:00.000Z", q["created_at"]
    assert q["permalink"] == "https://www.instagram.com/p/ABCcode/c/111/", q["permalink"]
    a = by["112"]
    assert a["is_official"] is True and a["in_reply_to_id"] == "111", a
    assert a["conversation_id"] == "555", a
    # dedup
    items2 = extract_ig_comments([payload, payload], media_id="555", official=["kring_pajak"])
    assert len({it["external_id"] for it in items2}) == 3, len(items2)
    # media codes
    feed = {"items": [{"pk": "555", "code": "ABCcode", "taken_at": 1757100000,
                       "media_type": 1, "caption": {"text": "promo"}}]}
    mc = extract_media_codes(feed)
    assert mc.get("555", {}).get("code") == "ABCcode", mc
    # threaded sort: komentar UTAMA (111) sebelum BALASAN-nya (112, level 1)
    ts = _thread_sort(items)
    _order = [it["external_id"] for it in ts]
    assert _order.index("111") < _order.index("112"), _order
    assert ts[_order.index("112")].get("_level") == 1, ts
    # ingest end-to-end (pairing Q&A): 111 terjawab oleh 112
    try:
        import sqlite3, tempfile
        import sosmed.db as sdb
        os.environ["SOSMED_DB_FILE"] = os.path.join(tempfile.mkdtemp(), "ig_test.db")
        os.environ["SOSMED_OFFICIAL_HANDLES"] = "kring_pajak"
        c = sdb.init_db(sdb.connect())
        r = sdb.ingest_items(c, items, default_platform="ig", source="test")
        assert r["ok"] and r["n_new"] == 3, r
        rowq = c.execute("SELECT item_type,status,answered_by FROM sosmed_items "
                         "WHERE external_id='111'").fetchone()
        assert rowq[0] == "pertanyaan" and rowq[1] == "terjawab", tuple(rowq)
        assert rowq[2] == "kring_pajak", tuple(rowq)
        c.close()
    except ImportError:
        pass
    print("SOSMED_IG_COLLECTOR_SMOKE_OK")


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
        rest = list(sys.argv[2:])
        out = None
        if "--out" in rest:
            i = rest.index("--out")
            if i + 1 < len(rest):
                out = rest[i + 1]
                del rest[i:i + 2]
            else:
                del rest[i]
        df = rest[0] if len(rest) > 0 else _yesterday()
        dt = rest[1] if len(rest) > 1 else df
        out = out or ("ig_dump_%s_%s.csv" % (df, dt))
        its, info = collect_range(df, dt, official_handles=[target_handle()], dump_path=out)
        print(json.dumps({"info": info, "n": len(its)}, ensure_ascii=False))
    else:
        _smoke()
