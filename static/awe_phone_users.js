(function(){
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function api(p){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(function(r){return r.json();});}
  function setStat(msg,k){var s=el('puStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function hideStat(){var s=el('puStat');if(s){s.className='status';s.innerHTML='';}}
  function fmtDur(s){s=parseInt(s,10);if(isNaN(s)||s<0)return '-';if(s<60)return s+'s';return Math.floor(s/60)+'m '+('0'+(s%60)).slice(-2)+'s';}

  // Gaya bubble transkrip + meta (disuntik sekali; kelas lain sudah ada di template).
  (function(){
    if(el('puTxCss'))return;
    var st=document.createElement('style');st.id='puTxCss';
    st.textContent='.tx-meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:10px 0;}.tx-box{background:var(--bg-base);border:1px solid var(--panel-border);border-radius:10px;padding:8px 12px;}.tx-box .k{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em;}.tx-box .v{font-size:14px;font-weight:600;color:var(--text-main);margin-top:3px;word-break:break-word;}.chat-log{display:flex;flex-direction:column;gap:10px;margin:10px 0 4px;}.bubble{max-width:80%;padding:9px 13px;border-radius:14px;font-size:13.5px;line-height:1.5;}.bubble .who{font-size:11px;font-weight:700;opacity:.7;margin-bottom:3px;}.bubble.agen{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px;}.bubble.pel{align-self:flex-start;background:var(--panel-border);color:var(--text-main);border-bottom-left-radius:4px;}';
    document.head.appendChild(st);
  })();

  function bars(list,mountId){
    var box=el(mountId);if(!box)return;list=list||[];
    if(!list.length){box.innerHTML='<p class="muted-text">Belum ada data.</p>';return;}
    var max=1;list.forEach(function(x){if((x.value||0)>max)max=x.value;});
    box.innerHTML=list.map(function(x){var pct=Math.round((x.value||0)/max*100);return '<div class="bar-row"><div class="lab" title="'+esc(x.label)+'">'+esc(x.label)+'</div><div class="bar"><span style="width:'+pct+'%;"></span></div><div class="val">'+(x.value||0)+'</div></div>';}).join('');
  }
  function renderKpi(k){
    k=k||{};var box=el('puKpi');if(!box)return;
    var items=[[k.total_callers||0,'Total penelepon'],[k.avg_daily_callers||0,'Rata-rata/hari'],[(k.repeat_callers||0)+' ('+(k.repeat_pct||0)+'%)','Penelepon berulang'],[k.multi_day_callers||0,'Aktif >1 hari'],[k.total_calls||0,'Total panggilan'],[fmtDur(k.avg_dur||0),'Rata-rata durasi'],[(k.frustrasi_pct||0)+'%','Panggilan frustrasi']];
    box.innerHTML=items.map(function(it){return '<div class="kpi"><div class="n">'+esc(it[0])+'</div><div class="l">'+esc(it[1])+'</div></div>';}).join('');
  }
  function renderCallers(rows){
    var tb=el('puBody');if(!tb)return;rows=rows||[];
    if(!rows.length){tb.innerHTML='<tr><td colspan="7" style="text-align:center;">Belum ada penelepon pada rentang ini.</td></tr>';return;}
    tb.innerHTML=rows.slice(0,200).map(function(r){return '<tr class="row-click" data-ani="'+esc(r.ani)+'"><td>'+esc(r.ani)+'</td><td>'+(r.calls||0)+'</td><td>'+(r.days||0)+'</td><td>'+esc(r.top_theme||'-')+'</td><td>'+esc(r.sentiment||'-')+'</td><td>'+esc(r.resolusi||'-')+'</td><td>'+esc(r.agent||'-')+'</td></tr>';}).join('');
    var trs=tb.querySelectorAll('tr.row-click');
    for(var i=0;i<trs.length;i++){trs[i].addEventListener('click',function(){loadConvs(this.getAttribute('data-ani'));});}
  }
  function render(d){
    var k=d.kpi||{};
    var pill=el('puPill');if(pill)pill.textContent=(k.total_callers||0)+' penelepon';
    renderKpi(k);
    bars(d.freq_dist,'puFreq');
    renderCallers(d.callers);
  }
  function load(){
    setStat('Memuat pengguna harian&hellip;');
    api({action:'daily_users',date_from:(el('pu_from')?el('pu_from').value:''),date_to:(el('pu_to')?el('pu_to').value:''),limit_rows:2000}).then(function(d){
      if(!d.ok){setStat('Gagal memuat: '+esc(d.error||'tidak diketahui'),'err');return;}
      hideStat();render(d);
    }).catch(function(e){setStat('Gagal: '+e,'err');});
  }

  // ---- Transkrip satu panggilan sebagai bubble interaksi ----
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
      var who=t.penutur||t.role||t.speaker||t.spk||'';var teks=t.teks||t.text||t.content||'';
      var side=sideFor(who);var label=who||(side==='agen'?'Agen':'Penelepon');
      return '<div class="bubble '+side+'"><div class="who">'+esc(label)+'</div>'+esc(teks)+'</div>';
    }).join('')+'</div>';
  }
  function tbox(k,v){return '<div class="tx-box"><div class="k">'+esc(k)+'</div><div class="v">'+esc(v||'-')+'</div></div>';}
  function renderCallDetail(it){
    var host=el('puCallDetail');if(!host)return;
    var a=it.analisis||{};
    var topik=it.topik||a.topik||'',jenis=it.jenis_layanan||a.jenis_layanan||'';
    var sentimen=it.sentiment||a.sentimen||a.sentiment||'',resolusi=it.resolusi||a.resolusi||'';
    var ringkasan=it.ringkasan||a.ringkasan||'';
    var dialog=(a.dialog&&a.dialog.length)?a.dialog:(it.transkrip||[]);
    var t=String(it.tanggal||'').replace('T',' ').slice(0,16);
    var html='<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;"><div class="sec-h" style="margin-top:0;">Transkrip &mdash; '+esc(t)+' <span class="code-inline">'+esc(it.sid||'')+'</span></div><button class="btn-modern btn-outline" id="puCallClose">Tutup transkrip</button></div>';
    html+='<div class="tx-meta">'+tbox('Durasi',fmtDur(it.durasi))+tbox('Agen',it.agent_name)+tbox('Topik',topik)+tbox('Jenis layanan',jenis)+tbox('Sentimen',sentimen)+tbox('Resolusi',resolusi)+'</div>';
    if(ringkasan){html+='<div class="sec-h">Ringkasan</div><p class="muted-text">'+esc(ringkasan)+'</p>';}
    html+='<div class="sec-h">Percakapan</div>';
    if(dialog&&dialog.length){html+=bubbles(dialog);}
    else if(it.stt_text){html+='<p class="muted-text">'+esc(it.stt_text)+'</p>';}
    else{html+='<p class="muted-text">Belum ada transkrip untuk panggilan ini.</p>';}
    host.innerHTML=html;host.style.display='block';
    var c=el('puCallClose');if(c)c.addEventListener('click',function(){host.style.display='none';host.innerHTML='';});
    host.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  function loadConvDetail(sid){
    if(!sid)return;var host=el('puCallDetail');if(!host)return;
    host.style.display='block';host.innerHTML='<p class="muted-text">Memuat transkrip&hellip;</p>';
    api({action:'detail',sid:sid}).then(function(d){
      if(!d.ok||!d.interaction){host.innerHTML='<p class="muted-text">Gagal memuat transkrip: '+esc(d.error||'')+'</p>';return;}
      renderCallDetail(d.interaction);
    }).catch(function(e){host.innerHTML='<p class="muted-text">Gagal: '+e+'</p>';});
  }

  function loadConvs(ani){
    if(!ani)return;var p=el('puConvPanel');if(!p)return;
    p.style.display='block';p.innerHTML='<p class="muted-text">Memuat panggilan '+esc(ani)+'&hellip;</p>';
    api({action:'daily_convs',ani:ani,date_from:(el('pu_from')?el('pu_from').value:''),date_to:(el('pu_to')?el('pu_to').value:''),limit_rows:500}).then(function(d){
      if(!d.ok){p.innerHTML='<p class="muted-text">Gagal: '+esc(d.error||'')+'</p>';return;}
      var rows=d.conversations||[];
      var head='<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;"><div class="sec-h" style="margin-top:0;">Panggilan dari <span class="code-inline">'+esc(ani)+'</span> ('+rows.length+') &mdash; klik satu baris untuk transkrip</div><button class="btn-modern btn-outline" id="puConvClose">Tutup</button></div>';
      if(!rows.length){p.innerHTML=head+'<p class="muted-text">Tidak ada panggilan.</p>';}
      else{
        var body=rows.map(function(r){var t=String(r.tanggal||'').replace('T',' ').slice(0,16);var judul=esc(r.theme||'-')+(r.ringkasan?(' &mdash; <span class="muted-text">'+esc(String(r.ringkasan).slice(0,120))+'</span>'):'');return '<tr class="row-click" data-sid="'+esc(r.sid||'')+'"><td>'+esc(t)+'</td><td>'+fmtDur(r.durasi)+'</td><td>'+judul+'</td><td>'+esc(r.sentiment||'-')+'</td><td>'+esc(r.resolusi||'-')+'</td><td>'+(r.analyzed?'&#10003;':'-')+'</td></tr>';}).join('');
        p.innerHTML=head+'<div style="overflow-x:auto;"><table class="table-modern"><thead><tr><th>Waktu</th><th>Durasi</th><th>Tema / Ringkasan</th><th>Sentimen</th><th>Resolusi</th><th>Analisis</th></tr></thead><tbody>'+body+'</tbody></table></div><div id="puCallDetail" style="display:none;margin-top:16px;"></div>';
        var trs=p.querySelectorAll('tr.row-click');
        for(var i=0;i<trs.length;i++){trs[i].addEventListener('click',function(){loadConvDetail(this.getAttribute('data-sid'));});}
      }
      var c=el('puConvClose');if(c)c.addEventListener('click',function(){p.style.display='none';p.innerHTML='';});
      p.scrollIntoView({behavior:'smooth',block:'nearest'});
    }).catch(function(e){p.innerHTML='<p class="muted-text">Gagal: '+e+'</p>';});
  }

  function init(){
    var t0=iso(new Date()),t30=iso(new Date(Date.now()-29*864e5));
    if(el('pu_to')&&!el('pu_to').value)el('pu_to').value=t0;
    if(el('pu_from')&&!el('pu_from').value)el('pu_from').value=t30;
    if(el('puLoad'))el('puLoad').addEventListener('click',load);
    load();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
