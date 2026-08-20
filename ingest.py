# -*- coding: utf-8 -*-
"""
ingest.py
---------
Menarik log Dialogflow dari Google Cloud Logging lalu menyimpannya ke SQLite
(analytics.db) untuk sistem analitik/dashboard. Anti-duplikat via insertId.

Sumber & parsing 100% konsisten dengan web_app.step1_pull_logs + step2 (regex
yang sama dari textPayload "Dialogflow Response").

Pakai lewat CLI atau dari web_app (scheduler/tombol "Tarik sekarang"):

    python ingest.py --yesterday            # default (data H-1)
    python ingest.py --days 30              # 30 hari terakhir (s/d kemarin)
    python ingest.py --start 2026-07-01 --end 2026-07-15
    python ingest.py --lang en --yesterday

Catatan retensi: Cloud Logging default menyimpan ~30 hari. Untuk "masa lampau"
lebih jauh, butuh sink ke BigQuery / log bucket beretensi panjang.
"""
import os
import re
import sys
import json
import time
import argparse
import datetime as _dt

import db.analytics_db as adb


# ---------------------------------------------------------------------
# Konfigurasi (env, konsisten dgn web_app CONFIG)
# ---------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file():
    """Muat .env sederhana bila python-dotenv tidak ada."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(BASE_DIR, ".env"))
        return
    except Exception:
        pass
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(path):
        return
    for line in open(path, "r", encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def config():
    return {
        "project_id": os.environ.get("PIPELINE_PROJECT_ID", "avaya-djp-klipbot-prod"),
        "service_account_file": os.environ.get("PIPELINE_SA_FILE")
        or os.path.join(BASE_DIR, "service-account.json"),
        "google_scope": "https://www.googleapis.com/auth/cloud-platform",
    }


# ---------------------------------------------------------------------
# Google auth (service-account -> access token)
# ---------------------------------------------------------------------
def google_token(cfg):
    file = cfg["service_account_file"]
    if not os.path.isfile(file):
        fallback = os.path.join(BASE_DIR, "service-account.json")
        if os.path.isfile(fallback):
            file = fallback
        else:
            raise Exception(
                "service-account.json tidak ditemukan (dicek: '%s' dan '%s'). "
                "Set PIPELINE_SA_FILE di .env atau taruh service-account.json di "
                "folder yang sama." % (cfg["service_account_file"], fallback)
            )
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GRequest
    creds = service_account.Credentials.from_service_account_file(
        file, scopes=[cfg["google_scope"]]
    )
    creds.refresh(GRequest())
    if not creds.token:
        raise Exception("Token Google gagal.")
    return creds.token


# ---------------------------------------------------------------------
# Tarik log dari Cloud Logging (identik dgn web_app.step1)
# ---------------------------------------------------------------------
def pull_entries(cfg, token, start, end, lang="id"):
    """start/end: 'YYYY-MM-DD' inklusif. Return list of log entry dict."""
    import requests

    tz = _dt.timezone(_dt.timedelta(hours=7))
    day_ms = 86400000
    start_ms = int(_dt.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=tz).timestamp()) * 1000
    end_excl_ms = int(_dt.datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=tz).timestamp()) * 1000 + day_ms

    url = "https://logging.googleapis.com/v2/entries:list"
    page_size = 5000
    max_retries = 5
    entries_all = []

    seg_start = start_ms
    while seg_start < end_excl_ms:
        seg_end = min(seg_start + day_ms, end_excl_ms)
        seg_start_iso = _dt.datetime.utcfromtimestamp(seg_start / 1000).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"
        seg_end_iso = _dt.datetime.utcfromtimestamp(seg_end / 1000).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"
        flt = " AND ".join([
            'textPayload:"Dialogflow Response"',
            'textPayload:"lang: \\"%s\\""' % lang,
            'timestamp >= "%s"' % seg_start_iso,
            'timestamp < "%s"' % seg_end_iso,
        ])
        page_token = ""
        while True:
            body = {
                "resourceNames": ["projects/" + cfg["project_id"]],
                "filter": flt,
                "orderBy": "timestamp asc",
                "pageSize": page_size,
            }
            if page_token:
                body["pageToken"] = page_token
            attempt = 0
            resp = None
            while True:
                try:
                    r = requests.post(
                        url, json=body,
                        headers={"Authorization": "Bearer " + token,
                                 "Content-Type": "application/json"},
                        timeout=120,
                    )
                    jj = r.json()
                except Exception as e:
                    jj = {"error": str(e)}
                    r = None
                if r is not None and 200 <= r.status_code < 300 and isinstance(jj, dict) and "error" not in jj:
                    resp = jj
                    break
                attempt += 1
                if attempt > max_retries:
                    raise Exception("Gagal tarik log %s..%s: %s" % (seg_start_iso, seg_end_iso, str(jj)[:300]))
                time.sleep(1)
            entries = resp.get("entries") if isinstance(resp.get("entries"), list) else []
            entries_all.extend(entries)
            if resp.get("nextPageToken"):
                page_token = resp["nextPageToken"]
            else:
                break
        seg_start = seg_end
    return entries_all


# ---------------------------------------------------------------------
# Parsing entri -> baris (regex identik dgn web_app.step2)
# ---------------------------------------------------------------------
def _match(text, pattern, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else ""


def parse_entries(entries):
    rows = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        insert_id = item.get("insertId", "")
        tp = item.get("textPayload", "")
        trace_id = user_phrase = bot_response = intent_name = lang = waktu = ""
        score = ""
        if tp:
            trace_id = _match(tp, r'session_id:\s*"([^"]+)"') or item.get("trace", "")
            waktu = _match(tp, r'timestamp:\s*"([^"]+)"')
            user_phrase = _match(tp, r'resolved_query:\s*"([^"]+)"')
            bot_response = _match(tp, r'fulfillment\s*\{\s*speech:\s*"((?:[^"\\]|\\.)*?)"', re.S).replace("\\n", "\n")
            intent_name = _match(tp, r'metadata\s*\{\s*[^}]+?intent_name:\s*"([^"]+)"', re.S)
            lang = _match(tp, r'lang:\s*"([^"]+)"')
            score = _match(tp, r'score:\s*([0-9.]+)')
        else:
            trace_id = item.get("trace", "")
        # fallback waktu: pakai timestamp entri Cloud Logging bila tak ada di payload
        if not waktu:
            waktu = item.get("timestamp", "")
        rows.append({
            "ID trace": trace_id, "waktu interaksi": waktu, "user phrase": user_phrase,
            "bot response": bot_response, "intent name": intent_name, "lang": lang,
            "insertId": insert_id, "score": ("" if score == "" else float(score)),
        })
    return rows


# ---------------------------------------------------------------------
# Orkestrasi
# ---------------------------------------------------------------------
def ensure_range(start, end, lang="id", db_path=None, force=False, verbose=True):
    """Pastikan rentang [start,end] tersimpan LENGKAP di DB (ingest pintar).

    - Tarik HARI demi HARI; lewati hari yang sudah 'complete' (kecuali force).
    - Sebuah hari ditandai 'complete' hanya jika seluruh hari itu sudah lewat
      (tanggal < hari ini Asia/Jakarta). Jika ditarik saat harinya masih
      berjalan -> ditandai 'partial' agar ditarik ulang keesokan harinya.
    - Simpan interaksi terparse + entri log MENTAH (untuk rebuild Step 1).
    """
    load_env_file()
    cfg = config()
    conn = adb.init_db(adb.connect(db_path))
    today = adb._jkt_today()
    started = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    days = adb.list_days(start, end)
    todo = adb.days_to_fetch(conn, start, end, lang, force=force)
    day_results = []
    total_fetched = total_inserted = total_skipped = 0
    status = "ok"
    note = ""
    token = None
    try:
        for d in todo:
            if token is None:
                token = google_token(cfg)
            entries = pull_entries(cfg, token, d, d, lang)
            rows = parse_entries(entries)
            ins, sk = adb.upsert_interactions(conn, rows)
            adb.upsert_raw_entries(conn, entries, d, lang)
            complete = 1 if d < today else 0
            adb.set_day_status(conn, d, lang,
                               "complete" if complete else "partial",
                               fetched=len(rows), inserted=ins)
            total_fetched += len(rows)
            total_inserted += ins
            total_skipped += sk
            day_results.append({"day": d, "fetched": len(rows), "inserted": ins,
                                "status": "complete" if complete else "partial"})
        adb.set_meta(conn, "last_ingest_at", started)
        adb.set_meta(conn, "last_ingest_range", "%s..%s" % (start, end))
    except Exception as e:
        status = "error"
        note = str(e)[:500]
    finished = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    adb.log_ingest(conn, started_at=started, finished_at=finished, start_date=start,
                   end_date=end, lang=lang, fetched=total_fetched,
                   inserted=total_inserted, skipped=total_skipped, status=status, note=note)
    todo_set = set(todo)
    skipped_complete = [d for d in days if d not in todo_set]
    result = {"ok": status == "ok", "start": start, "end": end, "lang": lang,
              "fetched": total_fetched, "inserted": total_inserted,
              "skipped": total_skipped, "status": status, "note": note,
              "days_fetched": day_results,
              "days_skipped_complete": skipped_complete,
              "total_days": len(days)}
    if verbose:
        print(json.dumps(result, ensure_ascii=False))
    conn.close()
    return result


def ingest_entries(entries, lang=None, db_path=None, verbose=True):
    # Impor entri log Cloud Logging yang SUDAH dimuat (mis. dari unggah file
    # manual di halaman Kelola Data) langsung ke analytics.db. Parser & dedup
    # 100% sama dengan penarikan dari Google (parse_entries + upsert_interactions).
    #   entries : list dict (ekspor Cloud Logging; punya textPayload/insertId/...)
    #   lang    : "id"/"en" -> hanya impor baris bahasa itu; None -> auto per payload
    # Return ringkasan dict. Tidak menyentuh status-hari agar tidak mengganggu
    # logika tarik-pintar dari Google (unggahan bisa berupa potongan sebagian hari).
    load_env_file()
    conn = adb.init_db(adb.connect(db_path))
    started = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    status = "ok"
    note = ""
    parsed = inserted = skipped = raw_stored = 0
    days = set()
    langs = set()
    per_day = {}
    try:
        entries = [e for e in (entries or []) if isinstance(e, dict)]
        rows = parse_entries(entries)
        pairs = list(zip(rows, entries))
        if lang:
            lg = str(lang).strip().lower()
            pairs = [(r, e) for (r, e) in pairs if (r.get("lang") or "").lower() == lg]
        parsed = len(pairs)
        prows = [r for (r, _e) in pairs]
        inserted, skipped = adb.upsert_interactions(conn, prows)
        groups = {}
        for (r, e) in pairs:
            d = adb._day_from_ts(r.get("waktu interaksi") or e.get("timestamp") or "")
            lg = (r.get("lang") or lang or "id")
            days.add(d)
            langs.add(lg)
            per_day[d] = per_day.get(d, 0) + 1
            groups.setdefault((d, lg), []).append(e)
        for (d, lg), es in groups.items():
            raw_stored += adb.upsert_raw_entries(conn, es, d, lg)
        adb.set_meta(conn, "last_ingest_at", started)
        adb.log_ingest(conn, started_at=started,
                       finished_at=_dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                       start_date=(min(days) if days else ""),
                       end_date=(max(days) if days else ""),
                       lang=(",".join(sorted(langs)) if langs else (lang or "")),
                       fetched=parsed, inserted=inserted, skipped=skipped,
                       status="ok", note="unggah manual")
    except Exception as ex:
        status = "error"
        note = str(ex)[:500]
    conn.close()
    result = {"ok": status == "ok", "source": "upload", "parsed": parsed,
              "inserted": inserted, "skipped": skipped, "raw_stored": raw_stored,
              "days": sorted(days), "per_day": per_day,
              "start": (min(days) if days else None),
              "end": (max(days) if days else None),
              "langs": sorted(langs), "status": status, "note": note}
    if verbose:
        print(json.dumps(result, ensure_ascii=False))
    return result


def ingest_range(start, end, lang="id", db_path=None, verbose=True, force=False):
    """Kompatibilitas lama: delegasi ke ensure_range (ingest pintar)."""
    return ensure_range(start, end, lang=lang, db_path=db_path, force=force,
                        verbose=verbose)


def _yesterday():
    today = adb._jkt_today()
    d = _dt.datetime.strptime(today, "%Y-%m-%d").date() - _dt.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ingest log Dialogflow -> SQLite")
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD (default = start)")
    ap.add_argument("--days", type=int, help="N hari terakhir (s/d kemarin)")
    ap.add_argument("--yesterday", action="store_true", help="hanya data H-1 (default)")
    ap.add_argument("--lang", default="id", choices=["id", "en"])
    ap.add_argument("--db", help="path file SQLite (default analytics.db)")
    ap.add_argument("--force", action="store_true",
                    help="tarik ulang semua hari di rentang (abaikan status 'complete')")
    args = ap.parse_args(argv)

    y = _yesterday()
    if args.start:
        start = args.start[:10]
        end = (args.end or args.start)[:10]
    elif args.days:
        end = y
        d = _dt.datetime.strptime(y, "%Y-%m-%d").date() - _dt.timedelta(days=args.days - 1)
        start = d.strftime("%Y-%m-%d")
    else:
        start = end = y  # --yesterday / default

    res = ingest_range(start, end, lang=args.lang, db_path=args.db, force=args.force)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
