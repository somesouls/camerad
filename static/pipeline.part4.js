// pipeline.part4.js — Sinyal analisis Step 6 (Analisis Manual Fallback).
// Dimuat SETELAH part2/part3; menimpa openModal6 & renderStep6 (fungsi global).
// Menambah bar chip filter sinyal (kolom "Sinyal" di tabel DIHAPUS atas permintaan;
// chip filter tetap ada). Fail-safe bila field sinyal belum ada -> normal.
// [Opsi A] Menambah kolom "Acuan" (audit acuan analis: Ya/Tidak + sumber cocok).
// [STATUS] Menambah kolom "STATUS" (TINDAK LANJUT / kosong) turunan OTOMATIS dari isi
//   kolom Intent Judgement LLM: berisi -> TINDAK LANJUT, dikosongkan -> kosong.
//   Catatan LLM DIBIARKAN UTUH (tidak diubah jadi BATAL). Ditambah penghitung
//   "masuk laporan" (N ditindaklanjuti / M batal-kosong / total) + filter STATUS +
//   opsi "Kosongkan" di menu rekomendasi. Semua murni frontend; logika laporan Step 10
//   sudah memakai kriteria "Intent Judgement LLM berisi", jadi konsisten.

var SIG_DEFS = [
  ['panjang','is_panjang','Panjang'],
  ['majemuk','is_majemuk','Majemuk'],
  ['multitopik','is_multi_topik','Multi-topik'],
  ['istilah','has_istilah','Istilah'],
  ['ambigu','is_ambigu','Ambigu'],
  ['kandidat','ambiguitas_kandidat','Kandidat mirip'],
  ['noperaturan','has_no_peraturan','No. peraturan'],
  ['akronim','akronim','Akronim'],
  ['bedallm','beda_llm','Beda LLM']
];

function sigField(key){ for(var i=0;i<SIG_DEFS.length;i++){ if(SIG_DEFS[i][0]===key) return SIG_DEFS[i]; } return null; }

function sigOn(r,key){
  var d=sigField(key); if(!d) return false;
  var s=r&&r.sinyal; if(!s) return false;
  var v=s[d[1]];
  return Array.isArray(v) ? v.length>0 : !!v;
}

function sigChips(id,keys){
  var h='<div class="sigchips" id="'+id+'">';
  keys.forEach(function(k){ var d=sigField(k); if(d) h+='<label class="sigchip"><input type="checkbox" data-sig="'+k+'"> '+d[2]+'</label>'; });
  return h+'</div>';
}

function activeSigs(id){
  var out=[]; var box=document.getElementById(id);
  if(box) box.querySelectorAll('input[data-sig]').forEach(function(c){ if(c.checked) out.push(c.getAttribute('data-sig')); });
  return out;
}

function rowMatchesSigs(r,keys){ for(var i=0;i<keys.length;i++){ if(!sigOn(r,keys[i])) return false; } return true; }

function sigBadges(r,keys){
  var out=[];
  keys.forEach(function(k){ if(sigOn(r,k)) out.push('<span class="sig">'+esc(sigField(k)[2])+'</span>'); });
  return out.length ? out.join(' ') : '<span class="sig-none">-</span>';
}

// [Opsi A] Sel "Acuan": Ya/Tidak + chip sumber pengetahuan yg jadi acuan judge.
function acuanCell(r){
  var a=r&&r.acuan;
  if(!a||typeof a!=='object') return '<span class="sig-none">-</span>';
  var srcs=(a.sources||[]);
  if(!a.available||!srcs.length) return '<span class="acuan-no">Tidak</span>';
  var chips=srcs.map(function(s){ return '<span class="acuan-src">'+esc(s)+'</span>'; }).join('');
  return '<span class="acuan-yes">Ya</span>'+chips;
}

// [STATUS] Turunan: baris ditindaklanjuti (masuk laporan) bila Intent Judgement LLM berisi.
function s6IsTL(r){ return !!(r && r.intent && String(r.intent).trim()!==''); }
function s6StatusHtml(r){ var tl=s6IsTL(r); return '<span class="s6stat '+(tl?'tl':'ko')+'">'+(tl?'TINDAK LANJUT':'kosong')+'</span>'; }
function s6UpdateStatusCell(i){ var cell=document.getElementById('stat'+i); if(cell) cell.innerHTML=s6StatusHtml((STEP6.rows||[])[i]); }
function s6Counts(){ var tl=0, rows=(STEP6.rows||[]); rows.forEach(function(r){ if(s6IsTL(r)) tl++; }); return {tl:tl, ko:rows.length-tl, total:rows.length}; }
function s6UpdateCounts(){ var el=document.getElementById('s6tl'); if(!el) return; var c=s6Counts(); el.innerHTML='<b>'+c.tl+'</b> ditindaklanjuti \u00b7 '+c.ko+' batal/kosong \u00b7 '+c.total+' total'; }

function bindSigChips(id,fn){ var box=document.getElementById(id); if(box) box.querySelectorAll('input[data-sig]').forEach(function(c){ c.onchange=fn; }); }

function injectSigCss(){
  if(document.getElementById('sigcss')) return;
  var st=document.createElement('style'); st.id='sigcss';
  st.textContent='.sigbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 14px;border-top:1px solid var(--border)}.sigbar .sglbl{font-size:11px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--text2)}.sigchips{display:flex;flex-wrap:wrap;gap:6px}.sigchip{display:inline-flex;align-items:center;gap:4px;padding:4px 9px;border:1px solid var(--border);border-radius:999px;font-size:12px;cursor:pointer;user-select:none;background:var(--soft2)}.sigchip input{width:auto;margin:0;cursor:pointer}.s6sig{max-width:150px;white-space:normal}.sig{display:inline-block;margin:1px 2px;padding:1px 7px;border-radius:999px;font-size:10.5px;font-weight:700;background:rgba(59,130,246,.14);color:#3b82f6}.sig-none{color:var(--text2)}.s6acuan{max-width:180px;white-space:normal}.acuan-yes{display:inline-block;margin:1px 3px 1px 0;padding:1px 8px;border-radius:999px;font-size:10.5px;font-weight:800;background:rgba(16,185,129,.16);color:#10b981}.acuan-no{display:inline-block;padding:1px 8px;border-radius:999px;font-size:10.5px;font-weight:700;background:rgba(148,163,184,.16);color:var(--text2)}.acuan-src{display:inline-block;margin:1px 2px;padding:1px 7px;border-radius:999px;font-size:10px;font-weight:700;background:rgba(59,130,246,.12);color:#3b82f6}.s6statcell{white-space:nowrap}.s6stat{display:inline-block;padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:800;letter-spacing:.02em}.s6stat.tl{background:rgba(16,185,129,.16);color:#10b981}.s6stat.ko{background:rgba(148,163,184,.16);color:var(--text2)}.s6tl{font-size:12px;font-weight:700;color:var(--text2);margin-left:6px;white-space:nowrap}.s6tl b{color:#10b981;font-size:13.5px}.s6opt.s6clear{color:#ef4444;font-weight:700;border-bottom:1px solid var(--border)}';
  document.head.appendChild(st);
}

var STEP6_SIG=['panjang','majemuk','multitopik','istilah','ambigu','kandidat','noperaturan','akronim'];

function openModal6(st){
  injectSigCss();
  var modal=document.getElementById('modal');
  modal.classList.add('wide');
  modal.innerHTML=
    '<div class="mhead"><div class="mbadge">6</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="s6bar">'+
      '<div class="fg"><label>Catatan LLM</label><select id="f6cat"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Confidence</label><select id="f6conf"><option value="">Semua</option><option>TINGGI</option><option>SEDANG</option><option>RENDAH</option></select></div>'+
      '<div class="fg"><label>Skor_Deteksi min (%)</label><input type="number" id="f6skor" min="0" max="100" step="1" style="width:120px" placeholder="0"></div>'+
      '<div class="fg"><label>Cari pertanyaan</label><input type="text" id="f6q" placeholder="kata kunci..."></div>'+
      '<div class="fg"><label>STATUS</label><select id="f6status"><option value="">Semua</option><option value="TL">TINDAK LANJUT</option><option value="KO">kosong</option></select></div>'+
      '<span class="count" id="s6count"></span>'+
      '<span class="s6tl" id="s6tl"></span>'+
    '</div>'+
    '<div class="sigbar"><span class="sglbl">Sinyal</span>'+sigChips('f6sig',STEP6_SIG)+'</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="s6wrap"><table class="s6table"><thead><tr>'+
      '<th>Pertanyaan User</th><th>Catatan LLM</th><th>Intent Judgement LLM</th><th>STATUS</th><th>Isi Intent</th><th>Skor</th><th>Conf</th><th>Acuan</th>'+
    '</tr></thead><tbody id="s6body"></tbody></table></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s6save">Simpan Perubahan</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 7 →</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  document.getElementById('s6save').onclick=saveStep6;
  ['f6cat','f6conf','f6skor','f6q','f6status'].forEach(function(id){ var el=document.getElementById(id); el.oninput=renderStep6; el.onchange=renderStep6; });
  bindSigChips('f6sig',renderStep6);
  var wrap=document.querySelector('.s6wrap'); if(wrap) wrap.onscroll=closeS6Menus;
  if(!window.__s6docbound){ window.__s6docbound=true; document.addEventListener('mousedown', function(e){ if(!(e.target.closest && e.target.closest('.s6combo'))) closeS6Menus(); }); }
  loadStep6();
}

function renderStep6(){
  var body=document.getElementById('s6body'); if(!body) return;
  var fcat=document.getElementById('f6cat').value;
  var fconf=document.getElementById('f6conf').value;
  var fskor=parseFloat(document.getElementById('f6skor').value);
  var fq=document.getElementById('f6q').value.trim().toLowerCase();
  var fstEl=document.getElementById('f6status'); var fstatus=fstEl?fstEl.value:'';
  var fsig=activeSigs('f6sig');
  var CAP=400, shown=0, matched=0, parts=[];
  STEP6.rows.forEach(function(r,i){
    if(fcat && r.catatan!==fcat) return;
    if(fconf && (r.conf||'')!==fconf) return;
    if(!isNaN(fskor) && parseSkor(r.skor) < fskor) return;
    if(fq && !(r.pertanyaan||'').toLowerCase().includes(fq)) return;
    if(fstatus==='TL' && !s6IsTL(r)) return;
    if(fstatus==='KO' && s6IsTL(r)) return;
    if(fsig.length && !rowMatchesSigs(r,fsig)) return;
    matched++;
    if(shown>=CAP) return;
    shown++;
    var pill = r.catatan==='TINDAK LANJUT'?'t':(r.catatan==='PERTANYAAN TIDAK MANDIRI'?'n':'m');
    parts.push(
      '<tr><td class="s6q">'+esc(r.pertanyaan||'')+'</td>'+
      '<td><span class="s6pill '+pill+'">'+esc(r.catatan||'-')+'</span></td>'+
      '<td><div class="s6combo"><input class="s6intent'+(r.edited?' edited':'')+'" data-i="'+i+'" value="'+esc(r.intent||'')+'" autocomplete="off"><button type="button" class="s6arrow" data-i="'+i+'" tabindex="-1">▾</button><div class="s6menu" id="menu'+i+'"></div></div></td>'+
      '<td class="s6statcell" id="stat'+i+'">'+s6StatusHtml(r)+'</td>'+
      '<td><div class="s6isi" id="isi'+i+'">'+esc(r.isi||'')+'</div></td>'+
      '<td id="skor'+i+'">'+esc(r.skor||'')+'</td>'+
      '<td id="conf'+i+'">'+esc(r.conf||'')+'</td>'+
      '<td class="s6acuan">'+acuanCell(r)+'</td></tr>'
    );
  });
  body.innerHTML=parts.join('');
  body.querySelectorAll('.s6intent').forEach(function(inp){ var i=parseInt(inp.dataset.i,10); inp.oninput=function(){ onIntentChange(i, inp.value); }; inp.onfocus=function(){ openS6Menu(i); }; });
  body.querySelectorAll('.s6arrow').forEach(function(btn){ var i=parseInt(btn.dataset.i,10); btn.onclick=function(e){ e.preventDefault(); var m=document.getElementById('menu'+i); if(m && m.classList.contains('open')) closeS6Menus(); else openS6Menu(i); }; });
  document.getElementById('s6count').textContent = matched+' baris'+(matched>CAP?(' (tampil '+CAP+', persempit dgn filter)'):'');
  s6UpdateCounts();
}

// [STATUS] Override menu rekomendasi: tambah opsi "Kosongkan" (keluarkan dari laporan).
function openS6Menu(i){
  closeS6Menus();
  var r=STEP6.rows[i]; if(!r) return;
  var menu=document.getElementById('menu'+i); if(!menu) return;
  var opts=r.options||[];
  var html='<div class="s6opt s6clear" data-clear="1">\u2715 Kosongkan (STATUS jadi kosong, keluar dari laporan)</div>';
  if(!opts.length){
    html+='<div class="s6opt s6empty">Tidak ada rekomendasi untuk baris ini</div>';
  } else {
    html+=opts.map(function(o,k){ return '<div class="s6opt" data-id="'+esc(o.id)+'"><div class="s6opt-id">'+(k+1)+'. '+esc(o.id)+'</div><div class="s6opt-meta">Skor '+esc(o.skor||'-')+' \u00b7 '+esc(o.conf||'-')+'</div></div>'; }).join('');
  }
  menu.innerHTML=html;
  var clr=menu.querySelector('.s6clear');
  if(clr){ clr.onmousedown=function(e){ e.preventDefault(); var inp=document.querySelector('.s6intent[data-i="'+i+'"]'); if(inp){ inp.value=''; } onIntentChange(i, ''); closeS6Menus(); }; }
  menu.querySelectorAll('.s6opt[data-id]').forEach(function(el){ el.onmousedown=function(e){ e.preventDefault(); var id=el.getAttribute('data-id'); var inp=document.querySelector('.s6intent[data-i="'+i+'"]'); if(inp){ inp.value=id; } onIntentChange(i, id); closeS6Menus(); }; });
  var inp=document.querySelector('.s6intent[data-i="'+i+'"]');
  if(inp){ var rect=inp.getBoundingClientRect(); menu.style.left=rect.left+'px'; menu.style.top=(rect.bottom+4)+'px'; menu.style.minWidth=Math.max(260, rect.width+40)+'px'; }
  menu.classList.add('open');
}

// [STATUS] Bungkus onIntentChange (part2) agar sel STATUS + penghitung ikut ter-update
// pada semua jalur perubahan (ketik, pilih rekomendasi, atau Kosongkan).
(function(){
  if(typeof onIntentChange==='function' && !onIntentChange.__s6wrapped){
    var _orig=onIntentChange;
    onIntentChange=function(i,value){ _orig(i,value); s6UpdateStatusCell(i); s6UpdateCounts(); };
    onIntentChange.__s6wrapped=true;
  }
})();
