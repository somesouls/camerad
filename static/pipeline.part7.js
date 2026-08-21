// pipeline.part7.js — Fase 2 (C): combo intent yang bisa DICARI + opsi
// "keluarkan dari tindak lanjut". Berlaku untuk Step 6 (Intent Judgement LLM)
// & Step 9 (Intent Seharusnya). Memakai /api/analytics/search-intents?q=.
// Membungkus openS6Menu & closeS6Menus (bukan menimpa render). Fail-safe:
// bila endpoint gagal, tombol tetap bisa dipakai manual.
(function(){
  if(window.__part7){return;} window.__part7=true;
  var EXCLUDE='Dikeluarkan dari daftar tindak lanjut';
  var _timer=null, _cache={}, _cur=null;

  function injectCss(){
    if(document.getElementById('p7css'))return;
    var st=document.createElement('style');st.id='p7css';
    st.textContent=[
      '#p7menu{position:fixed;z-index:9500;min-width:280px;max-width:460px;max-height:320px;overflow:auto;background:var(--bg,#fff);color:var(--text,#111);border:1px solid var(--border);border-radius:10px;box-shadow:0 14px 40px rgba(0,0,0,.25);display:none;padding:4px}',
      '#p7menu.show{display:block}',
      '.p7opt{padding:7px 10px;border-radius:7px;cursor:pointer;font-size:13px}',
      '.p7opt:hover{background:var(--soft2)}',
      '.p7opt .m{font-size:11px;color:var(--text2);margin-top:1px}',
      '.p7sec{padding:5px 10px 3px;font-size:10.5px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--text2)}',
      '.p7act{border-top:1px solid var(--border);margin-top:4px;padding-top:4px}',
      '.p7del{color:#ef4444;font-weight:600}',
      '.p7empty{padding:9px 10px;color:var(--text2);font-size:12px}',
      '.s6intent.excluded{color:#ef4444;font-weight:600;text-decoration:line-through}'
    ].join('');
    document.head.appendChild(st);
  }

  function esc2(s){ return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  function hide(){ var m=document.getElementById('p7menu'); if(m)m.classList.remove('show'); _cur=null; }

  function menuEl(){
    var m=document.getElementById('p7menu');
    if(m)return m;
    m=document.createElement('div');m.id='p7menu';document.body.appendChild(m);
    document.addEventListener('mousedown',function(e){ if(_cur && e.target!==_cur && !(e.target.closest && e.target.closest('#p7menu'))) hide(); });
    document.addEventListener('keydown',function(e){ if(e.key==='Escape') hide(); });
    window.addEventListener('resize',hide);
    return m;
  }

  function ctxOf(inp){
    if(!inp||!inp.closest)return null;
    if(inp.closest('#s6body')) return {step:6, store:window.STEP6};
    if(inp.closest('#s9body')) return {step:9, store:window.STEP9};
    return null;
  }

  function commit(inp, ctx, i, value){
    inp.value=value;
    inp.classList.toggle('excluded', value===EXCLUDE);
    if(ctx.step===6){
      if(value===EXCLUDE){
        if(ctx.store&&ctx.store.rows[i]){ ctx.store.rows[i].intent=EXCLUDE; ctx.store.rows[i].isi=''; ctx.store.rows[i].edited=true; }
        var isi=document.getElementById('isi'+i); if(isi)isi.textContent='';
        inp.classList.add('edited');
      } else if(typeof window.onIntentChange==='function'){ window.onIntentChange(i, value); }
      else if(ctx.store&&ctx.store.rows[i]){ ctx.store.rows[i].intent=value; ctx.store.rows[i].edited=true; inp.classList.add('edited'); }
    } else {
      if(ctx.store&&ctx.store.rows[i]){ ctx.store.rows[i].seharusnya=value; ctx.store.rows[i].edited=true; }
      inp.classList.add('edited');
    }
    hide();
  }

  function render(inp, ctx, i, results){
    var m=menuEl(); var h=''; var q=(inp.value||'').trim();
    if(ctx.step===6 && !q){
      var opts=(ctx.store&&ctx.store.rows[i]&&ctx.store.rows[i].options)||[];
      if(opts.length){
        h+='<div class="p7sec">Rekomendasi LLM</div>';
        opts.forEach(function(o){ h+='<div class="p7opt" data-v="'+esc2(o.id)+'"><div>'+esc2(o.id)+'</div><div class="m">Skor '+esc2(o.skor||'-')+' · '+esc2(o.conf||'-')+'</div></div>'; });
      }
    }
    if(q){
      if(results===null){ h+='<div class="p7empty">Mencari…</div>'; }
      else if(!results||!results.length){ h+='<div class="p7empty">Tidak ada intent cocok untuk "'+esc2(q)+'"</div>'; }
      else {
        h+='<div class="p7sec">Hasil pencarian</div>';
        results.forEach(function(r){ h+='<div class="p7opt" data-v="'+esc2(r.intent)+'"><div>'+esc2(r.intent)+'</div><div class="m">'+esc2(r.count||0)+'× · '+esc2(String(r.sample||'').slice(0,60))+'</div></div>'; });
      }
    }
    h+='<div class="p7act">';
    h+='<div class="p7opt p7del" data-act="exclude">🗑 Keluarkan dari tindak lanjut</div>';
    h+='<div class="p7opt" data-act="clear">✕ Kosongkan</div>';
    h+='</div>';
    m.innerHTML=h;
    m.querySelectorAll('.p7opt').forEach(function(el){
      el.onmousedown=function(e){ e.preventDefault();
        var act=el.getAttribute('data-act');
        if(act==='exclude'){ commit(inp,ctx,i,EXCLUDE); }
        else if(act==='clear'){ commit(inp,ctx,i,''); }
        else { commit(inp,ctx,i, el.getAttribute('data-v')||''); }
      };
    });
    var rect=inp.getBoundingClientRect();
    m.style.left=Math.max(6,Math.min(rect.left, window.innerWidth-470))+'px';
    m.style.top=(rect.bottom+4)+'px';
    m.style.minWidth=Math.max(280, rect.width+40)+'px';
    m.classList.add('show');
  }

  function doSearch(inp, ctx, i){
    var q=(inp.value||'').trim();
    if(!q){ render(inp,ctx,i,[]); return; }
    if(_cache[q]){ render(inp,ctx,i,_cache[q]); return; }
    render(inp,ctx,i,null);
    fetch('/api/analytics/search-intents?q='+encodeURIComponent(q),{credentials:'same-origin'})
      .then(function(r){return r.json();})
      .then(function(d){ var res=(d&&d.ok&&d.results)||[]; _cache[q]=res; if(_cur===inp) render(inp,ctx,i,res); })
      .catch(function(){ if(_cur===inp) render(inp,ctx,i,[]); });
  }

  function open(inp){
    injectCss();
    var ctx=ctxOf(inp); if(!ctx)return;
    var i=parseInt(inp.dataset.i,10); if(isNaN(i))return;
    _cur=inp;
    var q=(inp.value||'').trim();
    if(q){ render(inp,ctx,i,_cache[q]||null); if(!_cache[q]){ if(_timer)clearTimeout(_timer); _timer=setTimeout(function(){ if(_cur===inp) doSearch(inp,ctx,i); },180); } }
    else { render(inp,ctx,i,[]); }
  }

  document.addEventListener('focusin',function(e){ var t=e.target; if(t&&t.classList&&t.classList.contains('s6intent')) open(t); });
  document.addEventListener('input',function(e){ var t=e.target; if(!(t&&t.classList&&t.classList.contains('s6intent')))return; if(_cur!==t)return; var ctx=ctxOf(t); var i=parseInt(t.dataset.i,10); if(!ctx||isNaN(i))return; t.classList.toggle('excluded', (t.value||'').trim()===EXCLUDE); if(_timer)clearTimeout(_timer); _timer=setTimeout(function(){ if(_cur===t) doSearch(t,ctx,i); },220); });

  var _open6=window.openS6Menu;
  window.openS6Menu=function(i){ var inp=document.querySelector('.s6intent[data-i="'+i+'"]'); if(inp) open(inp); };
  var _close6=window.closeS6Menus;
  window.closeS6Menus=function(){ try{ if(_close6)_close6(); }catch(e){} hide(); };

  try{ console.log('[part7] combo cari intent + keluarkan aktif'); }catch(e){}
})();
