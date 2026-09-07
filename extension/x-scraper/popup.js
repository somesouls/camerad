// popup.js — Camerad X-Scraper (Linear Mode) v3.1
function setMsg(t, ok) {
  const el = document.getElementById("msg");
  el.innerText = t || "";
  el.style.color = ok === false ? "#f4212e" : (ok === true ? "#00ba7c" : "#8899a6");
}

function refresh() {
  chrome.storage.local.get(["totalScraped", "settings"], (r) => {
    document.getElementById("count").innerText = r.totalScraped || 0;
    const s = (r.settings && r.settings.serverUrl) || "http://localhost:8080";
    document.getElementById("server").value = s;
  });
}

document.addEventListener("DOMContentLoaded", () => {
  refresh();

  document.getElementById("server").onchange = (e) => {
    chrome.runtime.sendMessage({ action: "SET_SETTINGS", settings: { serverUrl: e.target.value.trim() } });
  };

  document.getElementById("downloadBtn").onclick = () => {
    chrome.runtime.sendMessage({ action: "DOWNLOAD_DATA" });
    setMsg("Mengunduh NDJSON…", true);
  };

  document.getElementById("sendBtn").onclick = () => {
    setMsg("Mengirim ke server…");
    chrome.runtime.sendMessage({ action: "SEND_TO_SERVER" }, (res) => {
      if (res && res.ok) {
        const d = res.data || {};
        setMsg(`Terkirim: ${d.n_new || 0} baru, ${d.n_dup || 0} pembaruan.`, true);
      } else {
        setMsg("Gagal: " + ((res && (res.error || (res.data && res.data.error))) || "cek server"), false);
      }
    });
  };

  document.getElementById("resetBtn").onclick = () => {
    if (!confirm("Hapus semua data hasil scrape di memori ekstensi?")) return;
    chrome.runtime.sendMessage({ action: "RESET_DATA" }, () => { refresh(); setMsg("Data direset.", true); });
  };
});
