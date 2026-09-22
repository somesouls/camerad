from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class PandaMotionContract(unittest.TestCase):
 def test_standalone_section_above_hero(self):
  html=(ROOT/'templates/landing/landing.html').read_text(encoding='utf-8')
  self.assertIn('/static/landing/panda-motion.css?v=panda-motion-v1',html)
  self.assertIn('class="panda-wordmark"',html)
  self.assertLess(html.index('class="panda-wordmark"'),html.index('class="hero"'))
  self.assertIn('/static/landing/panda-camerad.webp',html)
  self.assertIn('aria-label="CAMERAD"',html)
  self.assertIn('panda-wordmark__letter--c',html)
  self.assertIn('panda-wordmark__tail',html)
 def test_motion_and_accessibility(self):
  css=(ROOT/'static/landing/panda-motion.css').read_text(encoding='utf-8')
  for name in ('panda-roll-in','panda-c-enter','panda-tail-enter','panda-idle','prefers-reduced-motion:reduce'):
   self.assertIn(name,css)
  self.assertNotIn('position:fixed',css)
  self.assertLessEqual(len(css.splitlines()),400)
if __name__=='__main__': unittest.main()
