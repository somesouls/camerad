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
      '<td>'+s9fmt(r.qa)+'</td>'+
      '<td>'+s9fmt(r.df)+'</td>'+
      '<td>'+s9fmt(r.nli)+'</td>'+
      '<td>'+esc(r.putusan||'')+alasan+'</td>'+
      '<td class="s6q">'+esc(kand)+'</td>'+
      '<td><input class="s6intent" data-i="'+i+'" value="'+esc(r.seharusnya||'')+'" placeholder="ketik intent..."></td>'+
      '</tr>'
    );
  });
  body.innerHTML=parts.join('');
  body.querySelectorAll('.s6intent').forEach(inp=>{ const i=parseInt(inp.dataset.i,10); inp.oninput=()=>{ STEP9.rows[i].seharusnya=inp.value; STEP9.rows[i].edited=true; inp.classList.add('edited'); }; });
  document.getElementById('s9count').textContent = matched+' tampil \u00b7 '+underThr+' akan disimpan (Skor<'+(isNaN(thr)?'-':thr)+')'+(matched>CAP?(' \u00b7 tampil '+CAP):'');
}

function saveStep9(){
  const thr=parseFloat(document.getElementById('f9qa').value);
  if(isNaN(thr)||thr<=0||thr>1){ setStatus('err','\u26A0 Isi ambang QA Conf yang valid (0-1).'); return; }
  const edits={};
  STEP9.rows.forEach(r=>{ if(r.edited) edits[String(r.row)] = r.seharusnya||''; });
  const fd=new FormData(); fd.append('threshold', String(thr)); fd.append('edits', JSON.stringify(edits));
  const btn=document.getElementById('s9save'); btn.disabled=true;
  setStatus('run','<span class="sp"></span>Menyimpan sheet Analisis MKTA (QA Conf < '+thr+')...');
  api('step9',{method:'POST', body:fd}).then(res=>{
    btn.disabled=false;
    if(res && res.ok){ STATE.steps[9]=res.artifact; setStatus('ok','\u2714 Tersimpan '+(res.baris||0)+' baris ke sheet Analisis MKTA. File siap diunduh.'); showDone(9); renderRail(); }
    else { setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal menyimpan.')); }
  }).catch(e=>{ btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

function openModal10(st){
  const modal=document.getElementById('modal');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">10</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody">'+
      '<div class="hint">Membuat <b>sheet Rekap LM</b> (rekap per Nomor Rekaman + kolom CATATAN_LM), <b>sheet LM</b> &amp; <b>sheet Pembaruan</b> dari hasil Step 9, lalu menghasilkan Excel Utama + CSV. TGL Penyusunan = tanggal rekaman tiap baris.</div>'+
      '<div class="field"><label>Nama Penyusun</label><input type="text" id="f10nama" placeholder="mis. SAMSUL HIDAYATULLAH" value="'+esc((STATE.steps[10]&&STATE.steps[10].penyusun)||'')+'"><div class="hint">Dipakai di kolom NAMA PENYUSUN (sheet &amp; CSV Pembaruan).</div></div>'+
      srcToggle('s10',[{mode:'prev',label:'Dari hasil Step 9',enabled:true},{mode:'upload',label:'Unggah Excel (hasil edit Step 9)'}])+
      '<div class="field srcfield" data-group="s10" data-mode="prev"><div class="hint">Memakai artefak <b>Step 9</b> (sheet Analisis MKTA) dari server. Tidak perlu unggah ulang.</div></div>'+
      '<div class="field srcfield" data-group="s10" data-mode="upload"><label>File Excel (.xlsx, hasil edit Step 9)</label><input type="file" id="f_x10" accept=".xlsx"><div class="hint">Harus punya sheet <b>Analisis MKTA</b> (idealnya juga <b>Analisis Fallback</b>). Dipakai bila Anda mengedit manual hasil Step 9 di Excel.</div></div>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="summary" id="msummary"></div>'+
    '<div class="mfoot" id="s10foot">'+
      '<button class="btn" id="s10run">Buat Laporan</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  bindSourceToggle();
  document.getElementById('s10run').onclick=runStep10;
  const done=STATE.steps[10];
  if(done && done.status==='done'){ showSummary(10, done); step10Downloads(); }
}

function step10Downloads(){
  const foot=document.getElementById('s10foot'); if(!foot) return;
  const base=ENDPOINT+'?action=download&run='+encodeURIComponent(RUN);
  foot.innerHTML =
    '<button class="btn" id="s10run">Buat Ulang</button>'+
    '<button class="btn btn-sec" id="dlExcel">Unduh Excel Utama</button>'+
    '<button class="btn btn-sec" id="dlLm">Unduh CSV LM</button>'+
    '<button class="btn btn-sec" id="dlPemb">Unduh CSV Pembaruan</button>';
  document.getElementById('s10run').onclick=runStep10;
  document.getElementById('dlExcel').onclick=()=>{ window.location = base+'&step=10'; };
  document.getElementById('dlLm').onclick=()=>{ window.location = base+'&part=lm'; };
  document.getElementById('dlPemb').onclick=()=>{ window.location = base+'&part=pembaruan'; };
}

function runStep10(){
  const btn=document.getElementById('s10run'); if(btn) btn.disabled=true;
  document.getElementById('msummary').classList.remove('show');
  const nama=(document.getElementById('f10nama')||{}).value||'';
  const fd=new FormData(); fd.append('penyusun', nama);
  if(currentMode('s10')==='upload'){ const fx=file('f_x10'); if(!fx){ if(btn) btn.disabled=false; setStatus('err','\u26A0 Pilih file Excel (.xlsx) hasil edit Step 9 dulu, atau pilih "Dari hasil Step 9".'); return; } fd.append('xlsx_file', fx); }
  setStatus('run','<span class="sp"></span>Menyusun laporan LM &amp; Pembaruan...');
  api('step10',{method:'POST', body:fd}).then(res=>{
    if(res && res.ok){
      STATE.steps[10]=res.artifact;
      setStatus('ok','\u2714 Laporan selesai. LM: '+(res.lm_rows||0)+' baris \u00b7 Pembaruan: '+(res.pembaruan_rows||0)+' baris.');
      showSummary(10, res.artifact);
      step10Downloads();
      renderRail();
    } else {
      if(btn) btn.disabled=false;
      setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal membuat laporan.'));
    }
  }).catch(e=>{ if(btn) btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

function openModal11(st){
  const modal=document.getElementById('modal');
  const ng=(STATE.ngrok_url)||'';
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">11</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody">'+
      '<div class="hint">Menyuntikkan training phrase baru (dari <b>Analisis Fallback</b> + <b>Analisis MKTA</b>, baris TINDAK LANJUT) ke file <b>usersays_&lt;lang&gt;.json</b> di dalam ZIP export Dialogflow. Diproses backend Python. Output: <b>Excel + sheet "Status Pembaruan"</b> dan <b>ZIP usersays terbaru</b> (file lain digabung utuh).</div>'+
      '<div class="field"><label>Bahasa target</label>'+
        '<select id="f11lang"><option value="id">usersays_id (Indonesia)</option><option value="en">usersays_en (English)</option></select></div>'+
      '<div class="field"><label>ZIP export Dialogflow</label><input type="file" id="f11zip" accept=".zip"><div class="hint">Berisi folder <code>intents/</code> dengan file <code>*_usersays_id/en.json</code>.</div></div>'+
      '<div class="field"><label>Sumber daftar frasa</label>'+
        '<div class="srcbox" data-group="s11">'+
          '<button type="button" class="srcbtn on" data-mode="auto">Dari step sebelumnya</button>'+
          '<button type="button" class="srcbtn" data-mode="manual">Upload workbook</button>'+
        '</div></div>'+
      '<div class="srcfield" data-group="s11" data-mode="auto"><div class="hint">Workbook diambil otomatis dari hasil Step 10 (atau Step 9): sheet Analisis Fallback &amp; Analisis MKTA.</div></div>'+
      '<div class="srcfield" data-group="s11" data-mode="manual"><div class="field"><label>Workbook pipeline (.xlsx)</label><input type="file" id="f11wb" accept=".xlsx"><div class="hint">Harus punya sheet Analisis Fallback &amp; Analisis MKTA.</div></div></div>'+
      '<div class="field"><label>Ngrok URL (opsional)</label><input type="text" id="f_ngrok" placeholder="kosongkan bila mode all-in-one Colab" value="'+esc(ng)+'"><div class="hint">Diabaikan bila backend dijalankan lokal di Colab (default).</div></div>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="summary" id="msummary"></div>'+
    '<div class="mfoot" id="s11foot">'+
      '<button class="btn" id="s11run">Perbarui usersays</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  bindSourceToggle();
  const done=STATE.steps[11];
  if(done && done.summary && done.summary.bahasa){ const sel=document.getElementById('f11lang'); if(sel) sel.value=done.summary.bahasa; }
  document.getElementById('s11run').onclick=runStep11;
  if(done && done.status==='done'){ showSummary(11, done); step11Downloads(); }
}

function step11Downloads(){
  const foot=document.getElementById('s11foot'); if(!foot) return;
  const base=ENDPOINT+'?action=download&run='+encodeURIComponent(RUN);
  foot.innerHTML =
    '<button class="btn" id="s11run">Perbarui Ulang</button>'+
    '<button class="btn btn-sec" id="dlXls11">Unduh Excel (Status Pembaruan)</button>'+
    '<button class="btn btn-sec" id="dlZip11">Unduh ZIP usersays</button>';
  document.getElementById('s11run').onclick=runStep11;
  document.getElementById('dlXls11').onclick=()=>{ window.location = base+'&step=11'; };
  document.getElementById('dlZip11').onclick=()=>{ window.location = base+'&part=zip11'; };
}

function runStep11(){
  const fd=new FormData();
  try{
    const lang=val('f11lang')||'id';
    fd.append('lang', lang);
    const mode=currentMode('s11')||'auto';
    fd.append('mode', mode);
    const ng=val('f_ngrok'); if(ng){ STATE.ngrok_url=ng; fd.append('ngrok_url', ng); }
    const z=file('f11zip'); if(!z) throw 'Unggah ZIP export Dialogflow dulu.';
    fd.append('df_zip', z);
    if(mode==='manual'){ const w=file('f11wb'); if(!w) throw 'Opsi Upload workbook: pilih file workbook .xlsx (punya sheet Analisis).'; fd.append('workbook', w); }
  }catch(msg){ setStatus('err', esc(msg)); return; }
  const btn=document.getElementById('s11run'); if(btn) btn.disabled=true;
  const sm=document.getElementById('msummary'); if(sm) sm.classList.remove('show');
  setStatus('run','<span class="sp"></span>Mengirim ZIP + frasa ke backend & memperbarui usersays...');
  api('step11',{method:'POST', body:fd}).then(res=>{
    if(res && res.ok){
      STATE.steps[11]=res.artifact;
      const s=res.stats||{};
      setStatus('ok','\u2714 Selesai. Ditambahkan: '+(s.ditambahkan||0)+' \u00b7 Duplikat: '+(s.duplikat||0)+' \u00b7 Tidak ketemu: '+(s.intent_tidak_ketemu||0)+' \u00b7 File diperbarui: '+(s.file_diperbarui||0)+'.');
      showSummary(11, res.artifact);
      step11Downloads();
      renderRail();
    } else {
      if(btn) btn.disabled=false;
      setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memperbarui usersays.'));
    }
  }).catch(e=>{ if(btn) btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

function closeS6Menus(){ document.querySelectorAll('.s6menu.open').forEach(m=>m.classList.remove('open')); }

function openS6Menu(i){
  closeS6Menus();
  const r=STEP6.rows[i]; if(!r) return;
  const menu=document.getElementById('menu'+i); if(!menu) return;
  const opts=r.options||[];
  if(!opts.length){
    menu.innerHTML='<div class="s6opt s6empty">Tidak ada rekomendasi untuk baris ini</div>';
  } else {
    menu.innerHTML=opts.map((o,k)=>'<div class="s6opt" data-id="'+esc(o.id)+'"><div class="s6opt-id">'+(k+1)+'. '+esc(o.id)+'</div><div class="s6opt-meta">Skor '+esc(o.skor||'-')+' \u00b7 '+esc(o.conf||'-')+'</div></div>').join('');
    menu.querySelectorAll('.s6opt').forEach(el=>{ el.onmousedown=(e)=>{ e.preventDefault(); const id=el.getAttribute('data-id'); const inp=document.querySelector('.s6intent[data-i="'+i+'"]'); if(inp){ inp.value=id; } onIntentChange(i, id); closeS6Menus(); }; });
  }
  const inp=document.querySelector('.s6intent[data-i="'+i+'"]');
  if(inp){ const rect=inp.getBoundingClientRect(); menu.style.left=rect.left+'px'; menu.style.top=(rect.bottom+4)+'px'; menu.style.minWidth=Math.max(260, rect.width+40)+'px'; }
  menu.classList.add('open');
}

function saveStep6(){
  const edits=STEP6.rows.map(r=>({row:r.row, intent:r.intent||'', isi:r.isi||''}));
  const fd=new FormData(); fd.append('edits', JSON.stringify(edits));
  const btn=document.getElementById('s6save'); btn.disabled=true;
  setStatus('run','<span class="sp"></span>Menyimpan '+edits.length+' baris & membuat XLSX final...');
  api('step6',{method:'POST', body:fd}).then(res=>{
    btn.disabled=false;
    if(res && res.ok){ STATE.steps[6]=res.artifact; setStatus('ok','\u2714 Tersimpan. File final siap diunduh.'); showDone(6); renderRail(); }
    else { setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal menyimpan.')); }
  }).catch(e=>{ btn.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

function val(id){ const el=document.getElementById(id); return el?el.value.trim():''; }
function file(id){ const el=document.getElementById(id); return (el&&el.files&&el.files[0])?el.files[0]:null; }

document.getElementById('resetBtn').onclick=()=>{
  if(!confirm('Arsipkan dataset aktif? Data tetap tersimpan di database & bisa dimuat ulang lewat "Muat Dataset". Tampilan akan dikosongkan.')) return;
  api('reset',{}).then(()=>{
    localStorage.removeItem('dfp_run');
    RUN='';
    STATE={steps:{}};
    updateRunLabel();
    renderRail();
  });
};

const _ldb=document.getElementById('loadDsBtn'); if(_ldb) _ldb.onclick=openDatasetPicker;

fetch(ENDPOINT+'?action=state').then(r=>r.json()).then(res=>{
  if(res){
    STATE.steps = res.steps || {};
    if(res.ngrok_url) STATE.ngrok_url = res.ngrok_url;
    STATE.range_start = res.range_start || '';
    STATE.range_end = res.range_end || '';
    STATE.lang = res.lang || '';
    STATE.label = res.label || '';
    if(res.run){ RUN = res.run; localStorage.setItem('dfp_run', RUN); }
    else { RUN=''; localStorage.removeItem('dfp_run'); }
  }
  updateRunLabel();
  renderRail();
}).catch(()=>{ updateRunLabel(); renderRail(); });
renderRail();
