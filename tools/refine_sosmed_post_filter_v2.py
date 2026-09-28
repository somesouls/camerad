from pathlib import Path

p = Path("sosmed/monitor.py")
s = p.read_text(encoding="utf-8")
wrong = '''        if not rows:
            continue
        meta = _post_metadata(rows, plat, conv)
        if post_month and (meta.get("post_date") or "")[:7] != post_month:
            continue
        role, main_of, officials_of = _classify_conv(rows, off)
'''
original = '''        if not rows:
            continue
        role, main_of, officials_of = _classify_conv(rows, off)
'''
if wrong not in s:
    raise SystemExit("Sisipan monitor_list yang perlu dikoreksi tidak ditemukan")
s = s.replace(wrong, original, 1)

start = s.index("def monitor_posts(")
head, tail = s[:start], s[start:]
if original not in tail:
    raise SystemExit("Titik sisip monitor_posts tidak ditemukan")
correct = '''        if not rows:
            continue
        meta = _post_metadata(rows, plat, conv)
        if post_month and (meta.get("post_date") or "")[:7] != post_month:
            continue
        role, main_of, officials_of = _classify_conv(rows, off)
'''
tail = tail.replace(original, correct, 1)
p.write_text(head + tail, encoding="utf-8")
print("SOSMED_POST_MONTH_PLACEMENT_OK")
