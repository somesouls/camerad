function runStep8(){
  const s=step8Selected(); if(!s){ setStatus('err','\u26A0 Pilih ambang dulu.'); return; }
  if(s.count===0){ setStatus('err','\u26A0 Tidak ada baris pada ambang ini.'); return; }
  const btn=document.getElementById('s8run'); btn.disabled=true;
  const ngrok=val('f_ngrok'); if(ngrok) STATE.ngrok_url=ngrok;
  const mode=step8Mode();
  const target=s.count; let processedTotal=0;
  const doChunk=()=>{
    const fd=new FormData();
    fd.append('ngrok_url', ngrok);
    fd.append('threshold', String(s.th));
    fd.append('mode', mode);
    setStatus('run','<span class="sp"></span>Memproses ke Qwen... '+processedTotal+'/'+target+' selesai. Jangan tutup jendela ini.');
    api('step8',{method:'POST', body:fd}).then(res=>{
      if(!res || !res.ok){ btn.disabled=false; setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memproses.')); return; }
      STATE.steps[8]=res.artifact;
      processedTotal += (res.processed||0);
      if(res.done){
        btn.disabled=false;
        setStatus('ok','\u2714 Putusan selesai. Diproses '+processedTotal+' baris. File siap diunduh.');
        showDone(8); renderRail();
      } else if((res.processed||0)>0){
        doChunk();
      } else {
        btn.disabled=false;
        setStatus('err','\u26A0 0 baris terproses pada chunk ini, tetapi sisa '+(res.remaining||0)+'. Progres tersimpan \u2014 klik lagi untuk mencoba melanjutkan, atau cek log server.');
        showDone(8); renderRail();
      }
    }).catch(e=>{ btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)+' (progres tersimpan; klik lagi untuk lanjut)'); });
  };
  doChunk();
}

let STEP9 = {rows:[]};

function openModal9(st){
  const modal=document.getElementById('modal');
  modal.classList.add('wide');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">9</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="s6bar">'+
      '<div class="fg"><label>Skor Bahasa &lt; (ambang sheet)</label><input type="number" id="f9qa" min="0" max="1" step="0.05" value="0.5" style="width:110px"></div>'+
      '<div class="fg"><label>PUTUSAN</label><select id="f9put"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Kategori Mesin</label><select id="f9kat"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Skor Dialogflow \u2264</label><input type="number" id="f9df" min="0" max="1" step="0.05" placeholder="0" style="width:100px"></div>'+
      '<div class="fg"><label>Skor NLI \u2264</label><input type="number" id="f9nli" min="0" max="1" step="0.05" placeholder="0" style="width:100px"></div>'+
      '<div class="fg"><label>Cari pertanyaan</label><input type="text" id="f9q" placeholder="kata kunci..."></div>'+
      '<span class="count" id="s9count"></span>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="s6wrap"><table class="s6table"><thead><tr>'+
      '<th>Prioritas</th><th>Pertanyaan User</th><th>Intent (Bot)</th><th>Kategori Mesin</th><th>Skor Bahasa</th><th>Skor DF</th><th>NLI</th><th>PUTUSAN &amp; Alasan</th><th>Kandidat / Terdekat</th><th>Intent Seharusnya</th>'+
    '</tr></thead><tbody id="s9body"></tbody></table></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s9save">Simpan ke sheet Analisis MKTA</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 10 \u2192</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  document.getElementById('s9save').onclick=saveStep9;
  ['f9qa','f9put','f9kat','f9df','f9nli','f9q'].forEach(id=>{ const el=document.getElementById(id); el.oninput=renderStep9; el.onchange=renderStep9; });
  loadStep9();
}

function loadStep9(){
  setStatus('run','<span class="sp"></span>Memuat data dari hasil Step 8...');
  STEP9.rows=[];
  api('step9load',{}).then(res=>{
    if(!res || !res.ok){ setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memuat.')); return; }
    STEP9.rows = res.rows||[];
    if(res.threshold!==undefined && res.threshold!==null && !isNaN(parseFloat(res.threshold))){ const qel=document.getElementById('f9qa'); if(qel) qel.value=parseFloat(res.threshold); }
    STEP9.rows.forEach(r=>{ r.edited=false; });
    const puts=[...new Set(STEP9.rows.map(r=>r.putusan).filter(Boolean))];
    const sel=document.getElementById('f9put');
    puts.forEach(p=>{ const o=document.createElement('option'); o.value=p; o.textContent=p; sel.appendChild(o); });
    const kats=[...new Set(STEP9.rows.map(r=>r.kategori).filter(Boolean))];
    const selk=document.getElementById('f9kat');
    kats.forEach(k=>{ const o=document.createElement('option'); o.value=k; o.textContent=k; selk.appendChild(o); });
    document.getElementById('mstatus').className='status';
    renderStep9();
    const done=STATE.steps[9]; if(done && done.status==='done') showDone(9);
  }).catch(e=>setStatus('err','\u26A0 '+esc(e.message||e)));
}

function s9num(v){ const n=parseFloat(String(v==null?'':v).replace(',','.')); return isNaN(n)?null:n; }
function s9fmt(v){ const n=s9num(v); return n===null ? esc(String(v==null?'':v)) : n.toFixed(2); }

function renderStep9(){
  const body=document.getElementById('s9body'); if(!body) return;
  const thr=parseFloat(document.getElementById('f9qa').value);
  const fput=document.getElementById('f9put').value;
  const fkat=document.getElementById('f9kat').value;
  const fdf=parseFloat(document.getElementById('f9df').value);
  const fnli=parseFloat(document.getElementById('f9nli').value);
  const fq=document.getElementById('f9q').value.trim().toLowerCase();
  const CAP=400; let shown=0, matched=0, underThr=0;
  const parts=[];
  const order = STEP9.rows.map((r,i)=>i);
  order.sort((a,b)=>{ const pa=s9num(STEP9.rows[a].prioritas), pb=s9num(STEP9.rows[b].prioritas); return (pb===null?-1:pb)-(pa===null?-1:pa); });
  order.forEach(i=>{
    const r=STEP9.rows[i];
    const qa=s9num(r.qa);
    const inThr = !isNaN(thr) ? (qa!==null && qa<thr) : true;
    if(inThr) underThr++;
    if(!inThr) return;
    if(fput && r.putusan!==fput) return;
    if(fkat && r.kategori!==fkat) return;
    if(!isNaN(fdf)){ const d=s9num(r.df); if(d===null || d>fdf) return; }
    if(!isNaN(fnli)){ const nn=s9num(r.nli); if(nn===null || nn>fnli) return; }
    if(fq && !(r.pertanyaan||'').toLowerCase().includes(fq)) return;
    matched++;
    if(shown>=CAP) return; shown++;
    const kand = (r.kandidat||r.terdekat||'');
    const alasan = r.alasan ? '<div style="color:#9aa4b2;font-size:11px;margin-top:3px">'+esc(r.alasan)+'</div>' : '';
    parts.push(
      '<tr>'+
      '<td>'+esc(r.prioritas||'')+'</td>'+
      '<td class="s6q">'+esc(r.pertanyaan||'')+'</td>'+
      '<td class="s6q">'+esc(r.intent||'')+'</td>'+
      '<td>'+esc(r.kategori||'')+'</td>'+
      '<td>'+s9fmt(r.