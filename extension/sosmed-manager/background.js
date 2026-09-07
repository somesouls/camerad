// background.js

// Pastikan API tersedia sebelum memanggilnya
if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((error) => console.error("Gagal set panel behavior:", error));
}

console.log("Background Service Worker Ready");