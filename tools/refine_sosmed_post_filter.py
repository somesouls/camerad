from pathlib import Path


def repl(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit("Pola tidak ditemukan di %s: %r" % (path, old[:100]))
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Backend: filter bulan publikasi postingan, terpisah dari rentang komentar.
repl(
    "sosmed/monitor.py",
    '''def monitor_posts(conn, platform="", range_="all", start="", end="", q="",
                  limit=300):
''',
    '''def monitor_posts(conn, platform="", range_="all", start="", end="", q="",
                  post_month="", limit=300):
''')
repl(
    "sosmed/monitor.py",
    '''    convs = _candidate_convs(conn, norm_plat, s, e, q)
    out = []
''',
    '''    convs = _candidate_convs(conn, norm_plat, s, e, q)
    post_month = (post_month or "").strip()
    if not _re.match(r"^\\d{4}-(0[1-9]|1[0-2])$", post_month):
        post_month = ""
    out = []
''')
repl(
    "sosmed/monitor.py",
    '''        if not rows:
            continue
        role, main_of, officials_of = _classify_conv(rows, off)
''',
    '''        if not rows:
            continue
        meta = _post_metadata(rows, plat, conv)
        if post_month and (meta.get("post_date") or "")[:7] != post_month:
            continue
        role, main_of, officials_of = _classify_conv(rows, off)
''')
repl(
    "sosmed/monitor.py",
    '''        times.sort()
        meta = _post_metadata(rows, plat, conv)
        out.append({
''',
    '''        times.sort()
        out.append({
''')

# Route: teruskan parameter bulan khusus ke monitor_posts.
repl(
    "sosmed/routes.py",
    '''                start=_qp(request, "start"), end=_qp(request, "end"),
                q=_qp(request, "q"), limit=limit)
''',
    '''                start=_qp(request, "start"), end=_qp(request, "end"),
                q=_qp(request, "q"), post_month=_qp(request, "post_month"),
                limit=limit)
''')

# IG: URL asli hanya fallback; metadata API tetap sumber utama.
repl(
    "sosmed/ig_collector.py",
    '''def _is_ig_comment(n):
''',
    '''def _post_type_hint(value):
    """Petunjuk jenis dari URL input IG; /p/ tanpa query tetap ambigu."""
    value = str(value or "").strip().lower()
    if re.search(r"/(?:reel|reels|tv)/", value):
        return "Video"
    if "img_index=" in value or "image_index=" in value:
        return "Carousel"
    return ""


def _is_ig_comment(n):
''')
repl(
    "sosmed/ig_collector.py",
    '''        _only = []
        for _c in (only_codes or []):
            _cc = _post_code(_c)
            if _cc and _cc not in _only:
                _only.append(_cc)
''',
    '''        _only = []
        _input_type_hints = {}
        for _c in (only_codes or []):
            _cc = _post_code(_c)
            if _cc and _cc not in _only:
                _only.append(_cc)
            if _cc and _post_type_hint(_c):
                _input_type_hints[_cc] = _post_type_hint(_c)
''')
repl(
    "sosmed/ig_collector.py",
    '''        if _mt == "2" or "clip" in _pt or "video" in _pt:
            _it["post_type"] = "Video"
        elif _mt in ("1", "8") or _md.get("has_carousel"):
            _it["post_type"] = "Carousel"
''',
    '''        if _mt == "2" or "clip" in _pt or "video" in _pt:
            _it["post_type"] = "Video"
        elif _mt in ("1", "8") or _md.get("has_carousel"):
            _it["post_type"] = "Carousel"
        elif _input_type_hints.get(_it.get("conversation_id")):
            _it["post_type"] = _input_type_hints[_it.get("conversation_id")]
''')

# UI: pemilih month+year di samping Platform, hanya aktif pada tab postingan.
repl(
    "templates/sosmed_monitor.html",
    '''    <div class="fld"><label>Platform</label><select id="fPlatform"><option value="">Semua</option><option value="x">X</option><option value="ig">Instagram</option><option value="tiktok">TikTok</option></select></div>
    <div class="fld" id="fAnsWrap"><label>Status jawab</label><select id="fAns"><option value="">Semua</option><option value="belum">Belum dijawab</option><option value="ya">Sudah dijawab</option><option value="itd">ITD</option></select></div>
''',
    '''    <div class="fld"><label>Platform</label><select id="fPlatform"><option value="">Semua</option><option value="x">X</option><option value="ig">Instagram</option><option value="tiktok">TikTok</option></select></div>
    <div class="fld" id="fPostMonthWrap" style="display:none"><label>Bulan posting</label><input type="month" id="fPostMonth" aria-label="Bulan dan tahun postingan"></div>
    <div class="fld" id="fAnsWrap"><label>Status jawab</label><select id="fAns"><option value="">Semua</option><option value="belum">Belum dijawab</option><option value="ya">Sudah dijawab</option><option value="itd">ITD</option></select></div>
''')
repl(
    "templates/sosmed_monitor.html",
    '''  $('#fNimbrung').addEventListener('change',refresh);
  $('#fQ').addEventListener('keydown',function(e){if(e.key==='Enter')refresh();});
''',
    '''  $('#fNimbrung').addEventListener('change',refresh);
  $('#fPostMonth').addEventListener('change',function(){if(VIEW==='postingan')refresh();});
  $('#fQ').addEventListener('keydown',function(e){if(e.key==='Enter')refresh();});
''')
repl(
    "templates/sosmed_monitor.html",
    '''      $('#fPlatform').value='';
      $('#fAns').value='';
''',
    '''      $('#fPlatform').value='';
      $('#fPostMonth').value='';
      $('#fAns').value='';
''')
repl(
    "templates/sosmed_monitor.html",
    '''    if($('#fPlatform').value)p.set('platform',$('#fPlatform').value);
    if($('#fAns').value)p.set('answered',$('#fAns').value);
''',
    '''    if($('#fPlatform').value)p.set('platform',$('#fPlatform').value);
    if(VIEW==='postingan'&&$('#fPostMonth').value)p.set('post_month',$('#fPostMonth').value);
    if($('#fAns').value)p.set('answered',$('#fAns').value);
''')
repl(
    "templates/sosmed_monitor.html",
    '''  function refresh(){
    $('#btnExport').innerHTML=VIEW==='postingan'?'📚 Export semua postingan':'📥 Export XLS';
''',
    '''  function refresh(){
    $('#btnExport').innerHTML=VIEW==='postingan'?'📚 Export semua postingan':'📥 Export XLS';
    $('#fPostMonthWrap').style.display=VIEW==='postingan'?'':'none';
''')

# Tests: URL hints, month+year filter, and UI contract.
p = Path("tests/test_sosmed_monitor.py")
s = p.read_text(encoding="utf-8")
s = s.replace("import sosmed.monitor as smon\n", "import sosmed.monitor as smon\nimport sosmed.ig_collector as igc\n")
needle = '''    def test_manual_label_is_not_overwritten(self):
'''
addition = '''    def test_post_month_filter_uses_publication_month(self):
        jan = smon.monitor_posts(self.conn, post_month="2026-01")
        self.assertEqual({p["conversation_id"] for p in jan["posts"]},
                         {"post-video", "post-carousel"})
        feb = smon.monitor_posts(self.conn, post_month="2026-02")
        self.assertEqual({p["conversation_id"] for p in feb["posts"]},
                         {"unknown-a", "unknown-b"})
        invalid = smon.monitor_posts(self.conn, post_month="2026-99")
        self.assertEqual(len(invalid["posts"]), 4)

    def test_instagram_original_url_hints(self):
        self.assertEqual(igc._post_type_hint(
            "https://www.instagram.com/reels/Dc-kZ1DJHpH/"), "Video")
        self.assertEqual(igc._post_type_hint(
            "https://www.instagram.com/p/DdiTp8gicmM/?img_index=1"), "Carousel")
        self.assertEqual(igc._post_type_hint(
            "https://www.instagram.com/p/Dc-kZ1DJHpH/"), "")

    def test_month_filter_ui_contract(self):
        html = Path("templates/sosmed_monitor.html").read_text(encoding="utf-8")
        self.assertIn('type="month" id="fPostMonth"', html)
        self.assertIn("p.set('post_month',$('#fPostMonth').value)", html)

'''
if needle not in s:
    raise SystemExit("test insertion point missing")
s = s.replace(needle, addition + needle, 1)
s = s.replace("import tempfile\n", "import tempfile\nfrom pathlib import Path\n")
p.write_text(s, encoding="utf-8")

print("SOSMED_POST_MONTH_REFINEMENT_OK")
