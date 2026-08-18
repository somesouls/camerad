function currentMode(group){
  const box=document.querySelector('.srcbox[data-group="'+group+'"]');
  if(!box) return '';
  const on=box.querySelector('.srcbtn.on');
  return on?on.dataset.mode:'';
}

function runStep(n){
  const fd=new FormData();
  try{
    if(n===1){
      fd.append('start_date', val('f_start'));
      fd.append('end_date', val('f_end'));
      fd.append('bahasa', val('f_lang'));
      if(val('f_token')) fd.append('access_token', val('f_token'));
    } else if(n===2){
      if(currentMode('s2')==='prev'){ fd.append('from_step','1'); }
      else { const f=file('f_json'); if(!f) throw 'Pilih file JSON dulu.'; fd.append('json_file', f); }
    } else if(n===3){
      if(val('f_token')) fd.append('access_token', val('f_token'));
    } else if(n===4){
      if(val('f_ngrok')) STATE.ngrok_url = val('f_ngrok');
      fd.append('ngrok_url', val('f_ngrok'));
      const mode = currentMode('s4'); fd.append('mode', mode);
      if(mode==='manual'){
        const m=file('f_main'), t=file('f_train'), c=file('f_content');
        if(!m||!t||!c) throw 'Unggah ketiga file: workbook utama, Training Phrase, dan Intent.';
        fd.append('main_file', m); fd.append('training_file', t); fd.append('content_file', c);
      }
    } else if(n===5){
      if(val('f_ngrok')) STATE.ngrok_url = val('f_ngrok');
      fd.append('ngrok_url', val('f_ngrok'));
      if(currentMode('s5')==='prev'){ fd.append('from_step','4'); }
      else { const f=file('f_xlsx'); if(!f) throw 'Pilih file XLSX dulu.'; fd.append('xlsx_file', f); }
    } else if(n===7){
      if(val('f_ngrok')) STATE.ngrok_url = val('f_ngrok');
      fd.append('ngrok_url', val('f_ngrok'));
      const mode = currentMode('s7'); fd.append('mode', mode);
      if(mode==='manual'){ const f=file('f_xlsx'); if(!f) throw 'Pilih file XLSX (punya sheet Non Fallback).'; fd.append('xlsx_file', f); }
    } else if(n===12){
      const el=document.getElementById('f_avjson'); const fs=(el&&el.files)?el.files:[];
      if(!fs.length) throw 'Pilih minimal satu file JSON AWE Avaya.';
      for(let i=0;i<fs.length;i++) fd.append('json_files[]', fs[i]);
    } else if(n===13){
      if(val('f_token')) fd.append('access_token', val('f_token'));
    } else if(n===14){
      if(val('f_ngrok')) STATE.ngrok_url = val('f_ngrok');
      fd.append('ngrok_url', val('f_ngrok'));
      const mode = currentMode('s14'); fd.append('mode', mode);
      if(mode==='manual'){ const t=file('f_train'), c=file('f_content'); if(!t||!c) throw 'Unggah Training Phrase dan Intent.'; fd.append('training_file', t); fd.append('content_file', c); }
    } else if(n===15 || n===16){
      fd.append('ngrok_url', STATE.ngrok_url || '');
    }
  }catch(msg){ setStatus('err', esc(msg)); return; }

  const rb=document.getElementById('runBtn'); rb.disabled=true;
  document.getElementById('msummary').classList.remove('show');
  setStatus('run','<span class="sp"></span>Memproses Step '+n+'... Jangan tutup jendela ini.');

  if(n===14){ return runStep14Async(fd, rb); }

  api('step'+n, {method:'POST', body:fd}).then(res=>{
    rb.disabled=false;
    if(res && res.ok){
      adoptRun(res);
      STATE.steps[n]=res.artifact;
      setStatus('ok','\u2714 Step '+n+' selesai.');
      showSummary(n, res.artifact);
      showDone(n);
      renderRail();
    } else {
      const msg=(res&&res.error)?res.error:'Terjadi kesalahan.';
      setStatus('err','\u26A0 '+esc(msg));
      STATE.steps[n]={status:'error'};
      renderRail();
    }
  }).catch(e=>{
    setStatus('run','<span class="sp"></span>Gateway timeout, tapi server mungkin masih memproses Step '+n+'. Menunggu hasil otomatis... (jangan tutup jendela)');
    pollStepDone(n,
      (st)=>{ rb.disabled=false; STATE.steps[n]=st; setStatus('ok','\u2714 Step '+n+' selesai (terdeteksi otomatis).'); showSummary(n, st); showDone(n); renderRail(); },
      (why)=>{ rb.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)+' ('+esc(why)+'). Coba refresh halaman \u2014 bila step sudah hijau, hasil sudah tersimpan.'); }
    );
  });
}

function runStep14Async(fd, rb){
  setStatus('run','<span class="sp"></span>Memulai analisis di server (mode latar belakang, aman dari timeout)...');
  api('step14start', {method:'POST', body:fd}).then(res=>{
    if(!res || !res.ok || !res.job_id){ throw new Error((res&&res.error)?res.error:'Gagal memulai job.'); }
    STATE.avaya_job = res.job_id;
    setStatus('run','<span class="sp"></span>Job dimulai (id '+esc(res.job_id)+'). Memantau progres...');
    pollAvaya14(res.job_id, rb, 0);
  }).catch(e=>{ rb.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

function pollAvaya14(job, rb, tries){
  const max=720;
  const extra = STATE.ngrok_url ? '&ngrok_url='+encodeURIComponent(STATE.ngrok_url) : '';
  api('step14progress&job='+encodeURIComponent(job)+extra, {}).then(res=>{
    const p = (res && res.progress) ? res.progress : null;
    if(p && p.found){
      if(p.error){ rb.disabled=false; setStatus('err','\u26A0 Server melaporkan error:<br><pre style="white-space:pre-wrap;font-size:11px;max-height:220px;overflow:auto">'+esc(p.error)+'</pre>'); return; }
      const tot=p.total||0, dn=p.done||0, pct=tot?Math.round(dn/tot*100):0;
      const bar = tot? (' \u2014 '+dn+'/'+tot+' ('+pct+'%)') : '';
      setStatus('run','<span class="sp"></span>['+Math.round(p.elapsed||0)+'s] '+esc(p.stage||'memproses')+bar+'<br><span style="font-size:11px;color:var(--text2)">Berjalan di latar belakang \u2014 aman dari gateway timeout.</span>');
      if(p.finished){ fetchAvaya14(job, rb); return; }
    } else {
      setStatus('run','<span class="sp"></span>Menunggu job... (bila server baru di-restart, jalankan ulang Step 14)');
    }
    if(tries>=max){ rb.disabled=false; setStatus('err','\u26A0 Waktu tunggu habis (60 menit). Cek log Colab untuk detail.'); return; }
    setTimeout(()=>pollAvaya14(job, rb, tries+1), 5000);
  }).catch(e=>{
    if(tries>=max){ rb.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); return; }
    setTimeout(()=>pollAvaya14(job, rb, tries+1), 5000);
  });
}

function fetchAvaya14(job, rb){
  const extra = STATE.ngrok_url ? '&ngrok_url='+encodeURIComponent(STATE.ngrok_url) : '';
  setStatus('run','<span class="sp"></span>Analisis selesai. Mengambil &amp; menyimpan hasil...');
  api('step14fetch&job='+encodeURIComponent(job)+extra, {}).then(res=>{
    if(res && res.pending){ setTimeout(()=>fetchAvaya14(job, rb), 3000); return; }
    rb.disabled=false;
    if(res && res.ok && res.artifact){
      STATE.steps[14]=res.artifact;
      setStatus('ok','\u2714 Step 14 selesai.');
      showSummary(14, res.artifact);
      showDone(14);
      renderRail();
    } else {
      setStatus('err','\u26A0 '+esc((res&&res.error)?res.error:'Gagal mengambil hasil.'));
    }
  }).catch(e=>{ rb.disabled=false; setStatus('err','\u26A0 '+esc(e.message||e)); });
}

let STEP6 = {rows:[]};

function openModal6(st){
  const modal=document.getElementById('modal');
  modal.classList.add('wide');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">6</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="s6bar">'+
      '<div class="fg"><label>Catatan LLM</label><select id="f6cat"><option value="">Semua</option></select></div>'+
      '<div class="fg"><label>Confidence</label><select id="f6conf"><option value="">Semua</option><option>TINGGI</option><option>SEDANG</option><option>RENDAH</option></select></div>'+
      '<div class="fg"><label>Skor_Deteksi min (%)</label><input type="number" id="f6skor" min="0" max="100" step="1" style="width:120px" placeholder="0"></div>'+
      '<div class="fg"><label>Cari pertanyaan</label><input type="text" id="f6q" placeholder="kata kunci..."></div>'+
      '<span class="count" id="s6count"></span>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="s6wrap"><table class="s6table"><thead><tr>'+
      '<th>Pertanyaan User</th><th>Catatan LLM</th><th>Intent Judgement LLM</th><th>Isi Intent</th><th>Skor</th><th>Conf</th>'+
    '</tr></thead><tbody id="s6body"></tbody></table></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s6save">Simpan Perubahan</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 7 \u2192</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  document.getElementById('s6save').onclick=saveStep6;
  ['f6cat','f6conf','f6skor','f6q'].forEach(id=>{ const el=document.getElementById(id); el.oninput=renderStep6; el.onchange=renderStep6; });
  const wrap=document.querySelector('.s6wrap'); if(wrap) wrap.onscroll=closeS6Menus;
  if(!window.__s6docbound){ window.__s6docbound=true; document.addEventListener('mousedown', function(e){ if(!(e.target.closest && e.target.closest('.s6combo'))) closeS6Menus(); }); }
  loadStep6();
}

function loadStep6(){
  setStatus('run','<span class="sp"></span>Memuat data dari hasil Step 5...');
  STEP6.rows=[];
  api('step6load',{}).then(res=>{
    if(!res || !res.ok){ setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memuat.')); return; }
    STEP6.rows = res.rows||[];
    STEP6.rows.forEach(r=>{ r.edited=false; syncRowDerived(r); });
    const cats=[...new Set(STEP6.rows.map(r=>r.catatan).filter(Boolean))];
    const sel=document.getElementById('f6cat');
    cats.forEach(c=>{ const o=document.createElement('option'); o.value=c; o.textContent=c; sel.appendChild(o); });
    document.getElementById('mstatus').className='status';
    renderStep6();
    const done=STATE.steps[6];
    if(done && done.status==='done'){ showDone(6); }
  }).catch(e=>setStatus('err','\u26A0 '+esc(e.message||e)));
}

function syncRowDerived(r){
  const opt=(r.options||[]).find(o=>o.id===r.intent);
  if(opt){ r.isi=opt.ans; r.skor=opt.skor; r.conf=opt.conf; }
  else { r.isi=''; r.skor=''; r.conf=''; }
}

function parseSkor(s){ const v=parseFloat(String(s==null?'':s).replace('%','')); return isNaN(v)?-1:v; }

function renderStep6(){
  const body=document.getElementById('s6body'); if(!body) return;
  const fcat=document.getElementById('f6cat').value;
  const fconf=document.getElementById('f6conf').value;
  const fskor=parseFloat(document.getElementById('f6skor').value);
  const fq=document.getElementById('f6q').value.trim().toLowerCase();
  const CAP=400;
  let shown=0, matched=0;
  const parts=[];
  STEP6.rows.forEach((r,i)=>{
    if(fcat && r.catatan!==fcat) return;
    if(fconf && (r.conf||'')!==fconf) return;
    if(!isNaN(fskor) && parseSkor(r.skor) < fskor) return;
    if(fq && !(r.pertanyaan||'').toLowerCase().includes(fq)) return;
    matched++;
    if(shown>=CAP) return;
    shown++;
    const pill = r.catatan==='TINDAK LANJUT'?'t':(r.catatan==='PERTANYAAN TIDAK MANDIRI'?'n':'m');
    parts.push(
      '<tr>'+
      '<td class="s6q">'+esc(r.pertanyaan||'')+'</td>'+
      '<td><span class="s6pill '+pill+'">'+esc(r.catatan||'-')+'</span></td>'+
      '<td><div class="s6combo"><input class="s6intent'+(r.edited?' edited':'')+'" data-i="'+i+'" value="'+esc(r.intent||'')+'" autocomplete="off"><button type="button" class="s6arrow" data-i="'+i+'" tabindex="-1">\u25be</button><div class="s6menu" id="menu'+i+'"></div></div></td>'+
      '<td><div class="s6isi" id="isi'+i+'">'+esc(r.isi||'')+'</div></td>'+
      '<td id="skor'+i+'">'+esc(r.skor||'')+'</td>'+
      '<td id="conf'+i+'">'+esc(r.conf||'')+'</td>'+
      '</tr>'
    );
  });
  body.innerHTML=parts.join('');
  body.querySelectorAll('.s6intent').forEach(inp=>{ const i=parseInt(inp.dataset.i,10); inp.oninput=()=>onIntentChange(i, inp.value); inp.onfocus=()=>openS6Menu(i); });
  body.querySelectorAll('.s6arrow').forEach(btn=>{ const i=parseInt(btn.dataset.i,10); btn.onclick=(e)=>{ e.preventDefault(); const m=document.getElementById('menu'+i); if(m && m.classList.contains('open')) closeS6Menus(); else openS6Menu(i); }; });
  document.getElementById('s6count').textContent = matched+' baris'+(matched>CAP?(' (tampil '+CAP+', persempit dgn filter)'):'');
}

function onIntentChange(i, value){
  const r=STEP6.rows[i]; if(!r) return;
  r.intent=value; r.edited=true;
  syncRowDerived(r);
  const isi=document.getElementById('isi'+i); if(isi) isi.textContent=r.isi||'';
  const sk=document.getElementById('skor'+i); if(sk) sk.textContent=r.skor||'';
  const cf=document.getElementById('conf'+i); if(cf) cf.textContent=r.conf||'';
  const inp=document.querySelector('.s6intent[data-i="'+i+'"]'); if(inp) inp.classList.add('edited');
}

let STEP8 = {counts:[], total:0};

function step8Mode(){
  const on=document.querySelector('#s8toggle .srcbtn.on');
  return on?on.dataset.mode:'auto';
}

function openModal8(st){
  const modal=document.getElementById('modal');
  modal.innerHTML =
    '<div class="mhead"><div class="mbadge">8</div>'+
      '<div><h2>'+esc(st.title)+'</h2><p>'+esc(st.sub)+'</p></div>'+
      '<button class="mx" id="mxBtn" title="Tutup">&times;</button></div>'+
    '<div class="mbody">'+
      '<div class="field"><label>Server URL <span style="font-weight:400;color:var(--text2)">(opsional)</span></label><input type="text" id="f_ngrok" placeholder="Biarkan kosong (mode Colab)"><div class="hint">Menilai baris QA Conf rendah dengan Qwen (kolom PUTUSAN &amp; ALASAN). Diproses bertahap agar tak kena timeout. Kosongkan untuk localhost:8000.</div></div>'+
      '<div class="srcbox" id="s8toggle">'+
        '<button type="button" class="srcbtn on" data-mode="auto">Otomatis (hasil Step 7)</button>'+
        '<button type="button" class="srcbtn" data-mode="manual">Unggah XLSX</button>'+
      '</div>'+
      '<div class="field" id="s8upwrap" style="display:none"><label>File XLSX (ber-sheet "QA Conf MKTA")</label><input type="file" id="f_x8" accept=".xlsx"><button class="btn btn-sec" id="s8muat" style="margin-top:8px">Muat data</button></div>'+
      '<div class="field"><label>Ambang Skor Pemrosesan Bahasa (QA Conf) &mdash; proses baris di bawah nilai ini</label>'+
        '<div id="s8list" style="margin-top:6px"><div class="hint">Memuat...</div></div>'+
      '</div>'+
    '</div>'+
    '<div class="status" id="mstatus"></div>'+
    '<div class="mfoot">'+
      '<button class="btn" id="s8run" disabled>Lempar ke Qwen</button>'+
      '<button class="btn btn-sec" id="dlBtn" style="display:none">Unduh Hasil</button>'+
      '<button class="btn btn-ok" id="nextBtn" style="display:none">Lanjut ke Step 9 \u2192</button>'+
    '</div>';
  document.getElementById('overlay').classList.add('show');
  document.getElementById('mxBtn').onclick=closeModal;
  if(STATE.ngrok_url){ const el=document.getElementById('f_ngrok'); if(el) el.value=STATE.ngrok_url; }
  document.getElementById('s8run').onclick=runStep8;
  document.getElementById('s8muat').onclick=()=>loadStep8();
  document.querySelectorAll('#s8toggle .srcbtn').forEach(b=>{
    b.onclick=()=>{
      document.querySelectorAll('#s8toggle .srcbtn').forEach(x=>x.classList.remove('on'));
      b.classList.add('on');
      const manual=b.dataset.mode==='manual';
      document.getElementById('s8upwrap').style.display=manual?'':'none';
      document.getElementById('s8run').disabled=true;
      if(manual){ document.getElementById('s8list').innerHTML='<div class="hint">Unggah file lalu klik <b>Muat data</b>.</div>'; document.getElementById('mstatus').className='status'; }
      else { loadStep8(); }
    };
  });
  loadStep8();
}

function loadStep8(){
  const mode=step8Mode();
  const fd=new FormData(); fd.append('mode', mode);
  if(mode==='manual'){ const f=file('f_x8'); if(!f){ setStatus('err','\u26A0 Pilih file XLSX dulu.'); return; } fd.append('xlsx_file', f); }
  setStatus('run','<span class="sp"></span>Menghitung jumlah baris per ambang...');
  api('step8load',{method:'POST', body:fd}).then(res=>{
    if(!res || !res.ok){ setStatus('err','\u26A0 '+esc((res&&res.error)||'Gagal memuat.')); return; }
    STEP8.counts = res.counts||[]; STEP8.total = res.total||0;
    document.getElementById('mstatus').className='status';
    renderStep8();
    const done=STATE.steps[8]; if(done && done.status==='done') showDone(8);
  }).catch(e=>setStatus('err','\u26A0 '+esc(e.message||e)));
}

function renderStep8(){
  const box=document.getElementById('s8list'); if(!box) return;
  const fmt=t=>('< '+String(t).replace('.',','));
  let html='<div style="font-size:12.5px;color:var(--text2);margin-bottom:8px">Total baris QA Conf MKTA: <b>'+STEP8.total+'</b></div>';
  html+='<div style="display:flex;flex-direction:column;gap:2px">';
  STEP8.counts.forEach((c,idx)=>{
    const checked = (Math.abs(c.th-0.6)<1e-9) ? ' checked' : '';
    html+='<label style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;cursor:pointer">'+
      '<input type="radio" name="s8th" value="'+c.th+'"'+checked+'>'+
      '<span style="font-weight:600;min-width:70px">'+fmt(c.th)+'</span>'+
      '<span style="color:var(--text2);font-size:12.5px">'+c.count+' baris</span>'+
      '</label>';
  });
  html+='</div>';
  box.innerHTML=html;
  box.querySelectorAll('input[name=s8th]').forEach(r=>{ r.onchange=updateStep8Btn; });
  updateStep8Btn();
  document.getElementById('s8run').disabled=false;
}

function step8Selected(){
  const r=document.querySelector('input[name=s8th]:checked');
  if(!r) return null;
  const th=parseFloat(r.value);
  const c=STEP8.counts.find(x=>Math.abs(x.th-th)<1e-9);
  return {th:th, count:c?c.count:0};
}

function updateStep8Btn(){
  const s=step8Selected(); const btn=document.getElementById('s8run'); if(!btn) return;
  btn.textContent = s ? ('Lempar '+s.count+' baris ke Qwen') : 'Lempar ke Qwen';
}

