from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class LandingTest(unittest.TestCase):
 def read(self,p): return (ROOT/p).read_text(encoding='utf-8')
 def test_assets(self):
  h=self.read('templates/landing/landing.html')
  for x in ('design-system/tokens.css?v=green-pink-v1','design-system/theme.js?v=green-pink-v1','landing.css?v=green-pink-v1','landing.js?v=green-pink-v1'): self.assertIn(x,h)
  self.assertNotIn('<style',h);self.assertNotIn('<script>',h);self.assertNotIn('landing-theme.js',h)
 def test_palette(self):
  t=self.read('static/design-system/tokens.css').lower()
  for x in ('#071a14','#ff4fa3','#70e0b0','#f3fff9','#e91e78','#087f5b'): self.assertIn(x,t)
  for x in ('#f07840','#b43a00','#ffe8d1','#23231a','#8d4b2d','#d84a05'): self.assertNotIn(x,t)
  self.assertEqual(t.count(':root{'),1);self.assertEqual(t.count(':root[data-theme="light"]{'),1)
 def test_quality(self):
  c=self.read('static/landing/landing.css');j=self.read('static/landing/landing.js');r=self.read('routes/landing_routes.py')
  self.assertIn(':focus-visible',c);self.assertIn('prefers-reduced-motion',c);self.assertIn('100dvh',c)
  self.assertIn('IntersectionObserver',j);self.assertNotIn("addEventListener('scroll'",j)
  self.assertNotIn('time_ns',r);self.assertNotIn('Cache-Control',r)
 def test_size(self):
  for p in ('templates/landing/landing.html','static/landing/landing.css','static/landing/landing.js','static/design-system/tokens.css','static/design-system/theme.js'): self.assertLessEqual(len(self.read(p).splitlines()),400)
if __name__=='__main__': unittest.main()
