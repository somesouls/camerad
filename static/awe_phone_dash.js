(function(){
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function api(p){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(function(r){return r.json();});}
  function setStat(msg,k){var s=el('dbStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function hideStat(){var s=el('dbStat');if(s){s.className='status';s.innerHTML='';}}
  function fmtDur(s){s=parseInt(s,10);if(isNaN(s)||s<0)return '-';if(s<60)return s+'s';return Math.floor(s/60)+'m '+('0'+(s%60)).slice(-2)+'s';}
  function bars(list,mountId){
    var box=el(mountId);if(!box)return;list=list||[];
    if(!list.length){box.innerHTML='<p class="muted-text">Belum ada data.</p>';return;}
    var max=1;list.forEach(function(x){if((x.value||0)>max)max=x.value;});
    box.innerHTML=list.map(function(x){var pct=Math.round((x.value||0)/max*100);return '<div class="bar-row"><div class="lab" title="'+esc(x.label)+'">'+esc(x.label)+'</div><div class="bar"><span style="width:'+pct+'%;"></span></div><div class="val">'+(x.value||0)+'</div></div>';}).join('');
  }
  function kpis(items){var box=el('dbKpi');if(!box)return;box.innerHTML=items.map(function(it){return '<div class="kpi"><div class="n">'+esc(it[0])+'</div><div class="l">'+esc(it[1])+'</div></div>';}).join('');}
  function renderCov(cov){
    cov=cov||[];var tot=0,aud=0,tx=0,an=0;
    cov.forEach(function(r){tot+=(r.n_total||0);aud+=(r.n_audio||0);tx+=(r.n_transkrip||0);an+=(r.n_analisis||0);});
    bars([{label:'Total panggilan',value:tot},{label:'Ada audio',value:aud},{label:'Sudah transkrip',value:tx},{label:'Sudah dianalisis',value:an}],'dbFunnel');
    var tb=el('dbCovBody');
    if(tb){
      if(!cov.length){tb.innerHTML='<tr><td colspan="5" style="text-align:center;">Belum ada data pada rentang ini.</td></tr>';}
      else{tb.innerHTML=cov.map(function(r){return '<tr><td>'+esc(r.day)+'</td><td>'+(r.n_total||0)+'</td><td>'+(r.n_audio||0)+'</td><td>'+(r.n_transkrip||0)+'</td><td>'+(r.n_analisis||0)+'</td></tr>';}).join('');}
    }
    return {tot:tot,aud:aud,tx:tx,an:an};
  }
  function pctOf(a,b){return b?Math.round(a/b*100):0;}
  function render(du,cov){
    var f=renderCov((cov&&cov.coverage)||[]);
    var k=(du&&du.kpi)||{};
    kpis([
      [f.tot,'Total panggilan (rentang)'],
      [f.aud+' ('+pctOf(f.aud,f.tot)+'%)','Dengan audio'],
      [f.tx+' ('+pctOf(f.tx,f.tot)+'%)','Sudah transkrip'],
      [f.an+' ('+pctOf(f.an,f.tot)+'%)','Sudah dianalisis'],
      [k.total_callers||0,'Penelepon unik'],
      [fmtDur(k.avg_dur||0),'Rata-rata durasi'],
      [(k.frustrasi_pct||0)+'%','Panggilan frustrasi']
    ]);
    var s=(du&&du.sentiment)||{};
    bars([{label:'Positif',value:s.Positif||0},{label:'Netral',value:s.Netral||0},{label:'Negatif',value:s.Negatif||0},{label:'Tidak diketahui',value:s['Tidak diketahui']||0}],'dbSent');
    bars((du&&du.themes)||[],'dbThemes');
    bars((du&&du.resolusi)||[],'dbReso');
    var pill=el('dbPill');if(pill)pill.textContent=(f.tot||0)+' panggilan';
  }
  function load(){
    setStat('Memuat dashboard&hellip;');
    var df=(el('d_from')?el('d_from').value:''),dt=(el('d_to')?el('d_to').value:'');
    Promise.all([
      api({action:'daily_users',date_from:df,date_to:dt,limit_rows:2000}),
      api({action:'coverage',date_from:df,date_to:dt})
    ]).then(function(res){
      var du=res[0]||{},cov=res[1]||{};
      if(!du.ok&&!cov.ok){setStat('Gagal memuat: '+esc(du.error||cov.error||'tidak diketahui'),'err');return;}
      hideStat();render(du,cov);
    }).catch(function(e){setStat('Gagal: '+e,'err');});
  }
  function init(){
    var t0=iso(new Date()),t30=iso(new Date(Date.now()-29*864e5));
    if(el('d_to')&&!el('d_to').value)el('d_to').value=t0;
    if(el('d_from')&&!el('d_from').value)el('d_from').value=t30;
    if(el('dbLoad'))el('dbLoad').addEventListener('click',load);
    load();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
