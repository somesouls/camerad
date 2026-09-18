#!/usr/bin/env python3
"""Split the shared base template into static CSS/JS and add per-menu gates."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "templates" / "base.html"
CSS = ROOT / "static" / "base.css"
JS = ROOT / "static" / "base.js"

MENU_KEYS = {
    "/rag-chatbot": "m_rag_chatbot", "/rag-eval-chatbot": "m_rag_eval_chatbot", "/rag-vs-lora": "m_rag_compare", "/handoff": "m_handoff", "/rag-agent": "m_rag_agent", "/rag-eval": "m_rag_eval",
    "/dashboard": "m_dashboard", "/deflection": "m_deflection", "/data": "m_data", "/glossary": "m_glossary", "/disambig": "m_disambig", "/intentmap": "m_intentmap", "/lifecycle": "m_lifecycle", "/tools": "m_tools",
    "/dialogflow/percakapan": "m_df_percakapan", "/df-webhook": "m_df_webhook", "/awe/kelola": "m_awe_kelola", "/awe/dasbor": "m_awe_dasbor", "/awe/coverage": "m_awe_coverage", "/awe/taksonomi": "m_awe_taksonomi", "/awe/sentimen": "m_awe_sentimen", "/awe/percakapan": "m_awe_percakapan", "/awe/pengguna-harian": "m_awe_pengguna", "/awe/penilaian": "m_awe_penilaian",
    "/awe/telepon": "m_awe_telepon", "/awe/telepon/dashboard": "m_awe_telepon_dash", "/awe/telepon/coverage": "m_awe_telepon_cov", "/awe/telepon/taksonomi": "m_awe_telepon_tax", "/awe/telepon/sentimen": "m_awe_telepon_sen", "/awe/telepon/percakapan": "m_awe_telepon_detail", "/awe/telepon/pengguna": "m_awe_telepon_users",
    "/sosmed": "m_sosmed_qna", "/sosmed/monitor": "m_sosmed_monitor", "/sosmed/kelola": "m_sosmed_kelola", "/sosmed/sla": "m_sosmed_sla", "/sosmed/deflection": "m_sosmed_deflection", "/peraturan": "m_peraturan", "/sop": "m_sop", "/kamus": "m_kamus", "/rag-harness": "m_rag_harness", "/voicebot": "m_voicebot", "/voicebot/intents": "m_voicebot_intents", "/voicebot/lab": "m_voicebot_lab", "/studio": "m_studio", "/laporan": "m_laporan", "/users": "m_users",
}
GROUP_KEYS = {"RAG Chatbot": "rag_chatbot", "RAG Agent": "rag_agent", "Dialogflow": "dialogflow", "AWE Chat": "awe_chat", "AWE Phone": "awe_phone", "Sosmed": "sosmed", "Peraturan": "peraturan", "Voicebot": "voicebot", "Umum": "umum"}


def gate_sidebar(text):
    region = re.search(r'(?P<open>      <div class="side-scroll">\n)(?P<body>.*?)(?P<close>\n      </div>\n      <div class="side-foot">)', text, re.S)
    assert region, "side-scroll region not found"
    body = re.sub(r"^[ \t]*\{%\s*if\s+can_\w+\s*%\}\n", "", region.group("body"), flags=re.M)
    body = re.sub(r"^[ \t]*\{%\s*endif\s*%\}\n", "", body, flags=re.M)
    for href, key in MENU_KEYS.items():
        pattern = re.compile(r'(<a class="tool-side\{% if active_page == \'[^\']*\' %\} active\{% endif %\}" href="' + re.escape(href) + r'">.*?</a>)', re.S)
        body, count = pattern.subn(lambda match: "{%% if menu is not defined or menu.%s %%}%s{%% endif %%}" % (key, match.group(1)), body)
        assert count == 1, f"expected one menu anchor for {href}, got {count}"
    for label, key in GROUP_KEYS.items():
        pattern = re.compile(r'(<div class="sec-label">' + re.escape(label) + r'</div>)')
        body, count = pattern.subn(lambda match: "{%% if menu_group is not defined or menu_group.%s %%}%s{%% endif %%}" % (key, match.group(1)), body)
        assert count == 1, f"expected one section label for {label}, got {count}"
    assert body.count("{% if menu is not defined or menu.") == 46
    assert body.count("{% if menu_group is not defined or menu_group.") == 9
    assert "{% if can_" not in body
    return text[:region.start()] + region.group("open") + body + region.group("close") + text[region.end():]


def main():
    text = BASE.read_text(encoding="utf-8")
    link = '<link rel="stylesheet" href="/static/base.css">'
    script = '<script src="/static/base.js"></script>'
    if link in text and script in text and CSS.exists() and JS.exists():
        print("base assets already split")
        return
    style = re.search(r"<style>\n?(.*?)\n?</style>", text, re.S)
    scripts = re.findall(r"<script>\n?(.*?)\n?</script>", text, re.S)
    assert style, "inline style block not found"
    assert len(scripts) == 2, f"expected 2 inline scripts, got {len(scripts)}"
    text = gate_sidebar(text)
    text, count = re.subn(r"<style>\n?.*?\n?</style>", link, text, count=1, flags=re.S)
    assert count == 1
    text, count = re.subn(r"\n<script>\n.*?\n</script>\n<script>\n.*?\n</script>\n(?=\{% block scripts %\})", "\n" + script + "\n", text, count=1, flags=re.S)
    assert count == 1, "inline script blocks before scripts block not found"
    CSS.write_text(style.group(1).rstrip() + "\n", encoding="utf-8")
    JS.write_text(scripts[0].strip() + "\n" + scripts[1].strip() + "\n", encoding="utf-8")
    BASE.write_text(text, encoding="utf-8")
    final = BASE.read_text(encoding="utf-8")
    assert "<style>" not in final and final.count("<script>") == 0
    assert final.count("menu is not defined or menu.") == 46
    assert final.count("menu_group is not defined or menu_group.") == 9
    print("split complete: CSS, JS, and 46 menu gates / 9 group gates")


if __name__ == "__main__":
    main()
