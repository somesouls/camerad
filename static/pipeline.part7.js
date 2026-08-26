// pipeline.part7.js — Fase 2 (C): dropdown intent yang bisa DICARI.
// Susunan menu (sesuai permintaan analis):
//   [ kolom pencarian ]
//   1..N rekomendasi  (Step 6: 5 rekomendasi LLM dari r.options; Step 9: intent
//                      terdekat dari r.kandidat) — TETAP ADA, tidak dihilangkan
//   ✕ Kosongkan       (Step 6 = tidak ditindaklanjuti; Step 9 = match akurat/bukan MKTA)
// Kolom pencarian memakai aksi backend 'intents' (daftar intent Step 3 yang
// sudah ditarik / katalog intent). Menimpa openS6Menu & closeS6Menus, dan
// merender ke elemen #menu{i} native di dalam .s6combo (Step 6 & Step 9).
// Fail-open: bila katalog gagal dimuat, analis tetap bisa mengetik manual +
// Enter, memilih rekomendasi, atau Kosongkan.
(function(){
  if(window.__part7){return;} window.__part7=true;
  var CAT=null;      // daftar nama intent (null=belum dimuat)
  var CATLOADING=false;

  function injectCss(){
    if(document.getElementById('p7css'))return;
    var st=document.createElement('style');st.id='p7css';
    st.textContent=[
      '.s6menu .p7srch{position:sticky;top:0;background:var(--bg,#fff);padding:6px;border-bottom:1px solid var(--border);z-index:1}',
      '.s6menu .p7srch input{width:100%;box-sizing:border-box;padding:6px 9px;border:1px solid var(--border);border-radius:7px;font-size:13px;background:var(--soft2);color:var(--text)}',
      '.s6menu .p7res{max-height:230px;overflow:auto;padding:2px}',
      '.p7sec{padding:5px 10px 3px;font-size:10.5px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--text2)}',
      '.p7opt{padding:7px 10px;border-radius:7px;cursor:pointer;font-size:13px}',
      '.p7opt:hover{background:var(--soft2)}',
      '.p7opt .m{font-size:11px;color:var(--text2);margin-top:1px}',
      '.p7clr{border-top:1px solid var(--border);margin-top:4px;padding-top:8px;color:#ef4444;font-weight:600}',
      '.p7empty{padding:9px 10px;color:var(--text2);font-size:12px;line-height:1.4}'
    ].join('');
    document.head.appendChild(st);
  }
  function esc2(s){ return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  // Store dibaca sebagai variabel GLOBAL (STEP6/STEP9 dideklarasikan `let`).
  function ctxOf(inp){
    if(!inp||!inp.closest)return null;
    if(inp.closest('#s6body')){ try{ if(typeof STEP6!=='undefined') return {step:6,store:STEP6}; }catch(e){} }
    if(inp.closest('#s9body')){ try{ if(typeof STEP9!=='undefined') return {step:9,store:STEP9}; }catch(e){} }
    return null;
  }

  function loadCatalog(cb){
    if(CAT!==null){ if(cb)cb(); return; }
    if(CATLOADING){ return; }
    CATLOADING=true;
    try{
      api('intents',{}).then(function(res){
        CAT=(res&&res.ok&&res.intents)?res.intents:[];
        CATLOADING=false; if(cb)cb();
      }).catch(function(){ CAT=[]; CATLOADING=false; if(cb)cb(); });
    }catch(e){ CAT=[]; CATLOADING=false; if(cb)cb(); }
  }

  function recsFor(ctx,i){
    var r=(ctx.store&&ctx.store.rows[i])||null; if(!r)return [];
    if(ctx.step===6){
      return (r.options||[]).map(function(o){ return {id:o.id, meta:'Skor '+(o.skor||'-')+' · '+(o.conf||'-')}; });
    }
    // Step 9: intent terdekat (bisa lebih dari satu; pisah newline/;/|/,)
    var raw=(r.kandidat||r.terdekat||'');
    var parts=String(raw).split(/[\n;|,]+/).map(function(s){return s.trim();}).filter(Boolean);
    var seen={}, out=[];
    parts.forEach(function(p){ var k=p.toLowerCase(); if(!seen[k]){ seen[k]=1; out.push({id:p, meta:'terdekat'}); } });
    return out;
  }

  function commit(inp,ctx,i,value){
    inp.value=value;
    if(ctx.step===6){
      if(typeof onIntentChange==='function'){ onIntentChange(i, value); }
      else if(ctx.store&&ctx.store.rows[i]){ ctx.store.rows[i].intent=value; ctx.store.rows[i].edited=true; }
      inp.classList.add('edited');
    } else {
      if(ctx.store&&ctx.store.rows[i]){ ctx.store.rows[i].seharusnya=value; ctx.store.rows[i].edited=true; }
      inp.classList.add('edited');
      if(typeof s9UpdateCounts==='function'){ try{ s9UpdateCounts(); }catch(e){} }
    }
    closeMenus();
  }

  function renderResults(container, inp, ctx, i, q){
    var h=''; q=(q||'').trim(); var ql=q.toLowerCase();
    if(!ql){
      var recs=recsFor(ctx,i);
      if(recs.length){
        h+='<div class="p7sec">Rekomendasi</div>';
        recs.forEach(function(o,k){ h+='<div class="p7opt" data-v="'+esc2(o.id)+'"><div>'+(k+1)+'. '+esc2(o.id)+'</div><div class="m">'+esc2(o.meta)+'</div></div>'; });
      } else {
        h+='<div class="p7empty">Tidak ada rekomendasi untuk baris ini. Ketik untuk mencari intent, atau Kosongkan.</div>';
      }
    } else {
      if(CAT===null){ h+='<div class="p7empty">Memuat daftar intent…</div>'; }
      else {
        var res=[]; for(var j=0;j<CAT.length && res.length<50;j++){ if(String(CAT[j]).toLowerCase().indexOf(ql)>=0) res.push(CAT[j]); }
        if(!res.length){ h+='<div class="p7empty">Tidak ada intent cocok. Tekan Enter untuk pakai "'+esc2(q)+'".</div>'; }
        else { h+='<div class="p7sec">Hasil pencarian ('+res.length+')</div>'; res.forEach(function(nm){ h+='<div class="p7opt" data-v="'+esc2(nm)+'"><div>'+esc2(nm)+'</div></div>'; }); }
      }
    }
    h+='<div class="p7opt p7clr" data-act="clear">✕ Kosongkan</div>';
    container.innerHTML=h;
    container.querySelectorAll('.p7opt').forEach(function(el){
      el.onmousedown=function(e){ e.preventDefault();
        if(el.getAttribute('data-act')==='clear'){ commit(inp,ctx,i,''); }
        else { commit(inp,ctx,i, el.getAttribute('data-v')||''); }
      };
    });
  }

  function closeMenus(){ document.querySelectorAll('.s6menu.open').forEach(function(m){ m.classList.remove('open'); }); }

  function openMenu(i){
    injectCss();
    var inp=document.querySelector('.s6intent[data-i="'+i+'"]'); if(!inp) return;
    var ctx=ctxOf(inp); if(!ctx) return;
    var menu=document.getElementById('menu'+i); if(!menu) return;
    closeMenus();
    menu.innerHTML='<div class="p7srch"><input type="text" class="p7q" placeholder="Cari intent…" autocomplete="off"></div><div class="p7res"></div>';
    var res=menu.querySelector('.p7res');
    var q=menu.querySelector('.p7q');
    renderResults(res, inp, ctx, i, '');
    loadCatalog(function(){ if(menu.classList.contains('open')) renderResults(res, inp, ctx, i, q.value); });
    q.oninput=function(){ renderResults(res, inp, ctx, i, q.value); };
    q.onkeydown=function(e){ if(e.key==='Enter'){ e.preventDefault(); var v=q.value.trim(); if(v) commit(inp,ctx,i,v); } else if(e.key==='Escape'){ closeMenus(); } };
    var rect=inp.getBoundingClientRect();
    menu.style.left=Math.max(6,Math.min(rect.left, window.innerWidth-360))+'px';
    menu.style.top=(rect.bottom+4)+'px';
    menu.style.minWidth=Math.max(280, rect.width+40)+'px';
    menu.classList.add('open');
    setTimeout(function(){ try{ q.focus(); }catch(e){} },0);
  }

  window.openS6Menu=openMenu;
  window.closeS6Menus=closeMenus;

  try{ console.log('[part7] dropdown intent: pencarian katalog + rekomendasi + Kosongkan aktif'); }catch(e){}
})();
