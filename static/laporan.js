(function(){
  var cur = { id:null, title:"", question:"", content_md:"", databases:[], steps:[] };
  var editing = false;
  function $(id){ return document.getElementById(id); }
  function each(l,f){ Array.prototype.forEach.call(l,f); }
  var el = {
    list:$('rpList'), search:$('rpSearch'), title:$('rpTitle'), q:$('rpQ'),
    gen:$('rpGen'), save:$('rpSave'), md:$('rpMd'), pdf:$('rpPdf'),
    status:$('rpStatus'), report:$('rpReport'), meta:$('rpMeta'), ex:$('rpEx'), neu:$('rpNew'),
    edit:$('rpEdit'), aiToggle:$('rpAiToggle'), histBtn:$('rpHist'), toolbar:$('rpToolbar'),
    editActions:$('rpEditActions'), editSave:$('rpEditSave'), editCancel:$('rpEditCancel'), editStatus:$('rpEditStatus'),
    ai:$('rpAi'), aiInstr:$('rpAiInstr'), aiGo:$('rpAiGo'), aiCancel:$('rpAiCancel'), aiStatus:$('rpAiStatus'),
    histPanel:$('rpHistPanel'), histList:$('rpHistList')
  };
  function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }
  function inl(s){
    s=esc(s);
    s=s.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
    s=s.replace(/(^|[^*])\*([^*\n]+)\*/g,'$1<em>$2</em>');
    s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
    s=s.replace(/&lt;u&gt;/g,'<u>').replace(/&lt;\/u&gt;/g,'</u>');
    return s;
  }
  function buildList(items){
    function build(idx, indent){
      var ordered=items[idx].ordered, html=ordered?'<ol>':'<ul>';
      while(idx<items.length && items[idx].indent>=indent){
        var it=items[idx];
        html+='<li>'+inl(it.text);
        if(idx+1<items.length && items[idx+1].indent>it.indent){ var r=build(idx+1, items[idx+1].indent); html+=r[0]; idx=r[1]; }
        else { idx++; }
        html+='</li>';
      }
      html+=ordered?'</ol>':'</ul>';
      return [html, idx];
    }
    return build(0, items[0].indent)[0];
  }
  function mdRender(md){
    var lines=(md||'').split('\n'), out=[], i=0, n=lines.length;
    while(i<n){
      var line=lines[i], st=line.trim();
      if(st.indexOf('```')===0){ i++; var code=[]; while(i<n && lines[i].trim().indexOf('```')!==0){ code.push(lines[i]); i++; } i++; out.push('<pre><code>'+esc(code.join('\n'))+'</code></pre>'); continue; }
      if(line.indexOf('|')>=0 && i+1<n && /^[\s|:-]+$/.test(lines[i+1].trim()) && lines[i+1].indexOf('-')>=0){
        var header=st.replace(/^\||\|$/g,'').split('|').map(function(c){return c.trim();}); i+=2; var rows=[];
        while(i<n && lines[i].indexOf('|')>=0 && lines[i].trim()){ rows.push(lines[i].trim().replace(/^\||\|$/g,'').split('|').map(function(c){return c.trim();})); i++; }
        var h=header.map(function(c){return '<th>'+inl(c)+'</th>';}).join('');
        var b=rows.map(function(r){return '<tr>'+r.map(function(c){return '<td>'+inl(c)+'</td>';}).join('')+'</tr>';}).join('');
        out.push('<table><thead><tr>'+h+'</tr></thead><tbody>'+b+'</tbody></table>'); continue;
      }
      if(!st){ i++; continue; }
      if(st==='---'||st==='***'){ out.push('<hr>'); i++; continue; }
      var m=st.match(/^(#{1,4})\s+(.*)$/);
      if(m){ var lv=m[1].length; out.push('<h'+lv+'>'+inl(m[2])+'</h'+lv+'>'); i++; continue; }
      if(st.indexOf('>')===0){ var qs=[]; while(i<n && lines[i].trim().indexOf('>')===0){ qs.push(lines[i].trim().replace(/^>\s?/,'')); i++; } out.push('<blockquote>'+mdRender(qs.join('\n'))+'</blockquote>'); continue; }
      var lm=line.match(/^(\s*)([-*]|\d+[.)])\s+/);
      if(lm){
        var items=[];
        while(i<n){ var m2=lines[i].match(/^(\s*)([-*]|\d+[.)])\s+(.*)$/); if(!m2) break; items.push({ indent:Math.floor(m2[1].replace(/\t/g,'  ').length/2), ordered:/\d/.test(m2[2]), text:m2[3] }); i++; }
        out.push(buildList(items)); continue;
      }
      out.push('<p>'+inl(st)+'</p>'); i++;
    }
    return out.join('\n');
  }
  function tidyMd(md){
    var s=(md||'').replace(/\r/g,'');
    s=s.replace(/[ \t]+$/gm,'');
    s=s.replace(/\n{3,}/g,'\n\n');
    s=s.replace(/([^\n])\n(#{1,4}\s)/g,'$1\n\n$2');
    return s.trim();
  }
  function inlineOf(node){
    var s='';
    each(node.childNodes,function(c){
      if(c.nodeType===3){ s+=c.nodeValue; return; }
      if(c.nodeType!==1) return;
      var t=c.tagName.toLowerCase();
      if(t==='br'){ s+='\n'; return; }
      if(t==='strong'||t==='b'){ s+='**'+inlineOf(c)+'**'; return; }
      if(t==='em'||t==='i'){ s+='*'+inlineOf(c)+'*'; return; }
      if(t==='u'){ s+='<u>'+inlineOf(c)+'</u>'; return; }
      if(t==='code'){ s+='`'+c.textContent+'`'; return; }
      if(t==='a'){ s+='['+inlineOf(c)+']('+(c.getAttribute('href')||'')+')'; return; }
      s+=inlineOf(c);
    });
    return s;
  }
  function listMd(node, depth){
    var ordered=node.tagName.toLowerCase()==='ol', idx=1, res=[];
    each(node.children,function(li){
      if(li.tagName.toLowerCase()!=='li') return;
      var nested=[], holder=document.createElement('div');
      each(li.childNodes,function(ch){
        if(ch.nodeType===1 && /^(ul|ol)$/.test(ch.tagName.toLowerCase())) nested.push(ch);
        else holder.appendChild(ch.cloneNode(true));
      });
      var marker=ordered?(idx+'. '):'- ', pad=new Array(depth*2+1).join(' ');
      res.push(pad+marker+inlineOf(holder).replace(/\n/g,' ').trim());
      nested.forEach(function(nl){ res.push(listMd(nl, depth+1)); });
      idx++;
    });
    return res.join('\n');
  }
  function tableMd(node){
    var trs=node.querySelectorAll('tr'), header=null, body=[];
    each(trs,function(tr,ri){
      var cells=Array.prototype.map.call(tr.children,function(td){ return inlineOf(td).replace(/\s+/g,' ').trim(); });
      if(ri===0) header=cells; else body.push(cells);
    });
    if(!header) return '';
    var o='| '+header.join(' | ')+' |\n|'+header.map(function(){return ' --- ';}).join('|')+'|';
    body.forEach(function(r){ o+='\n| '+r.join(' | ')+' |'; });
    return o;
  }
  function htmlToMd(root){
    var lines=[];
    each(root.childNodes,function(node){
      if(node.nodeType===3){ var tx=node.nodeValue.replace(/\s+/g,' ').trim(); if(tx) lines.push(tx); return; }
      if(node.nodeType!==1) return;
      var t=node.tagName.toLowerCase();
      if(/^h[1-6]$/.test(t)){ var lv=Math.min(+t.charAt(1),4); lines.push(new Array(lv+1).join('#')+' '+inlineOf(node).replace(/\n/g,' ').trim()); return; }
      if(t==='ul'||t==='ol'){ lines.push(listMd(node,0)); return; }
      if(t==='blockquote'){ lines.push(htmlToMd(node).split('\n').map(function(l){return l?('> '+l):'>';}).join('\n')); return; }
      if(t==='pre'){ lines.push('```\n'+node.textContent.replace(/\n+$/,'')+'\n```'); return; }
      if(t==='hr'){ lines.push('---'); return; }
      if(t==='table'){ lines.push(tableMd(node)); return; }
      var s=inlineOf(node).replace(/\n{2,}/g,'\n').trim();
      if(s) lines.push(s);
    });
    return lines.join('\n\n').replace(/\n{3,}/g,'\n\n').trim();
  }
  function setStatus(t,spin){ el.status.innerHTML=(spin?'<span class="rp-spin"></span>':'')+esc(t||''); }
  function refreshButtons(){ var has=!!cur.content_md; el.save.disabled=!has; el.md.disabled=!has; el.pdf.disabled=!has; el.edit.disabled=!has; el.aiToggle.disabled=!has; el.histBtn.disabled=!has; }
  function showReport(){
    if(editing) return;
    if(cur.content_md){ el.report.innerHTML=mdRender(tidyMd(cur.content_md)); el.report.classList.add('show'); }
    else { el.report.classList.remove('show'); el.report.innerHTML=''; }
    var dbs=(cur.databases||[]).join(', ')||'-';
    var nq=(cur.steps||[]).filter(function(s){return s.type==='query';}).length;
    el.meta.textContent = cur.content_md ? ('Sumber data: '+dbs+' • '+nq+' query dijalankan'+(cur.id?(' • tersimpan #'+cur.id):' • belum disimpan')) : '';
    refreshButtons();
  }
  function adopt(r){ return { id:r.id, title:r.title||'', question:r.question||'', content_md:r.content_md||'', databases:r.databases||[], steps:r.steps||[] }; }
  function closePanels(except){ [['ai',el.ai],['hist',el.histPanel]].forEach(function(p){ if(p[0]!==except) p[1].classList.remove('show'); }); }
  function exec(cmd,val){ document.execCommand(cmd,false,val===undefined?null:val); el.report.focus(); }
  function insertTable(){ exec('insertHTML','<table><thead><tr><th>Kolom 1</th><th>Kolom 2</th></tr></thead><tbody><tr><td>&nbsp;</td><td>&nbsp;</td></tr><tr><td>&nbsp;</td><td>&nbsp;</td></tr></tbody></table><p><br></p>'); }
  function wrapCode(){ var sel=window.getSelection(); if(!sel||!sel.rangeCount) return; var txt=sel.toString(); if(!txt){ el.editStatus.textContent='Pilih teks dulu untuk dijadikan kode.'; return; } exec('insertHTML','<code>'+esc(txt)+'</code>'); }
  function startEdit(){
    if(!cur.content_md||editing) return;
    editing=true;
    el.report.innerHTML=mdRender(tidyMd(cur.content_md));
    el.report.classList.add('show','editing');
    el.report.setAttribute('contenteditable','true');
    el.toolbar.classList.add('show'); el.editActions.classList.add('show'); el.edit.classList.add('active');
    el.editStatus.textContent=''; closePanels(); el.report.focus();
  }
  function stopEdit(){
    editing=false;
    el.report.setAttribute('contenteditable','false');
    el.report.classList.remove('editing');
    el.toolbar.classList.remove('show'); el.editActions.classList.remove('show'); el.edit.classList.remove('active');
  }
  function editSave(){
    var md=tidyMd(htmlToMd(el.report));
    if(!md.trim()){ el.editStatus.textContent='Isi tidak boleh kosong.'; return; }
    cur.content_md=md;
    if(cur.id){
      el.editStatus.innerHTML='<span class="rp-spin"></span>Menyimpan…';
      fetch('/api/laporan/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:cur.id,content_md:md,title:(el.title.value.trim()||cur.title),note:'Edit manual'})})
        .then(function(r){return r.json();}).then(function(d){
          if(!d.ok){ el.editStatus.textContent='⚠ '+(d.error||'Gagal.'); return; }
          cur=adopt(d.report); stopEdit(); showReport(); setStatus('Perubahan tersimpan #'+cur.id+'.'); loadList();
        }).catch(function(e){ el.editStatus.textContent='⚠ '+e.message; });
    } else { stopEdit(); showReport(); setStatus('Perubahan diterapkan. Menyimpan sebagai laporan baru…'); save(); }
  }
  function editCancel(){ stopEdit(); showReport(); setStatus('Edit dibatalkan.'); }
  function generate(){
    var question=el.q.value.trim(); if(!question){ el.q.focus(); return; }
    if(editing) stopEdit(); closePanels(); el.gen.disabled=true; setStatus('Menyusun laporan dari data internal… (bisa beberapa saat)',true);
    var _s=0;
    fetch('/api/laporan/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:question,title:el.title.value.trim()})})
      .then(function(r){_s=r.status;return r.text();})
      .then(function(raw){
        var d; try{ d=JSON.parse(raw); }catch(_e){ var msg=(_s===401||_s===403)?'Sesi berakhir atau akses ditolak. Muat ulang lalu login.':((_s===502||_s===503||_s===504)?('Server AI melebihi batas waktu (gateway '+_s+'). Coba lagi atau persempit permintaan.'):'Respons server tidak dapat dibaca (bukan JSON).'); setStatus('⚠ '+msg); return; }
        if(!d.ok){ setStatus('⚠ '+(d.error||'Gagal menyusun laporan.')); return; }
        cur={ id:null, title:d.title||'', question:question, content_md:d.content_md||'', databases:d.databases||[], steps:d.steps||[] };
        if(d.title) el.title.value=d.title;
        showReport(); setStatus(d.note?('Selesai — catatan: '+d.note):'Laporan siap. Klik Simpan untuk menyimpan.');
      })
      .catch(function(e){ setStatus('⚠ '+e.message); })
      .then(function(){ el.gen.disabled=false; });
  }
  function save(cb){
    if(!cur.content_md) return;
    setStatus('Menyimpan…',true);
    fetch('/api/laporan/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:el.title.value.trim()||cur.title,question:cur.question,content_md:cur.content_md,databases:cur.databases,steps:cur.steps})})
      .then(function(r){return r.json();}).then(function(d){
        if(!d.ok){ setStatus('⚠ '+(d.error||'Gagal menyimpan.')); return; }
        cur.id=d.id; cur.title=el.title.value.trim()||cur.title; showReport(); setStatus('Tersimpan #'+d.id+'.'); loadList();
        if(typeof cb==='function') cb();
      }).catch(function(e){ setStatus('⚠ '+e.message); });
  }
  function ensureSaved(cb){ if(cur.id){ cb(); } else { save(cb); } }
  function exportAs(fmt){ ensureSaved(function(){ window.open('/api/laporan/export?id='+encodeURIComponent(cur.id)+'&fmt='+fmt,'_blank'); }); }
  function loadList(){
    var q=(el.search.value||'').trim();
    fetch('/api/laporan/list'+(q?('?q='+encodeURIComponent(q)):'')).then(function(r){return r.json();}).then(function(d){
      if(!d.ok){ el.list.innerHTML='<div class="rp-empty">Gagal memuat.</div>'; return; }
      if(!d.items.length){ el.list.innerHTML='<div class="rp-empty">Belum ada laporan tersimpan.</div>'; return; }
      el.list.innerHTML=d.items.map(function(it){ return '<div class="rp-item'+(cur.id===it.id?' active':'')+'" data-id="'+it.id+'"><div class="t">'+esc(it.title)+'</div><div class="m"><span>'+esc((it.created_at||'').replace('T',' '))+'</span><button class="del" data-del="'+it.id+'">Hapus</button></div></div>'; }).join('');
    }).catch(function(){ el.list.innerHTML='<div class="rp-empty">Gagal memuat.</div>'; });
  }
  function openReport(id){
    if(editing) stopEdit();
    setStatus('Membuka…',true);
    fetch('/api/laporan/get?id='+encodeURIComponent(id)).then(function(r){return r.json();}).then(function(d){
      if(!d.ok){ setStatus('⚠ '+(d.error||'Gagal.')); return; }
      cur=adopt(d.report); el.title.value=cur.title||''; el.q.value=cur.question||''; closePanels(); showReport(); setStatus('Dibuka #'+cur.id+'.'); loadList(); window.scrollTo({top:0,behavior:'smooth'});
    }).catch(function(e){ setStatus('⚠ '+e.message); });
  }
  function newReport(){ if(editing) stopEdit(); cur={ id:null,title:'',question:'',content_md:'',databases:[],steps:[] }; el.title.value=''; el.q.value=''; closePanels(); showReport(); setStatus(''); loadList(); el.q.focus(); }
  function toggleAi(){ if(!cur.content_md) return; if(editing) stopEdit(); if(el.ai.classList.contains('show')){ el.ai.classList.remove('show'); return; } closePanels('ai'); el.aiStatus.textContent=''; el.ai.classList.add('show'); el.aiInstr.focus(); }
  function aiGo(){
    var instr=el.aiInstr.value.trim(); if(!instr){ el.aiInstr.focus(); return; }
    var mo=document.querySelector('input[name=rpAiMode]:checked'), mode=mo?mo.value:'append';
    ensureSaved(function(){
      el.aiGo.disabled=true; el.aiStatus.innerHTML='<span class="rp-spin"></span>AI memperbarui laporan… (bisa beberapa saat)';
      var _s=0;
      fetch('/api/laporan/ai-update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:cur.id,instruction:instr,mode:mode})})
        .then(function(r){_s=r.status;return r.text();}).then(function(raw){
          var d; try{ d=JSON.parse(raw); }catch(_e){ var msg=(_s===502||_s===503||_s===504)?('Server AI melebihi batas waktu (gateway '+_s+'). Coba lagi atau persempit instruksi.'):'Respons server tidak dapat dibaca.'; el.aiStatus.textContent='⚠ '+msg; return; }
          if(!d.ok){ el.aiStatus.textContent='⚠ '+(d.error||'Gagal.'); return; }
          cur=adopt(d.report); showReport(); el.ai.classList.remove('show'); el.aiInstr.value='';
          setStatus('Laporan diperbarui AI ('+(d.mode==='revise'?'revisi menyeluruh':'tambah bagian')+').'+(d.note?(' Catatan: '+d.note):'')); loadList();
        }).catch(function(e){ el.aiStatus.textContent='⚠ '+e.message; }).then(function(){ el.aiGo.disabled=false; });
    });
  }
  function toggleHist(){ if(!cur.content_md) return; if(editing) stopEdit(); if(el.histPanel.classList.contains('show')){ el.histPanel.classList.remove('show'); return; } ensureSaved(function(){ closePanels('hist'); el.histPanel.classList.add('show'); loadVersions(); }); }
  function loadVersions(){
    el.histList.innerHTML='<div class="rp-empty2">Memuat…</div>';
    fetch('/api/laporan/versions?id='+encodeURIComponent(cur.id)).then(function(r){return r.json();}).then(function(d){
      if(!d.ok){ el.histList.innerHTML='<div class="rp-empty2">Gagal memuat.</div>'; return; }
      if(!d.items.length){ el.histList.innerHTML='<div class="rp-empty2">Belum ada versi. Versi dibuat otomatis tiap kali laporan diedit atau diperbarui AI.</div>'; return; }
      var SRC={edit:'Edit manual',ai:'Pembaruan AI',restore:'Pemulihan'};
      el.histList.innerHTML=d.items.map(function(v){ var s=SRC[v.source]||v.source||'—'; return '<div class="rp-hist-item"><div><b>#'+v.id+'</b> · '+esc(s)+'<div class="meta">'+esc((v.created_at||'').replace('T',' '))+(v.editor?(' · '+esc(v.editor)):'')+(v.note?(' · '+esc(v.note)):'')+'</div></div><div class="acts"><button data-vview="'+v.id+'">Lihat</button><button data-vrestore="'+v.id+'">Pulihkan</button></div></div>'; }).join('');
    }).catch(function(){ el.histList.innerHTML='<div class="rp-empty2">Gagal memuat.</div>'; });
  }
  el.histList.addEventListener('click',function(e){
    var vv=e.target.closest('[data-vview]'), vr=e.target.closest('[data-vrestore]');
    if(vv){ var vid=vv.getAttribute('data-vview'); fetch('/api/laporan/version?vid='+encodeURIComponent(vid)).then(function(r){return r.json();}).then(function(d){ if(!d.ok){ setStatus('⚠ '+(d.error||'Gagal.')); return; } el.report.innerHTML=mdRender(tidyMd((d.version&&d.version.content_md)||'')); el.report.classList.add('show'); window.scrollTo({top:0,behavior:'smooth'}); setStatus('Pratinjau versi #'+vid+' (belum diterapkan). Klik Pulihkan untuk menerapkan, atau buka ulang laporan untuk kembali.'); }).catch(function(ex){ setStatus('⚠ '+ex.message); }); return; }
    if(vr){ var rid=vr.getAttribute('data-vrestore'); if(!confirm('Pulihkan laporan ke versi #'+rid+'? Versi saat ini akan tersimpan lebih dulu di riwayat.')) return; fetch('/api/laporan/restore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:cur.id,vid:parseInt(rid,10)})}).then(function(r){return r.json();}).then(function(d){ if(!d.ok){ setStatus('⚠ '+(d.error||'Gagal.')); return; } cur=adopt(d.report); showReport(); setStatus('Dipulihkan ke versi #'+rid+'.'); loadVersions(); loadList(); }).catch(function(ex){ setStatus('⚠ '+ex.message); }); return; }
  });
  function tb(id,fn){ var b=$(id); if(b) b.addEventListener('click',function(e){ e.preventDefault(); fn(); }); }
  tb('tbBold',function(){ exec('bold'); }); tb('tbItalic',function(){ exec('italic'); }); tb('tbUnderline',function(){ exec('underline'); });
  tb('tbH2',function(){ exec('formatBlock','H2'); }); tb('tbH3',function(){ exec('formatBlock','H3'); }); tb('tbP',function(){ exec('formatBlock','P'); });
  tb('tbUl',function(){ exec('insertUnorderedList'); }); tb('tbOl',function(){ exec('insertOrderedList'); });
  tb('tbQuote',function(){ exec('formatBlock','BLOCKQUOTE'); }); tb('tbCode',wrapCode); tb('tbTable',insertTable);
  tb('tbClear',function(){ exec('removeFormat'); exec('formatBlock','P'); });
  var EX=['Ringkas tren fallback 30 hari terakhir + 3 intent prioritas','Bandingkan volume interaksi Dialogflow vs Sosmed bulan ini','Top 10 pertanyaan tanpa jawaban minggu lalu'];
  EX.forEach(function(t){ var b=document.createElement('button'); b.type='button'; b.textContent=t; b.onclick=function(){ el.q.value=t; generate(); }; el.ex.appendChild(b); });
  el.gen.onclick=generate; el.save.onclick=function(){ save(); }; el.md.onclick=function(){ exportAs('md'); }; el.pdf.onclick=function(){ exportAs('pdf'); }; el.neu.onclick=newReport;
  el.edit.onclick=startEdit; el.editSave.onclick=editSave; el.editCancel.onclick=editCancel;
  el.aiToggle.onclick=toggleAi; el.aiGo.onclick=aiGo; el.aiCancel.onclick=function(){ el.ai.classList.remove('show'); };
  el.histBtn.onclick=toggleHist;
  el.report.addEventListener('keydown',function(e){ if(editing && (e.ctrlKey||e.metaKey) && String(e.key).toLowerCase()==='s'){ e.preventDefault(); editSave(); } });
  el.list.addEventListener('click',function(e){
    var del=e.target.closest('[data-del]');
    if(del){ e.stopPropagation(); var did=parseInt(del.getAttribute('data-del'),10); if(confirm('Hapus laporan ini?')){ fetch('/api/laporan/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:did})}).then(function(r){return r.json();}).then(function(){ if(cur.id===did) newReport(); else loadList(); }); } return; }
    var item=e.target.closest('.rp-item'); if(item){ openReport(item.getAttribute('data-id')); }
  });
  var stmr; el.search.addEventListener('input',function(){ clearTimeout(stmr); stmr=setTimeout(loadList,300); });
  el.q.addEventListener('keydown',function(e){ if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){ generate(); } });
  showReport(); loadList();
})();
