(function(){
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function api(p){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(function(r){return r.json();});}
  function setStat(msg,k){var s=el('snStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function hideStat(){var s=el('snStat');if(s){s.className='status';s.innerHTML='';}}
  function pctOf(a,b){return b?Math.round(a/b*100):0;}
  function truthy(v){v=String(v==null?'':v).trim().toLowerCase();return v==='1'||v==='true'||v==='ya'||v==='yes'||v==='y';}
  function fmtDur(s){s=parseInt(s,10);if(isNaN(s)||s<0)return '-';if(s<60)return s+'s';return Math.floor(s/60)+'m '+('0'+(s%60)).slice(-2)+'s';}
  function normSent(s){s=String(s==null?'':s).trim().toLowerCase();if(!s)return 'Tidak diketahui';if(s.indexOf('pos')===0)return 'Positif';if(s.indexOf('neg')===0)return 'Negatif';if(s.indexOf('net')===0||s.indexOf('neu')===0)return 'Netral';return 'Lainnya';}
  function bars(list,mountId){
    var box=el(mountId);if(!box)return;list=list||[];
    if(!list.length){box.innerHTML='<p class="muted-text">Belum ada data.</p>';return;}
    var max=1;list.forEach(function(x){if((x.value||0)>max)max=x.value;});
    box.innerHTML=list.map(function(x){var pct=Math.round((x.value||0)/max*100);return '<div class="bar-row"><div class="lab" title="'+esc(x.label)+'">'+esc(x.label)+'</div><div class="bar"><span style="width:'+pct+'%;"></span></div><div class="val">'+(x.value||0)+'</div></div>';}).join('');
  }
  function kpis(items){var box=el('snKpi');if(!box)return;box.innerHTML=items.map(function(it){return '<div class="kpi"><div class="n">'+esc(it[0])+'</div><div class="l">'+esc(it[1])+'</div></div>';}).join('');}
  function aggAgents(rows){
    var m={};
    rows.forEach(function(r){
      var a=(r.agent_name==null||r.agent_name==='')?'(tanpa agen)':String(r.agent_name);
      var o=m[a]||(m[a]={agent:a,calls:0,pos:0,neg:0,neu:0,frus:0,dur:0,durN:0});
      o.calls++;
      var s=normSent(r.sentiment);
      if(s==='Positif')o.pos++;else if(s==='Negatif')o.neg++;else if(s==='Netral')o.neu++;
      if(truthy(r.frustrasi))o.frus++;
      var d=parseInt(r.durasi,10);if(!isNaN(d)&&d>0){o.dur+=d;o.durN++;}
    });
    return Object.keys(m).map(function(k){return m[k];}).sort(function(a,b){return b.calls-a.calls;});
  }
  function render(rows){
    rows=rows||[];
    var analyzed=rows.filter(function(r){return r.has_analisis;});
    var sc={Positif:0,Netral:0,Negatif:0,Lainnya:0,'Tidak diketahui':0};
    analyzed.forEach(function(r){sc[normSent(r.sentiment)]++;});
    var frus=0;analyzed.forEach(function(r){if(truthy(r.frustrasi))frus++;});
    var agents=aggAgents(analyzed);
    kpis([
      [analyzed.length,'Panggilan dianalisis'],
      [pctOf(sc.Positif,analyzed.length)+'%','Positif'],
      [pctOf(sc.Negatif,analyzed.length)+'%','Negatif'],
      [pctOf(frus,analyzed.length)+'%','Frustrasi'],
      [agents.length,'Jumlah agen']
    ]);
    bars([
      {label:'Positif',value:sc.Positif},
      {label:'Netral',value:sc.Netral},
      {label:'Negatif',value:sc.Negatif},
      {label:'Lainnya',value:sc.Lainnya},
      {label:'Tidak diketahui',value:sc['Tidak diketahui']}
    ],'snSent');
    var tb=el('snAgentBody');
    if(tb){
      if(!agents.length){tb.innerHTML='<tr><td colspan="7" style="text-align:center;">Belum ada data analisis pada rentang ini.</td></tr>';}
      else{tb.innerHTML=agents.map(function(o){var avg=o.durN?Math.round(o.dur/o.durN):0;return '<tr><td>'+esc(o.agent)+'</td><td>'+o.calls+'</td><td>'+pctOf(o.pos,o.calls)+'%</td><td>'+pctOf(o.neu,o.calls)+'%</td><td>'+pctOf(o.neg,o.calls)+'%</td><td>'+pctOf(o.frus,o.calls)+'%</td><td>'+esc(fmtDur(avg))+'</td></tr>';}).join('');}
    }
    var pill=el('snPill');if(pill)pill.textContent=analyzed.length+' dianalisis';
  }
  function load(){
    setStat('Memuat sentimen&hellip;');
    var df=(el('sn_from')?el('sn_from').value:''),dt=(el('sn_to')?el('sn_to').value:'');
    api({action:'list',date_from:df,date_to:dt,limit_rows:5000}).then(function(d){
      d=d||{};
      if(!d.ok){setStat('Gagal memuat: '+esc(d.error||'tidak diketahui'),'err');return;}
      render(d.interactions||[]);
      if((d.total||0)>=5000)setStat('Menampilkan 5000 baris teratas; persempit rentang untuk hasil lengkap.','ok');
      else hideStat();
    }).catch(function(e){setStat('Gagal: '+e,'err');});
  }
  function init(){
    var t0=iso(new Date()),t30=iso(new Date(Date.now()-29*864e5));
    if(el('sn_to')&&!el('sn_to').value)el('sn_to').value=t0;
    if(el('sn_from')&&!el('sn_from').value)el('sn_from').value=t30;
    if(el('snLoad'))el('snLoad').addEventListener('click',load);
    load();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
