# -*- coding: utf-8 -*-
"""
AVAYA dashboard patch - ringkasan + tombol "Lihat detail" (buka tab baru).

Pakai SETELAH %run llm_fix_final_combined.py (boleh bareng avaya_speedpatch):
    import avaya_dashpatch
    avaya_dashpatch.apply()

Apa yang dilakukan (build-agnostic, tidak mengubah template Anda):
- Membungkus render_dashboard_html: menambahkan CSS+JS kecil sebelum </body>.
- Setiap tabel besar (.tablecard) di dashboard dipangkas jadi RINGKASAN (mis. 5 baris),
  dengan tombol "Lihat detail" yang membuka SELURUH tabel itu di TAB BARU.
- Tabel yang barisnya sedikit dibiarkan apa adanya.
- Grafik/donut/KPI tetap tampil (itu bagian ringkasan visual).

Env opsional:
  AVAYA_DASH_PREVIEW (default 5)  jumlah baris pratinjau di dashboard utama
  AVAYA_DASH_MINROWS (default 6)  tabel dg baris >= ini yang dipangkas
"""
import os

_PREVIEW = int(os.environ.get("AVAYA_DASH_PREVIEW", "5"))
_MINROWS = int(os.environ.get("AVAYA_DASH_MINROWS", "6"))
_MARK = "/*__AVAYA_DASH_OVERLAY__*/"


def _overlay():
    css = (
        ".tablecard.dash-collapsed{position:relative}"
        ".tablecard.dash-collapsed table tbody tr:nth-child(n+" + str(_PREVIEW + 1) + "){display:none !important}"
        ".tablecard.dash-collapsed::after{content:'';position:absolute;left:0;right:0;bottom:0;height:40px;"
        "background:linear-gradient(rgba(0,0,0,0),var(--soft));pointer-events:none;border-radius:0 0 12px 12px}"
        ".dash-detailbar{display:flex;align-items:center;justify-content:space-between;gap:12px;"
        "margin:10px 0 0;padding:2px}"
        ".dash-count{color:var(--text2);font-size:13px}"
        ".dash-detailbtn{background:var(--blue);color:#fff;border:0;border-radius:8px;padding:8px 14px;"
        "font-size:13px;font-weight:650;cursor:pointer;white-space:nowrap}"
        ".dash-detailbtn:hover{opacity:.9}"
    )
    js = (
        "(function(){"
        "var PREVIEW=" + str(_PREVIEW) + ",MINROWS=" + str(_MINROWS) + ";"
        "function titleFor(card){var t='';var sec=card.closest('section');var h2=sec?sec.querySelector('h2'):null;"
        "var ct=card.querySelector('.chart-title');if(h2)t=h2.textContent.trim();"
        "if(ct)t=t?(t+' \\u2014 '+ct.textContent.trim()):ct.textContent.trim();return t||'Detail';}"
        "function openDetail(card){var table=card.querySelector('table');if(!table)return;"
        "var title=titleFor(card);var styles='';"
        "document.querySelectorAll('style').forEach(function(s){styles+=s.textContent;});"
        "var html='<!doctype html><html lang=\"id\"><head><meta charset=\"utf-8\">'+"
        "'<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">'+"
        "'<title>'+title+'</title><style>'+styles+'body{padding:24px}.wrap{padding:0}'+"
        "'h1.dt{font-size:22px;margin:0 0 16px;font-weight:750}'+"
        "'.tablecard{overflow-x:auto}</style></head><body><div class=\"wrap\">'+"
        "'<h1 class=\"dt\">'+title+'</h1><div class=\"card tablecard\">'+table.outerHTML+'</div></div></body></html>';"
        "var blob=new Blob([html],{type:'text/html'});var url=URL.createObjectURL(blob);"
        "var w=window.open(url,'_blank');if(!w){alert('Popup diblokir. Izinkan popup untuk membuka detail.');}"
        "setTimeout(function(){URL.revokeObjectURL(url);},60000);}"
        "function enhance(){document.querySelectorAll('.tablecard').forEach(function(card){"
        "if(card.__de)return;var table=card.querySelector('table');if(!table)return;"
        "var n=table.querySelectorAll('tbody tr').length;if(n<MINROWS)return;"
        "card.__de=1;card.classList.add('dash-collapsed');"
        "var bar=document.createElement('div');bar.className='dash-detailbar';"
        "var info=document.createElement('span');info.className='dash-count';"
        "info.textContent='Menampilkan '+Math.min(PREVIEW,n)+' dari '+n+' baris';"
        "var btn=document.createElement('button');btn.type='button';btn.className='dash-detailbtn';"
        "btn.innerHTML='Lihat detail \\u2197';btn.addEventListener('click',function(){openDetail(card);});"
        "bar.appendChild(info);bar.appendChild(btn);card.parentNode.insertBefore(bar,card.nextSibling);});}"
        "function loop(i){enhance();if(i<12)setTimeout(function(){loop(i+1);},300);}"
        "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',function(){loop(0);});}else{loop(0);}"
        "})();"
    )
    return "\n<style>" + _MARK + css + "</style>\n<script>" + js + "</script>\n"


def apply():
    import avaya_pipeline as ap
    if getattr(ap, "_DASHPATCH_APPLIED", False):
        print("[AVAYA-DASH] sudah terpasang.", flush=True)
        return
    _orig = ap.render_dashboard_html

    def _wrapped(*args, **kwargs):
        html = _orig(*args, **kwargs)
        try:
            if _MARK not in html:
                ov = _overlay()
                if "</body>" in html:
                    html = html.replace("</body>", ov + "</body>", 1)
                else:
                    html = html + ov
        except Exception as e:
            print("[AVAYA-DASH] inject gagal (%r) -> dashboard normal." % e, flush=True)
        return html
    ap.render_dashboard_html = _wrapped
    ap._DASHPATCH_APPLIED = True
    print("[AVAYA-DASH] terpasang | preview=%d minrows=%d" % (_PREVIEW, _MINROWS), flush=True)


if __name__ == "__main__":
    apply()
