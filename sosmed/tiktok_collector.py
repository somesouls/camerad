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

BALASAN BERJENJANG: berbeda dgn IG (balasan ikut lewat graphql saat scroll), TikTok
HANYA mengirim balasan (`/api/comment/list/reply/`) setelah tombol \"Lihat N balasan\"
DIKLIK. Karena itu _load_comments membuka SEMUA toggle balasan (role+text, force)
spersis mekanisme yang terbukti di ig_collector, lalu balasan tersadap otomatis.

FAIL-SOFT: import Playwright LAZY; fungsi murni extract_tt_comments teruji offline.

ENV:
  SOSMED_TT_STATE_FILE   : path storage_state (default <runs>/tiktok_state.json)
  SOSMED_TT_USERNAME     : @handle akun kedua (login otomatis)
  SOSMED_TT_PASSWORD     : password akun kedua
  SOSMED_TT_TARGET       : @handle akun RESMI dipantau (default SOSMED_X_TARGET / 'kring_pajak')
  SOSMED_TT_HEADLESS     : 1 (default) headless; 0 utk login manual
  SOSMED_TT_MAX_VIDEOS   : jumlah video terbaru dibuka (default 10; 0 = SEMUA video target)
  SOSMED_TT_MAX_SCROLLS  : batas scroll komentar per-video (default 8)
  SOSMED_TT_DEBUG        : 1 utk mencetak trace URL/keys respons (diagnosa)
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
  python -m sosmed.tiktok_collector dump --out hasil_tt.csv
"""
import os
import re
import json
import time
import random as _random
import datetime as _dt

_DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Tambahkan import stealth di bagian atas file
try:
    from playwright_stealth import stealth_sync
except ImportError:
    stealth_sync = None

def _human_sleep(minimum=1.5, maximum=3.5):
    """Jeda waktu acak agar terlihat seperti manusia membaca."""
    time.sleep(_random.uniform(minimum, maximum))

def _check_and_wait_captcha(page):
    """Mendeteksi apakah TikTok memunculkan Slider Captcha. Jika ada, tunggu manusia menyelesaikannya."""
    try:
        # Berdasarkan HTML Anda: <div class="TUXModal captcha-verify-container"...>
        captcha_locator = page.locator(".captcha-verify-container, #captcha-verify-container")
        if captcha_locator.count() > 0 and captcha_locator.is_visible():
            print("\n[!] CAPTCHA TERDETEKSI! Silakan geser puzzle di browser secara manual...")
            # Tunggu sampai elemen captcha hilang (manusia selesai menggeser)
            captcha_locator.wait_for(state="hidden", timeout=120000) # Tunggu maksimal 2 menit
            print("[+] Captcha berhasil dilewati, melanjutkan proses...\n")
            _human_sleep(2, 4)
    except Exception:
        pass

def _human_scroll(page, scrolls=3):
    """Scroll seperti manusia sungguhan, naik turun sedikit."""
    for _ in range(scrolls):
        _check_and_wait_captcha(page)
        # Scroll jarak acak
        scroll_y = _random.randint(400, 900)
        page.mouse.wheel(0, scroll_y)
        _human_sleep(0.5, 1.5)
        # Kadang manusia scroll ke atas sedikit
        if _random.random() > 0.7:
            page.mouse.wheel(0, -_random.randint(100, 300))
            _human_sleep(0.5, 1.0)


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
    """Panen daftar {aweme_id -> {create_time, owner}} dari payload item_list utk
    memilih video terbaru milik target yang akan dibuka."""
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
            _au = obj.get("author") or obj.get("author_user_info") or {}
            _oh = (str(_au.get("unique_id") or _au.get("uniqueId") or "").lstrip("@").lower()
                   if isinstance(_au, dict) else "")
            out[str(aid)] = {"create_time": ct, "owner": _oh}
        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_aweme_ids(v, out, depth + 1)
    else:
        for v in obj:
            if isinstance(v, (dict, list)):
                extract_aweme_ids(v, out, depth + 1)
    return out


def _thread_sort(items):
    """Urutkan komentar ala thread: komentar UTAMA lalu BALASAN-nya (berjenjang),
    dikelompokkan per video (conversation_id) & urut waktu. Memudahkan dibaca di
    CSV: utama | balasan | balasan ..."""
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


def _video_url(s, target=None):
    """Bentuk URL video TikTok dari input: URL penuh -> buang query; id telanjang
    -> https://www.tiktok.com/@<target>/video/<id>. MURNI, bisa diuji offline."""
    s = str(s or "").strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        return s.split("?")[0]
    tgt = (target or target_handle()).lstrip("@")
    return "https://www.tiktok.com/@%s/video/%s" % (tgt, s)


def _sleep(base):
    try:
        time.sleep(max(0.2, base + _random.uniform(-0.4, 0.8)))
    except Exception:
        pass


# ===========================================================================
# Bagian browser (Playwright) — import LAZY
# ===========================================================================
def _context_kwargs():
    """Mengatur konteks browser, memaksa resolusi besar agar Captcha tidak terpotong."""
    kw = dict(
        user_agent=(os.environ.get("SOSMED_TT_UA") or _DEFAULT_UA),
        locale="id-ID",
        viewport={"width": 1280, "height": 768} # PERBAIKAN: Perbesar resolusi agar slider captcha terlihat
    )
    tz = _tz_name()
    if tz:
        kw["timezone_id"] = tz
    return kw


def _persistent_dir():
    return (os.environ.get("SOSMED_TT_USER_DATA_DIR") or "").strip()


def _launch(pw, headless):
    """Memulai browser dengan Stealth Mode dan resolusi penuh."""
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--window-size=1920,1080" # PERBAIKAN: Paksa ukuran jendela Windows
    ]

    # PERBAIKAN: Gunakan Chrome asli yang terinstal di komputer, bukan Chromium
    channel = (os.environ.get("SOSMED_TT_CHANNEL") or "chrome").strip()
    udd = _persistent_dir()

    if udd:
        prof = (os.environ.get("SOSMED_TT_PROFILE_DIR") or "Default").strip()
        if prof:
            args.append("--profile-directory=%s" % prof)
        launch_kw = dict(headless=headless, args=args, channel=channel)

        # Jangan gunakan context kwargs viewport terpisah jika menggunakan persistent context
        # agar window tidak terpotong (bergantung pada --window-size di args)
        launch_kw["viewport"] = {"width": 1920, "height": 1080}

        ctx = pw.chromium.launch_persistent_context(udd, **launch_kw)
        if stealth_sync:
            ctx.on("page", lambda page: stealth_sync(page))
        return None, ctx, True

    launch_kw = dict(headless=headless, args=args, channel=channel)
    browser = pw.chromium.launch(**launch_kw)
    sf = state_file()
    kw = _context_kwargs()

    if os.path.exists(sf):
        try:
            ctx = browser.new_context(storage_state=sf, **kw)
            if stealth_sync: ctx.on("page", lambda page: stealth_sync(page))
            return browser, ctx, False
        except Exception:
            pass

    ctx = browser.new_context(**kw)
    if stealth_sync: ctx.on("page", lambda page: stealth_sync(page))
    return browser, ctx, False

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


_MORE_COMMENTS_RE = re.compile(
    r"(view\s+(more|all)?\s*comments?|"
    r"(lihat|muat)\s+komentar(\s+(lain|lainnya|sebelumnya))?)", re.I)
_MORE_REPLIES_RE = re.compile(
    r"(view\s+(all\s+)?(\d[\d.,]*\s+)?(more\s+)?repl(y|ies)|"
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
    Gabungkan lokator role=button + get_by_text, lalu klik biasa -> force.
    Dedup posisi agar tidak dobel. Kembalikan jumlah klik sukses (fail-soft)."""
    cands = []
    try:
        cands.extend(page.get_by_role("button", name=rx).all())
    except Exception:
        pass
    try:
        cands.extend(page.get_by_text(rx).all())
    except Exception:
        pass
    n = 0
    seen = set()
    for b in cands:
        if n >= limit:
            break
        box = None
        try:
            box = b.bounding_box()
        except Exception:
            box = None
        key = (round(box["x"]), round(box["y"])) if box else None
        if key is not None and key in seen:
            continue
        try:
            b.scroll_into_view_if_needed(timeout=800)
        except Exception:
            pass
        ok = False
        try:
            b.click(timeout=1200)
            ok = True
        except Exception:
            try:
                b.click(timeout=1500, force=True)
                ok = True
            except Exception:
                ok = False
        if ok:
            n += 1
            if key is not None:
                seen.add(key)
    return n


def _scroll_comment_section(page):
    """Scroll khusus di dalam panel komentar TikTok agar API memuat data baru."""
    _check_and_wait_captcha(page)

    # Paksa scroll element via JS (jauh lebih cepat dan pasti mengenai target)
    try:
        page.evaluate("""() => {
            // Cari container komentar TikTok berdasarkan atribut spesifiknya
            const panel = document.querySelector('[data-e2e="search-comment-container"]') ||
                          document.querySelector('div[class*="DivCommentListContainer"]');
            if(panel) {
                panel.scrollBy(0, 1500);
            } else {
                window.scrollBy(0, 1000);
            }
        }""")
    except Exception:
        pass
    _human_sleep(1.0, 2.0)


def _click_replies(page):
    """Klik semua tombol 'View X replies' / 'View X more' secara instan menggunakan JavaScript."""
    try:
        clicked = page.evaluate("""() => {
            let count = 0;
            // Ambil semua elemen view-more (balasan komentar)
            const btns = document.querySelectorAll('[data-e2e^="view-more-"]');
            for (let b of btns) {
                // Pastikan tombol terlihat (tidak disembunyikan CSS)
                const style = window.getComputedStyle(b);
                if (style.display !== 'none' && style.visibility !== 'hidden' && b.offsetHeight > 0) {
                    b.click();
                    count++;
                    if (count >= 15) break; // Batasi klik agar browser tidak crash
                }
            }
            return count;
        }""")
        return clicked or 0
    except Exception:
        return 0

def _load_comments(page, max_scrolls, diag=None):
    """Memuat komentar dan balasan dengan log responsif dan tidak macet."""
    if diag is None:
        diag = {}

    _human_sleep(3, 5) # Beri waktu render komentar awal
    _check_and_wait_captcha(page)

    for i in range(max(1, int(max_scrolls or 1))):
        print(f"      - Scroll & memuat balasan ({i+1}/{max_scrolls})...")

        # 1. Buka balasan berjenjang (Klik via JS sangat cepat & anti-macet)
        for _r in range(3):
            c = _click_replies(page)
            diag["more_replies"] = diag.get("more_replies", 0) + c
            if not c:
                break
            _human_sleep(1.0, 1.5)

        # 2. Scroll panel komentar ke bawah untuk memicu lazy load (Komentar baru)
        _scroll_comment_section(page)

    return diag

def collect_range(date_from=None, date_to=None, official_handles=None,
                  target=None, trigger="manual", dump_path=None,
                  only_urls=None, **_kw):
    """
    1. Buka profil target
    2. Parsing DOM HTML untuk mengambil link /video/ atau /photo/
    3. Buka link tersebut satu per satu dan load komentar

    Mode "Tarik postingan ini": bila only_urls diberikan (list URL/id video),
    LEWATI buka profil & scraping DOM; langsung proses video yang diminta.
    """
    date_from = date_from or _yesterday()
    date_to = date_to or date_from
    off = set((h or "").lower().lstrip("@") for h in (official_handles or []))
    tgt = (target or target_handle()).lstrip("@")
    off.add(tgt.lower())
    url = profile_url(tgt)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return [], {"ok": False, "need_playwright": True, "error": str(e)}

    headless = _flag("SOSMED_TT_HEADLESS", "1") # Set 0 saat pertama kali untuk selesaikan captcha
    max_videos = _int_env("SOSMED_TT_MAX_VIDEOS", 10)
    max_scrolls = _int_env("SOSMED_TT_MAX_SCROLLS", 8)

    by_id = {}
    awemes = {} # Untuk simpan API data jika lewat
    _diag = {"comments": 0, "items": 0, "json": 0, "more_comments": 0, "more_replies": 0}
    _debug = _flag("SOSMED_TT_DEBUG", "0")
    _trace = []

    with sync_playwright() as pw:
        browser, ctx, persistent = _launch(pw, headless)

        # Tetap pasang penyadap API (karena komentar HANYA bisa diambil utuh via JSON respon)
        def _on_response(resp):
            try:
                u = resp.url or ""
                if "tiktok.com" not in u: return
                is_cmt = "/api/comment/list" in u
                is_item = ("/api/post/item_list" in u or "/api/user/detail" in u)
                if not (is_cmt or is_item): return

                try: data = resp.json()
                except Exception: return

                _diag["json"] += 1
                if is_cmt:
                    before = len(by_id)
                    for it in extract_tt_comments(data, off):
                        by_id[it["external_id"]] = it
                    got = len(by_id) - before
                    if got: _diag["comments"] += 1
                if is_item:
                    extract_aweme_ids(data, awemes)
                    _diag["items"] += 1
            except Exception:
                pass

        ctx.on("response", _on_response)
        page = ctx.new_page()

        # Cek Login
        if not _is_logged_in(page):
            ok, err = _auto_login(ctx)
            if not ok:
                _close(browser, ctx)
                return [], {"ok": False, "need_login": True, "error": err, "url": url}

        # Mode "Tarik postingan ini": bila only_urls diberikan, LEWATI buka profil
        # & scraping DOM; langsung proses URL/id video yang diminta.
        _only = []
        for _s in (only_urls or []):
            _vu = _video_url(_s, tgt)
            if _vu and _vu not in _only:
                _only.append(_vu)
        if _only:
            valid_links = _only
            print(f"[*] Mode tarik-postingan: {len(valid_links)} URL diminta.")
        else:
            print(f"[*] Membuka profil: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass

            _human_sleep(4, 6)
            _check_and_wait_captcha(page)

            # LANGKAH 2: Scroll profil dan Kumpulkan Link dari HTML (DOM)
            print("[*] Menscroll halaman profil untuk memuat link video/photo...")
            _human_scroll(page, scrolls=4)
            _human_sleep(2, 3)

            # Ekstrak href menggunakan JavaScript DOM selector
            print("[*] Mengekstrak link postingan...")
            hrefs = page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href]'));
                return links.map(a => a.href);
            }""")

            # Filter hanya link video atau photo milik target
            valid_links = []
            target_pattern_video = f"/@{tgt}/video/"
            target_pattern_photo = f"/@{tgt}/photo/"

            for href in hrefs:
                if target_pattern_video in href or target_pattern_photo in href:
                    # Bersihkan URL dari parameter query (misal ?is_from_webapp=1)
                    clean_url = href.split("?")[0]
                    if clean_url not in valid_links:
                        valid_links.append(clean_url)

            print(f"[+] Ditemukan {len(valid_links)} link postingan di DOM.")

            # Batasi jumlah video yang akan diproses
            if max_videos and max_videos > 0:
                valid_links = valid_links[:max_videos]

        # LANGKAH 3 & 4: Buka satu per satu dengan klik elemen (seperti manusia)
        for idx, post_url in enumerate(valid_links):
            print(f"[*] Memproses ({idx+1}/{len(valid_links)}): {post_url}")
            try:
                # Ekstrak ID postingan dari URL untuk mencari elemen <a> di halaman
                post_id = post_url.rstrip("/").split("/")[-1].split("?")[0]
                video_el = page.locator(f'a[href*="{post_id}"]').first

                if video_el.count() > 0:
                    print("    -> Mengklik thumbnail di profil...")
                    video_el.scroll_into_view_if_needed()
                    _human_sleep(1, 2)
                    video_el.click()
                else:
                    print("    -> Navigasi direct URL (Fallback)...")
                    page.goto(post_url, referer=url, wait_until="domcontentloaded", timeout=45000)

            except Exception as e:
                print(f"[-] Gagal membuka postingan {post_url}: {e}")
                continue

            # Tunggu overlay video dan kolom komentar dimuat penuh
            _human_sleep(4, 6)
            _check_and_wait_captcha(page)

            # Scroll panel komentar dan ekspansi balasan
            print("    -> Memuat komentar...")
            _load_comments(page, max_scrolls, _diag)

            # Berikan waktu API merespon dan menyimpan JSON
            _human_sleep(2, 3)

            # Tutup overlay (kembali ke profil) atau go back
            try:
                # Cari tombol "Close" (X) dari overlay TikTok: <button data-e2e="browse-close">
                close_btn = page.locator('[data-e2e="browse-close"]').first
                if close_btn.count() > 0 and close_btn.is_visible():
                    print("    -> Menutup overlay video...")
                    close_btn.click()
                    _human_sleep(1.5, 2.5)
                else:
                    # Jika tidak ada tombol close (mungkin ter-reload), go back ke profil
                    print("    -> Kembali ke profil...")
                    page.go_back(wait_until="domcontentloaded")
                    _human_sleep(2, 4)
            except Exception:
                # Fallback paling aman jika navigasi error
                page.goto(url, wait_until="domcontentloaded")
                _human_sleep(2, 4)

        if not persistent:
            try:
                ctx.storage_state(path=state_file())
            except Exception:
                pass
        _close(browser, ctx)

    # Parsing hasil akhir
    items = list(by_id.values())
    for it in items:
        # Jika API komentar tidak membawa permalink, bentuk manual
        if it.get("conversation_id") and not it.get("permalink"):
            it["permalink"] = "https://www.tiktok.com/@%s/video/%s" % (tgt, it["conversation_id"])

    dumped = False
    if dump_path:
        dumped = _write_dump(dump_path, items, tgt)

    info = {
        "ok": True, "count": len(items),
        "target": tgt, "logged_in": True,
        "videos_opened": len(valid_links),
        "comments_seen": _diag["comments"],
        "more_replies_clicked": _diag["more_replies"]
    }

    if len(items) == 0:
        info["note"] = "0 hasil: Captcha mungkin menghalangi, atau elemen tidak ditemukan. Gunakan SOSMED_TT_HEADLESS=0 untuk inspeksi visual."

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
    # aweme ids + owner
    il = {"itemList": [{"id": "vid9", "desc": "info pajak", "createTime": 1757100000,
                        "author": {"uniqueId": "kring_pajak"}}]}
    aw = extract_aweme_ids(il)
    assert "vid9" in aw and aw["vid9"]["owner"] == "kring_pajak", aw
    # single-post: bentuk URL video dari URL penuh / id telanjang
    assert _video_url("https://www.tiktok.com/@kring_pajak/video/123?is_from_webapp=1") == \
        "https://www.tiktok.com/@kring_pajak/video/123"
    assert _video_url("7300", target="kring_pajak") == \
        "https://www.tiktok.com/@kring_pajak/video/7300"
    # threaded sort: komentar UTAMA (c1) sebelum BALASAN-nya (c2, level 1)
    ts = _thread_sort(items)
    _order = [it["external_id"] for it in ts]
    assert _order.index("c1") < _order.index("c2"), _order
    assert ts[_order.index("c2")].get("_level") == 1, ts
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
        out = out or ("tt_dump_%s_%s.csv" % (df, dt))
        its, info = collect_range(df, dt, official_handles=[target_handle()], dump_path=out)
        print(json.dumps({"info": info, "n": len(its)}, ensure_ascii=False))
    else:
        _smoke()
