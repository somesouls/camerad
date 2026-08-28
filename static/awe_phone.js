(function(){
// Kelola Data Phone - semua aksi via POST /api/awe/phone/probe (dispatch by action).
function el(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
function setStat(id,msg,k){var s=el(id);if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
function hideStat(id){var s=el(id);if(s){s.className='status';s.innerHTML='';}}
function api(payload){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(function(r){return r.json();});}

// Nilai awal tanggal: tarik = kemarin; status = 14 hari terakhir.
(function(){var y=iso(new Date(Date.now()-864e5));if(el('t_from')&&!el('t_from').value)el('t_from').value=y;if(el('t_to')&&!el('t_to').value)el('t_to').value=y;if(el('c_to'))el('c_to').value=iso(new Date());if(el('c_from'))el('c_from').value=iso(new Date(Date.now()-13*864e5));})();

// ---- Status / cakupan ----
function renderCov(d){
  var st=d.stats||{};
  el('covPill').textContent=(st.total||0)+' interaksi';
  el('covInfo').innerHTML='Total tersimpan: <b>'+(st.total||0)+'</b> - sudah transkrip: <b>'+(st.transkrip||0)+'</b> - sudah analisis: <b>'+(st.analisis||0)+'</b> - rentang data: <b>'+esc(st.date_min||'-')+'</b> s/d <b>'+esc(st.date_max||'-')+'</b>';
  var rows=d.coverage||[],tb=el('covBody');
  if(!rows.length){tb.innerHTML='<tr><td colspan="5" style="text-align:center;">Belum ada data pada rentang ini.</td></tr>';return;}
  tb.innerHTML=rows.map(function(r){return '<tr><td>'+esc(r.day)+'</td><td>'+(r.n_total||0)+'</td><td>'+(r.n_audio||0)+'</td><td>'+(r.n_transkrip||0)+'</td><td>'+(r.n_analisis||0)+'</td></tr>';}).join('');
}
function loadCov(){
  setStat('covStat','Memuat status...');
  api({action:'coverage',date_from:(el('c_from')?el('c_from').value:''),date_to:(el('c_to')?el('c_to').value:'')}).then(function(d){
    if(!d.ok){setStat('covStat','Gagal memuat: '+esc(d.error||''),'err');return;}
    hideStat('covStat');renderCov(d);
  }).catch(function(e){setStat('covStat','Gagal: '+e,'err');});
}
if(el('covRefresh'))el('covRefresh').addEventListener('click',loadCov);

// ---- Poll job latar (dipakai tarik & analisis) ----
function pollJob(job,statId,btn,onDone){
  api({action:'job_fetch',job:job}).then(function(d){
    if(d.pending){var p=d.progress||{};setStat(statId,esc(p.message||'Berjalan...'));setTimeout(function(){pollJob(job,statId,btn,onDone);},3000);return;}
    if(btn)btn.disabled=false;
    if(!d.ok){setStat(statId,'Gagal: '+esc(d.error||'')+(d.need_login?' (periksa kredensial)':''),'err');return;}
    onDone(d);loadCov();
  }).catch(function(e){setStat(statId,'Gagal: '+e,'err');if(btn)btn.disabled=false;});
}

// ---- Tahap 1: Tarik (kredensial .env di server) ----
el('tPullBtn').addEventListener('click',function(){
  var df=el('t_from').value,dt=el('t_to').value;
  if(!df||!dt){setStat('tStat','Isi rentang tanggal dulu.','err');return;}
  var lim=parseInt(el('t_limit').value,10)||25;
  el('tPullBtn').disabled=true;setStat('tStat','Login (kredensial .env) & menarik data telepon...');
  api({action:'pull_start',date_from:df,date_to:dt,limit_rows:lim}).then(function(d){
    if(!d.ok){setStat('tStat','Gagal: '+esc(d.error||'')+(d.need_login?' (set AVAYA_USERNAME/AVAYA_PASSWORD di .env)':''),'err');el('tPullBtn').disabled=false;return;}
    pollJob(d.job,'tStat',el('tPullBtn'),function(r){setStat('tStat',esc(r.message||'Selesai.'),'ok');});
  }).catch(function(e){setStat('tStat','Gagal: '+e,'err');el('tPullBtn').disabled=false;});
});

// ---- Tahap 2: Analisis (lambat, latar belakang) ----
el('aRunBtn').addEventListener('click',function(){
  var day=el('a_day').value,lim=parseInt(el('a_limit').value,10)||25,md=parseInt(el('a_mindur').value,10);
  if(isNaN(md))md=3;
  el('aRunBtn').disabled=true;setStat('aStat','Menjalankan STT + analisis LLM... (bisa lama, ~1 mnt/panggilan)');
  api({action:'analyze_start',day:day,limit_rows:lim,min_durasi:md}).then(function(d){
    if(!d.ok){setStat('aStat','Gagal: '+esc(d.error||''),'err');el('aRunBtn').disabled=false;return;}
    pollJob(d.job,'aStat',el('aRunBtn'),function(r){setStat('aStat',esc(r.message||'Selesai.'),'ok');});
  }).catch(function(e){setStat('aStat','Gagal: '+e,'err');el('aRunBtn').disabled=false;});
});

loadCov();
})();
