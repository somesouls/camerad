# fix_reindex_mismatch.py
import sqlite3
import time
import peraturan_db
import peraturan_semantic as psem

TARGET_DIM = 1024
BATCH = 16  # kecilkan dulu supaya kalau ada teks bermasalah tidak menggagalkan banyak row

conn = peraturan_db.init_db(peraturan_db.connect())

if not psem.is_available():
    raise SystemExit("Model embedding tidak tersedia")

dim = psem.embed_dim()
print("model dim:", dim)

rows = conn.execute("""
SELECT u.id, u.judul, u.isi
FROM peraturan_unit u
LEFT JOIN peraturan_vec v ON v.id = u.id
WHERE v.id IS NULL OR v.dim != ?
ORDER BY u.id
""", (dim,)).fetchall()

print("mismatch rows:", len(rows))

ok = 0
fail = 0
t0 = time.time()

for i in range(0, len(rows), BATCH):
    chunk = rows[i:i+BATCH]
    ids = [r["id"] for r in chunk]
    texts = [((r["judul"] or "") + " " + (r["isi"] or "")).strip() for r in chunk]

    try:
        arr = psem.embed_passages(texts)
    except Exception as e:
        print("[BATCH FAIL]", i, ids[0], "=>", repr(e))
        arr = None

    if arr is None:
        # fallback per row supaya ketahuan id mana yang gagal
        for id_, text in zip(ids, texts):
            try:
                v = psem.embed_passage(text)
                if v is None:
                    print("[FAIL]", id_, "embed_passage returned None")
                    fail += 1
                    continue
                blob = psem.to_blob(v)
                conn.execute("DELETE FROM peraturan_vec WHERE id=?", (id_,))
                conn.execute(
                    "INSERT INTO peraturan_vec(id, dim, emb) VALUES (?,?,?)",
                    (id_, int(len(v)), blob),
                )
                ok += 1
            except Exception as e:
                print("[FAIL]", id_, repr(e))
                fail += 1
        conn.commit()
        continue

    for j, id_ in enumerate(ids):
        try:
            v = arr[j]
            blob = psem.to_blob(v)
            conn.execute("DELETE FROM peraturan_vec WHERE id=?", (id_,))
            conn.execute(
                "INSERT INTO peraturan_vec(id, dim, emb) VALUES (?,?,?)",
                (id_, int(len(v)), blob),
            )
            ok += 1
        except Exception as e:
            print("[FAIL INSERT]", id_, repr(e))
            fail += 1

    conn.commit()
    print("progress:", min(i+BATCH, len(rows)), "/", len(rows), "ok:", ok, "fail:", fail)

print("done ok:", ok, "fail:", fail, "sec:", round(time.time() - t0, 1))

print(conn.execute("""
SELECT dim, COUNT(*)
FROM peraturan_vec
GROUP BY dim
ORDER BY dim
""").fetchall())

conn.close()