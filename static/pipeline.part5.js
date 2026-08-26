// pipeline.part5.js — Sinyal analisis Step 9 (Analisis Manual MKTA).
// Dimuat SETELAH part2/part3/part4; menimpa openModal9 & renderStep9 (fungsi global).
// Perubahan Fase 2:
//  - Kolom "Sinyal" & "Kandidat / Terdekat" DIHAPUS dari tabel (chip filter sinyal tetap).
//  - Kolom "Intent Seharusnya" jadi dropdown bisa-cari (part7): rekomendasi =
//    intent terdekat (r.kandidat), + pencarian katalog intent, + Kosongkan.
//    Default KOSONG (nilai awal = r.manual; kosong utk baris yang belum ditinjau =
//    match akurat / bukan MKTA). Memakai helper dari part4.js. Fail-safe.
//  - [Opsi A] Kolom "Acuan": Ya/Tidak + sumber acuan judge (helper acuanCell part4).
//  - [Fase 3] Info tindak lanjut: jumlah "Intent Seharusnya" terisi (ditindaklanjuti)
//    vs kosong (TANPA CATATAN), dihitung atas baris di bawah ambang (yang disimpan).
//    + Filter "Intent Seharusnya": Semua / Ada isi / Kosong.

var STEP9_SIG=['bedallm','panjang','majemuk','multitopik','istilah','ambigu','noperaturan','akronim'];

// Nilai "Intent Seharusnya" efektif untuk sebuah baris:
//  - kalau sudah diedit di UI -> pakai r.seharusnya (termasuk saat dikosongkan)
//  - kalau belum -> pakai nilai awal dari server r.manual
function s9curr(r){ if(!r) return ''; var v=(r.seharusnya!==undefined && r.seharusnya!==null) ? r.seharusnya : r.manual; return v==null?'':String(v); }

// Hitung terisi vs kosong, HANYA atas baris di bawah ambang (yang akan disimpan).
function s9SehCounts(){
  var thrEl=document.getElementById('f9qa');
  var thr=thrEl?parseFloat(thrEl.value):NaN;
  var filled=0, empty=0;
  var rows=(typeof STEP9!=='undefined' && STEP9.rows) ? STEP9.rows : [];
  rows.forEach(function(r){
    var qa=s9num(r.qa);
    var inThr = !isNaN(thr) ? (qa!==null && qa<thr) : true;
    if(!inThr) return;
    if(s9curr(r).trim()!=='') filled++; else empty++;
  });
  return {filled:filled, empty:empty, total:filled+empty};
}

function s9UpdateCounts(){
  var el=document.getElementById('s9seh'); if(!el) return;
  var c=s9SehCounts();
  el.innerHTML='<b style="color:#10b981">'+c.filled+'</b> ditindaklanjuti (ada isi) · <b style="color:#f59e0b">'+c.empty+'</b> TANPA CATATAN (kosong)';
}

function openModal9(st){
  if(typeof injectSigCss==='function') injectSigCss();
  var modal=document.getElementById('modal');
  modal.classList.add('wide');
  modal.innerHTML=
    '<div class="mhead"><div class="mbadge">9</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="s6bar">'+
      '<div class="fg"><label>Skor Bahasa &lt; (ambang sheet)</label><input type="number" id="f9qa" min="0" max="1" step="0.05" value="0.5" style="width:110px"></div>'+
      '<div class="fg"><label>PUTUSAN</label><select id="f9put"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Kategori Mesin</label><select id="f9kat"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Skor Dialogflow ≤</label><input type="number" id="f9df" min="0" max="1" step="0.05" placeholder="0" style="width:100px"></div>'+
      '<div class="fg"><label>Skor Dialogflow ≥</label><input type="number" id="f9dfmin" min="0" max="1" step="0.05" placeholder="0" style="width:100px"></div>'+
      '<div class="fg"><label>Skor NLI ≤</label><input type="number" id="f9nli" min="0" max="1" step="0.05" placeholder="0" style="width:100px"></div>'+
      '<div class="fg"><label>Cari pertanyaan</label><input type="text" id="f9q" placeholder="kata kunci..."></div>'+
      '<div class="fg"><label>Intent Seharusnya</label><select id="f9seh"><option value="">Semua</option><option value="ADA">Ada isi (tindak lanjut)</option><option value="KOSONG">Kosong (tanpa catatan)</option></select></div>'+
      '<span class="count" id="s9count"></span>'+
      '<span id="s9seh" style="font-size:12.5px;color:var(--text2);align-self:center;margin-left:14px;white-space:nowrap"></span>'+
    '</div>'+
    '<div class="sigbar"><span class="sglbl">Sinyal</span>'+sigChips('f9sig',STEP9_SIG)+
      '<button type="button" class="btn btn-sec" id="f9cw" style="padding:4px 10px;font-size:12px">Confidently wrong</button>'+
      '<button type="button" class="btn btn-sec" id="f9clr" style="padding:4px 10px;font-size:12px">Reset filter</button>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="s6wrap"><table class="s6table"><thead><tr>'+
      '<th>Prioritas</th><th>Pertanyaan User</th><th>Intent (Bot)</th><th>Kategori Mesin</th><th>Skor Bahasa</th><th>Skor DF</th><th>NLI</th><th>PUTUSAN &amp; Alasan</th><th>Acuan</th><th>Intent Seharusnya</th>'+
    '</tr></thead><tbody id="s9body"></tbody></table></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s9save">Simpan ke sheet Analisis MKTA</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 10 →</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  document.getElementById('s9save').onclick=saveStep9;
  ['f9qa','f9put','f9kat','f9df','f9dfmin','f9nli','f9q','f9seh'].forEach(function(id){ var el=document.getElementById(id); el.oninput=renderStep9; el.onchange=renderStep9; });
  bindSigChips('f9sig',renderStep9);
  var cw=document.getElementById('f9cw'); if(cw) cw.onclick=s9presetConfidentlyWrong;
  var clr=document.getElementById('f9clr'); if(clr) clr.onclick=s9resetFilter;
  var wrap=document.querySelector('.s6wrap'); if(wrap) wrap.onscroll=function(){ if(typeof closeS6Menus==='function') closeS6Menus(); };
  if(!window.__s6docbound){ window.__s6docbound=true; document.addEventListener('mousedown', function(e){ if(!(e.target.closest && e.target.closest('.s6combo'))){ if(typeof closeS6Menus==='function') closeS6Menus(); } }); }
  loadStep9();
}

function s9presetConfidentlyWrong(){
  var el;
  el=document.getElementById('f9dfmin'); if(el) el.value='0.7';
  el=document.getElementById('f9df'); if(el) el.value='';
  el=document.getElementById('f9nli'); if(el) el.value='';
  document.querySelectorAll('#f9sig input[data-sig]').forEach(function(c){ c.checked = (c.getAttribute('data-sig')==='bedallm'); });
  if(typeof renderStep9==='function') renderStep9();
}

function s9resetFilter(){
  ['f9df','f9dfmin','f9nli','f9q'].forEach(function(id){ var el=document.getElementById(id); if(el) el.value=''; });
  var p=document.getElementById('f9put'); if(p) p.value='';
  var k=document.getElementById('f9kat'); if(k) k.value='';
  var s=document.getElementById('f9seh'); if(s) s.value='';
  document.querySelectorAll('#f9sig input[data-sig]').forEach(function(c){ c.checked=false; });
  if(typeof renderStep9==='function') renderStep9();
}

function renderStep9(){
  var body=document.getElementById('s9body'); if(!body) return;
  var thr=parseFloat(document.getElementById('f9qa').value);
  var fput=document.getElementById('f9put').value;
  var fkat=document.getElementById('f9kat').value;
  var fdf=parseFloat(document.getElementById('f9df').value);
  var fdfmin=parseFloat(document.getElementById('f9dfmin').value);
  var fnli=parseFloat(document.getElementById('f9nli').value);
  var fq=document.getElementById('f9q').value.trim().toLowerCase();
  var fsehEl=document.getElementById('f9seh'); var fseh=fsehEl?fsehEl.value:'';
  var fsig=activeSigs('f9sig');
  var CAP=400, shown=0, matched=0, underThr=0, parts=[];
  var order = STEP9.rows.map(function(r,i){ return i; });
  order.sort(function(a,b){ var pa=s9num(STEP9.rows[a].prioritas), pb=s9num(STEP9.rows[b].prioritas); return (pb===null?-1:pb)-(pa===null?-1:pa); });
  order.forEach(function(i){
    var r=STEP9.rows[i];
    var qa=s9num(r.qa);
    var inThr = !isNaN(thr) ? (qa!==null && qa<thr) : true;
    if(inThr) underThr++;
    if(!inThr) return;
    if(fput && r.putusan!==fput) return;
    if(fkat && r.kategori!==fkat) return;
    if(!isNaN(fdf)){ var d=s9num(r.df); if(d===null || d>fdf) return; }
    if(!isNaN(fdfmin)){ var dm=s9num(r.df); if(dm===null || dm<fdfmin) return; }
    if(!isNaN(fnli)){ var nn=s9num(r.nli); if(nn===null || nn>fnli) return; }
    if(fq && !(r.pertanyaan||'').toLowerCase().includes(fq)) return;
    if(fseh){ var sehv=s9curr(r).trim(); if(fseh==='ADA' && sehv==='') return; if(fseh==='KOSONG' && sehv!=='') return; }
    if(fsig.length && !rowMatchesSigs(r,fsig)) return;
    matched++;
    if(shown>=CAP) return; shown++;
    var alasan = r.alasan ? '<div style="color:#9aa4b2;font-size:11px;margin-top:3px">'+esc(r.alasan)+'</div>' : '';
    parts.push(
      '<tr><td>'+esc(r.prioritas||'')+'</td>'+
      '<td class="s6q">'+esc(r.pertanyaan||'')+'</td>'+
      '<td class="s6q">'+esc(r.intent||'')+'</td>'+
      '<td>'+esc(r.kategori||'')+'</td>'+
      '<td>'+s9fmt(r.qa)+'</td>'+
      '<td>'+s9fmt(r.df)+'</td>'+
      '<td>'+s9fmt(r.nli)+'</td>'+
      '<td>'+esc(r.putusan||'')+alasan+'</td>'+
      '<td class="s6acuan">'+(typeof acuanCell==='function'?acuanCell(r):'')+'</td>'+
      '<td><div class="s6combo"><input class="s6intent" data-i="'+i+'" value="'+esc(s9curr(r))+'" autocomplete="off" placeholder="ketik / pilih intent..."><button type="button" class="s6arrow" data-i="'+i+'" tabindex="-1">▾</button><div class="s6menu" id="menu'+i+'"></div></div></td></tr>'
    );
  });
  body.innerHTML=parts.join('');
  body.querySelectorAll('.s6intent').forEach(function(inp){ var i=parseInt(inp.dataset.i,10); inp.oninput=function(){ STEP9.rows[i].seharusnya=inp.value; STEP9.rows[i].edited=true; inp.classList.add('edited'); s9UpdateCounts(); }; inp.onfocus=function(){ if(typeof openS6Menu==='function') openS6Menu(i); }; });
  body.querySelectorAll('.s6arrow').forEach(function(btn){ var i=parseInt(btn.dataset.i,10); btn.onclick=function(e){ e.preventDefault(); var m=document.getElementById('menu'+i); if(m && m.classList.contains('open')){ if(typeof closeS6Menus==='function') closeS6Menus(); } else if(typeof openS6Menu==='function'){ openS6Menu(i); } }; });
  document.getElementById('s9count').textContent = matched+' tampil · '+underThr+' akan disimpan (Skor<'+(isNaN(thr)?'-':thr)+')'+(matched>CAP?(' · tampil '+CAP):'');
  s9UpdateCounts();
}
