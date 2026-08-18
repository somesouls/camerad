const ENDPOINT = location.pathname;
const STEPS = [
  {n:1, title:'Tarik Data Dialogflow', sub:'Raw JSON dari Google Logging', type:'form1'},
  {n:2, title:'Convert JSON \u2192 XLSX', sub:'Multi-sheet interaksi', type:'form2'},
  {n:3, title:'Training & Intent', sub:'2 XLSX dalam ZIP', type:'form3'},
  {n:4, title:'Analisis Rekomendasi', sub:'SBERT+BGE Top 5 via Ngrok', type:'form4'},
  {n:5, title:'Qwen Judgement Top 5', sub:'Skor 0\u20135 via Ngrok', type:'form5'},
  {n:6, title:'Cross-check Manual', sub:'Koreksi manusia (Analisis Fallback)', type:'step6'},
  {n:7, title:'Analisis MKTA', sub:'Relevansi jawaban (Non Fallback)', type:'form7'},
  {n:8, title:'Putusan LLM MKTA', sub:'Filter QA Conf \u2192 Qwen', type:'step8'},
  {n:9, title:'Analisis Manual MKTA', sub:'Isi Intent Seharusnya', type:'step9'},
  {n:10, title:'Laporan LM & Pembaruan', sub:'Excel + CSV LM + CSV Pembaruan', type:'step10'},
  {n:11, title:'Pembaruan Intent Dialogflow', sub:'Suntik training phrase ke usersays JSON', type:'step11'},
  {n:12, title:'Avaya - Upload JSON', sub:'Gabung transkrip AWE Avaya', type:'avaya12'},
  {n:13, title:'Avaya - Tarik Intent Dialogflow', sub:'Sama seperti Step 3', type:'avaya13'},
  {n:14, title:'Avaya - Analisis', sub:'Coverage, deflection, sentimen (JSON)', type:'avaya14'},
  {n:15, title:'Avaya - Dashboard', sub:'Render HTML interaktif', type:'avaya15'},
  {n:16, title:'Avaya - Ekspor Excel', sub:'Workbook multi-sheet', type:'avaya16'},
];

// Penyimpanan kini berbasis DATASET (kunci = rentang tanggal log + bahasa),
// menggantikan "Run ID" acak. RUN menyimpan kunci dataset aktif (dkey).
let RUN = localStorage.getItem('dfp_run') || '';
let STATE = {steps:{}};

function adoptRun(res){
  if(res && res.run && res.run !== RUN){
    RUN = res.run;
    localStorage.setItem('dfp_run', RUN);
    const p = String(RUN).split('__');
    if(p.length===3){ STATE.range_start=p[0]; STATE.range_end=p[1]; STATE.lang=p[2]; }
    updateRunLabel();
  }
}

function updateRunLabel(){
  const el=document.getElementById('runLabel');
  if(!el) return;
  if(!RUN){ el.textContent='dataset: (belum ada \u2014 jalankan Step 1)'; return; }
  let txt = RUN;
  if(STATE.range_start && STATE.range_end){ txt = STATE.range_start+' \u2192 '+STATE.range_end+' ('+(STATE.lang||'id')+')'; }
  el.textContent = 'dataset: ' + txt;
}

const checkSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';

function api(action, opts){
  let u = ENDPOINT + '?action=' + action;
  if(RUN) u += '&run=' + encodeURIComponent(RUN);
  return fetch(u, opts||{}).then(async r=>{
    const text = await r.text();
    try { return JSON.parse(text); }
    catch(e){
      const snip = text.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim().slice(0,200);
      const looksHtml = text.trim().charAt(0)==='<';
      const hint = (looksHtml || r.status>=500 || r.status===0)
        ? 'Server balas halaman error (HTTP '+r.status+') \u2014 kemungkinan proses terlalu lama / gateway timeout (ngrok). Untuk Step 8 proses sudah per-chunk; klik lagi untuk melanjutkan progres.'
        : 'Respons server tidak valid (HTTP '+r.status+').';
      throw new Error(hint + (snip ? ' Cuplikan: '+snip : ''));
    }
  });
}

function pollStepDone(n, onDone, onFail){
  let tries=0; const max=360;
  const iv=setInterval(()=>{
    tries++;
    api('state',{}).then(res=>{
      if(res && res.steps){ STATE.steps=res.steps; if(res.ngrok_url) STATE.ngrok_url=res.ngrok_url; }
      const st=STATE.steps[n];
      if(st && st.status==='done'){ clearInterval(iv); onDone(st); }
      else if(st && st.status==='error'){ clearInterval(iv); onFail('server melaporkan error'); }
      else if(tries>=max){ clearInterval(iv); onFail('waktu tunggu habis'); }
    }).catch(()=>{ if(tries>=max){ clearInterval(iv); onFail('waktu tunggu habis'); } });
  }, 5000);
}

function stepStatus(n){
  const s = STATE.steps[n];
  if(s && s.status==='done') return 'done';
  if(s && s.status==='error') return 'error';
  const meta = STEPS.find(x=>x.n===n);
  if(meta.type==='soon') return 'soon';
  if(n===1 || n===12 || n===13) return 'ready';
  if(n===14){ const a=STATE.steps[12]; const b=STATE.steps[13]||STATE.steps[3]; return (a&&a.status==='done'&&b&&b.status==='done')?'ready':'pending'; }
  if(n===15||n===16){ const a=STATE.steps[14]; return (a&&a.status==='done')?'ready':'pending'; }
  const prev = STATE.steps[n-1];
  return (prev && prev.status==='done') ? 'ready' : 'pending';
}

function renderRail(){
  const container = document.getElementById('flows-container');
  container.innerHTML = '';
  const flows = [
    {title:'Analisis DialogFlow (Step 1-11)', diag:false, steps:STEPS.filter(s=>s.n<=11)},
  ];
  flows.forEach((fl,fi)=>{
    const head = document.createElement('div');
    head.style.cssText = 'margin:'+(fi===0?'0':'32px')+' 0 12px;padding-top:'+(fi===0?'0':'24px')+';font-size:13px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--text2);'+(fi===0?'':'border-top:1px dashed var(--border);');
    head.innerHTML = '<span>'+esc(fl.title)+'</span>' + (fl.diag ? ' <button type="button" id="diagBtn" style="margin-left:12px;font-size:11px;font-weight:700;padding:4px 10px;border-radius:8px;border:1px solid var(--border);background:var(--soft2);color:var(--text2);cursor:pointer">\uD83E\uDE7A Cek Server</button>' : '');
    container.appendChild(head);
    const wrap = document.createElement('div');
    wrap.className = 'railwrap';
    wrap.style.paddingTop = '4px';
    const rail = document.createElement('div');
    rail.className = 'rail';
    fl.steps.forEach((st,i)=>{
      if(i>0){
        const c = document.createElement('div');
        c.className = 'connector' + (stepStatus(st.n)==='done' && stepStatus(fl.steps[i-1].n)==='done' ? ' done':'');
        rail.appendChild(c);
      }
      const stat = stepStatus(st.n);
      const node = document.createElement('button');
      node.className = 'node' + (stat==='done'?' is-done':stat==='error'?' is-error':stat==='ready'?' is-ready':st.type==='soon'?' is-soon':'');
      node.innerHTML =
        '<span class="dot">' + (stat==='done'? checkSvg : st.n) + '</span>' +
        '<span class="lbl">' + st.title + '</span>' +
        '<span class="sub">' + st.sub + '</span>';
      node.onclick = ()=>openModal(st.n);
      rail.appendChild(node);
    });
    wrap.appendChild(rail);
    container.appendChild(wrap);
  });
  const db = document.getElementById('diagBtn'); if(db) db.onclick = checkServer;
  updateRunLabel();
}

function checkServer(){
  const modal = document.getElementById('modal');
  modal.classList.remove('wide');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">?</div>'+
      '<div><h2>Diagnostik Server</h2><p>Cek koneksi, versi modul &amp; template Avaya</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody" id="mbody"><div class="status show run"><span class="sp"></span>Menghubungi server...</div></div>'+
    '<div class="mfoot"><button class="btn btn-sec" id="closeBtn2">Tutup</button></div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick = closeModal;
  document.getElementById('closeBtn2').onclick = closeModal;
  const extra = STATE.ngrok_url ? '&ngrok_url='+encodeURIComponent(STATE.ngrok_url) : '';
  api('avayadiag'+extra, {}).then(res=>{
    const d = (res && res.diag) ? res.diag : res;
    let rows='';
    for(const k in d){
      if(k==='ok') continue;
      let v = d[k]; if(typeof v==='object') v=JSON.stringify(v);
      rows+='<div class="row"><span class="k">'+esc(k.replace(/_/g," "))+'</span><span class="v">'+esc(v)+'</span></div>';
    }
    document.getElementById('mbody').innerHTML='<div class="summary show">'+rows+'</div>';
  }).catch(e=>{
    document.getElementById('mbody').innerHTML='<div class="status show err">\u26A0 '+esc(e.message||e)+'</div>';
  });
}

function esc(s){ return String(s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

function openDatasetPicker(){
  const modal = document.getElementById('modal');
  modal.classList.remove('wide');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">\u2261</div>'+
      '<div><h2>Muat Dataset</h2><p>Pilih dataset (rentang tanggal + bahasa) untuk dimuat</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody" id="dsbody"><div class="status show run"><span class="sp"></span>Memuat daftar...</div></div>'+
    '<div class="mfoot"><button class="btn btn-sec" id="closeBtn2">Tutup</button></div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick = closeModal;
  document.getElementById('closeBtn2').onclick = closeModal;
  api('datasets',{}).then(res=>{
    const list = (res && res.datasets) || [];
    if(!list.length){ document.getElementById('dsbody').innerHTML='<div class="hint">Belum ada dataset. Jalankan Step 1 untuk membuat dataset baru.</div>'; return; }
    let html='<div class="summary show" style="display:block">';
    list.forEach(d=>{
      const act = d.active ? ' <span class="s6pill t">aktif</span>' : '';
      const arch = (d.status==='archived') ? ' <span class="s6pill n">arsip</span>' : '';
      const label = (d.range_start && d.range_end) ? (esc(d.range_start)+' \u2192 '+esc(d.range_end)+' ('+esc(d.lang||'id')+')') : esc(d.label||d.run);
      html+='<div class="row"><span class="k">'+label+act+arch+'</span>'+
            '<span class="v"><button class="btn btn-sec s6load" data-run="'+esc(d.run)+'" style="padding:5px 12px;font-size:12.5px">Muat</button></span></div>';
    });
    html+='</div>';
    document.getElementById('dsbody').innerHTML=html;
    document.querySelectorAll('.s6load').forEach(b=>{ b.onclick=()=>loadDataset(b.getAttribute('data-run')); });
  }).catch(e=>{ document.getElementById('dsbody').innerHTML='<div class="status show err">\u26A0 '+esc(e.message||e)+'</div>'; });
}

function loadDataset(run){
  const oldRun = RUN;
  RUN = run;
  api('activate',{}).then(res=>{
    if(res && res.ok){
      localStorage.setItem('dfp_run', RUN);
      STATE = {steps: res.steps || {}};
      STATE.range_start = res.range_start || '';
      STATE.range_end = res.range_end || '';
      STATE.lang = res.lang || '';
      STATE.label = res.label || '';
      if(res.ngrok_url) STATE.ngrok_url = res.ngrok_url;
      updateRunLabel();
      renderRail();
      closeModal();
    } else {
      RUN = oldRun;
      alert('Gagal memuat dataset: ' + ((res&&res.error)||'tidak diketahui'));
    }
  }).catch(e=>{ RUN = oldRun; alert('Gagal memuat dataset: ' + (e.message||e)); });
}

function srcToggle(group, opts){
  let html='<div class="srcbox" data-group="'+group+'">';
  let firstEnabledDone=false;
  opts.forEach(o=>{
    const enabled = o.enabled!==false;
    let on='';
    if(enabled && !firstEnabledDone){ on=' on'; firstEnabledDone=true; }
    const dis = enabled ? '' : ' disabled style="opacity:.45;cursor:not-allowed"';
    html+='<button type="button" class="srcbtn'+on+'" data-group="'+group+'" data-mode="'+o.mode+'"'+dis+'>'+o.label+'</button>';
  });
  html+='</div>';
  return html;
}

function formHtml(n){
  if(n===1){
    return ''+
     '<div class="field"><label>Start Date</label><input type="date" id="f_start"></div>'+
     '<div class="field"><label>End Date</label><input type="date" id="f_end"><div class="hint">Rentang maksimal 31 hari, dan harus sebelum hari ini (WIB). Rentang tanggal + bahasa ini menjadi kunci dataset (pengganti Run ID).</div></div>'+
     '<div class="field"><label>Bahasa</label><select id="f_lang"><option value="id">id</option><option value="en">en</option></select></div>'+
     '<div class="field"><label>Access Token Google <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_token" placeholder="Kosongkan bila pakai service-account.json"><div class="hint">Isi bila server tidak memakai service-account.json.</div></div>';
  }
  if(n===2){
    const d1 = STATE.steps[1] && STATE.steps[1].status==='done';
    return srcToggle('s2',[{mode:'prev',label:'Hasil Step 1',enabled:d1},{mode:'upload',label:'Unggah File'}])+
     '<div class="field srcfield" data-group="s2" data-mode="prev"><div class="hint">Memakai hasil <b>Step 1</b> (raw log) dari server. Tidak perlu unggah ulang.</div></div>'+
     '<div class="field srcfield" data-group="s2" data-mode="upload"><label>File JSON</label><input type="file" id="f_json" accept=".json,application/json"><div class="hint">Hasil tarikan Step 1 (raw log Dialogflow).</div></div>';
  }
  if(n===3){
    return '<div class="hint" style="margin-bottom:14px">Menarik seluruh intent aktif dari Dialogflow lalu membuat 2 file XLSX (Training Phrase &amp; Isi Intent) dalam satu ZIP. Tidak perlu input file.</div>'+
     '<div class="field"><label>Access Token Google <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_token" placeholder="Kosongkan bila pakai service-account.json"></div>';
  }
  if(n===4){
    const d2 = STATE.steps[2] && STATE.steps[2].status==='done';
    const d3 = STATE.steps[3] && STATE.steps[3].status==='done';
    const autoOk = d2 && d3;
    return '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Mode Colab all-in-one: <b>kosongkan saja</b> \u2014 otomatis pakai localhost:8000. Isi hanya bila FastAPI di server terpisah.</div></div>'+
     srcToggle('s4',[{mode:'auto',label:'Otomatis (Step 2 + 3)',enabled:autoOk},{mode:'manual',label:'Unggah 3 file'}])+
     '<div class="field srcfield" data-group="s4" data-mode="auto"><div class="hint">Workbook utama diambil dari <b>Step 2</b> (sheet Fallback). Training Phrase &amp; Intent diekstrak otomatis dari ZIP <b>Step 3</b>.'+(autoOk?'':' <b style="color:var(--red)">Jalankan Step 2 &amp; 3 dulu, atau pilih Unggah 3 file.</b>')+'</div></div>'+
     '<div class="field srcfield" data-group="s4" data-mode="manual"><label>Workbook utama (sheet "Fallback")</label><input type="file" id="f_main" accept=".xlsx"><div class="hint">Hasil Step 2.</div></div>'+
     '<div class="field srcfield" data-group="s4" data-mode="manual"><label>Training Phrase (.xlsx)</label><input type="file" id="f_train" accept=".xlsx"></div>'+
     '<div class="field srcfield" data-group="s4" data-mode="manual"><label>Intent (.xlsx)</label><input type="file" id="f_content" accept=".xlsx"></div>';
  }
  if(n===5){
    const d4 = STATE.steps[4] && STATE.steps[4].status==='done';
    return '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Mode Colab all-in-one: <b>kosongkan saja</b> \u2014 otomatis pakai localhost:8000.</div></div>'+
     srcToggle('s5',[{mode:'prev',label:'Hasil Step 4',enabled:d4},{mode:'upload',label:'Unggah XLSX'}])+
     '<div class="field srcfield" data-group="s5" data-mode="prev"><div class="hint">Memakai hasil <b>Step 4</b> (Top-5) dari server.</div></div>'+
     '<div class="field srcfield" data-group="s5" data-mode="upload"><label>File XLSX Top 5</label><input type="file" id="f_xlsx" accept=".xlsx"></div>';
  }
  if(n===7){
    const d6 = STATE.steps[6] && STATE.steps[6].status==='done';
    const d5 = STATE.steps[5] && STATE.steps[5].status==='done';
    const autoOk = d6 || d5 || (STATE.steps[2] && STATE.steps[2].status==='done');
    return '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Menilai apakah <b>bot response</b> benar-benar menjawab <b>pertanyaan user</b> pada interaksi Non Fallback. Kosongkan untuk localhost:8000.</div></div>'+
     srcToggle('s7',[{mode:'auto',label:'Otomatis (hasil Step 6)',enabled:autoOk},{mode:'manual',label:'Unggah XLSX'}])+
     '<div class="field srcfield" data-group="s7" data-mode="auto"><div class="hint">Membaca sheet <b>Non Fallback</b> dari hasil <b>Step 6</b> (atau hasil terbaru yang tersedia), lalu menambah sheet <b>Analisis MKTA</b>.'+(autoOk?'':' <b style="color:var(--red)">Jalankan minimal Step 2 dulu.</b>')+'</div></div>'+
     '<div class="field srcfield" data-group="s7" data-mode="manual"><label>Workbook (.xlsx, punya sheet "Non Fallback")</label><input type="file" id="f_xlsx" accept=".xlsx"></div>';
  }
  if(n===12){
    return '<div class="field"><label>File JSON AWE Avaya <span style="font-weight:400;color:var(--text2)">(boleh beberapa)</span></label><input type="file" id="f_avjson" accept=".json,application/json" multiple><div class="hint">Pilih satu atau beberapa file sekaligus. Rentang tanggal lanjutan otomatis <b>digabung</b> &amp; dedup berdasarkan <b>sid</b>.</div></div>';
  }
  if(n===13){
    return '<div class="field"><label>Access Token Google <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_token" placeholder="Kosongkan bila pakai service-account.json"><div class="hint">Menarik seluruh intent aktif dari Dialogflow <b>persis seperti Step 3</b> \u2014 menghasilkan Training Phrase + Intent dalam ZIP. Dipakai untuk memetakan apakah pertanyaan pelanggan sudah tercover chatbot.</div></div>';
  }
  if(n===14){
    const d12 = STATE.steps[12] && STATE.steps[12].status==='done';
    const d13 = STATE.steps[13] && STATE.steps[13].status==='done';
    const d3  = STATE.steps[3]  && STATE.steps[3].status==='done';
    const autoOk = d12 && (d13 || d3);
    return '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Mode Colab all-in-one: <b>kosongkan saja</b> \u2014 otomatis pakai localhost:8000.</div></div>'+
     srcToggle('s14',[{mode:'auto',label:'Otomatis (Step 12 + 13)',enabled:autoOk},{mode:'manual',label:'Unggah Training + Intent'}])+
     '<div class="field srcfield" data-group="s14" data-mode="auto"><div class="hint">JSON gabungan dari <b>Step 12</b>; Training Phrase &amp; Intent dari <b>Step 13</b>'+(d13?'':(d3?' (memakai hasil Step 3)':''))+'.'+(autoOk?'':' <b style="color:var(--red)">Jalankan Step 12 &amp; 13 dulu, atau pilih Unggah manual.</b>')+'</div></div>'+
     '<div class="field srcfield" data-group="s14" data-mode="manual"><label>Training Phrase (.xlsx)</label><input type="file" id="f_train" accept=".xlsx"></div>'+
     '<div class="field srcfield" data-group="s14" data-mode="manual"><label>Intent (.xlsx)</label><input type="file" id="f_content" accept=".xlsx"></div>';
  }
  if(n===15){
    return '<div class="hint">Membangun <b>dashboard HTML interaktif</b> dari hasil <b>Step 14</b>. Tidak perlu input. Bila gagal, pesan error asli dari server akan tampil di sini (bukan lagi "HTTP 500" kosong).</div>';
  }
  if(n===16){
    return '<div class="hint">Membangun <b>workbook Excel</b> multi-sheet (Ringkasan, Percakapan, Agent, Pelanggan, Kandidat Intent) dari hasil <b>Step 14</b>. Tidak perlu input.</div>';
  }
  return '';
}

function openModal(n){
  const st = STEPS.find(x=>x.n===n);
  const modal = document.getElementById('modal');
  modal.classList.remove('wide');
  if(st.type==='step6'){ openModal6(st); return; }
  if(st.type==='step8'){ openModal8(st); return; }
  if(st.type==='step9'){ openModal9(st); return; }
  if(st.type==='step10'){ openModal10(st); return; }
  if(st.type==='step11'){ openModal11(st); return; }
  const badge = st.type==='soon' ? '&#9679;' : n;
  let inner =
    '<div class="mhead"><div class="mbadge">'+badge+'</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>';

  if(st.type==='soon'){
    inner += '<div class="soon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><div><b>Step '+n+' belum tersedia</b></div><div style="margin-top:6px;font-size:13px">Kerangka UI &amp; mekanisme "lanjut" sudah siap. Tinggal tambahkan logika step ini di index.php.</div></div>'+
      '<div class="mfoot"><button class="btn btn-sec" id="closeBtn2">Tutup</button></div>';
  } else {
    inner += '<div class="mbody" id="mbody">'+formHtml(n)+'</div>'+
      '<div class="status" id="mstatus"></div>'+
      '<div class="summary" id="msummary"></div>'+
      '<div class="mfoot">'+
        '<button class="btn" id="runBtn">Jalankan Step '+n+'</button>'+
        '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
        '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step '+(n+1)+' \u2192</button>'+
      '</div>';
  }

  modal.innerHTML = inner;
  document.getElementById('overlay').classList.add('show');

  document.getElementById('mxBtn').onclick = closeModal;
  const cb2 = document.getElementById('closeBtn2'); if(cb2) cb2.onclick = closeModal;

  if(st.type!=='soon'){
    bindSourceToggle();
    prefillDates(n);
    if(n===4 || n===5 || n===14){ const el=document.getElementById('f_ngrok'); if(el && STATE.ngrok_url) el.value=STATE.ngrok_url; }
    document.getElementById('runBtn').onclick = ()=>runStep(n);
    const done = STATE.steps[n];
    if(done && done.status==='done'){ showSummary(n, done); showDone(n); }
  }
}

function prefillDates(n){
  if(n!==1) return;
  const d=new Date(); d.setDate(d.getDate()-1);
  const y=new Date(); y.setDate(y.getDate()-7);
  const fmt=x=>x.toISOString().slice(0,10);
  document.getElementById('f_end').value=fmt(d);
  document.getElementById('f_start').value=fmt(y);
}

function bindSourceToggle(){
  document.querySelectorAll('.srcbox').forEach(box=>{
    const group=box.dataset.group;
    const apply=(mode)=>{
      document.querySelectorAll('.srcfield[data-group="'+group+'"]').forEach(f=>{
        f.style.display = (f.dataset.mode===mode) ? '' : 'none';
      });
    };
    box.querySelectorAll('.srcbtn').forEach(b=>{
      b.onclick=()=>{
        if(b.disabled) return;
        box.querySelectorAll('.srcbtn').forEach(x=>x.classList.remove('on'));
        b.classList.add('on');
        apply(b.dataset.mode);
      };
    });
    const on=box.querySelector('.srcbtn.on');
    apply(on?on.dataset.mode:'');
  });
}

function closeModal(){ document.getElementById('overlay').classList.remove('show'); }

function setStatus(kind, html){
  const s=document.getElementById('mstatus');
  s.className='status show '+kind;
  s.innerHTML=html;
}

function showSummary(n, art){
  const box=document.getElementById('msummary');
  const sm=art.summary||{};
  let rows='';
  rows+='<div class="row"><span class="k">Nama file</span><span class="v">'+esc(art.name)+'</span></div>';
  for(const k in sm){
    let v=sm[k];
    if(Array.isArray(v)){ if(!v.length) continue; v=v.join(' | '); }
    if(v===null||v==='') continue;
    rows+='<div class="row"><span class="k">'+esc(k.replace(/_/g," "))+'</span><span class="v">'+esc(v)+'</span></div>';
  }
  box.innerHTML=rows;
  box.classList.add('show');
}

function showDone(n){
  document.getElementById('dlBtn').style.display='';
  document.getElementById('dlBtn').onclick=()=>{ window.location = ENDPOINT+'?action=download&run='+encodeURIComponent(RUN)+'&step='+n; };
  const next=STEPS.find(x=>x.n===n+1);
  const nb=document.getElementById('nextBtn');
  if(next){ nb.style.display=''; nb.onclick=()=>openModal(n+1); }
  const rb=document.getElementById('runBtn'); if(rb) rb.textContent='Jalankan Ulang Step '+n;
  if(n===15){
    const dl=document.getElementById('dlBtn');
    if(dl){ dl.style.display=''; dl.textContent='Buka Dashboard'; dl.onclick=()=>window.open(ENDPOINT+'?action=download&run='+encodeURIComponent(RUN)+'&step=15&part=avayadash','_blank'); }
  }
}
