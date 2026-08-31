(function(){
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function api(p){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(function(r){return r.json();});}
  function setStat(msg,k){var s=el('pdStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function hideStat(){var s=el('pdStat');if(s){s.className='status';s.innerHTML='';}}
  function fmtDur(s){s=parseInt(s,10);if(isNaN(s)||s<0)return '-';if(s<60)return s+'s';return Math.floor(s/60)+'m '+('0'+(s%60)).slice(-2)+'s';}

  (function(){
    if(el('phoneDailyCss'))return;
    var st=document.createElement('style');st.id='phoneDailyCss';
    st.textContent='#pdCard .kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:6px 0 4px;}#pdCard .kpi{background:var(--bg-base);border:1px solid var(--panel-border);border-radius:12px;padding:12px 14px;}#pdCard .kpi .n{font-size:21px;font-weight:800;color:var(--text-main);}#pdCard .kpi .l{font-size:12px;color:var(--text-muted);margin-top:2px;}#pdCard .bar-row{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:13px;}#pdCard .bar-row .lab{width:40%;color:var(--text-main);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}#pdCard .bar-row .bar{flex:1;height:13px;border-radius:7px;background:var(--panel-border);overflow:hidden;}#pdCard .bar-row .bar span{display:block;height:100%;background:var(--accent);}#pdCard .bar-row .val{width:42px;text-align:right;color:var(--text-muted);}#pdCard .cols2{display:grid;grid-template-columns:1fr 1fr;gap:20px;}@media(max-width:720px){#pdCard .cols2{grid-template-columns:1fr;}}#pdCard .row-click{cursor:pointer;}#pdCard .row-click:hover td{background:var(--accent-glow);}';
    document.head.appendChild(st);
  })();

  var card=document.createElement('section');
  card.className='c-card';card.id='pdCard';
  card.innerHTML='<div class="stage-head"><h2 class="stage-title">Pengguna Harian &mdash; Telepon</h2><span class="pill" id="pdPill">&mdash;</span></div><p class="use-note">&#8505;&#65039; Melihat siapa yang paling sering menghubungi Kring Pajak, dikelompokkan per <b>nomor telepon penelepon (ANI)</b>, dengan <b>deteksi tema otomatis</b> dari hasil analisis. Klik satu nomor untuk melihat seluruh panggilannya.</p><div class="form-row"><div class="field"><label>Dari tanggal</label><input type="date" id="pd_from"></div><div class="field"><label>Sampai tanggal</label><input type="date" id="pd_to"></div><div class="field"><button class="btn-modern" id="pdLoad">Muat</button></div></div><div class="kpi-grid" id="pdKpi"></div><div class="cols2" style="margin-top:14px;"><div><div class="sec-h">Tema teratas (otomatis)</div><div id="pdThemes"><p class="muted-text">&mdash;</p></div></div><div><div class="sec-h">Frekuensi menghubungi</div><div id="pdFreq"><p class="muted-text">&mdash;</p></div><div class="sec-h" style="margin-top:14px;">Sentimen</div><div id="pdSent"><p class="muted-text">&mdash;</p></div></div></div><div class="sec-h" style="margin-top:16px;">Penelepon teratas</div><div style="overflow-x:auto;"><table class="table-modern"><thead><tr><th>No. Penelepon</th><th>Panggilan</th><th>Hari</th><th>Tema teratas</th><th>Sentimen</th><th>Resolusi</th><th>Agen tersering</th></tr></thead><tbody id="pdBody"><tr><td colspan="7" style="text-align:center;">&mdash;</td></tr></tbody></table></div><div class="status" id="pdStat"></div><div id="pdConvPanel" style="display:none;margin-top:16px;"></div>';

  // --- Kartu Tarik Otomatis (auto-pull) ---
  var paCard=document.createElement('section');
  paCard.className='c-card';paCard.id='paCard';
  paCard.innerHTML='<div class="stage-head"><h2 class="stage-title">Tarik Otomatis (Auto-pull) &mdash; Telepon</h2><span class="pill" id="paPill">&mdash;</span></div><p class="use-note">&#8505;&#65039; Menarik interaksi telepon <b>H-1 otomatis tiap hari</b> (seperti Livechat/Dialogflow), memakai kredensial <b>.env</b> (AVAYA_USERNAME/PASSWORD). Aktifkan penjadwal dengan <span class="code-inline">AWE_PHONE_SCHEDULER=1</span>. Analisis STT+LLM opsional via <span class="code-inline">AWE_PHONE_INGEST_ANALYZE=1</span> (lambat). Tombol di bawah menarik sekarang (latar belakang).</p><div id="paInfo"><p class="muted-text">Memuat status&hellip;</p></div><div class="form-row" style="margin-top:10px;"><div class="field"><label>Dari tanggal (opsional)</label><input type="date" id="pa_from"></div><div class="field"><label>Sampai tanggal (opsional)</label><input type="date" id="pa_to"></div><div class="field"><button class="btn-modern" id="paNow">Tarik Sekarang</button></div><div class="field"><button class="btn-modern btn-outline" id="paRefresh">Segarkan status</button></div></div><div class="status" id="paStat"></div>';

  function mount(){
    var ph=document.querySelector('.page-header');
    if(ph&&ph.parentNode){ph.parentNode.insertBefore(card,ph.nextSibling);return;}
    var c=document.querySelector('.c-card');
    if(c&&c.parentNode){c.parentNode.insertBefore(card,c);return;}
    document.body.appendChild(card);
  }

  function bars(list,mountId){
    var box=el(mountId);if(!box)return;
    list=list||[];
    if(!list.length){box.innerHTML='<p class="muted-text">Belum ada data.</p>';return;}
    var max=1;list.forEach(function(x){if((x.value||0)>max)max=x.value;});
    box.innerHTML=list.map(function(x){
      var pct=Math.round((x.value||0)/max*100);
      return '<div class="bar-row"><div class="lab" title="'+esc(x.label)+'">'+esc(x.label)+'</div><div class="bar"><span style="width:'+pct+'%;"></span></div><div class="val">'+(x.value||0)+'</div></div>';
    }).join('');
  }

  function renderKpi(k){
    k=k||{};
    var items=[[k.total_callers||0,'Total penelepon'],[k.avg_daily_callers||0,'Rata-rata/hari'],[(k.repeat_callers||0)+' ('+(k.repeat_pct||0)+'%)','Penelepon berulang'],[k.total_calls||0,'Total panggilan'],[(k.analyzed_calls||0)+' ('+(k.analyzed_pct||0)+'%)','Sudah dianalisis'],[fmtDur(k.avg_dur||0),'Rata-rata durasi'],[(k.frustrasi_pct||0)+'%','Panggilan frustrasi']];
    el('pdKpi').innerHTML=items.map(function(it){return '<div class="kpi"><div class="n">'+esc(it[0])+'</div><div class="l">'+esc(it[1])+'</div></div>';}).join('');
  }

  function renderCallers(rows){
    var tb=el('pdBody');if(!tb)return;
    rows=rows||[];
    if(!rows.length){tb.innerHTML='<tr><td colspan="7" style="text-align:center;">Belum ada penelepon pada rentang ini.</td></tr>';return;}
    tb.innerHTML=rows.slice(0,100).map(function(r){
      return '<tr class="row-click" data-ani="'+esc(r.ani)+'"><td>'+esc(r.ani)+'</td><td>'+(r.calls||0)+'</td><td>'+(r.days||0)+'</td><td>'+esc(r.top_theme||'-')+'</td><td>'+esc(r.sentiment||'-')+'</td><td>'+esc(r.resolusi||'-')+'</td><td>'+esc(r.agent||'-')+'</td></tr>';
    }).join('');
    var trs=tb.querySelectorAll('tr.row-click');
    for(var i=0;i<trs.length;i++){trs[i].addEventListener('click',function(){loadConvs(this.getAttribute('data-ani'));});}
  }

  function render(d){
    var k=d.kpi||{};
    el('pdPill').textContent=(k.total_callers||0)+' penelepon';
    renderKpi(k);
    bars(d.themes,'pdThemes');
    bars(d.freq_dist,'pdFreq');
    var s=d.sentiment||{};
    bars([{label:'Positif',value:s.Positif||0},{label:'Netral',value:s.Netral||0},{label:'Negatif',value:s.Negatif||0},{label:'Tidak diketahui',value:s['Tidak diketahui']||0}],'pdSent');
    renderCallers(d.callers);
  }

  function load(){
    setStat('Memuat pengguna harian&hellip;');
    api({action:'daily_users',date_from:(el('pd_from')?el('pd_from').value:''),date_to:(el('pd_to')?el('pd_to').value:''),limit_rows:2000}).then(function(d){
      if(!d.ok){setStat('Gagal memuat: '+esc(d.error||''),'err');return;}
      hideStat();render(d);
    }).catch(function(e){setStat('Gagal: '+e,'err');});
  }

  function loadConvs(ani){
    if(!ani)return;
    var p=el('pdConvPanel');if(!p)return;
    p.style.display='block';p.innerHTML='<p class="muted-text">Memuat panggilan '+esc(ani)+'&hellip;</p>';
    api({action:'daily_convs',ani:ani,date_from:(el('pd_from')?el('pd_from').value:''),date_to:(el('pd_to')?el('pd_to').value:''),limit_rows:500}).then(function(d){
      if(!d.ok){p.innerHTML='<p class="muted-text">Gagal: '+esc(d.error||'')+'</p>';return;}
      var rows=d.conversations||[];
      var head='<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;"><div class="sec-h" style="margin-top:0;">Panggilan dari <span class="code-inline">'+esc(ani)+'</span> ('+rows.length+')</div><button class="btn-modern btn-outline" id="pdConvClose">Tutup</button></div>';
      if(!rows.length){p.innerHTML=head+'<p class="muted-text">Tidak ada panggilan.</p>';}
      else{
        var body=rows.map(function(r){
          var t=String(r.tanggal||'').replace('T',' ').slice(0,16);
          var judul=esc(r.theme||'-')+(r.ringkasan?(' &mdash; <span class="muted-text">'+esc(String(r.ringkasan).slice(0,120))+'</span>'):'');
          return '<tr><td>'+esc(t)+'</td><td>'+fmtDur(r.durasi)+'</td><td>'+judul+'</td><td>'+esc(r.sentiment||'-')+'</td><td>'+esc(r.resolusi||'-')+'</td><td>'+(r.analyzed?'&#10003;':'-')+'</td></tr>';
        }).join('');
        p.innerHTML=head+'<div style="overflow-x:auto;"><table class="table-modern"><thead><tr><th>Waktu</th><th>Durasi</th><th>Tema / Ringkasan</th><th>Sentimen</th><th>Resolusi</th><th>Analisis</th></tr></thead><tbody>'+body+'</tbody></table></div>';
      }
      var c=el('pdConvClose');if(c)c.addEventListener('click',function(){p.style.display='none';p.innerHTML='';});
      p.scrollIntoView({behavior:'smooth',block:'nearest'});
    }).catch(function(e){p.innerHTML='<p class="muted-text">Gagal: '+e+'</p>';});
  }

  // --- Auto-pull: status + tarik sekarang ---
  function apJson(url,opt){return fetch(url,opt).then(function(r){return r.json();});}
  function paSet(msg,k){var s=el('paStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function paHide(){var s=el('paStat');if(s){s.className='status';s.innerHTML='';}}
  function paRow(lab,val,color){
    return '<div style="display:flex;justify-content:space-between;gap:12px;padding:4px 0;border-bottom:1px solid var(--panel-border);font-size:13px;"><span style="color:var(--text-muted);">'+esc(lab)+'</span><span style="color:'+(color||'var(--text-main)')+';font-weight:600;text-align:right;">'+val+'</span></div>';
  }
  function paRender(d){
    d=d||{};
    var pill=el('paPill');
    if(pill){pill.textContent=d.enabled?'Penjadwal AKTIF':'Penjadwal nonaktif';}
    var last=d.last||{};
    var mm=('0'+esc(d.minute)).slice(-2);
    var rows=[];
    rows.push(paRow('Penjadwal harian',d.enabled?('aktif, jam '+esc(d.hour)+':'+mm+' WIB'):'nonaktif (set AWE_PHONE_SCHEDULER=1)',d.enabled?'var(--accent)':'var(--text-muted)'));
    rows.push(paRow('Kredensial .env',d.configured?'sudah diset':'BELUM diset',d.configured?'var(--text-main)':'#d9534f'));
    rows.push(paRow('Cap tarik / hari',esc(d.limit||'-')));
    rows.push(paRow('Analisis STT+LLM',d.analyze?'otomatis setelah tarik':'manual (di menu analisis)'));
    if(d.running){rows.push(paRow('Status','sedang menarik&hellip;','var(--accent)'));}
    if(last&&(last.started_at||last.message||last.error)){
      rows.push(paRow('Tarik terakhir',(esc(last.finished_at||last.started_at||'')+' '+(last.ok?'&#10003;':'&#10007;')),last.ok?'var(--text-main)':'#d9534f'));
      rows.push('<p class="muted-text" style="margin:6px 0 0;font-size:12.5px;">'+esc(last.message||last.error||'-')+' <span style="opacity:.7;">('+esc(last.trigger||'')+(last.range?(', '+esc(last.range)):'')+')</span></p>');
    }
    el('paInfo').innerHTML=rows.join('');
  }
  function paLoad(){
    apJson('/api/awe/phone/autopull/status').then(function(d){
      if(!d.ok){paSet('Gagal memuat status: '+esc(d.error||''),'err');return;}
      paHide();paRender(d);
    }).catch(function(e){paSet('Gagal: '+e,'err');});
  }
  function paNow(){
    paSet('Memulai tarik otomatis&hellip;');
    apJson('/api/awe/phone/autopull/now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({date_from:(el('pa_from')?el('pa_from').value:''),date_to:(el('pa_to')?el('pa_to').value:'')})}).then(function(d){
      if(!d.ok){paSet('Gagal: '+esc(d.error||''),'err');return;}
      paSet('&#10003; '+esc(d.message||'Dimulai.'),'ok');
      setTimeout(paLoad,1500);setTimeout(paLoad,8000);
    }).catch(function(e){paSet('Gagal: '+e,'err');});
  }

  function init(){
    mount();
    if(card&&card.parentNode){card.parentNode.insertBefore(paCard,card);}
    else{document.body.insertBefore(paCard,document.body.firstChild);}
    var t0=iso(new Date()),t30=iso(new Date(Date.now()-29*864e5));
    if(el('pd_to')&&!el('pd_to').value)el('pd_to').value=t0;
    if(el('pd_from')&&!el('pd_from').value)el('pd_from').value=t30;
    if(el('pdLoad'))el('pdLoad').addEventListener('click',load);
    if(el('paNow'))el('paNow').addEventListener('click',paNow);
    if(el('paRefresh'))el('paRefresh').addEventListener('click',paLoad);
    paLoad();
    load();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
