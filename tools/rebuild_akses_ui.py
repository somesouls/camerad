from pathlib import Path

p = Path(__file__).resolve().parents[1] / 'templates' / 'akses.html'
s = p.read_text(encoding='utf-8')

style_start = s.index('{% block head %}<style>') + len('{% block head %}<style>')
style_end = s.index('</style>{% endblock %}', style_start)
new_css = r'''
/* Access workspace: calm hierarchy, clear actions, responsive editing. */
.access-page { max-width:1240px; margin:0 auto; padding:30px 24px 72px; }
.access-hero { display:flex; align-items:flex-end; justify-content:space-between; gap:24px; margin-bottom:24px; }
.access-eyebrow { margin:0 0 8px; color:var(--accent); font-size:11px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.access-hero h1 { margin:0; font-size:30px; line-height:1.15; letter-spacing:-.03em; }
.access-hero p { max-width:700px; margin:10px 0 0; color:var(--text-muted); font-size:14px; line-height:1.6; }
.access-actions { display:flex; align-items:center; gap:8px; flex:none; }
.access-actions a { display:inline-flex; align-items:center; gap:7px; min-height:38px; padding:0 12px; border:1px solid var(--panel-border); border-radius:10px; color:var(--text-muted); text-decoration:none; font-size:12px; font-weight:600; background:var(--panel-bg); }
.access-actions a:hover { color:var(--text-main); border-color:var(--accent); background:var(--accent-glow); }
.access-tabs { display:flex; gap:6px; padding:5px; margin-bottom:20px; border:1px solid var(--panel-border); border-radius:14px; background:rgba(15,23,42,.45); max-width:620px; }
.access-tab { flex:1; min-height:48px; padding:7px 14px; border:0; border-radius:10px; background:transparent; color:var(--text-muted); text-align:left; cursor:pointer; font:inherit; transition:.2s; }
.access-tab:hover { color:var(--text-main); background:var(--panel-border); }
.access-tab.is-active { color:var(--text-main); background:var(--accent-glow); box-shadow:0 3px 12px rgba(0,0,0,.12); }
.access-tab strong { display:block; font-size:13px; }
.access-tab span { display:block; margin-top:2px; font-size:11px; color:inherit; opacity:.72; }
.access-panel { display:none; animation:accessFade .22s ease-out; }
.access-panel.is-active { display:block; }
@keyframes accessFade { from{opacity:0; transform:translateY(4px)} to{opacity:1; transform:none} }
.access-callout { display:flex; gap:12px; align-items:flex-start; padding:14px 16px; margin-bottom:18px; border:1px solid var(--accent-glow); border-radius:12px; background:var(--c-blue); color:var(--text-main); }
.access-callout .callout-icon { width:28px; height:28px; display:flex; align-items:center; justify-content:center; flex:none; border-radius:9px; background:var(--accent-glow); color:var(--accent); }
.access-callout strong { display:block; margin-bottom:2px; font-size:13px; }
.access-callout p { margin:0; color:var(--text-muted); font-size:12px; line-height:1.55; }
.panel-card { min-width:0; border:1px solid var(--panel-border); border-radius:16px; background:var(--panel-bg); box-shadow:var(--shadow-sm); overflow:hidden; }
.panel-card > .panel-head { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; padding:18px 20px 14px; border-bottom:1px solid var(--panel-border); }
.panel-head h2, .panel-head h3 { margin:0; color:var(--text-main); font-size:15px; }
.panel-head p { margin:4px 0 0; color:var(--text-muted); font-size:12px; line-height:1.5; }
.panel-head .panel-kicker { margin-bottom:5px; color:var(--accent); font-size:10px; font-weight:800; letter-spacing:.09em; text-transform:uppercase; }
.panel-body { padding:18px 20px; }
.role-layout { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(360px,.9fr); gap:18px; align-items:start; }
.role-list-card { min-height:330px; }
.role-editor-card { position:sticky; top:18px; }
.table-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:13px 20px; border-bottom:1px solid var(--panel-border); color:var(--text-muted); font-size:12px; }
.table-toolbar strong { color:var(--text-main); font-size:13px; }
.table-scroll { overflow-x:auto; }
.access-page table { width:100%; border-collapse:collapse; }
.access-page th, .access-page td { padding:12px 14px; text-align:left; font-size:12px; border-bottom:1px solid var(--panel-border); color:var(--text-main); vertical-align:middle; }
.access-page th { color:var(--text-muted); font-size:10px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; background:rgba(2,6,23,.14); white-space:nowrap; }
.access-page tbody tr { transition:.16s; }
.access-page tbody tr:hover { background:var(--accent-glow); }
.access-page tbody tr:last-child td { border-bottom:0; }
.access-page td:first-child { min-width:150px; }
.role-key { margin-top:2px; color:var(--text-muted); font:11px ui-monospace,SFMono-Regular,Menlo,monospace; }
.badge { display:inline-flex; align-items:center; padding:3px 7px; border-radius:999px; font-size:10px; font-weight:700; background:var(--panel-border); color:var(--text-muted); }
.sys { background:var(--c-orange); color:var(--c-orange-txt); }
.pill { display:inline-block; padding:3px 7px; margin:2px 3px 2px 0; border-radius:6px; font-size:10px; background:var(--bg-base); border:1px solid var(--panel-border); color:var(--text-muted); }
.access-page .desc { color:var(--text-muted); font-size:11px; }
.btn { display:inline-flex; align-items:center; justify-content:center; gap:6px; min-height:32px; padding:0 10px; border:1px solid var(--panel-border); border-radius:8px; background:var(--bg-base); color:var(--text-main); font-size:12px; font-weight:600; cursor:pointer; }
.btn:hover { border-color:var(--accent); color:var(--accent); background:var(--accent-glow); }
.btn.danger:hover { border-color:#ef4444; color:#f87171; background:rgba(239,68,68,.1); }
.btn.subtle { color:var(--text-muted); background:transparent; }
.primary { display:inline-flex; align-items:center; justify-content:center; min-height:38px; padding:0 15px; border:0; border-radius:9px; background:var(--accent); color:#fff; font-size:12px; font-weight:700; cursor:pointer; box-shadow:0 5px 14px var(--accent-glow); }
.primary:hover { background:var(--accent-hover); transform:translateY(-1px); }
.form-grid { display:grid; grid-template-columns:1.2fr 1.2fr .55fr; gap:12px; }
.fld { min-width:0; }
.access-page label { display:block; margin:0 0 6px; color:var(--text-muted); font-size:11px; font-weight:700; }
.access-page input, .access-page select { width:100%; min-height:38px; padding:8px 10px; border:1px solid var(--panel-border); border-radius:9px; background:var(--bg-base); color:var(--text-main); font:inherit; font-size:12px; outline:none; }
.access-page input:focus, .access-page select:focus { border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-glow); }
.editor-title { display:flex; align-items:center; gap:9px; margin-bottom:16px; }
.editor-dot { width:9px; height:9px; border-radius:50%; background:var(--accent); box-shadow:0 0 0 4px var(--accent-glow); }
.editor-title h3 { margin:0; font-size:15px; }
.editor-note { padding:10px 12px; margin:16px 0 14px; border-radius:10px; background:rgba(148,163,184,.08); color:var(--text-muted); font-size:11px; line-height:1.55; }
.editor-section-label { margin:18px 0 8px !important; color:var(--text-main) !important; font-size:11px !important; }
.caps { display:flex; flex-wrap:wrap; gap:7px; padding:10px; border:1px solid var(--panel-border); border-radius:10px; background:rgba(2,6,23,.12); }
.caps .chk { min-width:auto; margin:0; padding:6px 9px; border:1px solid var(--panel-border); border-radius:8px; background:var(--bg-base); }
.chk { display:flex; align-items:center; gap:8px; padding:7px 0; color:var(--text-main); font-size:12px; }
.chk input { width:auto; min-height:0; accent-color:var(--accent); }
.grp { margin-top:9px; overflow:hidden; border:1px solid var(--panel-border); border-radius:10px; }
.grp > summary { display:flex; align-items:center; justify-content:space-between; cursor:pointer; padding:10px 12px; background:rgba(2,6,23,.15); color:var(--text-main); font-size:12px; font-weight:700; list-style:none; }
.grp > summary::-webkit-details-marker { display:none; }
.grp > summary::after { content:'+'; color:var(--text-muted); font-size:16px; font-weight:400; }
.grp[open] > summary::after { content:'−'; }
.grp .grp-body { padding:8px 12px 10px; }
.editor-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-top:18px; }
.msg { display:none; margin-top:12px; padding:9px 11px; border-radius:9px; font-size:12px; }
.msg.ok { display:block; background:var(--c-green); color:var(--c-green-txt); }
.msg.no { display:block; background:var(--c-red); color:var(--c-red-txt); }
.advanced-card { margin-top:18px; }
.advanced-card > summary { display:flex; align-items:center; gap:10px; cursor:pointer; padding:16px 20px; color:var(--text-main); font-size:13px; font-weight:700; list-style:none; }
.advanced-card > summary::-webkit-details-marker { display:none; }
.advanced-card > summary::before { content:'⚙'; width:26px; height:26px; display:flex; align-items:center; justify-content:center; border-radius:8px; background:var(--c-orange); color:var(--c-orange-txt); }
.advanced-card > summary::after { content:'+'; margin-left:auto; color:var(--text-muted); font-size:18px; font-weight:400; }
.advanced-card[open] > summary::after { content:'−'; }
.advanced-body { padding:0 20px 20px; border-top:1px solid var(--panel-border); }
.menu-layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:18px; align-items:start; }
.menu-card .panel-body { min-height:420px; }
.choice-row { display:flex; align-items:flex-end; gap:10px; }
.choice-row .fld { flex:1; }
.choice-row .btn { flex:none; }
.menu-toolbar { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:14px 0 4px; }
.menu-toolbar .spacer { flex:1; }
.state { min-height:18px; margin-top:8px; color:var(--text-muted); font-size:11px; line-height:1.5; }
.state.is-configured { color:var(--c-green-txt); }
.menu-scroll { max-height:620px; overflow:auto; padding-right:3px; }
.menu-scroll::-webkit-scrollbar { width:5px; }
.menu-scroll::-webkit-scrollbar-thumb { background:var(--panel-border); border-radius:5px; }
.menu-card .editor-actions { position:sticky; bottom:0; padding-top:12px; background:linear-gradient(transparent,var(--panel-bg) 18%); }
.helper-list { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:18px; }
.helper-item { padding:12px; border:1px solid var(--panel-border); border-radius:10px; background:rgba(2,6,23,.1); }
.helper-item b { display:block; margin-bottom:3px; font-size:12px; }
.helper-item span { color:var(--text-muted); font-size:11px; line-height:1.45; }
.access-page .hint { margin:5px 0 0; color:var(--text-muted); text-align:left; font-size:11px; line-height:1.5; }
@media (max-width:1000px){ .role-layout,.menu-layout{ grid-template-columns:1fr; } .role-editor-card{ position:static; } .menu-card .menu-scroll{ max-height:none; } }
@media (max-width:700px){ .access-page{ padding:22px 14px 50px; } .access-hero{ display:block; } .access-actions{ margin-top:16px; } .access-tabs{ max-width:none; } .access-tab{ padding:7px 9px; } .access-tab span{ display:none; } .form-grid{ grid-template-columns:1fr 1fr; } .form-grid .fld:last-child{ grid-column:span 2; } .helper-list{ grid-template-columns:1fr; } .panel-card > .panel-head,.panel-body{ padding-left:14px; padding-right:14px; } .advanced-body{ padding-left:14px; padding-right:14px; } }
@media (max-width:460px){ .access-hero h1{ font-size:24px; } .form-grid{ grid-template-columns:1fr; } .form-grid .fld:last-child{ grid-column:auto; } .access-tabs{ display:grid; grid-template-columns:1fr 1fr; } .choice-row{ display:block; } .choice-row .btn{ margin-top:8px; } }
'''
s = s[:style_start] + new_css + s[style_end:]

content_start = s.index('{% block content %}') + len('{% block content %}')
scripts_start = s.index('{% block scripts %}')
new_content = r'''
<div class="wrap access-page">
  <header class="access-hero">
    <div>
      <p class="access-eyebrow">Administrasi &amp; keamanan</p>
      <h1>Kelola akses &amp; peran</h1>
      <p>Atur siapa yang dapat melakukan apa. Mulai dari peran dan kapabilitas, lalu gunakan akses sidebar per-tautan untuk kontrol yang lebih presisi.</p>
    </div>
    <div class="access-actions">
      <a href="/users">👥 Pengguna</a>
      <a href="/">← Kembali</a>
    </div>
  </header>

  <nav class="access-tabs" aria-label="Bagian pengaturan akses" role="tablist">
    <button class="access-tab is-active" type="button" role="tab" aria-selected="true" data-access-tab="roles-panel">
      <strong>👤 Peran &amp; pengguna</strong><span>Identitas, kapabilitas, dan override area/API</span>
    </button>
    <button class="access-tab" type="button" role="tab" aria-selected="false" data-access-tab="menus-panel">
      <strong>🔗 Akses sidebar</strong><span>Atur izin per tautan menu</span>
    </button>
  </nav>

  <section id="roles-panel" class="access-panel is-active" role="tabpanel">
    <div class="access-callout">
      <div class="callout-icon">i</div>
      <div><strong>Gunakan dua lapisan secara berurutan</strong><p>Peran memberi dasar akses. Setelah itu, buka tab <b>Akses sidebar</b> bila perlu membatasi menu tertentu tanpa membuat terlalu banyak peran.</p></div>
    </div>

    <div class="role-layout">
      <article class="panel-card role-list-card">
        <div class="panel-head"><div><div class="panel-kicker">Langkah 1</div><h2>Daftar peran</h2><p>Pilih peran untuk mengubah detailnya.</p></div><button class="btn" id="new_role_btn" type="button">＋ Peran baru</button></div>
        <div class="table-toolbar"><span><strong>Peran yang tersedia</strong></span><span id="role_count">Memuat…</span></div>
        <div class="table-scroll"><table><thead><tr><th>Peran</th><th>Level</th><th>Kapabilitas</th><th>Area/API</th><th>Pengguna</th><th></th></tr></thead><tbody id="rtb"></tbody></table></div>
      </article>

      <article class="panel-card role-editor-card">
        <div class="panel-body">
          <div class="editor-title"><span class="editor-dot"></span><h3 id="rtitle">Tambah Peran Baru</h3></div>
          <p class="editor-note">Buat peran berdasarkan tanggung jawab. Pengaturan tautan sidebar dilakukan terpisah di tab berikutnya agar konfigurasi tetap mudah dipahami.</p>
          <input type="hidden" id="orig_key" />
          <div class="form-grid">
            <div class="fld"><label for="rkey">Kunci peran</label><input id="rkey" placeholder="spv_analis" autocomplete="off" /></div>
            <div class="fld"><label for="rlabel">Nama tampilan</label><input id="rlabel" placeholder="SPV Analis" autocomplete="off" /></div>
            <div class="fld"><label for="rlevel">Level jenjang</label><input id="rlevel" type="number" min="0" max="99" value="4" /></div>
          </div>
          <label class="editor-section-label">Kapabilitas aksi backend</label>
          <div class="caps" id="rcaps"></div>
          <label class="editor-section-label">Izin area/API dasar</label>
          <p class="hint">Kontrol coarse untuk endpoint dan aksi backend. Bukan sumber kebenaran tautan sidebar.</p>
          <div id="rareas"></div>
          <div class="editor-actions"><button class="primary" id="rsave" type="button">Simpan peran</button><button class="btn subtle" id="rreset" type="button">Batal / reset</button></div>
          <div class="msg" id="rmsg"></div>
        </div>
      </article>
    </div>

    <details class="panel-card advanced-card">
      <summary>Override area/API per pengguna <span class="badge">Opsional</span></summary>
      <div class="advanced-body">
        <p class="hint">Gunakan hanya jika satu pengguna perlu pengecualian dari izin area/API perannya. Untuk pengecualian tautan sidebar, gunakan tab <b>Akses sidebar</b>.</p>
        <div class="choice-row" style="margin-top:14px;"><div class="fld"><label for="guser">Pilih pengguna</label><select id="guser"></select></div></div>
        <div id="gareas" style="margin-top:12px;"></div>
        <div class="editor-actions"><button class="primary" id="gsave" type="button">Simpan override area/API</button></div>
        <div class="msg" id="gmsg"></div>
      </div>
    </details>
  </section>

  <section id="menus-panel" class="access-panel" role="tabpanel" aria-hidden="true">
    <div class="access-callout">
      <div class="callout-icon">🔗</div>
      <div><strong>Ini adalah sumber kebenaran sidebar</strong><p>Atur default per tautan untuk sebuah peran. Jika satu orang perlu pengecualian, gunakan override pengguna di panel sebelahnya. Pengaturan lama area/API tetap menjadi fallback.</p></div>
    </div>

    <div class="menu-layout">
      <article class="panel-card menu-card">
        <div class="panel-head"><div><div class="panel-kicker">Langkah 2A</div><h2>Akses sidebar per peran</h2><p>Default tautan menu untuk semua pengguna dalam peran tersebut.</p></div></div>
        <div class="panel-body">
          <div class="choice-row"><div class="fld"><label for="mrole">Pilih peran</label><select id="mrole"></select></div></div>
          <div id="mrole_state" class="state"></div>
          <div class="menu-toolbar"><button class="btn" type="button" id="mrole_all">✓ Centang semua</button><button class="btn" type="button" id="mrole_none">Kosongkan</button><span class="spacer"></span><span class="hint">Menu Studio selalu tersedia</span></div>
          <div id="mrole_menus" class="menu-scroll"></div>
          <div class="editor-actions"><button class="primary" id="mrole_save" type="button">Simpan akses peran</button><button class="btn subtle" id="mrole_reset" type="button">Reset ke area/API</button></div>
          <div class="msg" id="mrole_msg"></div>
        </div>
      </article>

      <article class="panel-card menu-card">
        <div class="panel-head"><div><div class="panel-kicker">Langkah 2B</div><h2>Override sidebar per pengguna</h2><p>Gunakan saat satu pengguna berbeda dari default perannya.</p></div></div>
        <div class="panel-body">
          <div class="choice-row"><div class="fld"><label for="muser">Pilih pengguna</label><select id="muser"></select></div></div>
          <div class="state">Per tautan: <b>Ikuti peran</b>, <b>Tampilkan</b>, atau <b>Sembunyikan</b>.</div>
          <div id="muser_menus" class="menu-scroll"></div>
          <div class="editor-actions"><button class="primary" id="muser_save" type="button">Simpan override sidebar</button></div>
          <div class="msg" id="muser_msg"></div>
        </div>
      </article>
    </div>

    <div class="helper-list">
      <div class="helper-item"><b>Peran = default</b><span>Mulai dari aturan umum yang berlaku untuk seluruh anggota peran.</span></div>
      <div class="helper-item"><b>Pengguna = pengecualian</b><span>Tambahkan atau cabut tautan tertentu tanpa membuat peran baru.</span></div>
      <div class="helper-item"><b>Reset = kembali aman</b><span>Hapus konfigurasi granular dan gunakan fallback area/API lama.</span></div>
    </div>
  </section>
</div>
{% endblock %}
'''
s = s[:content_start] + new_content + s[scripts_start:]

ui_script = r'''<script>
(function () {
  const tabs = Array.from(document.querySelectorAll('.access-tab'));
  const panels = Array.from(document.querySelectorAll('.access-panel'));
  function activate(id) {
    tabs.forEach(function (tab) {
      const active = tab.dataset.accessTab === id;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    panels.forEach(function (panel) {
      const active = panel.id === id;
      panel.classList.toggle('is-active', active);
      panel.setAttribute('aria-hidden', active ? 'false' : 'true');
    });
  }
  tabs.forEach(function (tab) { tab.addEventListener('click', function () { activate(tab.dataset.accessTab); }); });
  const newRole = document.getElementById('new_role_btn');
  if (newRole) newRole.addEventListener('click', function () {
    activate('roles-panel');
    if (typeof resetRoleForm === 'function') resetRoleForm();
    setTimeout(function () { const key = document.getElementById('rkey'); if (key) key.focus(); }, 0);
  });
  const count = document.getElementById('role_count');
  const rows = document.getElementById('rtb');
  function syncCount() { if (count && rows) count.textContent = rows.querySelectorAll('tr').length + ' peran'; }
  if (rows && window.MutationObserver) new MutationObserver(syncCount).observe(rows, { childList: true });
  syncCount();
})();
</script>
'''
s = s.replace('{% block scripts %}', '{% block scripts %}\n' + ui_script, 1)
p.write_text(s, encoding='utf-8')
print('access page UX redesigned')
'''