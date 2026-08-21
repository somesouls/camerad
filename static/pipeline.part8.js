// pipeline.part8.js — Fase 2 (A): filter "Kecualikan" (sembunyikan baris) by
// sinyal DAN/ATAU intent. Berlaku Step 6 & Step 9. Menyisipkan bar kecualikan
// di modal lalu menyembunyikan baris yang cocok setelah render. Membungkus
// renderStep6/renderStep9 (dimuat setelah part6/part7). Fail-safe.
(function(){
  if(window.__part8){return;} window.__part8=true;
  var EXCLUDE='Dikeluarkan dari daftar tindak lanjut';

  function injectCss(){
    if(document.getElementById('p8css'))return;
    var st=document.createElement('style');st.id='p8css';
    st.textContent='.exbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 14px;border-top:1px dashed var(--border);background:var(--soft)}.exbar .exlbl{font-size:11px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:#ef4444}.exbar input[type=text]{width:220px}.exbar .exchk{display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--text2)}.exbar .exchk input{width:auto;margin:0}.exbar .exclr{font-size:12px;color:var(--text2);cursor:pointer;text-decoration:underline}';
    document.head.appendChild(st);
  }

  function chipsHtml(id, keys){
    if(typeof sigChips==='function'){ return sigChips(id, keys); }
    return '<div class="sigchips" id="'+id+'"></div>';
  }

  function ids(step){
    return step===6
      ? {sig:'ex6sig', int:'ex6int', done:'ex6done', clr:'ex6clr', bar:'exbar6', body:'s6body', cnt:'s6count', SIG:window.STEP6_SIG, store:window.STEP6, render:'renderStep6'}
      : {sig:'ex9sig', int:'ex9int', done:'ex9done', clr:'ex9clr', bar:'exbar9', body:'s9body', cnt:'s9count', SIG:window.STEP9_SIG, store:window.STEP9, render:'renderStep9'};
  }

  function ensureBar(step){
    var d=ids(step);
    if(document.getElementById(d.bar)) return;
    var body=document.getElementById(d.body); if(!body) return;
    var wrap=body.closest('.s6wrap'); if(!wrap||!wrap.parentNode) return;
    var bar=document.createElement('div'); bar.className='exbar'; bar.id=d.bar;
    bar.innerHTML='<span class="exlbl">Kecualikan</span>'+
      chipsHtml(d.sig, d.SIG||[])+
      '<input type="text" id="'+d.int+'" placeholder="intent mengandung (pisah koma)...">'+
      '<label class="exchk"><input type="checkbox" id="'+d.done+'"> sembunyikan yang sudah dikeluarkan</label>'+
      '<span class="exclr" id="'+d.clr+'">reset kecualikan</span>';
    wrap.parentNode.insertBefore(bar, wrap);
    var rerender=function(){ if(typeof window[d.render]==='function') window[d.render](); };
    if(typeof bindSigChips==='function') bindSigChips(d.sig, rerender);
    var it=document.getElementById(d.int); if(it) it.oninput=rerender;
    var dn=document.getElementById(d.done); if(dn) dn.onchange=rerender;
    var clr=document.getElementById(d.clr); if(clr) clr.onclick=function(){
      var box=document.getElementById(d.sig); if(box) box.querySelectorAll('input[data-sig]').forEach(function(c){ c.checked=false; });
      if(it) it.value=''; if(dn) dn.checked=false; rerender();
    };
  }

  function excludedRow(step, r){
    if(!r) return false;
    var d=ids(step);
    var keys=(typeof activeSigs==='function') ? activeSigs(d.sig) : [];
    if(keys.length && typeof sigOn==='function'){
      for(var k=0;k<keys.length;k++){ if(sigOn(r, keys[k])) return true; }
    }
    var itEl=document.getElementById(d.int);
    var term=((itEl&&itEl.value)||'').trim().toLowerCase();
    if(term){
      var terms=term.split(',').map(function(s){return s.trim();}).filter(Boolean);
      var hay=((step===6?(r.intent||''):((r.intent||'')+' '+(r.seharusnya||'')))+'').toLowerCase();
      for(var t=0;t<terms.length;t++){ if(hay.indexOf(terms[t])>=0) return true; }
    }
    var dnEl=document.getElementById(d.done);
    if(dnEl&&dnEl.checked){
      var v=step===6?(r.intent||''):(r.seharusnya||'');
      if(v===EXCLUDE) return true;
    }
    return false;
  }

  function applyExclude(step){
    var d=ids(step);
    var body=document.getElementById(d.body); var store=d.store;
    if(!body||!store) return;
    var trs=body.querySelectorAll('tr'); var hidden=0;
    for(var k=0;k<trs.length;k++){
      var inp=trs[k].querySelector('.s6intent[data-i]');
      var i=inp?parseInt(inp.dataset.i,10):-1;
      var r=(i>=0&&store.rows[i])?store.rows[i]:null;
      if(excludedRow(step, r)){ trs[k].style.display='none'; hidden++; }
    }
    if(hidden){
      var cnt=document.getElementById(d.cnt);
      if(cnt && cnt.textContent.indexOf('disembunyikan')<0){ cnt.textContent += ' · '+hidden+' disembunyikan'; }
    }
  }

  function wrapRender(name, step){
    var orig=window[name]; if(typeof orig!=='function') return;
    window[name]=function(){
      var out=orig.apply(this,arguments);
      try{ injectCss(); ensureBar(step); applyExclude(step); }catch(e){}
      return out;
    };
  }
  wrapRender('renderStep6',6);
  wrapRender('renderStep9',9);

  try{ console.log('[part8] filter kecualikan aktif'); }catch(e){}
})();
