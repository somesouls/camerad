# -*- coding: utf-8 -*-
"""Verifikasi cepat perubahan PR #8 (roadmap RAG camerad).

Jalankan dari root repo (tempat rag_engine.py berada):
    python cek_roadmap_pr8.py

Skrip ini HANYA MEMBACA (tidak mengubah DB/berkas apa pun) dan aman dihapus
setelah verifikasi. Tujuannya membuktikan secara empiris apakah perubahan PR
sudah aktif dan apa dampaknya terhadap perakitan konteks, filter peraturan,
kejujuran sumber, serta guardrail anti-karang.
"""
import os
import json
import inspect


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
        return True
    except Exception:
        return False


def garis(t):
    print("")
    print("=" * 70)
    print(t)
    print("=" * 70)


def main():
    env_ok = _load_env()
    import rag_engine as e
    import rag_router as rt
    import rag_config_db as rcfg

    QS = ["tidak bisa login coretax", "cara lapor spt tahunan",
          "kena denda telat lapor", "lupa efin", "tarif pph final umkm"]

    # ---- 1) Versi kode aktif -------------------------------------------
    garis("1) VERSI KODE AKTIF (pastikan berada di branch PR #8)")
    try:
        src = inspect.getsource(e._assemble)
    except Exception:
        src = ""
    tuple3 = "return body, sources, used" in src
    print("   _assemble -> 3-nilai (body, sources, used)?", tuple3)
    print("   fungsi baru tersedia?", {
        "_peraturan_status": hasattr(e, "_peraturan_status"),
        "_ungrounded_citations": hasattr(e, "_ungrounded_citations"),
        "_skip_peraturan_aplikasi": hasattr(e, "_skip_peraturan_aplikasi"),
        "_anti_halusinasi_on": hasattr(e, "_anti_halusinasi_on"),
    })
    print("   PRIORITAS['aplikasi'] =", rt.PRIORITAS.get("aplikasi"))
    if not tuple3:
        print("   >> PERINGATAN: kode LAMA masih aktif. Jalankan:")
        print("      git fetch origin")
        print("      git switch fix/rag-konteks-dan-guardrail")
        print("      (skrip tetap lanjut memakai kode yang ada).")

    # ---- 2) Filter status peraturan + jangkauan regulasi kunci ----------
    garis("2) FILTER STATUS PERATURAN + JANGKAUAN REGULASI KUNCI")
    try:
        print("   _peraturan_status() =",
              e._peraturan_status() if hasattr(e, "_peraturan_status") else "(fungsi belum ada)")
    except Exception as ex:
        print("   _peraturan_status GAGAL:", str(ex)[:120])
    try:
        import peraturan_db as pdb
        status_baru = e._peraturan_status() if hasattr(e, "_peraturan_status") else ("berlaku", "diubah")
        for q in ("tarif pph final umkm",
                  "penghasilan usaha peredaran bruto tertentu setengah persen"):
            print("   -- kueri:", q)
            for tag, st in (("LAMA (berlaku)", ("berlaku",)),
                            ("BARU (%s)" % ",".join(status_baru), tuple(status_baru))):
                out = []
                try:
                    for r in pdb.search(q, 6, st):
                        d = dict(r)
                        out.append("%s %s/%s p.%s" % (
                            d.get("jenis_peraturan") or "",
                            d.get("nomor") or "", d.get("tahun") or "",
                            d.get("pasal") or "-"))
                except Exception as ex:
                    out = ["GAGAL: " + str(ex)[:80]]
                print("      [%s]" % tag)
                for x in out:
                    print("         -", x)
    except Exception as ex:
        print("   peraturan_db GAGAL:", str(ex)[:120])

    # ---- 3) Anggaran konteks per-sumber + kejujuran sumber_dipakai ------
    garis("3) ANGGARAN KONTEKS PER-SUMBER + SUMBER YANG BENAR-BENAR DIPAKAI")
    print("   env RAG_SUMBER_BUDGET =", os.environ.get("RAG_SUMBER_BUDGET"),
          "| RAG_MAKS_KONTEKS =", os.environ.get("RAG_MAKS_KONTEKS"),
          "| RAG_SKIP_PERATURAN_APLIKASI =", os.environ.get("RAG_SKIP_PERATURAN_APLIKASI"))
    for pf in ("chatbot", "agent"):
        p = rcfg.get_profile(pf) or {}
        allowed = e.effective_sources(p, None)
        try:
            e._clip_peraturan_ctx.set(e._clip_peraturan_for(p))
        except Exception:
            pass
        try:
            mode = e._resolve_mode(p)
        except Exception:
            mode = ""
        if mode == "full":
            fast = False
        elif mode == "llm":
            fast = True
        elif mode == "tanpa_llm":
            fast = False
        else:
            fast = e._is_fast_profile(p)
        maks_loop = 0 if fast else int(p.get("maks_loop") or 0)
        for q in QS:
            try:
                r = rt.route(q, allowed)
            except Exception as ex:
                print("   ==", pf, "|", q, "| ROUTER GAGAL:", str(ex)[:100])
                continue
            ordered = [s for s in r["ordered"] if s in allowed]
            defer = set()
            if "peraturan" in ordered and r["domain"] in ("aplikasi", "umum") and maks_loop > 0:
                defer.add("peraturan")
            try:
                skip = e._skip_peraturan_aplikasi()
            except Exception:
                skip = False
            if fast and r["domain"] == "aplikasi" and "peraturan" in ordered and skip:
                defer.add("peraturan")
            active = [s for s in ordered if s not in defer]
            cache = {}
            try:
                out = e._assemble(active, cache, q)
                if isinstance(out, tuple) and len(out) == 3:
                    ctx, _src, used = out
                else:
                    ctx, _src = out
                    used = "(n/a - kode lama 2-nilai)"
            except Exception as ex:
                print("   ==", pf, "|", q, "| _assemble GAGAL:", str(ex)[:120])
                continue
            rincian = {}
            for k in active:
                t, s = cache.get(k, ("", []))
                t = t or ""
                lab = rcfg.SUMBER_LABEL.get(k, k)
                rincian[k] = {"panjang": len(t),
                              "label_masuk_ctx": (lab in ctx),
                              "ekor_masuk": bool(t) and (t[-40:] in ctx)}
            print("")
            print("   ==", pf, "|", q)
            print("      domain=%s mode=%s fast=%s" % (r["domain"], mode, fast))
            print("      urutan_aktif=%s | ditunda=%s" % (active, sorted(defer)))
            print("      ctx_len=%d | dipotong_ekor_global=%s" % (len(ctx), ctx.endswith(chr(8230))))
            print("      sumber_dipakai(jujur)=%s" % (used,))
            print("      rincian=%s" % json.dumps(rincian, ensure_ascii=False))

    # ---- 4) Guardrail anti-karang --------------------------------------
    garis("4) GUARDRAIL ANTI-KARANG (deteksi sitasi di luar konteks)")
    try:
        print("   _anti_halusinasi_on() =",
              e._anti_halusinasi_on() if hasattr(e, "_anti_halusinasi_on") else "(fungsi belum ada)",
              "| env RAG_ANTI_HALUSINASI =", os.environ.get("RAG_ANTI_HALUSINASI"))
        if hasattr(e, "_ungrounded_citations"):
            ctx_dummy = "Sanksi telat lapor SPT diatur pada UU KUP Pasal 7."
            ans_karang = ("Sesuai PP 20 Tahun 2026 dan PER-11/PJ/2025 halaman 609 "
                          "serta KEP-71/PJ/2026, denda dikenakan sebesar sekian.")
            ans_bersih = "Denda telat lapor diatur pada UU KUP Pasal 7."
            print("   sitasi karang terdeteksi =", e._ungrounded_citations(ans_karang, ctx_dummy))
            print("   pada jawaban bersih       =", e._ungrounded_citations(ans_bersih, ctx_dummy))
        else:
            print("   (fungsi _ungrounded_citations belum ada - kode lama)")
    except Exception as ex:
        print("   guardrail GAGAL:", str(ex)[:150])

    # ---- 5) End-to-end (butuh .env LLM) --------------------------------
    garis("5) END-TO-END jawab_lab (butuh koneksi LLM)")
    print("   .env termuat:", env_ok)
    for pf in ("chatbot",):
        for q in QS + ["pp 20"]:
            try:
                r = e.jawab_lab(q, pf, None, prod_mode=True)
            except Exception as ex:
                print("   ==", pf, "|", q, "| GAGAL:", str(ex)[:150])
                continue
            if not isinstance(r, dict) or not r.get("ok"):
                print("   ==", pf, "|", q, "| ok=False | error=",
                      str((r or {}).get("error"))[:150],
                      "| ada_diagnostics=", ("diagnostics" in (r or {})))
                continue
            d = r.get("diagnostics") or {}
            print("")
            print("   ==", pf, "|", q, "| grounded=", r.get("grounded"))
            print("      sumber_dipakai =", d.get("sumber_dipakai"))
            print("      halusinasi_sitasi =", d.get("halusinasi_sitasi"))
            print("      jawaban =", str(r.get("answer"))[:160].replace(chr(10), " "))


if __name__ == "__main__":
    main()
