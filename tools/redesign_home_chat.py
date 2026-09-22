from pathlib import Path
import re
root=Path(__file__).resolve().parents[1]
index=root/'templates/index.html'
source=index.read_text(encoding='utf-8')
match=re.search(r'{% block scripts %}\s*<script>([\s\S]*?)</script>\s*{% endblock %}',source)
if not match: raise SystemExit('inline chat script not found')
js=match.group(1).strip()
old="const IS_AGENT = {{ 'true' if is_agent else 'false' }};\n  const USER_INITIAL = {{ user_avatar|tojson }};"
new="const appRoot = document.getElementById('homeApp');\n  const IS_AGENT = appRoot.dataset.isAgent === 'true';\n  const USER_INITIAL = appRoot.dataset.userInitial || 'S';"
if old not in js: raise SystemExit('config anchor not found')
js=js.replace(old,new)
js,count=re.subn(r'  function landingGrid\(\)\{[\s\S]*?\n  \}\n\n  function renderChat', '  function renderChat', js, count=1)
if count!=1: raise SystemExit('landing grid anchor not found')
pattern=r"    if\(!s \|\| !s\.messages\.length\)\{[\s\S]*?\n    \}\n\n    stream\.classList\.remove\('is-empty'\);"
replacement="""    if(!s || !s.messages.length){
      stream.classList.add('is-empty');
      inner.replaceChildren(el('homeEmptyTemplate').content.cloneNode(true));
      var welcome=el('welcomeTitle');
      if(welcome) welcome.textContent=getGreeting()+', Rekan.';
      renderSources([]);
      updateLock();
      return;
    }

    stream.classList.remove('is-empty');"""
js,count=re.subn(pattern,replacement,js,count=1)
if count!=1: raise SystemExit('empty state anchor not found')
js=js.replace("if(ln) ln.style.display = (pend && !sending) ? 'block' : 'none';","if(ln) ln.hidden = !(pend && !sending);")
js=js.replace('<span style="opacity:.7">· ','<span class="src-ref-inline">· ')
js=js.replace('<span class="ss-ref" style="margin:0">','<span class="ss-ref ss-ref-inline">')
js='\n'.join(line for line in js.splitlines() if not line.strip().startswith('//'))
(root/'static/app/home.js').write_text("'use strict';\n"+js.strip()+"\n",encoding='utf-8')
html='''{% extends "base.html" %}
{% block title %}Camerad Studio — Asisten Kring Pajak{% endblock %}
{% block page_label %}Asisten Kring Pajak{% endblock %}
{% block head %}<link rel="stylesheet" href="/static/app/home.css?v=green-pink-v1">{% endblock %}
{% block main %}
<div class="home-app" id="homeApp" data-is-agent="{{ 'true' if is_agent else 'false' }}" data-user-initial="{{ user_avatar|e }}">
  <div class="chat-layout">
    <section class="chat-col" aria-label="Percakapan">
      <div class="stream" id="stream"><div class="stream-inner" id="streamInner"></div></div>
      <div class="composer">
        <div class="composer-inner">
          <label class="sr-only" for="input">Pertanyaan perpajakan</label>
          <div class="inputwrap">
            <textarea id="input" rows="1" placeholder="Tanyakan prosedur, layanan, atau ketentuan perpajakan…"></textarea>
            <button class="sendbtn" id="sendBtn" type="button" title="Kirim pesan" aria-label="Kirim pesan"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg></button>
          </div>
          <div class="lock-note" id="lockNote" hidden>Beri penilaian pada jawaban terakhir sebelum bertanya lagi.</div>
          <div class="hint">Jawaban berbasis pengetahuan internal. Tetap verifikasi dengan sumber resmi sebelum digunakan.</div>
        </div>
      </div>
    </section>
    <aside class="source-side" id="sourceSide" aria-label="Sumber rujukan">
      <div class="ss-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>SUMBER RUJUKAN</div>
      <div class="ss-body" id="ssBody"><div class="ss-empty">Sumber yang relevan dengan jawaban terakhir akan ditampilkan di sini.</div></div>
    </aside>
  </div>
  <template id="homeEmptyTemplate">
    <section class="home-welcome">
      <p class="home-eyebrow">ASISTEN INTERNAL</p>
      <h1 id="welcomeTitle">Selamat datang, Rekan.</h1>
      <p>Tanyakan prosedur atau ketentuan perpajakan. Jawaban disusun dari basis pengetahuan dan menyertakan rujukan yang tersedia.</p>
      {% if not is_agent %}
      <div class="quick-tools"><span>AKSES CEPAT</span><nav class="quick-grid" aria-label="Akses cepat">
        {% if menu is not defined or menu.m_dashboard %}<a class="quick-card" href="/dashboard"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/></svg><span><b>Dashboard analitik</b><small>Pantau performa layanan</small></span></a>{% endif %}
        {% if menu is not defined or menu.m_data %}<a class="quick-card" href="/data"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/></svg><span><b>Kelola data</b><small>Dataset dan sinkronisasi</small></span></a>{% endif %}
        {% if menu is not defined or menu.m_glossary %}<a class="quick-card" href="/glossary"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5"/></svg><span><b>Glosarium pajak</b><small>Istilah dan definisi</small></span></a>{% endif %}
        {% if menu is not defined or menu.m_disambig %}<a class="quick-card" href="/disambig"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18l6-6-6-6M4 6v12"/></svg><span><b>Disambiguasi</b><small>Atur pemetaan frasa</small></span></a>{% endif %}
        {% if menu is not defined or menu.m_intentmap %}<a class="quick-card" href="/intentmap"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3h7v7H3zM14 14h7v7h-7z"/></svg><span><b>Peta intent</b><small>Maksud dan aturan</small></span></a>{% endif %}
        {% if menu is not defined or menu.m_tools %}<a class="quick-card" href="/tools"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg><span><b>Analisis Dialogflow</b><small>Audit alur percakapan</small></span></a>{% endif %}
      </nav></div>
      {% endif %}
    </section>
  </template>
</div>
{% endblock %}
{% block scripts %}<script src="/static/app/home.js?v=green-pink-v1"></script>{% endblock %}
'''
index.write_text(html,encoding='utf-8')
print('home chat redesigned')
