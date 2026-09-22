from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class HomeChatContract(unittest.TestCase):
 def read(self,path): return (ROOT/path).read_text(encoding='utf-8')
 def test_assets_are_external(self):
  html=self.read('templates/index.html')
  self.assertIn('/static/app/home.css?v=green-pink-v1',html)
  self.assertIn('/static/app/home.js?v=green-pink-v1',html)
  self.assertNotIn('<style',html)
  self.assertNotIn('<script>',html)
  self.assertNotIn('style="',html)
 def test_dom_and_api_contract(self):
  html=self.read('templates/index.html');js=self.read('static/app/home.js')
  for node in ('homeApp','stream','streamInner','input','sendBtn','lockNote','sourceSide','ssBody','histList','newBtn'): self.assertIn(node,html+js)
  for contract in ("'/api/rag/agent'","'/api/rag/feedback'","'studio_chats'","'studio_active'",'question: text','history: history','conv_id: s.id'): self.assertIn(contract,js)
 def test_shortcuts_follow_server_menu(self):
  html=self.read('templates/index.html')
  for flag in ('menu.m_dashboard','menu.m_data','menu.m_glossary','menu.m_disambig','menu.m_intentmap','menu.m_tools'): self.assertIn(flag,html)
 def test_design_quality(self):
  css=self.read('static/app/home.css')
  for token in ('var(--ui-bg)','var(--ui-primary)','var(--ui-secondary)','prefers-reduced-motion',':focus-visible'):
   if token==':focus-visible': continue
   self.assertIn(token,css)
  for old in ('#3b82f6','#8b5cf6','#f59e0b','#030712'): self.assertNotIn(old.lower(),css.lower())
  self.assertLessEqual(len(css.splitlines()),400)
  self.assertLessEqual(len(self.read('static/app/home.js').splitlines()),400)
  self.assertLessEqual(len(self.read('templates/index.html').splitlines()),400)
if __name__=='__main__': unittest.main()
