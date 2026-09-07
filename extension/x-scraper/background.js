// background.js — Camerad X-Scraper (Linear Mode) v3.3
// FIX: Membunuh bug exponential looping pada ghost tab.

const DEFAULTS = {
  serverUrl: "http://localhost:8080",
  officialHandles: ["kring_pajak", "kringpajak", "pajakrepublik", "ditjenpajakri"],
  ghostMinMs: 1500,
  ghostMaxMs: 3500,
  ghostSoftMs: 2500,      
  ghostSoftMaxMs: 4000,
  ghostFailsafeMs: 12000, 
};

let settings = { ...DEFAULTS };
let mem = { queue: [], seen: [], scraped: {}, isProcessing: false, activeTabId: null };
let _closeTimer = null, _softTimer = null;
let ready = null;

function rnd(min, max) { return Math.floor(min + Math.random() * (max - min)); }

function ensureReady() {
  if (ready) return ready;
  ready = (async () => {
    const r = await chrome.storage.local.get(["state", "settings"]);
    if (r.settings) settings = { ...DEFAULTS, ...r.settings };
    if (r.state) {
      mem.queue = Array.isArray(r.state.queue) ? r.state.queue : [];
      mem.seen = Array.isArray(r.state.seen) ? r.state.seen : [];
      mem.scraped = (r.state.scraped && typeof r.state.scraped === "object") ? r.state.scraped : {};
    }
  })();
  return ready;
}

async function persist() {
  await chrome.storage.local.set({
    state: { queue: mem.queue, seen: mem.seen, scraped: mem.scraped },
    totalScraped: Object.keys(mem.scraped).length,
    latestData: Object.values(mem.scraped),
  });
}

async function saveSettings() { await chrome.storage.local.set({ settings }); }

function officialSet() {
  return new Set((settings.officialHandles || []).map((h) => String(h).toLowerCase().replace(/^@/, "")));
}

function toIso(created) {
  if (!created) return "";
  const d = new Date(created);
  return isNaN(d.getTime()) ? String(created) : d.toISOString();
}

function unwrapTweet(node) {
  if (!node || typeof node !== "object") return null;
  if (node.__typename === "TweetWithVisibilityResults" && node.tweet) return node.tweet;
  if (node.tweet && node.tweet.rest_id && node.tweet.legacy) return node.tweet;
  return node;
}

function getUserHandle(tw) {
  const u = tw && tw.core && tw.core.user_results && tw.core.user_results.result;
  if (u) {
    if (u.core && u.core.screen_name) return String(u.core.screen_name);
    if (u.legacy && u.legacy.screen_name) return String(u.legacy.screen_name);
  }
  if (tw && tw.legacy && tw.legacy.screen_name) return String(tw.legacy.screen_name);
  return "unknown";
}

function getUserName(tw) {
  const u = tw && tw.core && tw.core.user_results && tw.core.user_results.result;
  if (u) {
    if (u.core && u.core.name) return String(u.core.name);
    if (u.legacy && u.legacy.name) return String(u.legacy.name);
  }
  return "";
}

function getFullText(tw) {
  try {
    const note = tw.note_tweet && tw.note_tweet.note_tweet_results && tw.note_tweet.note_tweet_results.result;
    if (note && note.text) return String(note.text);
  } catch (e) {}
  if (tw.legacy && typeof tw.legacy.full_text === "string") return tw.legacy.full_text;
  if (typeof tw.full_text === "string") return tw.full_text;
  return "";
}

function getMedia(tw) {
  const out = [];
  try {
    const ent = (tw.legacy && (tw.legacy.extended_entities || tw.legacy.entities)) || {};
    for (const m of (ent.media || [])) if (m.media_url_https) out.push(m.media_url_https);
  } catch (e) {}
  return out;
}

function isTweetNode(n) {
  if (!n || typeof n !== "object" || !n.rest_id || !n.legacy) return false;
  const isUser = n.__typename === "User" || (n.legacy && n.legacy.screen_name);
  if (isUser) return false;
  return n.__typename === "Tweet" || n.__typename === "TweetWithVisibilityResults" ||
    typeof n.legacy.full_text === "string" || typeof n.legacy.conversation_id_str === "string";
}

function extractTweets(obj, off, results = [], depth = 0) {
  if (!obj || typeof obj !== "object" || depth > 60) return results;
  const cand = unwrapTweet(obj);
  if (cand && isTweetNode(cand)) {
    try {
      const handle = getUserHandle(cand);
      const text = getFullText(cand);
      if (text || cand.legacy.conversation_id_str) {
        results.push({
          tweet_id: String(cand.rest_id),
          conversation_id: String(cand.legacy.conversation_id_str || cand.rest_id),
          in_reply_to_id: cand.legacy.in_reply_to_status_id_str ? String(cand.legacy.in_reply_to_status_id_str) : null,
          author_handle: handle,
          author_name: getUserName(cand),
          is_official: off.has(String(handle).toLowerCase()),
          text: text,
          created_at: toIso(cand.legacy.created_at),
          created_at_raw: cand.legacy.created_at || "",
          like_count: (cand.legacy.favorite_count | 0),
          reply_count: (cand.legacy.reply_count | 0),
          repost_count: (cand.legacy.retweet_count | 0),
          media: getMedia(cand),
          lang: cand.legacy.lang || "",
          raw_json: cand,
        });
      }
    } catch (err) {}
  }
  try {
    for (const key in obj) {
      const v = obj[key];
      if (v && typeof v === "object") extractTweets(v, off, results, depth + 1);
    }
  } catch (e) {}
  return results;
}

function broadcastStats() {
  const payload = { action: "UPDATE_STATS", totalTweets: Object.keys(mem.scraped).length, queueCount: mem.queue.length };
  chrome.tabs.query({ url: ["*://*.x.com/*", "*://*.twitter.com/*"] }, (tabs) => {
    (tabs || []).forEach((tab) => { try { chrome.tabs.sendMessage(tab.id, payload).catch(() => {}); } catch (e) {} });
  });
}

function clearTimers() {
  if (_closeTimer) { clearTimeout(_closeTimer); _closeTimer = null; }
  if (_softTimer) { clearTimeout(_softTimer); _softTimer = null; }
}

async function closeAndNext(tabId) {
  if (mem.activeTabId !== tabId) return;
  clearTimers();
  
  // FIX FATAL: Set null TERLEBIH DAHULU agar onRemoved tidak terpicu
  mem.activeTabId = null; 
  
  try { await chrome.tabs.remove(tabId); } catch (e) {}
  await persist();
  setTimeout(() => { processNextTab(); }, rnd(settings.ghostMinMs, settings.ghostMaxMs));
}

async function processNextTab() {
  await ensureReady();
  if (!mem.isProcessing) { broadcastStats(); return; }
  
  let nextId = null;
  while (mem.queue.length) {
    const c = String(mem.queue.shift());
    if (!mem.scraped[c]) { nextId = c; break; }
  }
  
  await persist();
  broadcastStats();
  
  if (!nextId) { 
    mem.isProcessing = false; 
    mem.activeTabId = null; 
    await persist(); 
    broadcastStats(); 
    return; 
  }

  chrome.tabs.create({ url: `https://x.com/i/status/${nextId}`, active: false }, (tab) => {
    if (!tab) { setTimeout(() => processNextTab(), 800); return; }
    mem.activeTabId = tab.id;
    clearTimers();
    _closeTimer = setTimeout(() => { closeAndNext(tab.id); }, settings.ghostFailsafeMs);
  });
}

chrome.tabs.onRemoved.addListener((tabId) => {
  if (mem.activeTabId === tabId) {
    clearTimers();
    mem.activeTabId = null;
    if (mem.isProcessing) setTimeout(() => processNextTab(), rnd(settings.ghostMinMs, settings.ghostMaxMs));
  }
});

async function sendToServer() {
  await ensureReady();
  const items = Object.values(mem.scraped);
  if (!items.length) return { ok: false, error: "Belum ada data untuk dikirim. Jalankan scrape dulu." };
  const base = (settings.serverUrl || DEFAULTS.serverUrl).replace(/\/$/, "");
  const url = base + "/api/sosmed/ingest";
  const ndjson = items.map((t) => JSON.stringify(t)).join("\n");
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/x-ndjson" }, body: ndjson });
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) return { ok: false, code: r.status, error: `Server balas HTTP ${r.status} di ${url}`, data };
    return { ok: data.ok !== false, code: r.status, data };
  } catch (e) {
    return { ok: false, error: `Tak bisa hubungi ${url}. Pastikan backend jalan di port itu. (${e && e.message ? e.message : e})` };
  }
}

async function handle(request, sender) {
  await ensureReady();
  switch (request.action) {
    case "ADD_TO_QUEUE": {
      const seenSet = new Set(mem.seen);
      let added = 0;
      (request.ids || []).forEach((id) => {
        id = String(id);
        if (!seenSet.has(id) && !mem.scraped[id]) { mem.queue.push(id); seenSet.add(id); added++; }
      });
      mem.seen = Array.from(seenSet);
      if (added) await persist();
      broadcastStats();
      return { ok: true, added, queue: mem.queue.length };
    }
    case "START_GHOST_TABS": {
      if (!mem.isProcessing && mem.queue.length > 0) {
        mem.isProcessing = true;
        await persist();
        processNextTab();
      }
      return { ok: true, queued: mem.queue.length, processing: mem.isProcessing };
    }
    case "STOP_GHOST_TABS": {
      mem.isProcessing = false;
      clearTimers();
      await persist();
      return { ok: true };
    }
    case "PROCESS_JSON": {
      const off = officialSet();
      const tweets = extractTweets(request.data, off);
      let added = 0;
      tweets.forEach((t) => { if (!mem.scraped[t.tweet_id]) added++; mem.scraped[t.tweet_id] = t; });
      if (tweets.length) await persist();
      broadcastStats();
      
      if (sender && sender.tab && sender.tab.id === mem.activeTabId) {
        if (_softTimer) clearTimeout(_softTimer);
        _softTimer = setTimeout(() => { closeAndNext(sender.tab.id); }, rnd(settings.ghostSoftMs, settings.ghostSoftMaxMs));
      }
      return { ok: true, added, total: Object.keys(mem.scraped).length };
    }
    case "DOWNLOAD_DATA": {
      const ndjson = Object.values(mem.scraped).map((t) => JSON.stringify(t)).join("\n");
      const blobUrl = "data:application/x-ndjson;charset=utf-8," + encodeURIComponent(ndjson);
      await chrome.downloads.download({ url: blobUrl, filename: `camerad_x_${Date.now()}.ndjson` }).catch(() => {});
      return { ok: true, total: Object.keys(mem.scraped).length };
    }
    case "SEND_TO_SERVER": return await sendToServer();
    case "GET_SETTINGS": return { ok: true, settings, total: Object.keys(mem.scraped).length, queue: mem.queue.length };
    case "SET_SETTINGS":
      settings = { ...settings, ...(request.settings || {}) };
      await saveSettings();
      return { ok: true, settings };
    case "RESET_DATA":
      mem = { queue: [], seen: [], scraped: {}, isProcessing: false, activeTabId: null };
      clearTimers();
      await persist();
      broadcastStats();
      return { ok: true };
    default:
      return { ok: false, error: "unknown action" };
  }
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  handle(request, sender).then((r) => { try { sendResponse(r); } catch (e) {} })
    .catch((e) => { try { sendResponse({ ok: false, error: String(e && e.message ? e.message : e) }); } catch (_) {} });
  return true; 
});