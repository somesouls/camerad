(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
  const esc = value => String(value == null ? '' : value).replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
  const GROUPS = [
    {group:'RAG Chatbot & Agent',areas:['rag_config']},{group:'Dialogflow',areas:['dialogflow','df_webhook']},
    {group:'AWE Chat',areas:['awe','assess']},{group:'Kelola Data',areas:['awe_manage']},
    {group:'Sosmed & Laporan',areas:['common']},{group:'Peraturan',areas:['peraturan']},
    {group:'Voicebot',areas:['voicebot']},{group:'Umum',areas:['users']}
  ];
  let catalog = {areas:[],caps:[]}, areaLabel = {}, roles = [], users = [], menuGroups = [], menus = [], baseline = [];
  const ui = {
    rtb:$('#rtb'), dialog:$('#roleDialog'), rmsg:$('#rmsg'), origKey:$('#origKey'), rkey:$('#rkey'),
    rlabel:$('#rlabel'), rlevel:$('#rlevel'), mrole:$('#mrole'), muser:$('#muser'), guser:$('#guser'),
    roleMenus:$('#mroleMenus'), userMenus:$('#muserMenus'), gareas:$('#gareas')
  };

  function message(el, text, ok) { el.textContent = text; el.className = 'message ' + (ok ? 'ok' : 'error'); }
  function clearMessage(el) { el.textContent = ''; el.className = 'message'; }
  async function request(url, options) {
    const res = await fetch(url, options); let data;
    try { data = await res.json(); } catch (_) { throw new Error('Respons server tidak valid.'); }
    if (!res.ok || !data.ok) throw new Error(data.error || 'Permintaan gagal.');
    return data;
  }
  async function busy(button, task) {
    const old = button.textContent; button.disabled = true; button.textContent = 'Memproses…';
    try { return await task(); } finally { button.disabled = false; button.textContent = old; }
  }

  function activate(panelId) {
    $$('.step').forEach(tab => { const on = tab.dataset.panel === panelId; tab.classList.toggle('is-active', on); tab.setAttribute('aria-selected', on ? 'true' : 'false'); });
    $$('.workspace-panel').forEach(panel => { const on = panel.id === panelId; panel.classList.toggle('is-active', on); panel.setAttribute('aria-hidden', on ? 'false' : 'true'); });
    history.replaceState(null, '', '#' + panelId);
  }
  $$('.step').forEach(tab => tab.addEventListener('click', () => activate(tab.dataset.panel)));

  function areaGroups(mode) {
    return GROUPS.map(group => {
      const items = group.areas.filter(key => Object.prototype.hasOwnProperty.call(areaLabel, key));
      if (!items.length) return '';
      const body = items.map(key => mode === 'role'
        ? `<label class="area-option"><input type="checkbox" data-area="${esc(key)}"><span>${esc(areaLabel[key] || key)} <small>(${esc(key)})</small></span></label>`
        : `<label class="area-option"><span>${esc(areaLabel[key] || key)} <small>(${esc(key)})</small></span><select data-garea="${esc(key)}"><option value="">Ikuti peran</option><option value="1">Izinkan</option><option value="0">Tolak</option></select></label>`).join('');
      return `<details class="area-group" open><summary>${esc(group.group)}</summary><div class="area-body">${body}</div></details>`;
    }).join('');
  }
  function buildRoleForm() {
    $('#rcaps').innerHTML = catalog.caps.map(cap => `<label class="chip-check"><input type="checkbox" data-cap="${esc(cap)}">${esc(cap)}</label>`).join('');
    $('#rareas').innerHTML = areaGroups('role'); ui.gareas.innerHTML = areaGroups('grant');
  }
  function resetRoleForm() {
    clearMessage(ui.rmsg); ui.origKey.value = ''; ui.rkey.value = ''; ui.rkey.disabled = false; ui.rlabel.value = ''; ui.rlevel.value = '4';
    $$('#rcaps input').forEach(box => box.checked = box.dataset.cap === 'read'); $$('#rareas input').forEach(box => box.checked = false);
    $('#rtitle').textContent = 'Tambah peran';
  }
  function openRole(role) {
    resetRoleForm();
    if (role) {
      ui.origKey.value = role.key; ui.rkey.value = role.key; ui.rkey.disabled = true; ui.rlabel.value = role.label || ''; ui.rlevel.value = role.level;
      $$('#rcaps input').forEach(box => box.checked = (role.caps || []).includes(box.dataset.cap));
      $$('#rareas input').forEach(box => box.checked = (role.areas || []).includes(box.dataset.area));
      $('#rtitle').textContent = 'Ubah peran: ' + (role.label || role.key);
    }
    ui.dialog.showModal(); setTimeout(() => (role ? ui.rlabel : ui.rkey).focus(), 0);
  }
  function renderRoles() {
    const q = $('#roleSearch').value.trim().toLowerCase(); let shown = 0;
    ui.rtb.innerHTML = roles.map(role => {
      const hay = [role.key, role.label, ...(role.caps || []), ...(role.areas || [])].join(' ').toLowerCase(); if (q && !hay.includes(q)) return '';
      shown += 1;
      const caps = (role.caps || []).map(x => `<span class="tag">${esc(x)}</span>`).join('') || '—';
      const areas = (role.areas || []).map(x => `<span class="tag">${esc(areaLabel[x] || x)}</span>`).join('') || '—';
      return `<tr><td class="role-name"><b>${esc(role.label || role.key)} ${role.is_system ? '<span class="tag system">sistem</span>' : ''}</b><span class="role-key">${esc(role.key)}</span></td><td>${Number(role.level || 0)}</td><td>${caps}</td><td>${areas}</td><td>${Number(role.user_count || 0)}</td><td><div class="row-actions"><button class="button secondary" data-edit="${esc(role.key)}">Ubah</button>${role.is_system ? '' : `<button class="button ghost danger" data-delete="${esc(role.key)}">Hapus</button>`}</div></td></tr>`;
    }).join('');
    $('#roleCount').textContent = shown + ' dari ' + roles.length + ' peran'; $('#roleEmpty').hidden = shown !== 0;
  }
  function fillOptions() {
    const roleOptions = '<option value="">— pilih peran —</option>' + roles.map(r => `<option value="${esc(r.key)}">${esc(r.label || r.key)}</option>`).join('');
    const userOptions = '<option value="">— pilih pengguna —</option>' + users.map(u => `<option value="${u.id}">${esc(u.username)}${u.nama ? ' (' + esc(u.nama) + ')' : ''}</option>`).join('');
    ui.mrole.innerHTML = roleOptions; ui.muser.innerHTML = userOptions; ui.guser.innerHTML = userOptions;
  }

  function menuByGroup() { const out = {}; menus.forEach(menu => (out[menu.group] = out[menu.group] || []).push(menu)); return out; }
  function roleAccordion() {
    const grouped = menuByGroup();
    return menuGroups.map(group => {
      const items = grouped[group.key] || []; if (!items.length) return '';
      const rows = items.map(menu => { const fixed = baseline.includes(menu.key); return `<label class="permission-row" data-search="${esc((menu.label + ' ' + menu.path).toLowerCase())}"><input type="checkbox" data-mkey="${esc(menu.key)}" ${fixed ? 'checked disabled' : ''}><span class="permission-copy"><b>${esc(menu.label)} ${fixed ? '<em class="baseline">• selalu tampil</em>' : ''}</b><small>${esc(menu.path)}</small></span></label>`; }).join('');
      return `<details class="permission-group" open><summary>${esc(group.label)}<span class="group-count">${items.length} menu</span></summary><div class="permission-rows">${rows}</div></details>`;
    }).join('');
  }
  function userAccordion() {
    const grouped = menuByGroup();
    return menuGroups.map(group => {
      const items = grouped[group.key] || []; if (!items.length) return '';
      const rows = items.map(menu => { const fixed = baseline.includes(menu.key); return `<div class="permission-row" data-search="${esc((menu.label + ' ' + menu.path).toLowerCase())}"><span class="permission-copy"><b>${esc(menu.label)}</b><small>${esc(menu.path)}</small></span><select data-mkey="${esc(menu.key)}" ${fixed ? 'disabled' : ''}><option value="">Ikuti peran</option><option value="1">Tampilkan</option><option value="0">Sembunyikan</option></select></div>`; }).join('');
      return `<details class="permission-group" open><summary>${esc(group.label)}<span class="group-count">${items.length} menu</span></summary><div class="permission-rows">${rows}</div></details>`;
    }).join('');
  }
  function filterPermissions(input, root) {
    const q = input.value.trim().toLowerCase(); $$('.permission-group', root).forEach(group => { let visible = 0; $$('.permission-row', group).forEach(row => { const show = !q || row.dataset.search.includes(q); row.hidden = !show; if (show) visible += 1; }); group.hidden = visible === 0; });
  }
  function updateRoleSummary(dirty = true) {
    const checked = $$('input[data-mkey]:checked', ui.roleMenus).length, total = $$('input[data-mkey]', ui.roleMenus).length;
    $('#mroleSummary').textContent = checked + ' dari ' + total + ' menu dipilih'; if (dirty) $('#mroleDirty').textContent = 'Ada perubahan yang belum disimpan.';
  }
  function updateUserSummary() { const count = $$('select[data-mkey]', ui.userMenus).filter(s => s.value !== '').length; $('#muserSummary').textContent = count + ' pengecualian'; }
  function setRoleChecks(keys) { const set = new Set(keys || []); $$('input[data-mkey]', ui.roleMenus).forEach(box => { if (!box.disabled) box.checked = set.has(box.dataset.mkey); }); updateRoleSummary(false); }

  async function loadRoleMenus() {
    clearMessage($('#mroleMsg')); ui.roleMenus.innerHTML = roleAccordion();
    if (!ui.mrole.value) { ui.roleMenus.innerHTML = '<div class="skeleton">Pilih peran untuk mulai mengatur akses.</div>'; $('#mroleState').textContent = 'Pilih peran'; return; }
    try {
      const data = await request('/api/menu-access/role/' + encodeURIComponent(ui.mrole.value)); setRoleChecks(data.menus);
      $('#mroleState').textContent = data.configured ? 'Granular aktif' : 'Mengikuti area/API'; $('#mroleState').classList.toggle('configured', data.configured); $('#mroleDirty').textContent = 'Belum ada perubahan.';
    } catch (error) { message($('#mroleMsg'), error.message, false); }
  }
  async function loadUserMenus() {
    clearMessage($('#muserMsg')); ui.userMenus.innerHTML = userAccordion();
    const user = users.find(x => String(x.id) === ui.muser.value); $('#muserContext').textContent = user ? 'Peran: ' + (user.role || '—') : 'Pilih pengguna';
    if (!ui.muser.value) { ui.userMenus.innerHTML = '<div class="skeleton">Pilih pengguna untuk melihat pengecualian.</div>'; return; }
    try { const data = await request('/api/menu-access/user/' + encodeURIComponent(ui.muser.value)); Object.entries(data.grants || {}).forEach(([key,value]) => { const select = $(`select[data-mkey="${key}"]`, ui.userMenus); if (select) select.value = String(value); }); updateUserSummary(); }
    catch (error) { message($('#muserMsg'), error.message, false); }
  }
  async function loadGrants() {
    clearMessage($('#gmsg')); ui.gareas.innerHTML = areaGroups('grant'); if (!ui.guser.value) { ui.gareas.innerHTML = '<div class="skeleton">Pilih pengguna untuk melihat pengecualian API.</div>'; return; }
    try { const data = await request('/api/users/grants?user_id=' + encodeURIComponent(ui.guser.value)); Object.entries(data.grants || {}).forEach(([key,value]) => { const select = $(`select[data-garea="${key}"]`, ui.gareas); if (select) select.value = String(value); }); }
    catch (error) { message($('#gmsg'), error.message, false); }
  }

  $('#newRoleBtn').addEventListener('click', () => openRole()); $('#roleSearch').addEventListener('input', renderRoles);
  ui.rtb.addEventListener('click', async event => {
    const edit = event.target.closest('[data-edit]'), del = event.target.closest('[data-delete]');
    if (edit) openRole(roles.find(r => r.key === edit.dataset.edit));
    if (del && confirm(`Hapus peran ${del.dataset.delete}? Tindakan ini tidak dapat dibatalkan.`)) {
      try { await busy(del, () => request('/api/roles/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:del.dataset.delete})})); await loadRoles(); }
      catch (error) { alert(error.message); }
    }
  });
  $('#rsave').addEventListener('click', async event => {
    clearMessage(ui.rmsg); const payload = {label:ui.rlabel.value.trim(),level:Number(ui.rlevel.value),caps:$$('#rcaps input:checked').map(x => x.dataset.cap),areas:$$('#rareas input:checked').map(x => x.dataset.area)};
    if (ui.origKey.value) payload.orig_key = ui.origKey.value; else payload.key = ui.rkey.value.trim();
    try { await busy(event.currentTarget, () => request('/api/roles/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})); ui.dialog.close(); await loadRoles(); }
    catch (error) { message(ui.rmsg, error.message, false); }
  });

  ui.mrole.addEventListener('change', loadRoleMenus); ui.muser.addEventListener('change', loadUserMenus); ui.guser.addEventListener('change', loadGrants);
  $('#mroleSearch').addEventListener('input', e => filterPermissions(e.currentTarget, ui.roleMenus)); $('#muserSearch').addEventListener('input', e => filterPermissions(e.currentTarget, ui.userMenus));
  ui.roleMenus.addEventListener('change', () => updateRoleSummary(true)); ui.userMenus.addEventListener('change', updateUserSummary);
  $('#mroleAll').addEventListener('click', () => { $$('input[data-mkey]',ui.roleMenus).forEach(x => { if (!x.disabled) x.checked = true; }); updateRoleSummary(true); });
  $('#mroleNone').addEventListener('click', () => { $$('input[data-mkey]',ui.roleMenus).forEach(x => { if (!x.disabled) x.checked = false; }); updateRoleSummary(true); });
  $('#mroleSave').addEventListener('click', async event => {
    if (!ui.mrole.value) return message($('#mroleMsg'),'Pilih peran terlebih dahulu.',false);
    const selected = $$('input[data-mkey]:checked',ui.roleMenus).map(x => x.dataset.mkey);
    if (!confirm(`Simpan ${selected.length} menu sebagai akses bawaan peran ini?`)) return;
    try { const data = await busy(event.currentTarget, () => request('/api/menu-access/role/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role_key:ui.mrole.value,menus:selected})})); setRoleChecks(data.menus); $('#mroleDirty').textContent='Perubahan sudah disimpan.'; $('#mroleState').textContent='Granular aktif'; $('#mroleState').classList.add('configured'); message($('#mroleMsg'),'Akses peran berhasil disimpan.',true); }
    catch (error) { message($('#mroleMsg'),error.message,false); }
  });
  $('#mroleReset').addEventListener('click', async event => {
    if (!ui.mrole.value) return message($('#mroleMsg'),'Pilih peran terlebih dahulu.',false); if (!confirm('Hapus aturan granular dan kembali ke izin area/API dasar?')) return;
    try { await busy(event.currentTarget, () => request('/api/menu-access/role/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role_key:ui.mrole.value})})); await loadRoleMenus(); message($('#mroleMsg'),'Peran kembali mengikuti izin area/API.',true); }
    catch (error) { message($('#mroleMsg'),error.message,false); }
  });
  $('#muserSave').addEventListener('click', async event => {
    if (!ui.muser.value) return message($('#muserMsg'),'Pilih pengguna terlebih dahulu.',false); const grants={}; $$('select[data-mkey]',ui.userMenus).forEach(s => {if(s.value==='0'||s.value==='1')grants[s.dataset.mkey]=Number(s.value);});
    if (!confirm(`Simpan ${Object.keys(grants).length} pengecualian menu untuk pengguna ini?`)) return;
    try { await busy(event.currentTarget, () => request('/api/menu-access/user/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:Number(ui.muser.value),grants})})); message($('#muserMsg'),'Override menu berhasil disimpan.',true); }
    catch (error) { message($('#muserMsg'),error.message,false); }
  });
  $('#gsave').addEventListener('click', async event => {
    if (!ui.guser.value) return message($('#gmsg'),'Pilih pengguna terlebih dahulu.',false); const grants={}; $$('select[data-garea]',ui.gareas).forEach(s => {if(s.value==='0'||s.value==='1')grants[s.dataset.garea]=Number(s.value);});
    if (!confirm(`Simpan ${Object.keys(grants).length} pengecualian area/API?`)) return;
    try { await busy(event.currentTarget, () => request('/api/users/grants',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:Number(ui.guser.value),grants})})); message($('#gmsg'),'Override area/API berhasil disimpan.',true); }
    catch (error) { message($('#gmsg'),error.message,false); }
  });

  async function loadRoles() { const data=await request('/api/roles'); catalog={areas:data.areas||[],caps:data.caps||[]}; areaLabel={}; catalog.areas.forEach(x=>areaLabel[x.key]=x.label); roles=data.roles||[]; buildRoleForm(); renderRoles(); fillOptions(); }
  async function init() {
    try { const [roleData,userData,menuData]=await Promise.all([request('/api/roles'),request('/api/users'),request('/api/menu-access/catalog')]); catalog={areas:roleData.areas||[],caps:roleData.caps||[]}; areaLabel={}; catalog.areas.forEach(x=>areaLabel[x.key]=x.label); roles=roleData.roles||[]; users=userData.users||[]; menuGroups=menuData.groups||[]; menus=menuData.menus||[]; baseline=menuData.baseline||[]; buildRoleForm(); renderRoles(); fillOptions(); const hash=location.hash.slice(1); activate($('#'+hash)?.classList.contains('workspace-panel')?hash:'rolesPanel'); const targetUser=new URLSearchParams(location.search).get('user_id'); if(targetUser&&users.some(u=>String(u.id)===targetUser)){ui.muser.value=targetUser;ui.guser.value=targetUser;await loadUserMenus();await loadGrants();} }
    catch (error) { $('#roleCount').textContent='Gagal memuat'; $('#roleEmpty').hidden=false; $('#roleEmpty').textContent=error.message; }
  }
  init();
})();
