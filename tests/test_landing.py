from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class LandingTest(unittest.TestCase):
 def read(self,p): return (ROOT/p).read_text(encoding='utf-8')
 def test_assets(self):
  h=self.read('templates/landing/landing.html')
  for x in ('design-system/tokens.css?v=green-pink-v1','design-system/theme.js?v=green-pink-v2','landing.css?v=green-pink-v2','landing.js?v=green-pink-v1'): self.assertIn(x,h)
  self.assertNotIn('<style',h);self.assertNotIn('<script>',h);self.assertNotIn('landing-theme.js',h)
 def test_internal_copy(self):
  h=self.read('templates/landing/landing.html').lower()
  for x in ('portal internal','ruang kerja','review manusia','akses mengikuti'): self.assertIn(x,h)
  for x in ('decision intelligence','human-guided','tanpa kotak hitam','apa itu camerad?'): self.assertNotIn(x,h)
 def test_palette(self):
  t=self.read('static/design-system/tokens.css').lower()
  for x in ('#071a14','#ff4fa3','#70e0b0','#f3fff9','#e91e78','#087f5b'): self.assertIn(x,t)
  for x in ('#f07840','#b43a00','#ffe8d1','#23231a','#8d4b2d','#d84a05'): self.assertNotIn(x,t)
 def test_compact_login_and_credit(self):
  login=self.read('templates/login.html');css=self.read('static/auth/login.css');credit=self.read('templates/credit.html')
  self.assertIn('/static/auth/login.css?v=compact-v1',login);self.assertIn('height:min(560px',css);self.assertIn('overflow:hidden',css)
  self.assertIn('/static/auth/credit.css?v=compact-v1',credit);self.assertIn('data-theme-icon="moon"',credit);self.assertIn('data-theme-icon="star"',credit)
  for h in (login,credit): self.assertNotIn('<style',h);self.assertNotIn('<script>',h)
 def test_shared_theme(self):
  theme=self.read('static/design-system/theme.js');login=self.read('templates/login.html');js=self.read('static/auth/login.js')
  self.assertIn("'camerad-theme'",theme);self.assertIn('toggle-password',login);self.assertIn("password.type==='text'",js)
 def test_quality(self):
  c=self.read('static/landing/landing.css');j=self.read('static/landing/landing.js');r=self.read('routes/landing_routes.py')
  self.assertIn(':focus-visible',c);self.assertIn('prefers-reduced-motion',c);self.assertIn('100dvh',c)
  self.assertIn('IntersectionObserver',j);self.assertNotIn("addEventListener('scroll'",j);self.assertNotIn('time_ns',r)
 def test_size(self):
  for p in ('templates/landing/landing.html','templates/login.html','templates/credit.html','static/landing/landing.css','static/landing/landing.js','static/design-system/tokens.css','static/design-system/theme.js','static/auth/login.css','static/auth/login.js','static/auth/credit.css'): self.assertLessEqual(len(self.read(p).splitlines()),400)
if __name__=='__main__': unittest.main()
