// pipeline.part4.js — Sinyal analisis Step 6 (Analisis Manual Fallback).
// Dimuat SETELAH part2/part3; menimpa openModal6 & renderStep6 (fungsi global).
// Menambah bar chip filter sinyal (kolom "Sinyal" di tabel DIHAPUS atas permintaan;
// chip filter tetap ada). Fail-safe bila field sinyal belum ada -> normal.

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

function bindSigChips(id,fn){ var box=document.getElementById(id); if(box) box.querySelectorAll('input[data-sig]').forEach(function(c){ c.onchange=fn; }); }

function injectSigCss(){
  if(document.getElementById('sigcss')) return;
  var st=document.createElement('style'); st.id='sigcss';
  st.textContent='.sigbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 14px;border-top:1px solid var(--border)}.sigbar .sglbl{font-size:11px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--text2)}.sigchips{display:flex;flex-wrap:wrap;gap:6px}.sigchip{display:inline-flex;align-items:center;gap:4px;padding:4px 9px;border:1px solid var(--border);border-radius:999px;font-size:12px;cursor:pointer;user-select:none;background:var(--soft2)}.sigchip input{width:auto;margin:0;cursor:pointer}.s6sig{max-width:150px;white-space:normal}.sig{display:inline-block;margin:1px 2px;padding:1px 7px;border-radius:999px;font-size:10.5px;font-weight:700;background:rgba(59,130,246,.14);color:#3b82f6}.sig-none{color:var(--text2)}';
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
      '<span class="count" id="s6count"></span>'+
    '</div>'+
    '<div class="sigbar"><span class="sglbl">Sinyal</span>'+sigChips('f6sig',STEP6_SIG)+'</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="s6wrap"><table class="s6table"><thead><tr>'+
      '<th>Pertanyaan User</th><th>Catatan LLM</th><th>Intent Judgement LLM</th><th>Isi Intent</th><th>Skor</th><th>Conf</th>'+
    '</tr></thead><tbody id="s6body"></tbody></table></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s6save">Simpan Perubahan</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 7 →</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  document.getElementById('s6save').onclick=saveStep6;
  ['f6cat','f6conf','f6skor','f6q'].forEach(function(id){ var el=document.getElementById(id); el.oninput=renderStep6; el.onchange=renderStep6; });
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
  var fsig=activeSigs('f6sig');
  var CAP=400, shown=0, matched=0, parts=[];
  STEP6.rows.forEach(function(r,i){
    if(fcat && r.catatan!==fcat) return;
    if(fconf && (r.conf||'')!==fconf) return;
    if(!isNaN(fskor) && parseSkor(r.skor) < fskor) return;
    if(fq && !(r.pertanyaan||'').toLowerCase().includes(fq)) return;
    if(fsig.length && !rowMatchesSigs(r,fsig)) return;
    matched++;
    if(shown>=CAP) return;
    shown++;
    var pill = r.catatan==='TINDAK LANJUT'?'t':(r.catatan==='PERTANYAAN TIDAK MANDIRI'?'n':'m');
    parts.push(
      '<tr><td class="s6q">'+esc(r.pertanyaan||'')+'</td>'+
      '<td><span class="s6pill '+pill+'">'+esc(r.catatan||'-')+'</span></td>'+
      '<td><div class="s6combo"><input class="s6intent'+(r.edited?' edited':'')+'" data-i="'+i+'" value="'+esc(r.intent||'')+'" autocomplete="off"><button type="button" class="s6arrow" data-i="'+i+'" tabindex="-1">▾</button><div class="s6menu" id="menu'+i+'"></div></div></td>'+
      '<td><div class="s6isi" id="isi'+i+'">'+esc(r.isi||'')+'</div></td>'+
      '<td id="skor'+i+'">'+esc(r.skor||'')+'</td>'+
      '<td id="conf'+i+'">'+esc(r.conf||'')+'</td></tr>'
    );
  });
  body.innerHTML=parts.join('');
  body.querySelectorAll('.s6intent').forEach(function(inp){ var i=parseInt(inp.dataset.i,10); inp.oninput=function(){ onIntentChange(i, inp.value); }; inp.onfocus=function(){ openS6Menu(i); }; });
  body.querySelectorAll('.s6arrow').forEach(function(btn){ var i=parseInt(btn.dataset.i,10); btn.onclick=function(e){ e.preventDefault(); var m=document.getElementById('menu'+i); if(m && m.classList.contains('open')) closeS6Menus(); else openS6Menu(i); }; });
  document.getElementById('s6count').textContent = matched+' baris'+(matched>CAP?(' (tampil '+CAP+', persempit dgn filter)'):'');
}
