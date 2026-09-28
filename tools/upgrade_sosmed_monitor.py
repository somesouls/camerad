from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Pola tidak ditemukan di {path}: {old[:90]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---- backend monitor: metadata posting, label otomatis, ekspor semua kolom ----
replace_once("sosmed/monitor.py", "import re as _re\n", "import re as _re\nimport json as _json\nimport datetime as _dt\n")
replace_once(
    "sosmed/monitor.py",
    '''def set_post_label(conn, plat, conv, label):
    """Simpan/hapus nama postingan. label kosong = hapus."""
    label = (label or "").strip()
    key = _pl_key(plat, conv)
    if label:
        try:
            sdb.set_meta(conn, key, label)
        except Exception:
            pass
    else:
        try:
            conn.execute("DELETE FROM sosmed_meta WHERE key=?", (key,))
            conn.commit()
        except Exception:
            pass
    return True
''',
    '''def _pl_source_key(plat, conv):
    return "postlabelsource:%s:%s" % ((plat or ""), (conv or ""))


def set_post_label(conn, plat, conv, label):
    """Simpan/hapus label manual. Label manual selalu menang atas label otomatis."""
    label = (label or "").strip()
    key = _pl_key(plat, conv)
    skey = _pl_source_key(plat, conv)
    try:
        if label:
            sdb.set_meta(conn, key, label)
            sdb.set_meta(conn, skey, "manual")
        else:
            conn.execute("DELETE FROM sosmed_meta WHERE key IN (?,?)", (key, skey))
            conn.commit()
    except Exception:
        pass
    return True
''')
marker = '''# ---------------------------------------------------------------------------
# Deteksi akun resmi (robust: kolom is_official ATAU handle live)
# ---------------------------------------------------------------------------
'''
metadata_code = r'''# ---------------------------------------------------------------------------
# Metadata postingan + label otomatis (label manual tetap menang)
# ---------------------------------------------------------------------------
_MONTH_NAMES = ("", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
                "Juli", "Agustus", "September", "Oktober", "November", "Desember")


def _raw_dict(row):
    raw = row.get("raw_json") if isinstance(row, dict) else None
    if not raw:
        return {}
    try:
        val = _json.loads(raw) if isinstance(raw, str) else raw
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def _meta_date(value):
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)) or str(value).strip().isdigit():
            n = int(float(value))
            if n > 100000000000:
                n //= 1000
            return _dt.datetime.fromtimestamp(n, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    try:
        return sdb._iso(value)
    except Exception:
        return str(value or "")


def _normal_post_type(value, raw=None):
    v = str(value or "").strip().lower()
    if v in ("2", "video", "reel", "reels", "clips") or "video" in v or "reel" in v:
        return "Video"
    if v in ("1", "8", "carousel", "album", "photo", "image", "sidecar"):
        return "Carousel"
    if "carousel" in v or "photo" in v or "image" in v or "sidecar" in v:
        return "Carousel"
    if isinstance(raw, dict) and raw.get("carousel_media"):
        return "Carousel"
    return ""


def _post_metadata(rows, plat="", conv=""):
    explicit_date = ""
    post_type = ""
    for row in rows:
        raw = _raw_dict(row)
        if not explicit_date:
            for key in ("post_created_at", "post_date", "published_at", "taken_at", "create_time"):
                if raw.get(key) not in (None, ""):
                    explicit_date = _meta_date(raw.get(key))
                    if explicit_date:
                        break
        if not post_type:
            for key in ("post_type", "product_type", "media_type"):
                post_type = _normal_post_type(raw.get(key), raw)
                if post_type:
                    break
        link = str(row.get("permalink") or "").lower()
        if not post_type and ("/reel/" in link or "/video/" in link):
            post_type = "Video"
        elif not post_type and "/photo/" in link:
            post_type = "Carousel"
    times = sorted(str(r.get("created_at") or "") for r in rows if r.get("created_at"))
    post_date = explicit_date or (times[0] if times else "")
    return {
        "post_date": post_date,
        "post_date_source": "metadata" if explicit_date else "komentar_pertama",
        "post_type": post_type,
    }


def ensure_auto_post_labels(conn):
    """Isi/perbarui label otomatis; label lama/manual tidak pernah ditimpa."""
    pairs = conn.execute(
        "SELECT DISTINCT platform, conversation_id FROM sosmed_items "
        "WHERE conversation_id IS NOT NULL AND conversation_id!=''"
    ).fetchall()
    posts = []
    for plat, conv in pairs:
        rows = [dict(x) for x in conn.execute(
            "SELECT created_at,permalink,raw_json FROM sosmed_items "
            "WHERE platform=? AND conversation_id=? ORDER BY datetime(created_at),id",
            (plat, conv)).fetchall()]
        meta = _post_metadata(rows, plat, conv)
        posts.append((meta.get("post_date") or "", plat, conv, meta))
    posts.sort(key=lambda x: (x[0], x[1], x[2]))

    groups = {}
    for post_date, plat, conv, meta in posts:
        month_key = (post_date[:7] if len(post_date) >= 7 else "0000-00")
        kind = meta.get("post_type") or ""
        groups.setdefault((month_key, kind), []).append((post_date, plat, conv, meta))

    changed = False
    for (month_key, kind), members in groups.items():
        try:
            month_num = int(month_key[5:7])
        except Exception:
            month_num = 0
        month = _MONTH_NAMES[month_num] if 0 < month_num < 13 else "Tanpa Tanggal"
        for idx, (_date, plat, conv, _meta) in enumerate(members):
            old = get_post_label(conn, plat, conv)
            src = sdb.get_meta(conn, _pl_source_key(plat, conv), "") or ""
            if old and src != "auto":
                continue
            suffix = chr(65 + idx) if idx < 26 else str(idx + 1)
            if kind:
                label = "%s %s" % (month, kind)
                if len(members) > 1:
                    label += " " + suffix
            else:
                label = "%s %s" % (month, suffix)
            if old != label or src != "auto":
                conn.execute(
                    "INSERT INTO sosmed_meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (_pl_key(plat, conv), label))
                conn.execute(
                    "INSERT INTO sosmed_meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (_pl_source_key(plat, conv), "auto"))
                changed = True
    if changed:
        conn.commit()


'''
replace_once("sosmed/monitor.py", marker, metadata_code + marker)
replace_once(
    "sosmed/monitor.py",
    '''    ensure_review_columns(conn)
    off = _off_set()
    labels = _all_post_labels(conn)
    norm_plat = sdb._norm_platform(platform) if platform else ""
    rng = (range_ or "all").lower()
''',
    '''    ensure_review_columns(conn)
    ensure_auto_post_labels(conn)
    off = _off_set()
    labels = _all_post_labels(conn)
    norm_plat = sdb._norm_platform(platform) if platform else ""
    rng = (range_ or "all").lower()
''')
replace_once(
    "sosmed/monitor.py",
    '''        times.sort()
        out.append({
            "platform": plat,
''',
    '''        times.sort()
        meta = _post_metadata(rows, plat, conv)
        out.append({
            "platform": plat,
''')
replace_once(
    "sosmed/monitor.py",
    '''            "post_label": labels.get(_pl_key(plat, conv), ""),
            "n_items": len(rows),
''',
    '''            "post_label": labels.get(_pl_key(plat, conv), ""),
            "post_label_source": sdb.get_meta(conn, _pl_source_key(plat, conv), "") or "manual",
            "post_date": meta.get("post_date") or "",
            "post_date_source": meta.get("post_date_source") or "",
            "post_type": meta.get("post_type") or "",
            "n_items": len(rows),
''')
insert_before = '''def monitor_thread(conn, item_id):
'''
export_code = '''def monitor_post_export(conn, platform, conversation_id):
    """Semua baris dan semua kolom fisik sosmed_items untuk satu postingan."""
    ensure_review_columns(conn)
    plat = sdb._norm_platform(platform) if platform else ""
    columns = [r[1] for r in conn.execute(
        'PRAGMA table_info("sosmed_items")').fetchall()]
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM sosmed_items WHERE platform=? AND conversation_id=? "
        "ORDER BY datetime(created_at) ASC, id ASC", (plat, conversation_id)).fetchall()]
    if not rows:
        return {"ok": False, "error": "Postingan tidak ditemukan."}
    meta = _post_metadata(rows, plat, conversation_id)
    return {"ok": True, "platform": plat, "conversation_id": conversation_id,
            "post_label": get_post_label(conn, plat, conversation_id),
            "post_date": meta.get("post_date") or "",
            "post_type": meta.get("post_type") or "",
            "columns": columns, "items": rows, "total": len(rows)}


'''
replace_once("sosmed/monitor.py", insert_before, export_code + insert_before)

# ---- route ekspor terisolasi ----
route_marker = '''# ---------------------------------------------------------------------------
# SLA & Analitik (gabungan Coverage & SLA + Analitik)
# ---------------------------------------------------------------------------
'''
route_code = '''async def api_monitor_post_export(request: Request):
    """Data ekspor satu postingan: seluruh baris dan kolom fisik database."""
    platform = _qp(request, "platform")
    conv = _qp(request, "conversation_id") or _qp(request, "conv")
    if not conv:
        return JSONResponse({"ok": False, "error": "conversation_id wajib."}, status_code=400)

    def _do():
        c = _conn()
        try:
            return smon.monitor_post_export(c, platform, conv)
        finally:
            c.close()
    result = await run_in_threadpool(_do)
    return JSONResponse(result, status_code=200 if result.get("ok") else 404)


'''
replace_once("sosmed/routes.py", route_marker, route_code + route_marker)
replace_once(
    "sosmed/routes.py",
    '''    app.add_api_route("/api/sosmed/monitor-post", api_monitor_post, methods=["GET"])
''',
    '''    app.add_api_route("/api/sosmed/monitor-post", api_monitor_post, methods=["GET"])
    app.add_api_route("/api/sosmed/monitor-post-export", api_monitor_post_export, methods=["GET"])
''')

# ---- collector: simpan tanggal dan jenis postingan ke raw_json item ----
replace_once(
    "sosmed/ig_collector.py",
    '''            out[str(pk).split("_")[0]] = {"code": str(code),
                                          "taken_at": obj.get("taken_at") or 0,
                                          "owner": _oh}
''',
    '''            out[str(pk).split("_")[0]] = {"code": str(code),
                                          "taken_at": obj.get("taken_at") or 0,
                                          "owner": _oh,
                                          "media_type": obj.get("media_type"),
                                          "product_type": obj.get("product_type") or "",
                                          "has_carousel": bool(obj.get("carousel_media"))}
''')
replace_once(
    "sosmed/ig_collector.py",
    '''    items = list(by_id.values())
    dumped = False
''',
    '''    items = list(by_id.values())
    _meta_by_code = {d.get("code"): d for d in media_codes.values() if d.get("code")}
    for _it in items:
        _md = _meta_by_code.get(_it.get("conversation_id")) or {}
        if _md.get("taken_at"):
            _it["post_created_at"] = _epoch_to_iso(_md.get("taken_at"))
        _mt = str(_md.get("media_type") or "").lower()
        _pt = str(_md.get("product_type") or "").lower()
        if _mt == "2" or "clip" in _pt or "video" in _pt:
            _it["post_type"] = "Video"
        elif _mt in ("1", "8") or _md.get("has_carousel"):
            _it["post_type"] = "Carousel"
    dumped = False
''')
replace_once(
    "sosmed/tiktok_collector.py",
    '''    # Parsing hasil akhir
    items = list(by_id.values())
    for it in items:
        # Jika API komentar tidak membawa permalink, bentuk manual
        if it.get("conversation_id") and not it.get("permalink"):
            it["permalink"] = "https://www.tiktok.com/@%s/video/%s" % (tgt, it["conversation_id"])
''',
    '''    # Parsing hasil akhir + metadata posting untuk Pengawasan SPV.
    items = list(by_id.values())
    _url_by_id = {u.rstrip("/").split("/")[-1].split("?")[0]: u for u in valid_links}
    for it in items:
        _conv = it.get("conversation_id") or ""
        _post_url = _url_by_id.get(_conv) or ""
        if "/photo/" in _post_url:
            it["post_type"] = "Carousel"
        elif "/video/" in _post_url:
            it["post_type"] = "Video"
        _aw = awemes.get(_conv) or {}
        if _aw.get("create_time"):
            it["post_created_at"] = _epoch_to_iso(_aw.get("create_time"))
        # Jika API komentar tidak membawa permalink, bentuk manual.
        if _conv and not it.get("permalink"):
            it["permalink"] = _post_url or "https://www.tiktok.com/@%s/video/%s" % (tgt, _conv)
''')

# ---- UI Pengawasan SPV ----
replace_once(
    "templates/sosmed_monitor.html",
    '''.pname { max-width: 360px; }
''',
    '''.pname { max-width: 360px; }
.pactions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.pmeta { display:flex; gap:8px 16px; flex-wrap:wrap; margin-top:8px; font-size:11.5px; color:var(--text-muted); }
.pmeta strong { color:var(--text-main); }
''')
replace_once(
    "templates/sosmed_monitor.html",
    '''    if(VIEW==='postingan'){
      $('#tblWrap').style.display='none';$('#postWrap').style.display='';loadPosts();
''',
    '''    $('#btnExport').innerHTML=VIEW==='postingan'?'📚 Export semua postingan':'📥 Export XLS';
    if(VIEW==='postingan'){
      $('#tblWrap').style.display='none';$('#postWrap').style.display='';loadPosts();
''')
replace_once(
    "templates/sosmed_monitor.html",
    '''          if(ps)ps.onclick=function(){savePostLabel(g);};
          g.querySelectorAll('tr[data-id]').forEach(bindRow);
''',
    '''          if(ps)ps.onclick=function(){savePostLabel(g);};
          var pe=card.querySelector('.pexport');
          if(pe)pe.onclick=function(){exportOnePost(g.dataset.plat,g.dataset.conv,card.dataset.label||g.dataset.conv);};
          g.querySelectorAll('tr[data-id]').forEach(bindRow);
''')
start = '''// ---- EXPORT TO EXCEL (.xlsx) ----
  $('#btnExport').onclick = function() {
'''
end = '''  
  // ---- HTML GENERATORS ----
'''
text = Path("templates/sosmed_monitor.html").read_text(encoding="utf-8")
a = text.index(start)
b = text.index(end, a)
new_export = r'''// ---- EXPORT TO EXCEL (.xlsx) ----
  function cleanName(s){return String(s||'postingan').replace(/[\\/:*?"<>|\[\]]/g,'-').slice(0,80);}
  function exportUrl(platform,conv){
    var p=new URLSearchParams();p.set('platform',platform||'');p.set('conversation_id',conv||'');
    return '/api/sosmed/monitor-post-export?'+p.toString();
  }
  function sheetFromExport(d){
    var rows=d.items||[], columns=d.columns||[];
    var ws=XLSX.utils.json_to_sheet(rows,{header:columns});
    ws['!cols']=columns.map(function(c){return {wch:c==='text'||c==='raw_json'?55:Math.max(12,Math.min(24,c.length+3))};});
    return ws;
  }
  function exportOnePost(platform,conv,label){
    toast('Menyiapkan ekspor postingan…');
    jget(exportUrl(platform,conv)).then(function(d){
      if(!d||!d.ok){toast((d&&d.error)||'Gagal mengekspor postingan');return;}
      var wb=XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb,sheetFromExport(d),'Semua Kolom');
      XLSX.writeFile(wb,'Sosmed_'+cleanName(label||d.post_label||conv)+'_'+new Date().toISOString().slice(0,10)+'.xlsx');
      toast('Ekspor postingan selesai');
    }).catch(function(){toast('Gagal mengekspor postingan');});
  }
  function exportAllPosts(){
    toast('Menyiapkan workbook semua postingan…');
    Promise.all(currentData.map(function(p){return jget(exportUrl(p.platform,p.conversation_id));})).then(function(all){
      var wb=XLSX.utils.book_new(),used={};
      all.forEach(function(d,i){
        if(!d||!d.ok)return;
        var base=cleanName(d.post_label||('Postingan '+(i+1))).slice(0,27)||('Post '+(i+1));
        var name=base,n=2;while(used[name]){name=(base.slice(0,27)+' '+n++).slice(0,31);}used[name]=true;
        XLSX.utils.book_append_sheet(wb,sheetFromExport(d),name);
      });
      if(!wb.SheetNames.length){toast('Tidak ada data untuk diekspor');return;}
      XLSX.writeFile(wb,'Sosmed_Semua_Postingan_'+new Date().toISOString().slice(0,10)+'.xlsx');
      toast('Ekspor semua postingan selesai');
    }).catch(function(){toast('Gagal mengekspor semua postingan');});
  }
  $('#btnExport').onclick = function() {
    if (!currentData || !currentData.length) {toast('Tidak ada data untuk diexport');return;}
    if(VIEW==='postingan'){exportAllPosts();return;}
    var ws_data=[["Platform","Akun Nanya","Waktu","Isi Komentar","Jenis","Status","Tgl Dijawab","SLA","Tiket CRM"]];
    currentData.forEach(function(it){
      var st=it.eff_status||'belum';
      ws_data.push([it.platform||'',"@"+(it.author_handle||''),it.created_at||'',it.text||'',
        Number(it.is_nimbrung||0)===1?'Nimbrung':'Utama',
        st==='ya'?'Sudah Dijawab':(st==='itd'?'ITD':'Belum Dijawab'),
        it.eff_answered_at||'',gap(it.eff_gap_s),it.crm_url||'']);
    });
    var wb=XLSX.utils.book_new(),ws=XLSX.utils.aoa_to_sheet(ws_data);
    ws['!cols']=[{wch:10},{wch:18},{wch:20},{wch:55},{wch:14},{wch:18},{wch:20},{wch:12},{wch:28}];
    XLSX.utils.book_append_sheet(wb,ws,'Data Sosmed');
    XLSX.writeFile(wb,'Report_Sosmed_interaksi_'+new Date().toISOString().slice(0,10)+'.xlsx');
  };
'''
Path("templates/sosmed_monitor.html").write_text(text[:a] + new_export + end + text[b+len(end):], encoding="utf-8")
replace_once(
    "templates/sosmed_monitor.html",
    '''        '<div class="ph"><span class="badge '+pc(p.platform)+'">'+esc(p.platform)+'</span>'+(p.post_label?'<span class="pttl">'+esc(p.post_label)+'</span>':'')+link+'<span class="muted mono" style="margin-left:auto;">'+esc(p.last_at||'')+'</span></div>'+\n        '<div class="pname-row"><input class="cell-in pname" placeholder="Beri nama postingan (mis. P1 Lupa Kata Sandi)" value="'+esc(p.post_label||'')+'"><button class="btn sm psave">Simpan nama</button></div>'+\n''',
    '''        '<div class="ph"><span class="badge '+pc(p.platform)+'">'+esc(p.platform)+'</span>'+(p.post_label?'<span class="pttl">'+esc(p.post_label)+'</span>':'')+link+'</div>'+\n        '<div class="pmeta"><span>Tanggal posting: <strong class="mono">'+esc(p.post_date||'—')+'</strong>'+(p.post_date_source==='komentar_pertama'?' <em>(fallback komentar pertama)</em>':'')+'</span><span>Jenis: <strong>'+esc(p.post_type||'Belum terdeteksi')+'</strong></span><span>Tarikan terakhir: <strong class="mono">'+esc(p.last_at||'—')+'</strong></span></div>'+\n        '<div class="pname-row"><input class="cell-in pname" placeholder="Ubah label otomatis bila perlu" value="'+esc(p.post_label||'')+'"><div class="pactions"><button class="btn sm psave">Simpan label</button><button class="btn sm ghost pexport">📥 Export postingan</button></div></div>'+\n''')
replace_once(
    "templates/sosmed_monitor.html",
    '''      '<div class="pcard">'+
''',
    '''      '<div class="pcard" data-label="'+esc(p.post_label||p.conversation_id)+'">'+
''')
replace_once("templates/sosmed_monitor.html", "btn.textContent='Simpan nama';", "btn.textContent='Simpan label';")
replace_once("templates/sosmed_monitor.html", "toast('Nama postingan tersimpan')", "toast('Label postingan tersimpan')")

# ---- focused tests ----
Path("tests/test_sosmed_monitor.py").write_text(r'''import os
import tempfile
import unittest

import sosmed.db as sdb
import sosmed.monitor as smon


class SosmedMonitorPostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sdb.init_db(sdb.connect(os.path.join(self.tmp.name, "sosmed.db")))
        items = [
            {"platform":"ig","id":"i1","conversation_id":"post-video","author_handle":"a",
             "created_at":"2026-01-12T10:00:00Z","text":"tanya",
             "post_created_at":"2026-01-10T08:00:00Z","post_type":"Video"},
            {"platform":"ig","id":"i2","conversation_id":"post-carousel","author_handle":"b",
             "created_at":"2026-01-20T10:00:00Z","text":"tanya dua",
             "post_created_at":"2026-01-18T08:00:00Z","post_type":"Carousel"},
            {"platform":"x","id":"x1","conversation_id":"unknown-a","author_handle":"c",
             "created_at":"2026-02-01T10:00:00Z","text":"x satu"},
            {"platform":"x","id":"x2","conversation_id":"unknown-b","author_handle":"d",
             "created_at":"2026-02-03T10:00:00Z","text":"x dua"},
        ]
        sdb.ingest_items(self.conn, items, source="test")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_auto_labels_and_post_date(self):
        result = smon.monitor_posts(self.conn)
        posts = {p["conversation_id"]: p for p in result["posts"]}
        self.assertEqual(posts["post-video"]["post_label"], "Januari Video")
        self.assertEqual(posts["post-carousel"]["post_label"], "Januari Carousel")
        self.assertEqual(posts["post-video"]["post_date"][:10], "2026-01-10")
        self.assertEqual(posts["unknown-a"]["post_label"], "Februari A")
        self.assertEqual(posts["unknown-b"]["post_label"], "Februari B")
        self.assertEqual(posts["unknown-a"]["post_date_source"], "komentar_pertama")

    def test_manual_label_is_not_overwritten(self):
        smon.set_post_label(self.conn, "ig", "post-video", "Kampanye EFIN")
        smon.ensure_auto_post_labels(self.conn)
        self.assertEqual(smon.get_post_label(self.conn, "ig", "post-video"), "Kampanye EFIN")

    def test_export_returns_every_database_column(self):
        out = smon.monitor_post_export(self.conn, "ig", "post-video")
        expected = [r[1] for r in self.conn.execute('PRAGMA table_info("sosmed_items")')]
        self.assertTrue(out["ok"])
        self.assertEqual(out["columns"], expected)
        self.assertEqual(set(out["items"][0]), set(expected))
        self.assertIn("raw_json", out["columns"])
        self.assertIn("spv_note", out["columns"])


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print("SOSMED_MONITOR_UPGRADE_OK")
