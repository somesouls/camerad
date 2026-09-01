(function(){
  // Halaman Kelola Data Phone: HANYA kartu Tarik Otomatis (auto-pull).
  // Kartu "Pengguna Harian" dihapus dari sini - sudah ada menu terpisah
  // /awe/telepon/pengguna (awe_phone_users.js).
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

  var paCard=document.createElement('section');
  paCard.className='c-card';paCard.id='paCard';
  paCard.innerHTML='<div class="stage-head"><h2 class="stage-title">Tarik Otomatis (Auto-pull) &mdash; Telepon</h2><span class="pill" id="paPill">&mdash;</span></div><p class="use-note">&#8505;&#65039; Menarik interaksi telepon <b>H-1 otomatis tiap hari</b> (seperti Livechat/Dialogflow), memakai kredensial <b>.env</b> (AVAYA_USERNAME/PASSWORD). Aktifkan penjadwal dengan <span class="code-inline">AWE_PHONE_SCHEDULER=1</span>. Analisis STT+LLM opsional via <span class="code-inline">AWE_PHONE_INGEST_ANALYZE=1</span> (lambat). Tombol di bawah menarik sekarang (latar belakang).</p><div id="paInfo"><p class="muted-text">Memuat status&hellip;</p></div><div class="form-row" style="margin-top:10px;"><div class="field"><label>Dari tanggal (opsional)</label><input type="date" id="pa_from"></div><div class="field"><label>Sampai tanggal (opsional)</label><input type="date" id="pa_to"></div><div class="field"><button class="btn-modern" id="paNow">Tarik Sekarang</button></div><div class="field"><button class="btn-modern btn-outline" id="paRefresh">Segarkan status</button></div></div><div class="status" id="paStat"></div>';

  function apJson(url,opt){
    return fetch(url,opt).then(function(r){
      return r.json().then(function(j){
        j=j||{};
        if(!r.ok&&j.ok===undefined)j.ok=false;
        if(!r.ok&&!j.error&&!j.detail)j.error='HTTP '+r.status;
        return j;
      },function(){return {ok:false,error:'HTTP '+r.status};});
    });
  }
  function paErr(d){var e=d&&(d.error||(typeof d.detail==='string'?d.detail:''));return e||'tidak diketahui';}
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
      if(!d.ok){paSet('Gagal memuat status: '+esc(paErr(d)),'err');return;}
      paHide();paRender(d);
    }).catch(function(e){paSet('Gagal: '+e,'err');});
  }
  function paNow(){
    paSet('Memulai tarik otomatis&hellip;');
    apJson('/api/awe/phone/autopull/now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({date_from:(el('pa_from')?el('pa_from').value:''),date_to:(el('pa_to')?el('pa_to').value:'')})}).then(function(d){
      if(!d.ok){paSet('Gagal: '+esc(paErr(d)),'err');return;}
      paSet('&#10003; '+esc(d.message||'Dimulai.'),'ok');
      setTimeout(paLoad,1500);setTimeout(paLoad,8000);
    }).catch(function(e){paSet('Gagal: '+e,'err');});
  }

  function mount(){
    var ph=document.querySelector('.page-header');
    if(ph&&ph.parentNode){ph.parentNode.insertBefore(paCard,ph.nextSibling);return;}
    var c=document.querySelector('.c-card');
    if(c&&c.parentNode){c.parentNode.insertBefore(paCard,c);return;}
    document.body.insertBefore(paCard,document.body.firstChild);
  }

  function init(){
    mount();
    if(el('paNow'))el('paNow').addEventListener('click',paNow);
    if(el('paRefresh'))el('paRefresh').addEventListener('click',paLoad);
    paLoad();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
