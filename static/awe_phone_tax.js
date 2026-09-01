(function(){
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function iso(d){return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);}
  function api(p){return fetch('/api/awe/phone/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)}).then(function(r){return r.json();});}
  function setStat(msg,k){var s=el('txStat');if(!s)return;s.className='status show'+(k?(' '+k):'');s.innerHTML=msg;}
  function hideStat(){var s=el('txStat');if(s){s.className='status';s.innerHTML='';}}
  function pctOf(a,b){return b?Math.round(a/b*100):0;}
  function truthy(v){v=String(v==null?'':v).trim().toLowerCase();return v==='1'||v==='true'||v==='ya'||v==='yes'||v==='y';}
  function bars(list,mountId){
    var box=el(mountId);if(!box)return;list=list||[];
    if(!list.length){box.innerHTML='<p class="muted-text">Belum ada data.</p>';return;}
    var max=1;list.forEach(function(x){if((x.value||0)>max)max=x.value;});
    box.innerHTML=list.map(function(x){var pct=Math.round((x.value||0)/max*100);return '<div class="bar-row"><div class="lab" title="'+esc(x.label)+'">'+esc(x.label)+'</div><div class="bar"><span style="width:'+pct+'%;"></span></div><div class="val">'+(x.value||0)+'</div></div>';}).join('');
  }
  function kpis(items){var box=el('txKpi');if(!box)return;box.innerHTML=items.map(function(it){return '<div class="kpi"><div class="n">'+esc(it[0])+'</div><div class="l">'+esc(it[1])+'</div></div>';}).join('');}
  function agg(rows,key){
    var m={};
    rows.forEach(function(r){
      var raw=r[key];var k=(raw==null||raw==='')?'(tidak diketahui)':String(raw);
      var o=m[k]||(m[k]={label:k,value:0,frus:0});
      o.value++;if(truthy(r.frustrasi))o.frus++;
    });
    return Object.keys(m).map(function(k){return m[k];}).sort(function(a,b){return b.value-a.value;});
  }
  function render(rows){
    rows=rows||[];
    var analyzed=rows.filter(function(r){return r.has_analisis;});
    var byTopik=agg(analyzed,'topik');
    var byLayanan=agg(analyzed,'jenis_layanan');
    var frus=0;analyzed.forEach(function(r){if(truthy(r.frustrasi))frus++;});
    kpis([
      [rows.length,'Total panggilan (rentang)'],
      [analyzed.length,'Sudah dianalisis'],
      [byTopik.length,'Ragam topik'],
      [pctOf(frus,analyzed.length)+'%','Panggilan frustrasi'],
      [(byTopik[0]?byTopik[0].label:'-'),'Topik teratas']
    ]);
    bars(byTopik.slice(0,15).map(function(o){return {label:o.label,value:o.value};}),'txTopik');
    bars(byLayanan.slice(0,15).map(function(o){return {label:o.label,value:o.value};}),'txLayanan');
    var peluang=byTopik.slice().sort(function(a,b){return (b.frus-a.frus)||(b.value-a.value);}).slice(0,20);
    var tb=el('txPeluangBody');
    if(tb){
      if(!peluang.length){tb.innerHTML='<tr><td colspan="4" style="text-align:center;">Belum ada data analisis pada rentang ini.</td></tr>';}
      else{tb.innerHTML=peluang.map(function(o){return '<tr><td>'+esc(o.label)+'</td><td>'+o.value+'</td><td>'+o.frus+'</td><td>'+pctOf(o.frus,o.value)+'%</td></tr>';}).join('');}
    }
    var pill=el('txPill');if(pill)pill.textContent=analyzed.length+' dianalisis';
  }
  function load(){
    setStat('Memuat taksonomi&hellip;');
    var df=(el('tx_from')?el('tx_from').value:''),dt=(el('tx_to')?el('tx_to').value:'');
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
    if(el('tx_to')&&!el('tx_to').value)el('tx_to').value=t0;
    if(el('tx_from')&&!el('tx_from').value)el('tx_from').value=t30;
    if(el('txLoad'))el('txLoad').addEventListener('click',load);
    load();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
