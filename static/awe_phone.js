(function(){
// Kelola Data Phone - semua aksi via POST /api/awe/phone/probe (dispatch by action).
function el(id){return document.getElementById(id);}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
function setStat(id,msg,k){var s=el(id);if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
function hideStat(id){var s=el(id);if(s){s.className='status';s.innerHTML='';}}
function api(payload){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(function(r){return r.json();});}

// Nilai awal tanggal: tarik = kemarin; status & daftar = 14 hari terakhir.
(function(){var y=iso(new Date(Date.now()-864e5));if(el('t_from')&&!el('t_from').value)el('t_from').value=y;if(el('t_to')&&!el('t_to').value)el('t_to').value=y;var t0=iso(new Date()),t14=iso(new Date(Date.now()-13*864e5));if(el('c_to'))el('c_to').value=t0;if(el('c_from'))el('c_from').value=t14;if(el('l_to'))el('l_to').value=t0;if(el('l_from'))el('l_from').value=t14;})();

// Suntik gaya detail (bubble/meta/tag) sekali - agar awe_phone.html tetap ramping.
(function(){if(document.getElementById('awePhoneCss'))return;var st=document.createElement('style');st.id='awePhoneCss';st.textContent='.row-click{cursor:pointer;}.row-click:hover td{background:var(--accent-glow);}.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px;}.meta-box{background:var(--bg-base);border:1px solid var(--panel-border);border-radius:10px;padding:10px 12px;}.meta-box .k{font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em;}.meta-box .v{font-size:14px;font-weight:600;color:var(--text-main);margin-top:3px;word-break:break-word;}.sec-h{font-size:14px;font-weight:700;color:var(--text-main);margin:16px 0 8px;}.tag{display:inline-block;font-size:12px;padding:2px 10px;border-radius:99px;background:var(--c-blue);color:var(--c-blue-txt);margin:2px 4px 2px 0;}.chat-log{display:flex;flex-direction:column;gap:10px;margin:8px 0 4px;}.bubble{max-width:80%;padding:9px 13px;border-radius:14px;font-size:13.5px;line-height:1.5;}.bubble .who{font-size:11px;font-weight:700;opacity:.7;margin-bottom:3px;}.bubble.agen{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px;}.bubble.pel{align-self:flex-start;background:var(--panel-border);color:var(--text-main);border-bottom-left-radius:4px;}';document.head.appendChild(st);})();

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
    onDone(d);loadCov();loadList();
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

// ---- Daftar & Detail Interaksi (Fase 5) ----
function fmtDur(s){s=parseInt(s,10);if(isNaN(s)||s<0)return '-';if(s<60)return s+'s';return Math.floor(s/60)+'m '+('0'+(s%60)).slice(-2)+'s';}
function fmtTime(r){var t=String(r.tanggal||r.day||'');return esc(t.replace('T',' ').slice(0,16));}
function yn(v){if(v===true||v==='true'||v==='True'||v===1||v==='1')return 'Ya';if(v==null||v===''||v==='false'||v==='False'||v===0||v==='0')return 'Tidak';return esc(String(v));}
function tags(arr){arr=arr||[];if(!arr.length)return '<span class="muted-text">-</span>';return arr.map(function(x){return '<span class="tag">'+esc(x)+'</span>';}).join('');}
function renderList(d){
  var tb=el('lBody');if(!tb)return;
  var rows=d.interactions||[];
  if(el('lPill'))el('lPill').textContent=(d.total||rows.length||0)+' baris';
  if(!rows.length){tb.innerHTML='<tr><td colspan="5" style="text-align:center;">Belum ada interaksi pada rentang ini.</td></tr>';return;}
  tb.innerHTML=rows.map(function(r){
    var judul=esc(r.topik||r.ringkasan||'(belum dianalisis)');
    var an=r.has_analisis?'&#10003; analisis':(r.has_transkrip?'transkrip':'-');
    return '<tr class="row-click" data-sid="'+esc(r.sid)+'"><td>'+fmtTime(r)+'</td><td>'+fmtDur(r.durasi)+'</td><td>'+judul+'</td><td>'+esc(r.sentiment||'-')+'</td><td>'+an+'</td></tr>';
  }).join('');
  var trs=tb.querySelectorAll('tr.row-click');
  for(var i=0;i<trs.length;i++){trs[i].addEventListener('click',function(){loadDetail(this.getAttribute('data-sid'));});}
}
function loadList(){
  if(!el('lBody'))return;
  setStat('lStat','Memuat daftar...');
  api({action:'list',date_from:(el('l_from')?el('l_from').value:''),date_to:(el('l_to')?el('l_to').value:''),limit_rows:((el('l_limit')&&parseInt(el('l_limit').value,10))||100)}).then(function(d){
    if(!d.ok){setStat('lStat','Gagal memuat daftar: '+esc(d.error||''),'err');return;}
    hideStat('lStat');renderList(d);
  }).catch(function(e){setStat('lStat','Gagal: '+e,'err');});
}
function bubbles(dialog){
  if(!dialog||!dialog.length)return '';
  return '<div class="chat-log">'+dialog.map(function(t){
    var who=t.penutur||t.role||t.speaker||'';var teks=t.teks||t.text||t.content||'';
    var agen=String(who).toLowerCase().indexOf('agen')>=0;
    return '<div class="bubble '+(agen?'agen':'pel')+'"><div class="who">'+esc(who||(agen?'Agen':'Penelepon'))+'</div>'+esc(teks)+'</div>';
  }).join('')+'</div>';
}
function renderDetail(it){
  var p=el('dPanel');if(!p)return;
  var a=it.analisis||{};
  var topik=it.topik||a.topik||'',jenis=it.jenis_layanan||a.jenis_layanan||'';
  var sentimen=it.sentiment||a.sentimen||a.sentiment||'',emosi=it.emotion||a.emosi||a.emotion||'';
  var resolusi=it.resolusi||a.resolusi||'',ringkasan=it.ringkasan||a.ringkasan||'';
  var frust=(it.frustrasi!=null?it.frustrasi:a.frustrasi);
  var ent=it.entitas||a.entitas||{};var poin=it.poin_penting||a.poin_penting||[];
  var catatan=(a.catatan_kualitas!=null?a.catatan_kualitas:(it.catatan_kualitas||''));
  var dialog=(a.dialog&&a.dialog.length)?a.dialog:(it.transkrip||[]);
  var pelanggan=it.customer||it.ani||'-';
  function box(k,v){return '<div class="meta-box"><div class="k">'+k+'</div><div class="v">'+esc(v||'-')+'</div></div>';}
  var html='<div class="detail-wrap"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;"><div class="sec-h" style="margin-top:0;">Detail interaksi <span class="code-inline">'+esc(it.sid||'')+'</span></div><button class="btn-modern btn-outline" id="dClose">Tutup</button></div>';
  html+='<div class="detail-grid">'+box('Waktu',fmtTime(it))+box('Durasi',fmtDur(it.durasi))+box('Agen',it.agent_name)+box('Penelepon',pelanggan)+box('Topik',topik)+box('Jenis layanan',jenis)+box('Sentimen',sentimen)+box('Emosi',emosi)+box('Resolusi',resolusi)+'<div class="meta-box"><div class="k">Frustrasi</div><div class="v">'+yn(frust)+'</div></div></div>';
  if(ringkasan){html+='<div class="sec-h">Ringkasan</div><p class="muted-text">'+esc(ringkasan)+'</p>';}
  html+='<div class="sec-h">Entitas</div><div class="muted-text" style="margin-bottom:4px;">Nama: '+tags(ent.nama)+'</div><div class="muted-text" style="margin-bottom:4px;">Nomor: '+tags(ent.nomor)+'</div><div class="muted-text">Lainnya: '+tags(ent.lainnya)+'</div>';
  if(poin&&poin.length){html+='<div class="sec-h">Poin penting</div><ul class="muted-text" style="padding-left:18px;">'+poin.map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul>';}
  if(catatan){html+='<div class="sec-h">Catatan kualitas</div><p class="muted-text">'+esc(catatan)+'</p>';}
  html+='<div class="sec-h">Transkrip percakapan</div>';
  if(dialog&&dialog.length){html+=bubbles(dialog);}
  else if(it.stt_text){html+='<p class="muted-text">'+esc(it.stt_text)+'</p>';}
  else{html+='<p class="muted-text">Belum ada transkrip.</p>';}
  html+='</div>';
  p.innerHTML=html;p.style.display='block';
  var c=el('dClose');if(c)c.addEventListener('click',function(){p.style.display='none';p.innerHTML='';});
  p.scrollIntoView({behavior:'smooth',block:'start'});
}
function loadDetail(sid){
  if(!sid)return;
  setStat('lStat','Memuat detail...');
  api({action:'detail',sid:sid}).then(function(d){
    if(!d.ok||!d.interaction){setStat('lStat','Gagal memuat detail: '+esc(d.error||''),'err');return;}
    hideStat('lStat');renderDetail(d.interaction);
  }).catch(function(e){setStat('lStat','Gagal: '+e,'err');});
}
if(el('lLoadBtn'))el('lLoadBtn').addEventListener('click',loadList);

loadCov();
loadList();
})();
