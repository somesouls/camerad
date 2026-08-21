// pipeline.part6.js — Fase 2 (B): tombol mata "lihat percakapan penuh".
// Dimuat SETELAH part2..part5. Membungkus renderStep6 & renderStep9 (bukan
// menimpa) agar tiap baris punya tombol mata yang membuka transkrip percakapan
// penuh via id_trace -> /api/deflection/transcript?session_id=<id> (logika sama
// dengan openTranscript di Analisis Deflection).
//
// FIX: STEP6/STEP9 dideklarasikan dengan `let` di part2/part3, sehingga BUKAN
// properti window. Versi lama membaca window.STEP6/window.STEP9 -> undefined ->
// daftar baris kosong -> session_id selalu kosong -> tombol selalu disabled.
// Sekarang store dibaca sebagai variabel global langsung (STEP6/STEP9).
(function(){
  if(window.__part6){return;} window.__part6=true;

  function injectCss(){
    if(document.getElementById('p6css'))return;
    var st=document.createElement('style');st.id='p6css';
    st.textContent=[
      '.eyebtn{border:1px solid var(--border);background:var(--soft2);border-radius:7px;cursor:pointer;font-size:13px;line-height:1;padding:3px 7px;margin-right:6px;color:var(--text)}',
      '.eyebtn:hover{background:var(--soft)}',
      '.eyebtn[disabled]{opacity:.35;cursor:not-allowed}',
      '.p6ov{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9000;display:none;align-items:center;justify-content:center}',
      '.p6ov.show{display:flex}',
      '.p6modal{background:var(--bg,#fff);color:var(--text,#111);width:min(760px,94vw);max-height:86vh;border-radius:14px;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.4)}',
      '.p6head{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--border)}',
      '.p6head h3{margin:0;font-size:15px;flex:1}',
      '.p6head .sid{font-size:11px;color:var(--text2);font-weight:600}',
      '.p6x{border:0;background:transparent;font-size:22px;cursor:pointer;color:var(--text2);line-height:1}',
      '.p6body{padding:14px 16px;overflow:auto}',
      '.p6turn{margin-bottom:14px;display:flex;flex-direction:column}',
      '.p6b{max-width:82%;padding:8px 11px;border-radius:12px;font-size:13px;line-height:1.45;white-space:pre-wrap;word-break:break-word}',
      '.p6u{background:#3b82f6;color:#fff;border-bottom-right-radius:4px;align-self:flex-end}',
      '.p6r{background:var(--soft2);border:1px solid var(--border);border-bottom-left-radius:4px;align-self:flex-start}',
      '.p6meta{font-size:10.5px;color:var(--text2);margin:3px 2px 0}',
      '.p6fb{display:inline-block;margin-left:6px;padding:0 6px;border-radius:999px;background:rgba(239,68,68,.15);color:#ef4444;font-size:10px;font-weight:700}',
      '.p6empty{color:var(--text2);text-align:center;padding:24px}'
    ].join('');
    document.head.appendChild(st);
  }

  function esc2(s){ return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  function closeT(){ var ov=document.getElementById('p6ov'); if(ov) ov.classList.remove('show'); }

  function ensureModal(){
    var ov=document.getElementById('p6ov');
    if(ov)return ov;
    ov=document.createElement('div');ov.id='p6ov';ov.className='p6ov';
    ov.innerHTML='<div class="p6modal"><div class="p6head"><h3>Detail Percakapan</h3><span class="sid" id="p6sid"></span><button class="p6x" id="p6x" title="Tutup">&times;</button></div><div class="p6body" id="p6body"></div></div>';
    document.body.appendChild(ov);
    ov.addEventListener('mousedown',function(e){ if(e.target===ov) closeT(); });
    ov.querySelector('#p6x').onclick=closeT;
    document.addEventListener('keydown',function(e){ if(e.key==='Escape') closeT(); });
    return ov;
  }

  function showTranscript(sid){
    injectCss(); ensureModal();
    var ov=document.getElementById('p6ov');
    document.getElementById('p6sid').textContent='session: '+sid;
    var body=document.getElementById('p6body');
    body.innerHTML='<div class="p6empty">Memuat percakapan…</div>';
    ov.classList.add('show');
    fetch('/api/deflection/transcript?session_id='+encodeURIComponent(sid),{credentials:'same-origin'})
      .then(function(r){return r.json();})
      .then(function(d){
        if(!d||!d.ok){ body.innerHTML='<div class="p6empty">Gagal memuat: '+esc2((d&&d.error)||'tidak diketahui')+'</div>'; return; }
        var turns=d.turns||[];
        if(!turns.length){ body.innerHTML='<div class="p6empty">Tidak ada percakapan untuk sesi ini.</div>'; return; }
        var h='';
        turns.forEach(function(t){
          var fb=t.is_fallback?'<span class="p6fb">fallback</span>':'';
          var it=t.intent?(' · '+esc2(t.intent)):'';
          h+='<div class="p6turn">';
          if(t.user_phrase){ h+='<div class="p6b p6u">'+esc2(t.user_phrase)+'</div>'; }
          if(t.bot_response){ h+='<div class="p6b p6r">'+esc2(t.bot_response)+'</div>'; }
          h+='<div class="p6meta">'+esc2(t.ts||'')+it+fb+'</div>';
          h+='</div>';
        });
        body.innerHTML=h;
      })
      .catch(function(){ body.innerHTML='<div class="p6empty">Gagal memuat percakapan.</div>'; });
  }
  window.p6ShowTranscript=showTranscript;

  // Ambil store sebagai variabel GLOBAL (bukan window.*), karena STEP6/STEP9
  // dideklarasikan dengan `let`/`const` di file lain (tidak menempel di window).
  function storeFor(bodyId){
    try{ if(bodyId==='s6body' && typeof STEP6!=='undefined') return STEP6; }catch(e){}
    try{ if(bodyId==='s9body' && typeof STEP9!=='undefined') return STEP9; }catch(e){}
    return null;
  }

  function addEyes(bodyId,rows){
    var body=document.getElementById(bodyId); if(!body||!rows)return;
    var trs=body.querySelectorAll('tr');
    for(var k=0;k<trs.length;k++){
      var tr=trs[k];
      if(tr.querySelector('.eyebtn'))continue;
      var inp=tr.querySelector('.s6intent[data-i]');
      var i=inp?parseInt(inp.dataset.i,10):-1;
      var r=(i>=0&&rows[i])?rows[i]:null;
      var sid=(r&&(r.id_trace||r.session_id||r.insert_id))||'';
      var cell=tr.querySelector('td.s6q'); if(!cell)continue;
      var btn=document.createElement('button');
      btn.type='button'; btn.className='eyebtn'; btn.textContent='👁';
      if(sid){ btn.title='Lihat percakapan penuh'; (function(s){ btn.onclick=function(e){ e.stopPropagation(); showTranscript(s); }; })(sid); }
      else { btn.disabled=true; btn.title='ID percakapan tidak tersedia untuk baris ini'; }
      cell.insertBefore(btn, cell.firstChild);
    }
  }

  function wrapRender(name,bodyId){
    var orig=window[name];
    if(typeof orig!=='function')return;
    window[name]=function(){
      var out=orig.apply(this,arguments);
      try{ var store=storeFor(bodyId); addEyes(bodyId,(store&&store.rows)||[]); }catch(e){}
      return out;
    };
  }
  wrapRender('renderStep6','s6body');
  wrapRender('renderStep9','s9body');

  try{ console.log('[part6] tombol mata percakapan aktif (store global STEP6/STEP9)'); }catch(e){}
})();
