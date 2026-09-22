'use strict';
const appRoot = document.getElementById('homeApp');
  const IS_AGENT = appRoot.dataset.isAgent === 'true';
  const USER_INITIAL = appRoot.dataset.userInitial || 'S';

  const LS_KEY='studio_chats', LS_ACTIVE='studio_active';
  let sessions=loadSessions();
  let activeId=localStorage.getItem(LS_ACTIVE)||null;
  let sending=false;

  const el=id=>document.getElementById(id);
  function loadSessions(){ try{return JSON.parse(localStorage.getItem(LS_KEY))||[];}catch(e){return [];} }
  function saveSessions(){ localStorage.setItem(LS_KEY, JSON.stringify(sessions)); }
  function uid(){ return 'c_'+Date.now().toString(36)+Math.random().toString(36).slice(2,6); }
  function active(){ return sessions.find(s=>s.id===activeId)||null; }

  function getGreeting() {
    const hour = new Date().getHours();
    if (hour < 11) return 'Selamat Pagi';
    if (hour < 15) return 'Selamat Siang';
    if (hour < 18) return 'Selamat Sore';
    return 'Selamat Malam';
  }

  function esc(s){ return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
  function citeChips(t, sources){
    if(!sources || !sources.length) return t;
    return t.replace(/\[(\d{1,3})\]/g, function(m, n){
      var num=parseInt(n,10), idx=-1;
      for(var i=0;i<sources.length;i++){ if(sources[i] && sources[i].cite_no && parseInt(sources[i].cite_no,10)===num){ idx=i; break; } }
      if(idx<0 && num>=1 && num<=sources.length) idx=num-1;
      if(idx<0) return m;
      var sc=sources[idx]||{};
      var tip=esc((sc.sumber||'Sumber')+': '+(sc.judul||''));
      if(sc.url){ return '<a class="cite-chip" href="'+esc(sc.url)+'" target="_blank" rel="noopener" title="'+tip+'">['+num+']</a>'; }
      return '<a class="cite-chip" href="#" data-src="'+idx+'" title="'+tip+'">['+num+']</a>';
    });
  }
  function mdLite(src, sources){
    const blocks=[];
    let t=(src||'').replace(/```(\w*)\n?([\s\S]*?)```/g,(m,lang,code)=>{ blocks.push('<pre><code>'+esc(code.replace(/\n$/,''))+'</code></pre>'); return '\u0000'+(blocks.length-1)+'\u0000'; });
    t=esc(t);
    t=t.replace(/`([^`]+)`/g,(m,c)=>'<code>'+c+'</code>');
    t=t.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
    t=t.replace(/(^|[^*])\*([^*\n]+)\*/g,'$1<em>$2</em>');
    t=t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
    t=citeChips(t, sources);
    t=t.replace(/\n{2,}/g,'</p><p>').replace(/\n/g,'<br>');
    t='<p>'+t+'</p>';
    t=t.replace(/\u0000(\d+)\u0000/g,(m,i)=>blocks[+i]);
    return t;
  }

  const botAvatar='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 1 0 10 10H12V2z"/><path d="M12 12 2.1 7.1"/><path d="M12 12l9.9 4.9"/></svg>';
  const linkIcon='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';

  function renderSidebar(){
    const list=el('histList');
    if(!sessions.length){ list.innerHTML='<div class="empty-hist">Belum ada chat tersimpan.</div>'; return; }
    list.innerHTML='';
    sessions.slice().sort((a,b)=>b.updated-a.updated).forEach(s=>{
      const it=document.createElement('div');
      it.className='chat-item'+(s.id===activeId?' active':'');
      it.innerHTML='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span class="t">'+esc(s.title||'Percakapan Baru')+'</span><button class="del" title="Hapus"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>';
      it.onclick=()=>selectChat(s.id);
      it.querySelector('.del').onclick=(e)=>{ e.stopPropagation(); delChat(s.id); };
      list.appendChild(it);
    });
  }

  function srcChipsHtml(sources){
    if(!sources || !sources.length) return '';
    var chips = sources.map(function(s){
      var label = '<span class="lbl">'+esc(s.sumber||'Sumber')+'</span> '+esc(s.judul||'');
      if(s.ref) label += ' <span class="src-ref-inline">· '+esc(s.ref)+'</span>';
      if(s.url){
        return '<a class="src-chip" href="'+esc(s.url)+'" target="_blank" rel="noopener">'+linkIcon+label+'</a>';
      }
      return '<span class="src-chip preview" title="Pratinjau · tanpa tautan">'+label+'</span>';
    }).join('');
    return '<div class="src-wrap">'+chips+'</div>';
  }

  function renderSources(sources){
    var body=el('ssBody'); if(!body) return;
    if(!sources || !sources.length){
      body.innerHTML='<div class="ss-empty">Tautan sumber untuk jawaban akan tampil di sini. Hanya sumber yang relevan dengan jawaban terakhir yang ditampilkan.</div>';
      return;
    }
    body.innerHTML = sources.map(function(s){
      var badge='<span class="ss-badge">'+esc(s.sumber||'Sumber')+'</span>';
      var ref = s.ref ? '<span class="ss-ref ss-ref-inline">'+esc(s.ref)+'</span>' : '';
      var title='<div class="ss-title">'+esc(s.judul||'(tanpa judul)')+'</div>';
      if(s.url){
        return '<a class="ss-card" href="'+esc(s.url)+'" target="_blank" rel="noopener"><div class="s-top">'+badge+ref+'</div>'+title+'<div class="ss-link">'+linkIcon+esc(s.url)+'</div></a>';
      }
      return '<div class="ss-card preview"><div class="s-top">'+badge+ref+'<span class="ss-ref ss-ref-inline">pratinjau</span></div>'+title+'</div>';
    }).join('');
  }

  function fbHtml(m, idx){
    if(m.role!=='assistant' || !m.log_id) return '';
    var up = m.feedback==='up' ? ' active' : '';
    var down = m.feedback==='down' ? ' active' : '';
    return '<div class="fb-bar">'+
      '<button class="fb-btn up'+up+'" data-idx="'+idx+'" data-rating="up" title="Jawaban akurat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg></button>'+
      '<button class="fb-btn down'+down+'" data-idx="'+idx+'" data-rating="down" title="Jawaban keliru / kurang tepat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg></button>'+
      '<span class="fb-note">'+(m.feedback ? 'Terima kasih atas penilaian Anda.' : 'Apakah jawaban ini akurat?')+'</span>'+
      '</div>';
  }

  function renderChat(){
    const stream=el('stream');
    const inner=el('streamInner');
    const s=active();

    if(!s || !s.messages.length){
      stream.classList.add('is-empty');
      inner.replaceChildren(el('homeEmptyTemplate').content.cloneNode(true));
      var welcome=el('welcomeTitle');
      if(welcome) welcome.textContent=getGreeting()+', Rekan.';
      renderSources([]);
      updateLock();
      return;
    }

    stream.classList.remove('is-empty');
    inner.innerHTML='';
    s.messages.forEach((m,idx)=>inner.appendChild(msgRow(m, idx)));
    scrollBottom();
    var lastSrc=[];
    for(var i=s.messages.length-1;i>=0;i--){ var mm=s.messages[i]; if(mm.role==='assistant' && mm.sources && mm.sources.length){ lastSrc=mm.sources; break; } }
    renderSources(lastSrc);
    updateLock();
  }

  function msgRow(m, idx){
    const role=m.role;
    const row=document.createElement('div');
    row.className='msg '+(role==='user'?'user':'bot');
    const av=role==='user'
      ? '<div class="avatar">'+esc((USER_INITIAL||'S').charAt(0))+'</div>'
      : '<div class="avatar">'+botAvatar+'</div>';
    const name=role==='user'?'Anda':'Asisten Kring Pajak';
    var extra='';
    if(role==='assistant'){ extra = srcChipsHtml(m.sources) + fbHtml(m, idx); }
    row.innerHTML=av+'<div class="bubble"><div class="role">'+name+'</div><div class="body">'+mdLite(m.content, m.sources)+'</div>'+extra+'</div>';
    return row;
  }

  function addTyping(){
    const inner=el('streamInner');
    const row=document.createElement('div'); row.className='msg bot';
    row.innerHTML='<div class="avatar">'+botAvatar+'</div><div class="bubble"><div class="role">Asisten Kring Pajak</div><div class="body"><div class="typing"><i></i><i></i><i></i></div></div></div>';
    inner.appendChild(row); scrollBottom(); return row;
  }

  function scrollBottom(){ const s=el('stream'); s.scrollTop=s.scrollHeight; }

  function newChat(){ activeId=null; localStorage.removeItem(LS_ACTIVE); renderSidebar(); renderChat(); el('input').focus(); closeSide(); }
  function selectChat(id){ activeId=id; localStorage.setItem(LS_ACTIVE,id); renderSidebar(); renderChat(); closeSide(); }
  function delChat(id){ sessions=sessions.filter(s=>s.id!==id); if(activeId===id) activeId=null; saveSessions(); renderSidebar(); renderChat(); }
  function ensureSession(firstText){
    let s=active();
    if(s) return s;
    s={id:uid(), title:(firstText||'Percakapan Baru').slice(0,40), messages:[], updated:Date.now()};
    sessions.unshift(s); activeId=s.id; localStorage.setItem(LS_ACTIVE,s.id); saveSessions();
    return s;
  }

  function setSending(v){ sending=v; updateLock(); }
  function autoGrow(t){ t.style.height='auto'; t.style.height=Math.min(t.scrollHeight,180)+'px'; }

  function pendingFeedback(){
    if(!IS_AGENT) return false;
    var s=active(); if(!s) return false;
    for(var i=s.messages.length-1;i>=0;i--){
      var m=s.messages[i];
      if(m.role==='assistant'){ return !!m.log_id && !m.feedback; }
    }
    return false;
  }
  function updateLock(){
    var pend=pendingFeedback();
    var locked = sending || pend;
    var inp=el('input'), sb=el('sendBtn'), ln=el('lockNote');
    if(inp) inp.disabled = locked;
    if(sb) sb.disabled = locked;
    if(ln) ln.hidden = !(pend && !sending);
  }

  async function sendFeedback(idx, rating){
    var s=active(); if(!s) return;
    var m=s.messages[idx]; if(!m || !m.log_id) return;
    if(m.feedback===rating) return;
    m.feedback=rating; saveSessions(); renderChat();
    try{
      await fetch('/api/rag/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({log_id:m.log_id, rating:rating})});
    }catch(e){ /* diamkan; penilaian sudah tersimpan lokal */ }
  }

  async function send(){
    if(sending) return;
    if(pendingFeedback()) return;
    const inp=el('input'); const text=inp.value.trim(); if(!text) return;
    const s=ensureSession(text);

    const history = s.messages.map(m=>({role:m.role, content:m.content}));

    s.messages.push({role:'user', content:text}); s.updated=Date.now();
    if(s.messages.filter(m=>m.role==='user').length===1) s.title=text.slice(0,40);

    saveSessions(); inp.value=''; autoGrow(inp); renderSidebar(); renderChat();

    const typing = addTyping();
    setSending(true);

    try{
      const r = await fetch('/api/rag/agent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text, history: history, conv_id: s.id })
      });
      let data;
      try {
        data = await r.json();
      } catch(parseErr) {
        typing.remove();
        s.messages.push({ role:'assistant', content:'\u26A0 Koneksi terputus sebelum jawaban tiba (HTTP '+r.status+' — kemungkinan batas waktu domain publik/proxy). Silakan kirim ulang pertanyaan yang sama; bila berulang, uji lewat http://localhost:8080 langsung di PC server.' });
        s.updated = Date.now();
        saveSessions(); renderChat(); renderSidebar(); setSending(false);
        if(!pendingFeedback()) el('input').focus();
        return;
      }
      typing.remove();

      if(r.status===429 || (data && data.limit)){
        s.messages.push({ role:'assistant', content:'\u26A0 '+((data&&data.error)||'Kuota pertanyaan harian Anda sudah habis.') });
      } else if(data && data.ok){
        s.messages.push({
          role:'assistant',
          content:(data.answer||'(kosong)'),
          sources:(data.sources||[]),
          log_id:(data.log_id||null),
          feedback:null
        });
      } else {
        s.messages.push({ role:'assistant', content:'\u26A0 '+((data&&data.error)||('HTTP '+r.status)) });
      }

      s.updated = Date.now();
      saveSessions();
      renderChat();
      renderSidebar();
      setSending(false);
      if(!pendingFeedback()) el('input').focus();

    }catch(e){
      typing.remove();
      s.messages.push({role:'assistant', content:'\u26A0 Gagal menghubungi server: ' + e.message});
      s.updated = Date.now();
      saveSessions();
      renderChat();
      renderSidebar();
      setSending(false);
    }
  }

  function openSide(){ el('side').classList.add('open'); el('backdrop').classList.add('show'); }
  function closeSide(){ el('side').classList.remove('open'); el('backdrop').classList.remove('show'); }

  el('newBtn').onclick=newChat;
  el('sendBtn').onclick=send;
  el('menuBtn').onclick=openSide;
  el('backdrop').onclick=closeSide;
  el('streamInner').addEventListener('click', function(ev){
    var cc = ev.target.closest ? ev.target.closest('.cite-chip[data-src]') : null;
    if(cc){ ev.preventDefault(); var ci=parseInt(cc.getAttribute('data-src'),10); var cards=document.querySelectorAll('#ssBody .ss-card'); if(cards[ci]){ cards[ci].scrollIntoView({behavior:'smooth',block:'center'}); cards[ci].classList.remove('flash'); void cards[ci].offsetWidth; cards[ci].classList.add('flash'); } return; }
    var b = ev.target.closest ? ev.target.closest('.fb-btn') : null;
    if(b){ sendFeedback(parseInt(b.getAttribute('data-idx'),10), b.getAttribute('data-rating')); }
  });
  const inp=el('input');
  inp.addEventListener('input',()=>autoGrow(inp));
  inp.addEventListener('keydown',e=>{ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); send(); } });

  renderSidebar(); renderChat(); inp.focus();
