from pathlib import Path
p=Path(__file__).resolve().parents[1]/'templates/landing/landing.html'
s=p.read_text(encoding='utf-8')
css='  <link rel="stylesheet" href="/static/landing/panda-motion.css?v=panda-motion-v1">\n'
anchor='  <link rel="stylesheet" href="/static/landing/landing.css?v=green-pink-v2">\n'
if css not in s:
    if anchor not in s: raise SystemExit('stylesheet anchor not found')
    s=s.replace(anchor,anchor+css,1)
section='''    <section class="panda-wordmark" aria-label="Camerad dengan panda sebagai huruf A">
      <div class="panda-wordmark__inner">
        <p class="panda-wordmark__label">Kenalkan · Camerad</p>
        <div class="panda-wordmark__stage" role="img" aria-label="CAMERAD">
          <span class="panda-wordmark__letter panda-wordmark__letter--c" aria-hidden="true">C</span>
          <span class="panda-wordmark__panda" aria-hidden="true"><img src="/static/landing/panda-camerad.webp" alt="" width="512" height="768" decoding="async" fetchpriority="high"></span>
          <span class="panda-wordmark__tail" aria-hidden="true"><span class="panda-wordmark__letter">M</span><span class="panda-wordmark__letter">E</span><span class="panda-wordmark__letter">R</span><span class="panda-wordmark__letter">A</span><span class="panda-wordmark__letter">D</span></span>
        </div>
        <p class="panda-wordmark__hint" aria-hidden="true">C · panda sebagai A · MERAD</p>
      </div>
    </section>
'''
anchor2='  <main id="main">\n'
if 'class="panda-wordmark"' not in s:
    if anchor2 not in s: raise SystemExit('main anchor not found')
    s=s.replace(anchor2,anchor2+section,1)
p.write_text(s,encoding='utf-8')
print('panda motion section added')
