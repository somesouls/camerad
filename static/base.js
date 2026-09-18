(function(){
  var root = document.documentElement;
  var body = document.body;
  var saved = localStorage.getItem('theme') || 'dark';
  if (saved === 'light') root.setAttribute('data-theme', 'light');
  var tb = document.getElementById('theme-btn');
  if (tb) tb.addEventListener('click', function(){
    var c = root.getAttribute('data-theme') || 'dark';
    var n = (c === 'dark') ? 'light' : 'dark';
    root.setAttribute('data-theme', n);
    localStorage.setItem('theme', n);
  });
  var side = document.getElementById('side');
  var bd = document.getElementById('backdrop');
  var mb = document.getElementById('menuBtn');
  var pinBtn = document.getElementById('pinBtn');
  function openSide(){ if (side) side.classList.add('open'); if (bd) bd.classList.add('show'); }
  function closeSide(){ if (side) side.classList.remove('open'); if (bd) bd.classList.remove('show'); }
  if (mb) mb.addEventListener('click', openSide);
  if (bd) bd.addEventListener('click', closeSide);
  var pinned = localStorage.getItem('sidebarPinned') === '1';
  if (pinned) body.classList.add('sidebar-pinned');
  if (pinBtn) pinBtn.addEventListener('click', function(){
    pinned = !pinned;
    body.classList.toggle('sidebar-pinned', pinned);
    localStorage.setItem('sidebarPinned', pinned ? '1' : '0');
  });
  var onChatPage = !!document.getElementById('stream');
  if (!onChatPage) {
    var LS_KEY = 'studio_chats', LS_ACTIVE = 'studio_active';
    var esc = function(s){ return (s||'').replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); };
    var load = function(){ try { return JSON.parse(localStorage.getItem(LS_KEY)) || []; } catch(e){ return []; } };
    var save = function(a){ localStorage.setItem(LS_KEY, JSON.stringify(a)); };
    var renderHist = function(){
      var list = document.getElementById('histList'); if (!list) return;
      var sessions = load(), activeId = localStorage.getItem(LS_ACTIVE) || null;
      if (!sessions.length) { list.innerHTML = '<div class="empty-hist">Belum ada chat tersimpan.</div>'; return; }
      list.innerHTML = '';
      sessions.slice().sort(function(a,b){ return b.updated - a.updated; }).forEach(function(s){
        var it = document.createElement('div');
        it.className = 'chat-item' + (s.id === activeId ? ' active' : '');
        it.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span class="t">' + esc(s.title || 'Percakapan Baru') + '</span><button class="del" title="Hapus"><svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>';
        it.addEventListener('click', function(){ localStorage.setItem(LS_ACTIVE, s.id); window.location.href = '/'; });
        it.querySelector('.del').addEventListener('click', function(e){
          e.stopPropagation();
          var arr = load().filter(function(x){ return x.id !== s.id; });
          save(arr);
          if ((localStorage.getItem(LS_ACTIVE) || null) === s.id) localStorage.removeItem(LS_ACTIVE);
          renderHist();
        });
        list.appendChild(it);
      });
    };
    var nb = document.getElementById('newBtn');
    if (nb) nb.addEventListener('click', function(){ localStorage.removeItem(LS_ACTIVE); window.location.href = '/'; });
    renderHist();
  }
  var scrollBox = document.querySelector('.side-scroll');
  if (scrollBox) {
    var CHEV = '<span class="acc-chev"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg></span>';
    var NO_COLLAPSE = ['Riwayat Percakapan'];
    var groups = [];
    var secLabels = Array.prototype.slice.call(scrollBox.querySelectorAll('.sec-label'));
    secLabels.forEach(function(label){
      var items = [];
      var el = label.nextElementSibling;
      while (el && !el.classList.contains('sec-label')) { items.push(el); el = el.nextElementSibling; }
      if (!items.length) return;
      var bodyEl = document.createElement('div');
      bodyEl.className = 'acc-body';
      label.insertAdjacentElement('afterend', bodyEl);
      items.forEach(function(it){ bodyEl.appendChild(it); });
      var text = (label.textContent || '').trim();
      var collapsible = (NO_COLLAPSE.indexOf(text) === -1);
      if (collapsible) {
        label.classList.add('acc-toggle');
        label.setAttribute('role', 'button');
        label.setAttribute('tabindex', '0');
        label.insertAdjacentHTML('beforeend', CHEV);
      }
      groups.push({ label: label, body: bodyEl, collapsible: collapsible });
    });
    function setOpen(target){
      groups.forEach(function(g){
        if (!g.collapsible) { g.body.style.maxHeight = '1000px'; return; }
        var isOpen = (g === target);
        g.label.classList.toggle('acc-collapsed', !isOpen);
        g.label.classList.toggle('is-active', isOpen);
        g.label.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        g.body.style.maxHeight = isOpen ? '1000px' : '0px';
        Array.prototype.forEach.call(g.body.children, function(it){
          if (it.classList) it.classList.toggle('active-accordion-item', isOpen);
        });
      });
    }
    var activeGroup = null;
    groups.forEach(function(g){
      if (!g.collapsible) return;
      var found = false;
      Array.prototype.forEach.call(g.body.children, function(it){
        if (it.classList && it.classList.contains('tool-side') && it.classList.contains('active')) found = true;
      });
      if (found) activeGroup = g;
    });
    if (!activeGroup) {
      for (var gi = 0; gi < groups.length; gi++) { if (groups[gi].collapsible) { activeGroup = groups[gi]; break; } }
    }
    if (activeGroup) setOpen(activeGroup);
    groups.forEach(function(g){
      if (!g.collapsible) return;
      g.label.addEventListener('click', function(){ setOpen(g); });
      g.label.addEventListener('keydown', function(e){
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(g); }
      });
    });
    var act = scrollBox.querySelector('.tool-side.active');
    if (act && act.scrollIntoView) {
      setTimeout(function(){ act.scrollIntoView({ block: 'center' }); }, 0);
    }
  }
})();
(function(){
  var GUARD = false;
  function guarded(fn){ GUARD = true; fn(); setTimeout(function(){ GUARD = false; }, 0); }
  function numOf(s){
    if (!s) return null;
    s = s.trim();
    if (!/\d/.test(s)) return null;
    var c = s.replace(/[^\d.,\-]/g, '');
    if (!c) return null;
    var lc = c.lastIndexOf(','), ld = c.lastIndexOf('.');
    if (lc > -1 && ld > -1) { c = lc > ld ? c.replace(/\./g, '').replace(',', '.') : c.replace(/,/g, ''); }
    else if (lc > -1) { var p = c.split(','); c = (p.length === 2 && p[1].length <= 2) ? c.replace(',', '.') : c.replace(/,/g, ''); }
    var n = parseFloat(c);
    return isNaN(n) ? null : n;
  }
  function cmpVal(av, bv){
    var an = numOf(av), bn = numOf(bv);
    if (an !== null && bn !== null) return an - bn;
    var dre = /[a-zA-Z]{3}.*\d{4}|\d{4}-\d{2}-\d{2}/;
    if (dre.test(av) && dre.test(bv)) {
      var ad = Date.parse(av), bd = Date.parse(bv);
      if (!isNaN(ad) && !isNaN(bd)) return ad - bd;
    }
    return av.localeCompare(bv, 'id', { numeric: true, sensitivity: 'base' });
  }
  function wireSort(table, thead, tbody){
    var hr = thead.querySelectorAll(':scope > tr');
    if (hr.length !== 1) return;
    var ths = Array.prototype.slice.call(hr[0].children);
    ths.forEach(function(th, idx){
      if (th.hasAttribute('data-no-sort') || th.classList.contains('dt-sortable')) return;
      if (th.querySelector('input,button,select,a,textarea')) return;
      if (!(th.textContent || '').trim()) return;
      th.classList.add('dt-sortable');
      th.setAttribute('role', 'button');
      th.setAttribute('tabindex', '0');
      th.setAttribute('aria-sort', 'none');
      var ic = document.createElement('span');
      ic.className = 'dt-sort-ic';
      th.appendChild(ic);
      function go(){
        var dir = th.getAttribute('data-dir') === 'asc' ? 'desc' : 'asc';
        ths.forEach(function(t){ t.removeAttribute('data-dir'); t.setAttribute('aria-sort', 'none'); });
        th.setAttribute('data-dir', dir);
        th.setAttribute('aria-sort', dir === 'asc' ? 'ascending' : 'descending');
        var rows = Array.prototype.slice.call(tbody.querySelectorAll(':scope > tr'));
        rows.sort(function(a, b){
          var av = (a.children[idx] ? a.children[idx].textContent : '').trim();
          var bv = (b.children[idx] ? b.children[idx].textContent : '').trim();
          var c = cmpVal(av, bv);
          return dir === 'asc' ? c : -c;
        });
        guarded(function(){ rows.forEach(function(r){ tbody.appendChild(r); }); });
        table.__dtRows = rows;
        render(table);
      }
      th.addEventListener('click', go);
      th.addEventListener('keydown', function(e){ if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    });
  }
  function ensureBar(table){
    if (table.__dtBar) return table.__dtBar;
    var b = document.createElement('div');
    b.className = 'dt-bar';
    b.innerHTML = '<div class="dt-info"></div><div class="dt-size"><label>Tampilkan <select class="dt-size-sel"><option value="10">10</option><option value="25">25</option><option value="50">50</option><option value="100">100</option><option value="all">Semua</option></select></label></div><div class="dt-nav"><button type="button" class="dt-prev" aria-label="Sebelumnya">&lsaquo;</button><span class="dt-pageno"></span><button type="button" class="dt-next" aria-label="Berikutnya">&rsaquo;</button></div>';
    guarded(function(){ table.insertAdjacentElement('afterend', b); });
    table.__dtBar = b;
    b.querySelector('.dt-size-sel').addEventListener('change', function(e){
      table.__dtSize = e.target.value === 'all' ? Infinity : parseInt(e.target.value, 10);
      table.__dtPage = 1;
      render(table);
    });
    b.querySelector('.dt-prev').addEventListener('click', function(){
      table.__dtPage = Math.max(1, (table.__dtPage || 1) - 1);
      render(table);
    });
    b.querySelector('.dt-next').addEventListener('click', function(){
      var rows = table.__dtRows || [], size = table.__dtSize || 10;
      var tp = size === Infinity ? 1 : Math.max(1, Math.ceil(rows.length / size));
      table.__dtPage = Math.min(tp, (table.__dtPage || 1) + 1);
      render(table);
    });
    return b;
  }
  function render(table){
    var rows = table.__dtRows || [], size = table.__dtSize || 10, total = rows.length;
    var tp = size === Infinity ? 1 : Math.max(1, Math.ceil(total / size));
    var page = Math.min(Math.max(1, table.__dtPage || 1), tp);
    table.__dtPage = page;
    var start = size === Infinity ? 0 : (page - 1) * size;
    var end = size === Infinity ? total : Math.min(start + size, total);
    rows.forEach(function(r, i){ r.style.display = (i >= start && i < end) ? '' : 'none'; });
    var b = table.__dtBar;
    if (!b) return;
    b.querySelector('.dt-info').textContent = total ? ('Menampilkan ' + (start + 1) + '\u2013' + end + ' dari ' + total) : 'Tidak ada data';
    b.querySelector('.dt-pageno').textContent = 'Hal ' + page + ' / ' + tp;
    b.querySelector('.dt-prev').disabled = page <= 1;
    b.querySelector('.dt-next').disabled = page >= tp;
    b.style.display = total > 0 ? 'flex' : 'none';
    var sel = b.querySelector('.dt-size-sel');
    if (sel) sel.value = size === Infinity ? 'all' : String(size);
  }
  function enhance(table){
    if (table.hasAttribute('data-no-enhance') || table.closest('.ask-answer')) return;
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.querySelectorAll(':scope > tr'));
    if (!table.__dtInit) {
      if (!rows.length) return;
      table.__dtInit = true;
      table.classList.add('dt-table');
      var thead = table.querySelector('thead');
      if (thead) wireSort(table, thead, tbody);
      table.__dtRows = rows;
      table.__dtSize = 10;
      table.__dtPage = 1;
      ensureBar(table);
      render(table);
      return;
    }
    var cached = table.__dtRows || [];
    var changed = rows.length !== cached.length;
    if (!changed) { for (var i = 0; i < rows.length; i++) { if (rows[i] !== cached[i]) { changed = true; break; } } }
    if (changed) { table.__dtRows = rows; table.__dtPage = 1; render(table); }
  }
  function scan(root){ Array.prototype.forEach.call(root.querySelectorAll('table'), enhance); }
  var contentArea = document.querySelector('.content-area') || document.body;
  scan(contentArea);
  new MutationObserver(function(){ if (!GUARD) scan(contentArea); }).observe(contentArea, { childList: true, subtree: true });
})();
