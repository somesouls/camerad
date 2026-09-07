// content_isolated.js — Camerad X-Scraper (Linear Mode) v3.6 (Panel Melayang dengan Reset)
if (window === window.top) {
  let autoScrollInterval = null;
  let idleRounds = 0;
  let lastHeight = 0;

  // 1. JEMBATAN PESAN (Wajib jalan di semua halaman)
  window.addEventListener("message", function (event) {
    if (event.source !== window || !event.data || event.data.type !== "CAMERAD_X_JSON") return;
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.sendMessage) {
      chrome.runtime.sendMessage({ action: "PROCESS_JSON", data: event.data.payload }).catch(() => {});
    }
  });

  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg) => {
      if (msg.action === "UPDATE_STATS") {
        const t = document.getElementById("cmrd-total");
        const q = document.getElementById("cmrd-queue");
        if (t) t.innerText = msg.totalTweets;
        if (q) q.innerText = msg.queueCount;
      }
    });
  }

  function rnd(min, max) { return Math.floor(min + Math.random() * (max - min)); }

  const isStatusPage = window.location.href.includes('/status/');

  // =========================================================================
  // MODE A: GHOST TAB (Robot Auto-Expander)
  // =========================================================================
  if (isStatusPage) {
    setInterval(() => {
        window.scrollBy({ top: rnd(400, 800), behavior: "smooth" });
        const buttons = document.querySelectorAll('div[role="button"], span');
        buttons.forEach(btn => {
            const text = (btn.innerText || "").toLowerCase().trim();
            if (text.length > 3 && text.length < 40) {
                if (text.includes("tampilkan") || text.includes("show ") || text.includes("balasan") || text.includes("replies")) {
                    btn.click();
                }
            }
        });
    }, 1500); 
  } 
  
  // =========================================================================
  // MODE B: HALAMAN PENCARIAN (UI Panel Melayang & Pengumpul ID)
  // =========================================================================
  else {
      function setMsg(txt, ok) {
        const el = document.getElementById("cmrd-msg");
        if (!el) return;
        el.innerText = txt || "";
        el.style.color = ok === false ? "#f4212e" : (ok === true ? "#00ba7c" : "#8899a6");
      }

      function collectIdsOnScreen() {
        const ids = [];
        document.querySelectorAll('a[href*="/status/"]').forEach((a) => {
          const m = a.href.match(/\/status\/(\d+)/);
          if (m && m[1]) ids.push(m[1]);
        });
        return ids;
      }

      function humanScrollStep() {
        const dy = rnd(600, 1400);
        window.scrollBy({ top: dy, behavior: "smooth" });

        const ids = collectIdsOnScreen();
        if (ids.length && chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({ action: "ADD_TO_QUEUE", ids }).catch(() => {});
        }

        const h = document.body.scrollHeight;
        if (h === lastHeight) {
          idleRounds++;
          if (idleRounds >= 4) { stopScroll(true); setMsg("Auto-scroll selesai (mentok di dasar).", true); return; }
        } else { idleRounds = 0; lastHeight = h; }

        if (autoScrollInterval !== null) autoScrollInterval = setTimeout(humanScrollStep, rnd(1400, 3600));
      }

      function startScroll() {
        idleRounds = 0; lastHeight = 0;
        autoScrollInterval = setTimeout(humanScrollStep, 400);
        const b = document.getElementById("cmrd-scroll");
        if (b) { b.innerText = "⏹ Berhenti Scroll"; b.style.background = "#f4212e"; }
        setMsg("Mengumpulkan ID sambil scroll…");
      }
      
      function stopScroll(silent) {
        if (autoScrollInterval) clearTimeout(autoScrollInterval);
        autoScrollInterval = null;
        const b = document.getElementById("cmrd-scroll");
        if (b) { b.innerText = "1. Mulai Auto-Scroll (Ambil ID)"; b.style.background = "#1d9bf0"; }
        if (!silent) setMsg("");
      }

      function injectFloatingUI() {
        if (document.getElementById("camerad-panel")) return;
        const panel = document.createElement("div");
        panel.id = "camerad-panel";
        panel.style.cssText = `position:fixed;bottom:20px;right:20px;width:290px;background:#15202b;color:#fff;border:1px solid #38444d;border-radius:12px;padding:14px;font-family:sans-serif;box-shadow:0 4px 15px rgba(0,0,0,.5);z-index:2147483647;`;
        
        // PENAMBAHAN TOMBOL RESET DI PANEL MELAYANG
        panel.innerHTML = `
          <h4 style="margin:0 0 10px;border-bottom:1px solid #38444d;padding-bottom:6px;font-size:14px;">🤖 Camerad (Linear + Auto Expand)</h4>
          <p style="margin:4px 0;font-size:13px;">Data Tersimpan: <b id="cmrd-total" style="color:#1d9bf0;">0</b></p>
          <p style="margin:4px 0;font-size:13px;">Antrean ID: <b id="cmrd-queue" style="color:#00ba7c;">0</b></p>
          <input id="cmrd-server" placeholder="http://localhost:8080" style="width:100%;box-sizing:border-box;margin-top:8px;padding:6px 8px;border-radius:8px;border:1px solid #38444d;background:#0f151b;color:#fff;font-size:12px;">
          <button id="cmrd-scroll" style="width:100%;margin-top:8px;padding:8px;background:#1d9bf0;color:#fff;border:none;border-radius:99px;cursor:pointer;font-weight:bold;">1. Mulai Auto-Scroll (Ambil ID)</button>
          <button id="cmrd-ghost" style="width:100%;margin-top:7px;padding:8px;background:#00ba7c;color:#fff;border:none;border-radius:99px;cursor:pointer;font-weight:bold;">2. Buka Satu per Satu</button>
          <button id="cmrd-download" style="width:100%;margin-top:7px;padding:8px;background:#8e44ad;color:#fff;border:none;border-radius:99px;cursor:pointer;font-weight:bold;">3. Download NDJSON</button>
          <button id="cmrd-send" style="width:100%;margin-top:7px;padding:8px;background:#e0932f;color:#fff;border:none;border-radius:99px;cursor:pointer;font-weight:bold;">4. Kirim ke Server</button>
          <button id="cmrd-reset" style="width:100%;margin-top:7px;padding:8px;background:#f4212e;color:#fff;border:none;border-radius:99px;cursor:pointer;font-weight:bold;">🗑 Reset Data</button>
          <p id="cmrd-msg" style="margin:8px 0 0;font-size:11.5px;color:#8899a6;min-height:14px;"></p>`;
        
        if (document.documentElement) document.documentElement.appendChild(panel);

        if (chrome.runtime && chrome.runtime.sendMessage) {
          chrome.runtime.sendMessage({ action: "GET_SETTINGS" }, (res) => {
            if (res && res.settings) {
              const s = document.getElementById("cmrd-server");
              if (s) s.value = res.settings.serverUrl || "http://localhost:8080";
            }
            const t = document.getElementById("cmrd-total");
            const q = document.getElementById("cmrd-queue");
            if (t && res) t.innerText = res.total || 0;
            if (q && res) q.innerText = res.queue || 0;
          });
        }

        const serverEl = document.getElementById("cmrd-server");
        if (serverEl) serverEl.onchange = () => {
          chrome.runtime.sendMessage({ action: "SET_SETTINGS", settings: { serverUrl: serverEl.value.trim() } });
          setMsg("URL server disimpan.", true);
        };

        const scrollBtn = document.getElementById("cmrd-scroll");
        if (scrollBtn) scrollBtn.onclick = () => { if (autoScrollInterval) stopScroll(); else startScroll(); };

        const ghostBtn = document.getElementById("cmrd-ghost");
        if (ghostBtn) ghostBtn.onclick = () => {
          chrome.runtime.sendMessage({ action: "START_GHOST_TABS" }, (res) => {
            if (res && res.processing && res.queued > 0) {
              setMsg(`Membuka ${res.queued} tweet satu per satu… (jangan tutup tab X ini)`, true);
            } else if (res && res.queued === 0) {
              setMsg("Antrean kosong. Jalankan Auto-Scroll dulu untuk kumpulkan ID.", false);
            } else {
              setMsg("Sedang berjalan…");
            }
          });
        };

        const dlBtn = document.getElementById("cmrd-download");
        if (dlBtn) dlBtn.onclick = () => { chrome.runtime.sendMessage({ action: "DOWNLOAD_DATA" }); setMsg("Mengunduh NDJSON…", true); };

        const sendBtn = document.getElementById("cmrd-send");
        if (sendBtn) sendBtn.onclick = () => {
          setMsg("Mengirim ke server…");
          chrome.runtime.sendMessage({ action: "SEND_TO_SERVER" }, (res) => {
            if (res && res.ok) {
              const d = res.data || {};
              setMsg(`Terkirim: ${d.n_new || 0} baru, ${d.n_dup || 0} pembaruan, ${d.n_in || 0} total.`, true);
            } else {
              setMsg((res && res.error) || "Gagal kirim: cek server.", false);
            }
          });
        };

        // EKSEKUTOR RESET DATA
        const resetBtn = document.getElementById("cmrd-reset");
        if (resetBtn) resetBtn.onclick = () => {
          if (confirm("Anda yakin ingin menghapus semua data hasil scrape dan mengosongkan antrean?")) {
            if (typeof chrome !== 'undefined' && chrome.runtime.sendMessage) {
              chrome.runtime.sendMessage({ action: "RESET_DATA" }).then(() => {
                const t = document.getElementById("cmrd-total");
                const q = document.getElementById("cmrd-queue");
                if (t) t.innerText = "0";
                if (q) q.innerText = "0";
                setMsg("Data berhasil dihapus.", true);
              }).catch(() => {});
            }
          }
        };
      }

      setInterval(injectFloatingUI, 2000);
  }
}