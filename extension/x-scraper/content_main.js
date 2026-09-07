// content_main.js — Camerad X-Scraper (Linear Mode) v3.3
(function () {
  // HANYA RUTE DETAIL THREAD, mengabaikan timeline pencarian.
  const TARGET = [
    "TweetDetail",
    "TweetResultByRestId"
  ];

  function emit(data) {
    try { window.postMessage({ type: "CAMERAD_X_JSON", payload: data }, "*"); } catch (e) {}
  }
  function isTarget(url) {
    return typeof url === "string" && TARGET.some((ep) => url.includes(ep));
  }

  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    const response = await originalFetch.apply(this, args);
    try {
      const url = (args[0] instanceof Request) ? args[0].url : (args[0] || "");
      if (isTarget(url)) {
        response.clone().json().then((data) => emit(data)).catch(() => {});
      }
    } catch (err) {}
    return response;
  };

  const originalOpen = window.XMLHttpRequest.prototype.open;
  window.XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    try {
      this.addEventListener("load", function () {
        try {
          if (isTarget((url || "").toString())) emit(JSON.parse(this.responseText));
        } catch (err) {}
      });
    } catch (e) {}
    return originalOpen.call(this, method, url, ...rest);
  };
})();