(function(){
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function api(p){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(function(r){return r.json();});}
  function setStat(msg,k){var s=el('cvStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function hideStat(){var s=el('cvStat');if(s){s.className='status';s.innerHTML='';}}
  function pctOf(a,b){return b?Math.round(a/b*100):0;}
  function bars(list,mountId){
    var box=el(mountId);if(!box)return;list=list||[];
    if(!list.length){box.innerHTML='<p class="muted-text">Belum ada data.</p>';return;}
    var max=1;list.forEach(function(x){if((x.value||0)>max)max=x.value;});
    box.innerHTML=list.map(function(x){var pct=Math.round((x.value||0)/max*100);return '<div class="bar-row"><div class="lab" title="'+esc(x.label)+'">'+esc(x.label)+'</div><div class="bar"><span style="width:'+pct+'%;"></span></div><div class="val">'+(x.value||0)+'</div></div>';}).join('');
  }
  function kpis(items){var box=el('cvKpi');if(!box)return;box.innerHTML=items.map(function(it){return '<div class="kpi"><div class="n">'+esc(it[0])+'</div><div class="l">'+esc(it[1])+'</div></div>';}).join('');}
  function render(d){
    var cov=(d&&d.coverage)||[],st=(d&&d.stats)||{};
    var tot=0,aud=0,tx=0,an=0;
    cov.forEach(function(r){tot+=(r.n_total||0);aud+=(r.n_audio||0);tx+=(r.n_transkrip||0);an+=(r.n_analisis||0);});
    kpis([
      [tot,'Total panggilan (rentang)'],
      [aud+' ('+pctOf(aud,tot)+'%)','Dengan audio'],
      [tx+' ('+pctOf(tx,tot)+'%)','Sudah transkrip'],
      [an+' ('+pctOf(an,tot)+'%)','Sudah dianalisis'],
      [(tot-an),'Belum dianalisis (peluang)'],
      [(st.total||0),'Total di basis data'],
      [((st.date_min||'-')+' s/d '+(st.date_max||'-')),'Rentang data tersimpan']
    ]);
    bars([
      {label:'Total panggilan',value:tot},
      {label:'Ada audio',value:aud},
      {label:'Sudah transkrip',value:tx},
      {label:'Sudah dianalisis',value:an}
    ],'cvFunnel');
    var tb=el('cvBody');
    if(tb){
      if(!cov.length){tb.innerHTML='<tr><td colspan="6" style="text-align:center;">Belum ada data pada rentang ini.</td></tr>';}
      else{tb.innerHTML=cov.map(function(r){var t=r.n_total||0;return '<tr><td>'+esc(r.day)+'</td><td>'+t+'</td><td>'+(r.n_audio||0)+'</td><td>'+(r.n_transkrip||0)+'</td><td>'+(r.n_analisis||0)+'</td><td>'+pctOf(r.n_analisis||0,t)+'%</td></tr>';}).join('');}
    }
    var pill=el('cvPill');if(pill)pill.textContent=tot+' panggilan';
  }
  function load(){
    setStat('Memuat coverage&hellip;');
    var df=(el('cv_from')?el('cv_from').value:''),dt=(el('cv_to')?el('cv_to').value:'');
    api({action:'coverage',date_from:df,date_to:dt}).then(function(d){
      d=d||{};
      if(!d.ok){setStat('Gagal memuat: '+esc(d.error||'tidak diketahui'),'err');return;}
      hideStat();render(d);
    }).catch(function(e){setStat('Gagal: '+e,'err');});
  }
  function init(){
    var t0=iso(new Date()),t30=iso(new Date(Date.now()-29*864e5));
    if(el('cv_to')&&!el('cv_to').value)el('cv_to').value=t0;
    if(el('cv_from')&&!el('cv_from').value)el('cv_from').value=t30;
    if(el('cvLoad'))el('cvLoad').addEventListener('click',load);
    load();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
