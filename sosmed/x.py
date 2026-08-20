# -*- coding: utf-8 -*-
"""sosmed_x.py — Collector X (Twitter) untuk Tool Sosmed, capability-aware.

Hanya STDLIB (urllib) supaya ringan & bisa diuji tanpa dependensi tambahan.
Pemetaan tweet -> item ternormalisasi (map_tweets_v2) BISA DIUJI OFFLINE dan
dipakai baik oleh impor manual (paste JSON hasil ekspor X API) maupun oleh
penarikan live.

== Catatan X API Free Tier ==
Free tier X API v2 praktis TIDAK memberi akses baca (mentions/replies/search).
Endpoint tsb butuh tier Basic ($) ke atas. Karena itu:
  - Fungsi live (pull_mentions/search_recent) menangani 401/403/429 dan
    mengembalikan pesan kapabilitas yang jelas, TIDAK melempar mentah.
  - `capabilities()` melaporkan apa yang tersedia dengan kredensial saat ini.
  - Alur utama MVP = impor manual; penarikan live otomatis lebih lengkap saat
    tier dinaikkan tanpa mengubah kode.

Kredensial dibaca dari environment (jangan di-hardcode / jangan commit):
  X_BEARER_TOKEN         : App-only Bearer (paling umum untuk baca v2)
  X_OFFICIAL_USERNAME    : username akun resmi (utk resolve user id & mentions)
"""
import os
import json
import urllib.parse as _up
import urllib.request as _ur
import urllib.error as _ue

API_BASE = "https://api.twitter.com/2"

_TWEET_FIELDS = ("created_at,author_id,conversation_id,in_reply_to_user_id,"
                 "referenced_tweets,public_metrics,lang,entities")
_EXPANSIONS = "author_id"
_USER_FIELDS = "username,name"


def _bearer():
    return (os.environ.get("X_BEARER_TOKEN") or "").strip()


def official_username():
    return (os.environ.get("X_OFFICIAL_USERNAME", "") or "").strip().lstrip("@")


class XCapabilityError(Exception):
    """Ditarik saat endpoint tidak tersedia untuk tier/kredensial saat ini."""


def _get(path, params=None, timeout=30):
    """GET ke X API v2. Return (status_code, json_or_none, error_text)."""
    tok = _bearer()
    if not tok:
        return 0, None, "X_BEARER_TOKEN belum diset."
    url = API_BASE + path
    if params:
        url += "?" + _up.urlencode(params)
    req = _ur.Request(url, headers={"Authorization": "Bearer " + tok,
                                    "User-Agent": "CameradSosmed/1.0"})
    try:
        with _ur.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(body) if body else {}, ""
    except _ue.HTTPError as e:
        try:
            etxt = e.read().decode("utf-8", "replace")
        except Exception:
            etxt = str(e)
        return e.code, None, etxt
    except Exception as e:
        return 0, None, str(e)


def _cap_message(status, err):
    if status in (401,):
        return "Kredensial X tidak valid / tidak berwenang (401). Cek X_BEARER_TOKEN."
    if status in (403,):
        return ("Endpoint ini tidak tersedia untuk tier X API Anda (403). "
                "Baca mentions/replies/search butuh tier Basic ke atas. "
                "Gunakan impor manual untuk sementara.")
    if status == 429:
        return "Rate limit X API tercapai (429). Coba lagi nanti."
    return "Gagal memanggil X API (status %s): %s" % (status, (err or "")[:300])


# ---------------------------------------------------------------------------
# Pemetaan tweet v2 -> item ternormalisasi (OFFLINE-TESTABLE)
# ---------------------------------------------------------------------------
def map_tweets_v2(payload, official_handles=None):
    """Ubah respons X API v2 (dict {data:[...], includes:{users:[...]}}) atau
    list tweet mentah menjadi list item siap untuk sosmed_db.ingest_items."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, list):
        data = payload
        includes = {}
    elif isinstance(payload, dict):
        data = payload.get("data") or []
        includes = payload.get("includes") or {}
    else:
        return []
    users_by_id = {}
    for u in (includes.get("users") or []):
        if isinstance(u, dict) and u.get("id"):
            users_by_id[str(u["id"])] = u
    off = set(h.lower() for h in (official_handles or []))

    items = []
    for tw in data:
        if not isinstance(tw, dict) or not tw.get("id"):
            continue
        author_id = str(tw.get("author_id") or "")
        u = users_by_id.get(author_id, {})
        handle = str(u.get("username") or tw.get("author_username") or "").lstrip("@")
        name = str(u.get("name") or handle)
        reply_to = ""
        for ref in (tw.get("referenced_tweets") or []):
            if isinstance(ref, dict) and ref.get("type") == "replied_to":
                reply_to = str(ref.get("id") or "")
                break
        pm = tw.get("public_metrics") or {}
        tags = []
        ent = tw.get("entities") or {}
        for h in (ent.get("hashtags") or []):
            tag = h.get("tag") if isinstance(h, dict) else None
            if tag:
                tags.append("#" + str(tag).lower())
        permalink = ("https://x.com/%s/status/%s" % (handle, tw["id"])) if handle \
            else "https://x.com/i/status/%s" % tw["id"]
        items.append({
            "platform": "x",
            "external_id": str(tw["id"]),
            "conversation_id": str(tw.get("conversation_id") or tw["id"]),
            "in_reply_to_id": reply_to,
            "permalink": permalink,
            "author_handle": handle,
            "author_name": name,
            "author_id": author_id,
            "is_official": "1" if handle.lower() in off else "",
            "created_at": tw.get("created_at") or "",
            "text": tw.get("text") or "",
            "language": tw.get("lang") or "",
            "like_count": pm.get("like_count", 0),
            "reply_count": pm.get("reply_count", 0),
            "repost_count": pm.get("retweet_count", 0),
            "hashtags_list": tags,
            "raw": tw,
        })
    return items


# ---------------------------------------------------------------------------
# Live (butuh tier baca; menangani batasan free-tier secara anggun)
# ---------------------------------------------------------------------------
def resolve_user_id(username):
    username = (username or "").lstrip("@")
    if not username:
        return None, "Username kosong."
    st, js, err = _get("/users/by/username/" + _up.quote(username),
                       {"user.fields": "username,name"})
    if st == 200 and js and js.get("data"):
        return str(js["data"]["id"]), ""
    return None, _cap_message(st, err)


def pull_mentions(user_id=None, max_results=50, since_id=None, official_handles=None):
    """Tarik mentions ke akun resmi. Return (items, info).
    Free tier umumnya 403 -> items kosong + pesan kapabilitas."""
    if not user_id:
        uid, err = resolve_user_id(official_username())
        if not uid:
            return [], {"ok": False, "error": err}
        user_id = uid
    params = {"tweet.fields": _TWEET_FIELDS, "expansions": _EXPANSIONS,
              "user.fields": _USER_FIELDS,
              "max_results": max(5, min(int(max_results), 100))}
    if since_id:
        params["since_id"] = str(since_id)
    st, js, err = _get("/users/%s/mentions" % user_id, params)
    if st != 200 or js is None:
        return [], {"ok": False, "status": st, "error": _cap_message(st, err)}
    items = map_tweets_v2(js, official_handles=official_handles)
    newest = (js.get("meta") or {}).get("newest_id")
    return items, {"ok": True, "count": len(items), "newest_id": newest}


def search_recent(query, max_results=50, official_handles=None):
    """Cari tweet publik terbaru (butuh tier Basic+). Return (items, info)."""
    params = {"query": query, "tweet.fields": _TWEET_FIELDS,
              "expansions": _EXPANSIONS, "user.fields": _USER_FIELDS,
              "max_results": max(10, min(int(max_results), 100))}
    st, js, err = _get("/tweets/search/recent", params)
    if st != 200 or js is None:
        return [], {"ok": False, "status": st, "error": _cap_message(st, err)}
    items = map_tweets_v2(js, official_handles=official_handles)
    return items, {"ok": True, "count": len(items)}


def capabilities():
    """Laporkan kapabilitas dengan kredensial saat ini (untuk UI Kelola Data)."""
    out = {"has_token": bool(_bearer()),
           "official_username": official_username(),
           "can_resolve_user": False, "can_read_mentions": False,
           "can_search": False, "notes": []}
    if not _bearer():
        out["notes"].append("X_BEARER_TOKEN belum diset — gunakan impor manual.")
        return out
    uname = official_username()
    if uname:
        uid, err = resolve_user_id(uname)
        out["can_resolve_user"] = bool(uid)
        if not uid:
            out["notes"].append(err)
        else:
            _items, info = pull_mentions(user_id=uid, max_results=5)
            out["can_read_mentions"] = bool(info.get("ok"))
            if not info.get("ok"):
                out["notes"].append(info.get("error", ""))
    else:
        out["notes"].append("X_OFFICIAL_USERNAME belum diset.")
    _s, sinfo = search_recent("kringpajak", max_results=10)
    out["can_search"] = bool(sinfo.get("ok"))
    if not sinfo.get("ok") and sinfo.get("error"):
        out["notes"].append("Search: " + sinfo["error"])
    return out


# ===========================================================================
# Smoke test (offline) — python3 sosmed_x.py
# ===========================================================================
if __name__ == "__main__":
    fixture = {
        "data": [
            {"id": "2001", "text": "@kring_pajak saya lupa EFIN #pajak",
             "author_id": "9", "conversation_id": "2001", "lang": "in",
             "created_at": "2026-08-04T10:00:00.000Z",
             "public_metrics": {"like_count": 3, "reply_count": 1,
                                "retweet_count": 0, "quote_count": 0},
             "entities": {"hashtags": [{"tag": "pajak"}]}},
            {"id": "2002", "text": "Halo, silakan DM ya. Terima kasih",
             "author_id": "1", "conversation_id": "2001",
             "created_at": "2026-08-04T10:20:00.000Z",
             "referenced_tweets": [{"type": "replied_to", "id": "2001"}],
             "public_metrics": {"like_count": 0, "reply_count": 0,
                                "retweet_count": 0, "quote_count": 0}},
        ],
        "includes": {"users": [
            {"id": "9", "username": "wpbingung", "name": "WP Bingung"},
            {"id": "1", "username": "kring_pajak", "name": "Kring Pajak"},
        ]},
        "meta": {"newest_id": "2002"},
    }
    items = map_tweets_v2(fixture, official_handles={"kring_pajak"})
    assert len(items) == 2, items
    q = items[0]
    assert q["external_id"] == "2001" and q["author_handle"] == "wpbingung", q
    assert q["permalink"] == "https://x.com/wpbingung/status/2001", q["permalink"]
    assert q["conversation_id"] == "2001" and q["like_count"] == 3, q
    a = items[1]
    assert a["in_reply_to_id"] == "2001", a
    assert a["is_official"] == "1", a

    # integrasi dengan sosmed_db: map -> ingest -> pairing
    import os as _os, tempfile as _tf
    _os.environ["SOSMED_DB_FILE"] = _os.path.join(_tf.mkdtemp(), "sx.db")
    _os.environ["SOSMED_OFFICIAL_HANDLES"] = "kring_pajak"
    import sosmed.db as sdb
    c = sdb.init_db(sdb.connect())
    r = sdb.ingest_items(c, items, source="pull_x")
    assert r["n_new"] == 2, r
    it = sdb.get_item(c, 1)
    assert it["topik"] == "Lupa EFIN", it["topik"]
    assert it["status"] == "terjawab" and it["response_time_s"] == 20 * 60, it
    c.close()

    # tanpa token -> capabilities aman (tidak crash)
    _os.environ.pop("X_BEARER_TOKEN", None)
    cap = capabilities()
    assert cap["has_token"] is False, cap
    print("SOSMED_X_SMOKE_OK")
