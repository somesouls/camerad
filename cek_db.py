# cek_db.py
import os, sqlite3, glob
import pipeline_store as pstore
try:
    from app_core import CONFIG
    runs_dir = CONFIG["runs_dir"]
except Exception as e:
    runs_dir = None
    print("(!) gagal impor app_core:", e)

db = pstore.default_db_path()
print("ENV PIPELINE_STORE_DB_FILE =", os.environ.get("PIPELINE_STORE_DB_FILE"))
print("DB dipakai   =", db)
print("DB ada?      =", os.path.isfile(db),
      "| ukuran file DB =", (os.path.getsize(db) if os.path.isfile(db) else 0), "bytes")

conn = pstore.connect(); conn.row_factory = sqlite3.Row
print("\n=== datasets ===")
for r in conn.execute("SELECT id,dkey,status,updated_at FROM datasets ORDER BY updated_at DESC"):
    print(dict(r))

print("\n=== step_artifact: size vs ISI DATA SEBENARNYA ===")
for r in conn.execute(
    "SELECT dataset_id, step, size, length(data) AS panjang_data, "
    "typeof(data) AS tipe_data, updated_at FROM step_artifact ORDER BY dataset_id, step"):
    print(dict(r))

print("\n=== cache disk _runs ===")
print("runs_dir =", runs_dir)
if runs_dir and os.path.isdir(runs_dir):
    for p in glob.glob(os.path.join(runs_dir, "*", "*")):
        print("  file:", p, "|", os.path.getsize(p), "bytes")
else:
    print("  (folder _runs tidak ada)")