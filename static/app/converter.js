(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const app = $('#converterApp');
  if (!app) return;
  const files = [], MAX_FILES = 10, MAX_BYTES = 20 * 1024 * 1024;
  let running = false, sequence = 0;
  const states = {pending:'Siap diproses', uploading:'Mengunggah…', queued:'Dalam antrean server', running:'Membaca teks…', done:'Selesai', error:'Gagal'};
  const notice = (text, error = false) => {
    $('#converterMessage').textContent = text;
    $('#converterMessage').className = 'cv-message' + (error ? ' error' : '');
  };
  const element = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  function controls() {
    $('#convertFiles').disabled = running || !files.some(item => ['pending','error'].includes(item.state));
    $('#clearFiles').disabled = running || !files.length;
    $('#copyAll').disabled = $('#downloadAll').disabled = !files.some(item => item.result);
    $('#resultEmpty').hidden = files.some(item => item.result);
  }
  function renderQueue() {
    const queue = $('#fileQueue'); queue.replaceChildren();
    files.forEach(item => {
      const li = element('li'), info = element('div');
      info.append(element('strong', item.file.name, 'cv-file-name'));
      info.append(element('span', `${(item.file.size / 1024 / 1024).toFixed(2)} MB · ${item.error || states[item.state] || item.state}${item.total ? ` (${item.done}/${item.total} halaman)` : ''}`, 'cv-file-meta'));
      if (item.total && item.state === 'running') {
        const progress = element('progress'); progress.max = item.total; progress.value = item.done; progress.setAttribute('aria-label', 'Halaman selesai'); info.append(progress);
      }
      const remove = element('button', 'Hapus', 'cv-button'); remove.type = 'button'; remove.disabled = running;
      remove.setAttribute('aria-label', `Hapus ${item.file.name}`);
      remove.addEventListener('click', () => removeItem(item)); li.append(info, remove); queue.append(li);
    }); controls();
  }
  function addFiles(incoming) {
    const rejected = [];
    Array.from(incoming).forEach(file => {
      if (files.length >= MAX_FILES) { rejected.push('Maksimal 10 file.'); return; }
      if (!/\.(pdf|png|jpe?g|webp|bmp|tiff?)$/i.test(file.name)) { rejected.push(`${file.name}: format tidak didukung.`); return; }
      if (!file.size || file.size > MAX_BYTES) { rejected.push(`${file.name}: harus berisi data dan maksimal 20 MB.`); return; }
      files.push({id: ++sequence, file, state:'pending', done:0, total:0});
    }); renderQueue(); notice(rejected.length ? rejected.join(' ') : 'File ditambahkan. Klik Ekstrak teks untuk memulai.', !!rejected.length);
  }
  async function api(url, options = {}) {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetch(url, {...options, signal:controller.signal, credentials:'same-origin'});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('Respons server tidak valid. Periksa koneksi atau unggah ulang.'); }
      if (!response.ok || !data.ok) throw new Error(data.error || 'Permintaan gagal.'); return data;
    } finally { clearTimeout(timer); }
  }
  const discard = item => item.jobId ? api('/api/converter/discard/' + encodeURIComponent(item.jobId), {method:'POST'}).catch(() => {}) : Promise.resolve();
  function removeItem(item) {
    discard(item); item.node?.remove(); if(item.preview) URL.revokeObjectURL(item.preview);
    files.splice(files.indexOf(item), 1); renderQueue();
  }
  function saveText(text, filename) {
    const url = URL.createObjectURL(new Blob([text], {type:'text/plain;charset=utf-8'}));
    const link = element('a'); link.href = url; link.download = filename.replace(/[\\/:*?"<>|\x00-\x1f]/g,'_'); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else {
        const active = document.activeElement, area = element('textarea'); area.value = text; area.style.position = 'fixed'; area.style.left = '-9999px'; document.body.append(area); area.select();
        const copied = document.execCommand('copy'); area.remove(); active?.focus(); if (!copied) throw new Error('manual');
      } notice('Teks berhasil disalin.');
    } catch (_) { notice('Browser menolak penyalinan. Pilih teks lalu Ctrl+C, atau unduh .txt.', true); }
  }
  function showResult(item) {
    item.node?.remove();
    const card = element('article', undefined, 'cv-result'), text = element('textarea'); text.id = `cvText${item.id}`; text.value = item.result.text;
    card.append(element('h3', item.file.name));
    card.append(element('p', `${item.result.method} · ${item.result.pages} halaman · ${text.value.length.toLocaleString('id-ID')} karakter`, 'cv-help'));
    if (item.file.type.startsWith('image/')) {
      item.preview = URL.createObjectURL(item.file); const image = element('img'); image.src = item.preview; image.alt = 'Preview ' + item.file.name; image.className = 'cv-preview'; card.append(image);
    }
    if (item.result.warnings?.length) card.append(element('p', item.result.warnings.join('\n'), 'cv-warning'));
    const label = element('label', 'Teks hasil — bisa diedit sebelum disalin atau diunduh'); label.htmlFor = text.id; card.append(label, text);
    text.addEventListener('input', () => { item.result.text = text.value; });
    const actions = element('div', undefined, 'cv-controls');
    const copy = element('button', 'Salin teks', 'cv-button'), download = element('button', 'Unduh .txt', 'cv-button');
    copy.type = download.type = 'button'; copy.addEventListener('click', () => copyText(text.value)); download.addEventListener('click', () => saveText(text.value, item.file.name.replace(/\.[^.]+$/, '') + '.txt'));
    actions.append(copy, download); card.append(actions); item.node = card; $('#fileResults').append(card); controls();
  }
  async function convert() {
    if (running) return; running = true; renderQueue();
    const pending = files.filter(item => ['pending','error'].includes(item.state));
    for (const item of pending) {
      item.error = ''; item.done = item.total = 0;
      try {
        await discard(item); item.jobId = null; item.state = 'uploading'; renderQueue();
        const form = new FormData(); form.append('file', item.file);
        const started = await api('/api/converter/start', {method:'POST', body:form}); item.jobId = started.job_id;
        const deadline = Date.now() + 600000;
        while (Date.now() < deadline) {
          const {job} = await api('/api/converter/status/' + encodeURIComponent(item.jobId));
          item.state = job.state; item.done = job.done; item.total = job.total; renderQueue();
          if (job.state === 'done') { item.result = job.result; showResult(item); await discard(item); item.jobId = null; break; }
          if (['error','cancelled'].includes(job.state)) throw new Error(job.error || 'Proses dibatalkan.');
          await new Promise(resolve => setTimeout(resolve, 1200));
        }
        if (!item.result) throw new Error('Batas waktu menunggu terlampaui. Coba lagi dengan file lebih kecil.');
      } catch (error) {
        item.state = 'error'; item.error = error.name === 'AbortError' ? 'Koneksi timeout; coba lagi.' : error.message;
        await discard(item); item.jobId = null; renderQueue();
      }
    }
    running = false; renderQueue();
    const failed = files.filter(item => item.state === 'error').length;
    notice(failed ? `${failed} file gagal. Hasil lainnya tetap tersedia. Klik Ekstrak teks untuk mencoba ulang.` : 'Ekstraksi selesai. Periksa teks sebelum menggunakannya.', !!failed);
  }
  const picker = () => $('#fileInput').click();
  $('#chooseFiles').addEventListener('click', picker);
  $('#dropZone').addEventListener('click', picker);
  $('#dropZone').addEventListener('keydown', event => { if (['Enter',' '].includes(event.key)) { event.preventDefault(); picker(); } });
  $('#fileInput').addEventListener('change', event => { addFiles(event.target.files); event.target.value = ''; });
  ['dragenter','dragover'].forEach(type => $('#dropZone').addEventListener(type, event => { event.preventDefault(); $('#dropZone').classList.add('dragging'); }));
  ['dragleave','drop'].forEach(type => $('#dropZone').addEventListener(type, event => { event.preventDefault(); $('#dropZone').classList.remove('dragging'); if (type === 'drop') addFiles(event.dataTransfer.files); }));
  document.addEventListener('paste', event => {
    if (event.target.closest('textarea, input, [contenteditable="true"]')) return;
    const images = Array.from(event.clipboardData?.items || []).filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
    if (images.length) { event.preventDefault(); addFiles(images.map((image, index) => new File([image], `screenshot-${Date.now()}-${index + 1}.${image.type === 'image/jpeg' ? 'jpg' : image.type.split('/')[1] || 'png'}`, {type:image.type}))); }
  });
  $('#pasteImage').addEventListener('click', async () => {
    if (!navigator.clipboard?.read || !window.isSecureContext) { notice('Klik area unggah lalu tekan Ctrl+V (Mac: ⌘V) untuk menempel screenshot.'); $('#dropZone').focus(); return; }
    try {
      const clipboard = await navigator.clipboard.read(); const images = [];
      for (const item of clipboard) { const type = item.types.find(type => type.startsWith('image/')); if(type) { const blob = await item.getType(type); images.push(new File([blob], `screenshot-${Date.now()}-${images.length + 1}.${type.split('/')[1]}`, {type})); } }
      if (images.length) addFiles(images); else notice('Clipboard tidak berisi gambar. Ambil screenshot lalu tempel lagi.', true);
    } catch (_) { notice('Izin clipboard ditolak. Klik area unggah lalu tekan Ctrl+V.'); $('#dropZone').focus(); }
  });
  const combined = () => files.filter(item => item.result).map(item => `=== ${item.file.name} ===\n${item.result.text}`).join('\n\n');
  $('#copyAll').addEventListener('click', () => copyText(combined()));
  $('#downloadAll').addEventListener('click', () => saveText(combined(), 'converter-gabungan.txt'));
  $('#clearFiles').addEventListener('click', () => { if (files.some(item => item.result) && !confirm('Hapus semua hasil dari halaman? Unduh atau salin hasil penting dahulu.')) return; [...files].forEach(removeItem); notice('Antrean dibersihkan.'); });
  $('#convertFiles').addEventListener('click', convert);
  window.addEventListener('beforeunload', event => { if (running || files.some(item => item.result)) { event.preventDefault(); event.returnValue = ''; } });
  renderQueue();
})();
