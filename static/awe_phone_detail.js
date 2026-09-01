(function(){
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function api(p){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(function(r){return r.json();});}
  function setStat(msg,k){var s=el('dtStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function hideStat(){var s=el('dtStat');if(s){s.className='status';s.innerHTML='';}}
  function truthy(v){v=String(v==null?'':v).trim().toLowerCase();return v==='1'||v==='true'||v==='ya'||v==='yes'||v==='y';}
  function fmtDur(s){s=parseInt(s,10);if(isNaN(s)||s<0)return '-';if(s<60)return s+'s';return Math.floor(s/60)+'m '+('0'+(s%60)).slice(-2)+'s';}
  function transcriptHtml(it){
    if(it.stt_text)return esc(it.stt_text);
    var t=it.transkrip;if(!t)return '';
    if(typeof t==='string')return esc(t);
    if(t.length){return t.map(function(seg){if(typeof seg==='string')return esc(seg);var spk=seg.speaker||seg.spk||seg.role||seg.channel||'';var txt=seg.text||seg.kalimat||seg.content||seg.transcript||'';return (spk?('<b>['+esc(String(spk))+']</b> '):'')+esc(String(txt));}).join('<br>');}
    return esc(JSON.stringify(t));
  }
  function renderList(rows){
    var tb=el('dtBody');if(!tb)return;rows=rows||[];
    if(!rows.length){tb.innerHTML='<tr><td colspan="8" style="text-align:center;">Belum ada panggilan pada rentang ini.</td></tr>';return;}
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
    h.push('<div class="sec-h" style="margin-top:16px;">Transkrip</div><div class="mono">'+(transcriptHtml(it)||'(tidak ada transkrip)')+'</div>');
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
  function load(){
    setStat('Memuat daftar&hellip;');
    var df=(el('dt_from')?el('dt_from').value:''),dt=(el('dt_to')?el('dt_to').value:'');
    api({action:'list',date_from:df,date_to:dt,limit_rows:500}).then(function(d){
      d=d||{};
      if(!d.ok){setStat('Gagal memuat: '+esc(d.error||'tidak diketahui'),'err');return;}
      renderList(d.interactions||[]);
      var pill=el('dtPill');if(pill)pill.textContent=(d.total||0)+' panggilan';
      if((d.total||0)>=500)setStat('Menampilkan 500 terbaru; persempit rentang untuk melihat lainnya.','ok');
      else hideStat();
    }).catch(function(e){setStat('Gagal: '+e,'err');});
  }
  function init(){
    var t0=iso(new Date()),t30=iso(new Date(Date.now()-29*864e5));
    if(el('dt_to')&&!el('dt_to').value)el('dt_to').value=t0;
    if(el('dt_from')&&!el('dt_from').value)el('dt_from').value=t30;
    if(el('dtLoad'))el('dtLoad').addEventListener('click',load);
    var tb=el('dtBody');if(tb)tb.addEventListener('click',function(e){var tr=e.target&&e.target.closest?e.target.closest('tr'):null;if(tr&&tr.getAttribute('data-sid'))openDetail(tr.getAttribute('data-sid'));});
    var cl=el('dtDrawerClose');if(cl)cl.addEventListener('click',closeDrawer);
    var dr=el('dtDrawer');if(dr)dr.addEventListener('click',function(e){if(e.target===dr)closeDrawer();});
    load();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
