(function(){
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function api(p){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(function(r){return r.json();});}
  function setStat(msg,k){var s=el('dtStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function hideStat(){var s=el('dtStat');if(s){s.className='status';s.innerHTML='';}}
  function truthy(v){v=String(v==null?'':v).trim().toLowerCase();return v==='1'||v==='true'||v==='ya'||v==='yes'||v==='y';}
  function fmtDur(s){s=parseInt(s,10);if(isNaN(s)||s<0)return '-';if(s<60)return s+'s';return Math.floor(s/60)+'m '+('0'+(s%60)).slice(-2)+'s';}

  // Gaya bubble transkrip disuntik sekali; kelas lain sudah ada di template.
  (function(){
    if(el('dtTxCss'))return;
    var st=document.createElement('style');st.id='dtTxCss';
    st.textContent='.chat-log{display:flex;flex-direction:column;gap:10px;margin:10px 0 4px;}.bubble{max-width:80%;padding:9px 13px;border-radius:14px;font-size:13.5px;line-height:1.5;}.bubble .who{font-size:11px;font-weight:700;opacity:.7;margin-bottom:3px;}.bubble.agen{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px;}.bubble.pel{align-self:flex-start;background:var(--panel-border);color:var(--text-main);border-bottom-left-radius:4px;}';
    document.head.appendChild(st);
  })();

  // Transkrip satu panggilan sebagai bubble interaksi (samakan dengan Pengguna Harian).
  function bubbles(dialog){
    if(!dialog||!dialog.length)return '';
    var order=[];
    function sideFor(who){
      var w=String(who||'').toLowerCase();
      if(w.indexOf('agen')>=0||w.indexOf('agent')>=0||w.indexOf('petugas')>=0||w.indexOf('cs')>=0)return 'agen';
      if(w.indexOf('pelanggan')>=0||w.indexOf('penelepon')>=0||w.indexOf('customer')>=0||w.indexOf('caller')>=0||w.indexOf('nasabah')>=0||w.indexOf('wp')>=0)return 'pel';
      var key=w||'?';var idx=order.indexOf(key);
      if(idx<0){order.push(key);idx=order.length-1;}
      return (idx%2===0)?'pel':'agen';
    }
    return '<div class="chat-log">'+dialog.map(function(t){
      if(typeof t==='string')return '<div class="bubble pel">'+esc(t)+'</div>';
      var who=t.penutur||t.role||t.speaker||t.spk||'';var teks=t.teks||t.text||t.content||t.transcript||t.kalimat||'';
      var side=sideFor(who);var label=who||(side==='agen'?'Agen':'Penelepon');
      return '<div class="bubble '+side+'"><div class="who">'+esc(label)+'</div>'+esc(teks)+'</div>';
    }).join('')+'</div>';
  }
  function renderList(rows){
    var tb=el('dtBody');if(!tb)return;rows=rows||[];
    if(!rows.length){tb.innerHTML='<tr><td colspan="8" style="text-align:center;">Belum ada panggilan pada rentang/filter ini.</td></tr>';return;}
    tb.innerHTML=rows.map(function(r){
      var badges=(r.has_transkrip?'<span class="chip">TX</span>':'')+(r.has_analisis?'<span class="chip">AI</span>':'');
      return '<tr class="row-click" data-sid="'+esc(r.sid)+'"><td>'+esc(r.tanggal||r.day||'-')+'</td><td>'+esc(r.ani||'-')+'</td><td>'+esc(r.agent_name||'-')+'</td><td>'+esc(fmtDur(r.durasi))+'</td><td>'+esc(r.sentiment||'-')+'</td><td>'+esc(r.resolusi||'-')+'</td><td>'+(truthy(r.frustrasi)?'Ya':'-')+'</td><td>'+(badges||'-')+'</td></tr>';
    }).join('');
  }
  function renderDetail(it){
    it=it||{};var h=[];
    h.push('<h3 style="margin:0 0 4px;">Panggilan '+esc(it.ani||'-')+'</h3>');
    h.push('<p class="muted-text">'+esc(it.tanggal||it.day||'-')+' · SID '+esc(it.sid||'-')+'</p>');
    h.push('<div class="kpi-grid" style="margin:12px 0;"><div class="kpi"><div class="n">'+esc(fmtDur(it.durasi))+'</div><div class="l">Durasi</div></div><div class="kpi"><div class="n">'+esc(it.sentiment||'-')+'</div><div class="l">Sentimen</div></div><div class="kpi"><div class="n">'+esc(it.resolusi||'-')+'</div><div class="l">Resolusi</div></div><div class="kpi"><div class="n">'+(truthy(it.frustrasi)?'Ya':'Tidak')+'</div><div class="l">Frustrasi</div></div></div>');
    h.push('<div class="dt-field"><b>Agen:</b> '+esc(it.agent_name||'-')+' &nbsp; <b>DNIS:</b> '+esc(it.dnis||'-')+' &nbsp; <b>Layanan:</b> '+esc(it.jenis_layanan||'-')+' &nbsp; <b>Topik:</b> '+esc(it.topik||'-')+'</div>');
    if(it.ringkasan){h.push('<div class="sec-h" style="margin-top:16px;">Ringkasan</div><p class="muted-text">'+esc(it.ringkasan)+'</p>');}
    var poin=it.poin_penting;
    if(poin&&poin.length){h.push('<div class="sec-h" style="margin-top:16px;">Poin penting</div><ul class="dt-list">'+poin.map(function(x){return '<li>'+esc(typeof x==='string'?x:JSON.stringify(x))+'</li>';}).join('')+'</ul>');}
    var ent=it.entitas;
    if(ent&&ent.length){h.push('<div class="sec-h" style="margin-top:16px;">Entitas</div><div>'+ent.map(function(x){return '<span class="chip">'+esc(typeof x==='string'?x:(x.value||x.nama||x.text||JSON.stringify(x)))+'</span>';}).join('')+'</div>');}
    var a=it.analisis||{};
    var dialog=(a.dialog&&a.dialog.length)?a.dialog:(Array.isArray(it.transkrip)?it.transkrip:null);
    h.push('<div class="sec-h" style="margin-top:16px;">Transkrip</div>');
    if(dialog&&dialog.length)h.push(bubbles(dialog));
    else{
      var plain=it.stt_text||(typeof it.transkrip==='string'?it.transkrip:'');
      if(plain)h.push('<div class="mono">'+esc(plain)+'</div>');
      else h.push('<p class="muted-text">(tidak ada transkrip)</p>');
    }
    var body=el('dtDrawerBody');if(body)body.innerHTML=h.join('');
  }
  function openDetail(sid){
    if(!sid)return;var body=el('dtDrawerBody');if(body)body.innerHTML='<p class="muted-text">Memuat&hellip;</p>';
    var dr=el('dtDrawer');if(dr)dr.classList.add('show');
    api({action:'detail',sid:sid}).then(function(d){
      d=d||{};
      if(!d.ok||!d.interaction){if(body)body.innerHTML='<p class="muted-text">Gagal memuat detail: '+esc(d.error||'tidak ditemukan')+'</p>';return;}
      renderDetail(d.interaction);
    }).catch(function(e){if(body)body.innerHTML='<p class="muted-text">Gagal: '+esc(e)+'</p>';});
  }
  function closeDrawer(){var dr=el('dtDrawer');if(dr)dr.classList.remove('show');}

  var dtState={offset:0,limit:25,total:0};
  function fval(id){var e=el(id);return e?e.value:'';}
  function dtLimit(){var n=parseInt(fval('dt_limit'),10);return (isNaN(n)||n<1)?25:n;}
  function listPayload(offset,withOpts){return {action:'list',date_from:fval('dt_from'),date_to:fval('dt_to'),limit_rows:dtLimit(),offset:offset||0,agent:fval('dt_agent'),sentiment:fval('dt_sentiment'),resolusi:fval('dt_resolusi'),frustrasi:fval('dt_frustrasi'),status:fval('dt_status'),with_options:!!withOpts};}
  function fillSelect(id,vals,ph){var s=el(id);if(!s)return;var cur=s.value;var html='<option value="">'+ph+'</option>';(vals||[]).forEach(function(v){html+='<option value="'+esc(v)+'">'+esc(v)+'</option>';});s.innerHTML=html;s.value=cur;if(s.value!==cur)s.value='';}
  function populateOptions(o){if(!o)return;fillSelect('dt_agent',o.agents,'Semua agen');fillSelect('dt_sentiment',o.sentiments,'Semua sentimen');fillSelect('dt_resolusi',o.resolutions,'Semua resolusi');}
  function updatePager(){
    var pg=el('dtPager');if(!pg)return;
    var total=dtState.total,off=dtState.offset,lim=dtState.limit;
    if(total<=0){pg.style.display='none';return;}
    var from=off+1,to=Math.min(off+lim,total);if(from>total)from=total;
    if(el('dtPageInfo'))el('dtPageInfo').textContent='Menampilkan '+from+'-'+to+' dari '+total;
    if(el('dtPrev'))el('dtPrev').disabled=(off<=0);
    if(el('dtNext'))el('dtNext').disabled=(off+lim>=total);
    pg.style.display=(total>lim)?'flex':'none';
  }
  function loadList(reset,withOpts){
    if(!el('dtBody'))return;
    if(reset)dtState.offset=0;
    dtState.limit=dtLimit();
    setStat('Memuat daftar&hellip;');
    api(listPayload(dtState.offset,withOpts)).then(function(d){
      d=d||{};
      if(!d.ok){setStat('Gagal memuat: '+esc(d.error||'tidak diketahui'),'err');return;}
      hideStat();
      dtState.total=d.total||0;
      if(d.offset!=null)dtState.offset=d.offset;
      if(d.limit!=null)dtState.limit=d.limit;
      if(d.options)populateOptions(d.options);
      renderList(d.interactions||[]);
      var pill=el('dtPill');if(pill)pill.textContent=(d.total||0)+' panggilan';
      updatePager();
    }).catch(function(e){setStat('Gagal: '+e,'err');});
  }
  function init(){
    var t0=iso(new Date()),t30=iso(new Date(Date.now()-29*864e5));
    if(el('dt_to')&&!el('dt_to').value)el('dt_to').value=t0;
    if(el('dt_from')&&!el('dt_from').value)el('dt_from').value=t30;
    if(el('dtLoad'))el('dtLoad').addEventListener('click',function(){loadList(true,true);});
    ['dt_agent','dt_sentiment','dt_resolusi','dt_frustrasi','dt_status'].forEach(function(id){var e=el(id);if(e)e.addEventListener('change',function(){loadList(true,false);});});
    if(el('dt_limit'))el('dt_limit').addEventListener('change',function(){loadList(true,false);});
    if(el('dtPrev'))el('dtPrev').addEventListener('click',function(){if(dtState.offset>0){dtState.offset=Math.max(dtState.offset-dtState.limit,0);loadList(false,false);}});
    if(el('dtNext'))el('dtNext').addEventListener('click',function(){if(dtState.offset+dtState.limit<dtState.total){dtState.offset+=dtState.limit;loadList(false,false);}});
    var tb=el('dtBody');if(tb)tb.addEventListener('click',function(e){var tr=e.target&&e.target.closest?e.target.closest('tr'):null;if(tr&&tr.getAttribute('data-sid'))openDetail(tr.getAttribute('data-sid'));});
    var cl=el('dtDrawerClose');if(cl)cl.addEventListener('click',closeDrawer);
    var dr=el('dtDrawer');if(dr)dr.addEventListener('click',function(e){if(e.target===dr)closeDrawer();});
    document.addEventListener('keydown',function(e){if(e.key==='Escape'||e.keyCode===27)closeDrawer();});
    loadList(true,true);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
