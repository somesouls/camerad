from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class BaseShellContract(unittest.TestCase):
 def read(self,path): return (ROOT/path).read_text(encoding='utf-8')
 def test_shared_design_system(self):
  html=self.read('templates/base.html')
  for asset in ('design-system/theme.js?v=green-pink-v2','design-system/tokens.css?v=green-pink-v1','app/app-shell.css?v=green-pink-v1','app/app-shell.js?v=green-pink-v1'): self.assertIn(asset,html)
  self.assertIn('<body class="app-shell">',html)
  self.assertIn('data-theme-toggle',html)
  self.assertIn('data-theme-icon="star"',html)
  self.assertIn('data-theme-icon="moon"',html)
  self.assertNotIn('style="',html)
 def test_access_contract_is_preserved(self):
  html=self.read('templates/base.html')
  for flag in ('menu_group.rag_chatbot','menu.m_rag_chatbot','menu.m_dashboard','menu.m_awe_kelola','menu.m_sosmed_qna','menu.m_peraturan','menu.m_voicebot','menu.m_users'): self.assertIn(flag,html)
  for route in ('/rag-chatbot','/dashboard','/awe/kelola','/sosmed','/peraturan','/voicebot','/users','/api/logout'): self.assertIn(f'href="{route}"',html)
 def test_legacy_theme_runtime_removed(self):
  js=self.read('static/base.js')
  self.assertNotIn("localStorage.getItem('theme')",js)
  self.assertNotIn("localStorage.setItem('theme'",js)
  self.assertIn("localStorage.getItem('sidebarPinned')",js)
  self.assertIn("LS_KEY = 'studio_chats'",js)
 def test_shell_quality(self):
  css=self.read('static/app/app-shell.css');js=self.read('static/app/app-shell.js')
  for value in ('var(--ui-bg)','var(--ui-primary)','var(--ui-surface)','prefers-reduced-motion',':focus-visible'): self.assertIn(value,css)
  for retired in ('#3b82f6','#60a5fa','#8b5cf6','#f59e0b'): self.assertNotIn(retired.lower(),css.lower())
  self.assertIn("event.key==='Escape'",js)
  self.assertLessEqual(len(css.splitlines()),400)
  self.assertLessEqual(len(js.splitlines()),400)

if __name__=='__main__': unittest.main()
