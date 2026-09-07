// content.js - V20 (IG caption-safe thread grouping + TikTok reply threads)

console.log("🔥 SOCIAL MANAGER V20 (IG THREAD FIX) READY 🔥");

if (window.location.href.includes("tiktok.com")) {
    const script = document.createElement('script');
    script.src = chrome.runtime.getURL('interceptor.js');
    script.onload = function() { this.remove(); };
    (document.head || document.documentElement).appendChild(script);

    const autoOpenInterval = setInterval(openCommentSection, 1000);
    setTimeout(() => clearInterval(autoOpenInterval), 10000);
}

// --- STATE NETWORK TIKTOK ---
let interceptedData = {};      // cid -> main comment metadata
let interceptedReplies = {};   // parent cid -> reply metadata array

window.addEventListener('message', (event) => {
    if (event.data.type !== 'TT_NET_INTERCEPT' || !event.data.payload) return;

    const payload = event.data.payload;
    const comments = payload.comments || [];

    if (payload.kind === 'reply') {
        const parentCid = payload.parentCid || 'unknown';
        if (!interceptedReplies[parentCid]) interceptedReplies[parentCid] = [];

        comments.forEach(c => {
            const item = normalizeTikTokApiComment(c, true);
            if (!item || !item.id) return;

            const exists = interceptedReplies[parentCid].some(x => x.id === item.id);
            if (!exists) interceptedReplies[parentCid].push(item);
        });
        return;
    }

    comments.forEach(c => {
        const item = normalizeTikTokApiComment(c, false);
        if (!item || !item.id) return;
        interceptedData[item.id] = item;
    });
});

function normalizeTikTokApiComment(c, isReply = false) {
    if (!c) return null;
    return {
        id: c.cid || c.comment_id || c.reply_id || '',
        parentId: c.reply_to_reply_id || c.parent_comment_id || c.comment_id || '',
        user: c.user?.unique_id || c.user?.nickname || c.user_id || 'Anonim',
        text: c.text || '',
        timestamp: c.create_time || '',
        link: c.share_info?.url || window.location.href,
        likes: c.digg_count || 0,
        replies: c.reply_comment_total || 0,
        isReply
    };
}

// --- MESSAGE LISTENER ---
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "SCRAPE_COMMENTS") {
        showToast("🔄 Menggabungkan data komentar + balasan...", "info");
        openCommentSection();

        setTimeout(() => {
            const result = scrapeComments();
            if (result.comments.length > 0) showToast(`✅ Terbaca: ${result.comments.length} thread`, "success");
            else showToast("⚠️ 0 Data. Pastikan komentar terlihat.", "warning");
            sendResponse({ success: true, data: result });
        }, 700);
        return true;
    }

    if (request.action === "AUTO_SCROLL_AND_SCRAPE") {
        openCommentSection();
        showToast("🤖 Ambil komentar utama dulu...", "warning");

        (async () => {
            await sleep(1000);
            await autoScrollSmart();

            showToast("💬 Membuka balasan komentar...", "warning");
            await expandAllReplyThreads();

            // Beberapa platform baru me-render reply setelah scroll kecil.
            await sleep(800);
            await autoScrollSmart({ maxScrolls: 20, waitMs: 650 });
            await expandAllReplyThreads({ maxPasses: 2, waitMs: 650 });

            showToast("🛑 Finalisasi data...", "info");
            const result = scrapeComments();
            sendResponse({ success: true, data: result });
        })();
        return true;
    }

    if (request.action === "SCROLL_TO_COMMENT") {
        scrollToComment(request.id);
        sendResponse({ success: true });
    }
});

// --- UI HELPER ---
function showToast(text, type = "info") {
    const old = document.getElementById('sm-toast');
    if (old) old.remove();
    const toast = document.createElement('div');
    toast.id = 'sm-toast';
    toast.innerText = text;
    Object.assign(toast.style, {
        position: 'fixed', bottom: '20px', right: '20px', padding: '12px 24px',
        backgroundColor: type === 'success' ? '#10b981' : (type === 'warning' ? '#f59e0b' : '#3b82f6'),
        color: 'white', borderRadius: '8px', zIndex: '9999999',
        fontFamily: 'sans-serif', fontSize: '14px', fontWeight: 'bold', boxShadow: '0 4px 10px rgba(0,0,0,0.2)'
    });
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function norm(str) {
    return String(str || '').replace(/\s+/g, ' ').trim();
}

function getText(el) {
    return norm(el?.innerText || el?.textContent || '');
}

// --- OPEN COMMENT TAB ---
function openCommentSection() {
    const commentTab = document.querySelector('#comments');
    if (!commentTab) return;

    const btn = commentTab.querySelector('button');
    const isActive = btn && btn.classList.contains('TUXTabBar-itemTitle--active');
    if (btn && !isActive) {
        console.log("🤖 Auto-opening Comments Tab...");
        btn.click();
        commentTab.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

// --- REPLY EXPANDER ---
async function expandAllReplyThreads(options = {}) {
    const maxPasses = options.maxPasses ?? 5;
    const waitMs = options.waitMs ?? 900;
    let totalClicks = 0;

    for (let pass = 0; pass < maxPasses; pass++) {
        const buttons = findReplyExpandButtons();
        let clickedThisPass = 0;

        for (const btn of buttons) {
            if (!btn || btn.dataset.smReplyClicked === '1') continue;
            if (!isElementVisible(btn)) continue;

            btn.dataset.smReplyClicked = '1';
            btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            await sleep(120);
            btn.click();
            clickedThisPass++;
            totalClicks++;

            // Jangan brutal. Kalau terlalu cepat, TikTok/IG sering gagal render.
            await sleep(250);
        }

        if (clickedThisPass === 0) break;
        await sleep(waitMs);
    }

    console.log(`SM: opened reply buttons = ${totalClicks}`);
}

function findReplyExpandButtons() {
    const textSelector = 'button, [role="button"], div[tabindex="0"], span, p';
    return Array.from(document.querySelectorAll(textSelector)).filter(el => isReplyExpandText(getText(el)));
}

function isReplyExpandText(text) {
    const t = norm(text).toLowerCase();
    if (!t) return false;

    // Jangan klik tombol input reply biasa.
    if (t === 'reply' || t === 'balas') return false;

    return (
        /view\s+(all\s+)?\d*\s*repl/.test(t) ||
        /view\s+more\s+repl/.test(t) ||
        /see\s+more\s+repl/.test(t) ||
        /lihat\s+(semua\s+)?\d*\s*balasan/.test(t) ||
        /tampilkan\s+(semua\s+)?\d*\s*balasan/.test(t) ||
        /balasan\s+lainnya/.test(t) ||
        /more\s+repl/.test(t)
    );
}

function isElementVisible(el) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
}

// --- SCRAPER ENGINE ---
function scrapeComments() {
    const url = window.location.href;
    let comments = [];

    if (url.includes('tiktok.com')) {
        comments = scrapeTikTokThreads();
    } else if (url.includes('instagram.com')) {
        comments = scrapeInstagramThreads();
    }

    return { platform: url.includes('tiktok') ? 'tiktok' : 'instagram', comments };
}

function scrapeTikTokThreads() {
    const comments = [];
    const wrappers = document.querySelectorAll('div[class*="DivCommentObjectWrapper"]');

    wrappers.forEach((wrapper, index) => {
        try {
            const uniqueId = `ext-tt-${index}`;
            wrapper.setAttribute('data-ext-id', uniqueId);

            const mainItem = wrapper.querySelector('div[class*="DivCommentItemWrapper"]');
            if (!mainItem) return;

            const mainDom = extractTikTokCommentItem(mainItem, 1);
            if (!mainDom.text) return;

            const matched = findInterceptedMain(mainDom.text);
            const main = {
                id: uniqueId,
                serverId: matched?.id || '',
                user: matched?.user || mainDom.user,
                text: matched?.text || mainDom.text,
                timestamp: matched?.timestamp || mainDom.timestamp,
                link: matched?.link || window.location.href,
                likes: matched?.likes ?? mainDom.likes,
                replies: matched?.replies ?? 0,
                replyItems: []
            };

            const domReplies = extractTikTokReplies(wrapper);
            const netReplies = main.serverId && interceptedReplies[main.serverId] ? interceptedReplies[main.serverId] : [];

            main.replyItems = mergeReplies(domReplies, netReplies);
            main.replies = Math.max(Number(main.replies || 0), main.replyItems.length);

            comments.push(main);
        } catch (err) {
            console.error('TikTok scrape error:', err);
        }
    });

    return comments;
}

function findInterceptedMain(text) {
    const target = norm(text);
    if (!target) return null;

    for (const key of Object.keys(interceptedData)) {
        const item = interceptedData[key];
        if (norm(item.text) === target) return item;
    }
    return null;
}

function extractTikTokReplies(wrapper) {
    const replyContainer = wrapper.querySelector('div[class*="DivReplyContainer"]');
    if (!replyContainer) return [];

    const items = Array.from(replyContainer.querySelectorAll('div[class*="DivCommentItemWrapper"]'));
    return items.map(item => extractTikTokCommentItem(item, 2)).filter(r => r.text);
}

function extractTikTokCommentItem(item, level = 1) {
    const usernameSelector = level === 2 ? '[data-e2e="comment-username-2"]' : '[data-e2e="comment-username-1"]';
    const textSelector = level === 2 ? '[data-e2e="comment-level-2"]' : '[data-e2e="comment-level-1"]';

    let user = 'Anonim';
    const userEl = item.querySelector(usernameSelector) || item.querySelector('a[href^="/@"]');
    if (userEl) {
        if (userEl.tagName === 'A') user = userEl.getAttribute('href').replace('/@', '').replace('/', '');
        else user = getText(userEl).split('\n')[0];
    }

    const text = getText(item.querySelector(textSelector));

    let timestamp = '';
    const subContent = item.querySelector('div[class*="DivCommentSubContentWrapper"]');
    if (subContent) {
        const spans = subContent.querySelectorAll('span');
        for (const span of spans) {
            const t = getText(span);
            if (t && t.toLowerCase() !== 'reply' && t.toLowerCase() !== 'balas' && t.length < 30) {
                timestamp = t;
                break;
            }
        }
        if (!timestamp) timestamp = getText(subContent).split('Reply')[0].split('Balas')[0].trim();
    }

    let likes = 0;
    const likeDiv = item.querySelector('div[class*="DivLikeContainer"]');
    if (likeDiv) likes = parseMetricString(getText(likeDiv));

    return { user, text, timestamp, link: window.location.href, likes };
}

function mergeReplies(domReplies, netReplies) {
    const map = new Map();

    [...domReplies, ...netReplies].forEach((r, idx) => {
        const key = norm(`${r.user}|${r.text}`) || `idx-${idx}`;
        if (!map.has(key)) {
            map.set(key, {
                user: r.user || 'Anonim',
                text: r.text || '',
                timestamp: r.timestamp || '',
                link: r.link || window.location.href,
                likes: r.likes || 0
            });
        }
    });

    return Array.from(map.values());
}

function scrapeInstagramThreads() {
    // V21 IG fix:
    // V19/V20 masih rawan karena reply ditempel ke "thread terakhir".
    // Sekarang grouping IG memakai ID komentar dari permalink /p/<shortcode>/c/<commentId>/.
    // Reply tidak boleh ditempel ke thread terakhir; reply hanya ditempel ke main comment yang parentId-nya cocok.
    const rows = [];
    const seenCommentIds = new Set();
    const timeElements = Array.from(document.querySelectorAll('time'));

    timeElements.forEach((timeEl, index) => {
        try {
            const row = findInstagramCommentRow(timeEl);
            if (!row) return;

            const item = extractInstagramCommentFromRow(row, timeEl, index);
            if (!item || !item.text || !item.user || !item.commentId) return;

            // Dedup keras berbasis ID server IG, bukan teks.
            if (seenCommentIds.has(item.commentId)) return;
            seenCommentIds.add(item.commentId);

            const parentId = findInstagramParentIdForReplyRow(row);
            item.parentId = parentId && parentId !== item.commentId ? parentId : '';

            rows.push({ row, item });
        } catch (e) {
            console.error('Instagram scrape collect error:', e);
        }
    });

    const threads = [];
    const threadByCommentId = new Map();

    // Pass 1: buat thread hanya dari komentar yang BUKAN berada di reply <ul>.
    rows.forEach(({ item }) => {
        if (item.parentId) return;

        const thread = {
            ...item,
            serverId: item.commentId,
            replies: Number(item.replies || 0),
            replyItems: []
        };

        threads.push(thread);
        threadByCommentId.set(item.commentId, thread);
    });

    // Pass 2: tempel reply ke main comment dengan parentId yang sama.
    rows.forEach(({ row, item }) => {
        if (!item.parentId) return;

        let parentThread = threadByCommentId.get(item.parentId);

        // Fallback defensif: kalau main comment belum masuk karena DOM IG aneh,
        // cari row parent dari kumpulan rows, lalu buat parent thread.
        if (!parentThread) {
            const parentRecord = rows.find(r => r.item.commentId === item.parentId && !r.item.parentId);
            if (parentRecord) {
                parentThread = {
                    ...parentRecord.item,
                    serverId: parentRecord.item.commentId,
                    replies: Number(parentRecord.item.replies || 0),
                    replyItems: []
                };
                threads.push(parentThread);
                threadByCommentId.set(parentRecord.item.commentId, parentThread);
            }
        }

        // Fallback terakhir: pakai main comment terdekat SEBELUM reply, tapi hanya jika parentId tidak ditemukan.
        // Ini tidak dipakai dalam kondisi normal.
        if (!parentThread) {
            parentThread = findNearestPreviousInstagramThread(row, threads);
        }

        if (!parentThread) return;

        const duplicate = parentThread.replyItems.some(r => r.commentId === item.commentId);
        if (!duplicate) {
            parentThread.replyItems.push({
                id: item.id,
                commentId: item.commentId,
                parentId: item.parentId,
                user: item.user,
                text: item.text,
                timestamp: item.timestamp,
                link: item.link,
                likes: item.likes
            });
        }
    });

    // Sinkronkan count reply yang benar.
    threads.forEach(t => {
        t.replies = Math.max(Number(t.replies || 0), Array.isArray(t.replyItems) ? t.replyItems.length : 0);
    });

    console.table(threads.map(t => ({
        mainId: t.commentId,
        user: t.user,
        replies: t.replyItems.length,
        replyParentIds: t.replyItems.map(r => r.parentId).join('|')
    })));

    return threads;
}

function findInstagramCommentRow(timeEl) {
    let node = timeEl?.parentElement;

    for (let depth = 0; node && node !== document.body && depth < 14; depth++, node = node.parentElement) {
        if (!node.querySelectorAll) continue;

        // Unit komentar harus punya satu timestamp saja.
        if (node.querySelectorAll('time').length !== 1) continue;

        // Harus punya permalink komentar /c/<id>/.
        // Caption akun punya time, tapi umumnya tidak punya permalink komentar seperti ini.
        if (!getInstagramCommentIdFromElement(node)) continue;

        // Caption digugurkan: komentar punya Reply/Balas sendiri.
        if (!hasInstagramOwnReplyAction(node)) continue;

        if (!getInstagramUsernameFromRow(node)) continue;

        return node;
    }

    return null;
}

function hasInstagramOwnReplyAction(row) {
    const controls = Array.from(row.querySelectorAll('button, [role="button"], div[tabindex="0"], span, p'));
    return controls.some(el => {
        const t = norm(el.innerText || el.textContent || '').toLowerCase();
        return t === 'reply' || t === 'balas';
    });
}

function getInstagramUsernameFromRow(row) {
    const links = Array.from(row.querySelectorAll('a[href^="/"]'));

    for (const a of links) {
        const href = a.getAttribute('href') || '';

        // Profil user IG: /username/
        // Buang hashtag, post, reel, comment permalink, dan route internal.
        if (!/^\/[^\/?#]+\/?$/.test(href)) continue;
        if (
            href.startsWith('/p/') ||
            href.startsWith('/reel/') ||
            href.startsWith('/tv/') ||
            href.startsWith('/c/') ||
            href.startsWith('/explore/') ||
            href.startsWith('/accounts/') ||
            href.startsWith('/stories/')
        ) continue;

        const fromText = getText(a).split('\n')[0].replace(/\s*Verified\s*$/i, '').trim();
        if (fromText && fromText.length <= 80) return fromText;

        const fromHref = href.replace(/^\/|\/$/g, '').trim();
        if (fromHref) return fromHref;
    }

    return '';
}

function extractInstagramCommentFromRow(row, timeEl, index) {
    const commentId = getInstagramCommentIdFromElement(row) || getInstagramCommentIdFromElement(timeEl);
    const uniqueId = commentId ? `ext-ig-${commentId}` : `ext-ig-${index}`;
    row.setAttribute('data-ext-id', uniqueId);

    const timestamp = timeEl.getAttribute('datetime') || getText(timeEl);

    let link = window.location.href;
    const dateLink = timeEl.closest('a[href*="/c/"]') || timeEl.closest('a');
    if (dateLink && dateLink.href) link = dateLink.href;

    const user = getInstagramUsernameFromRow(row);
    const text = extractInstagramTextFromRow(row, user, timeEl);
    const likes = extractInstagramLikesFromRow(row);
    const replies = extractInstagramReplyCountNearRow(row);

    return {
        id: uniqueId,
        serverId: commentId,
        commentId,
        parentId: '',
        user,
        text,
        timestamp,
        link,
        likes,
        replies,
        replyItems: []
    };
}

function getInstagramCommentIdFromElement(el) {
    if (!el) return '';

    const directHref = el.getAttribute && el.getAttribute('href');
    if (directHref) {
        const direct = parseInstagramCommentIdFromHref(directHref);
        if (direct) return direct;
    }

    const anchor = el.closest?.('a[href*="/c/"]') || el.querySelector?.('a[href*="/c/"]');
    if (!anchor) return '';

    return parseInstagramCommentIdFromHref(anchor.getAttribute('href') || anchor.href || '');
}

function parseInstagramCommentIdFromHref(href) {
    const m = String(href || '').match(/\/c\/(\d+)/);
    return m ? m[1] : '';
}

function findInstagramParentIdForReplyRow(row) {
    // Reply IG pada inspect berada di dalam <ul> setelah tombol "Hide all replies".
    // Parent ID diambil dari permalink komentar utama terakhir sebelum <ul> tersebut.
    const replyUl = row.closest('ul');
    if (!replyUl) return '';

    for (let root = replyUl.parentElement, depth = 0; root && root !== document.body && depth < 10; root = root.parentElement, depth++) {
        const anchors = Array.from(root.querySelectorAll('a[href*="/c/"]'));
        const beforeIds = [];

        anchors.forEach(a => {
            // Abaikan permalink reply yang berada DI DALAM ul.
            if (replyUl.contains(a)) return;

            // Ambil hanya permalink yang muncul sebelum ul dalam urutan DOM.
            const pos = a.compareDocumentPosition(replyUl);
            const isBeforeUl = Boolean(pos & Node.DOCUMENT_POSITION_FOLLOWING);
            if (!isBeforeUl) return;

            const id = parseInstagramCommentIdFromHref(a.getAttribute('href') || a.href || '');
            if (id) beforeIds.push(id);
        });

        if (beforeIds.length > 0) {
            return beforeIds[beforeIds.length - 1]; // ID main comment terdekat sebelum reply list.
        }
    }

    return '';
}

function findNearestPreviousInstagramThread(row, threads) {
    if (!row || threads.length === 0) return null;

    // Cari thread yang elemennya berada sebelum row dalam DOM.
    for (let i = threads.length - 1; i >= 0; i--) {
        const el = document.querySelector(`[data-ext-id="${threads[i].id}"]`);
        if (!el) continue;

        const pos = el.compareDocumentPosition(row);
        const isBeforeRow = Boolean(pos & Node.DOCUMENT_POSITION_FOLLOWING);
        if (isBeforeRow) return threads[i];
    }

    return null;
}

function extractInstagramTextFromRow(row, user, timeEl) {
    const timestampText = getText(timeEl);

    // Ambil span konten paling aman: span terluar, bukan nested span hashtag/mention.
    const spans = Array.from(row.querySelectorAll('span')).filter(span => {
        const t = getText(span);
        if (!t) return false;
        if (t === user) return false;
        if (t === timestampText) return false;
        if (/^verified$/i.test(t)) return false;
        if (/^(reply|balas)$/i.test(t)) return false;
        if (isReplyExpandText(t)) return false;
        if (/^\d+(\.\d+)?[km]?\s*(likes?|suka)$/i.test(t)) return false;
        if (span.closest('button') || span.closest('[role="button"]')) return false;

        // Jangan dobel hitung nested span di dalam span komentar.
        let p = span.parentElement;
        while (p && p !== row) {
            if (p.tagName === 'SPAN') return false;
            p = p.parentElement;
        }

        return true;
    });

    if (spans.length > 0) {
        const best = spans
            .map(span => getText(span))
            .filter(Boolean)
            .sort((a, b) => b.length - a.length)[0];

        const cleaned = cleanInstagramText(best, user, timestampText);
        if (cleaned) return cleaned;
    }

    return cleanInstagramText(getText(row), user, timestampText);
}

function cleanInstagramText(raw, user, timestampText) {
    let text = norm(raw);
    if (!text) return '';

    const removeTokens = [
        user,
        'Verified',
        timestampText,
        'Reply',
        'Balas'
    ].filter(Boolean);

    for (const token of removeTokens) {
        const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        text = text.replace(new RegExp(`(^|\\s)${escaped}(?=\\s|$)`, 'gi'), ' ');
    }

    text = text
        .replace(/\b\d+(\.\d+)?[km]?\s*(likes?|suka)\b/gi, ' ')
        .replace(/\bsee translation\b/gi, ' ')
        .replace(/\bterjemahkan\b/gi, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    return text;
}

function extractInstagramLikesFromRow(row) {
    const candidates = Array.from(row.querySelectorAll('span, a, div'))
        .map(getText)
        .filter(Boolean);

    for (const txt of candidates) {
        const low = txt.toLowerCase();
        if (/^\d+(\.\d+)?[km]?\s*(likes?|suka)$/.test(low)) {
            return parseMetricString(low);
        }
    }

    return 0;
}

function extractInstagramReplyCountNearRow(row) {
    // Count hanya sebagai indikasi. Nilai final disinkronkan dengan replyItems.length.
    let container = row.parentElement;
    for (let depth = 0; container && depth < 4; depth++, container = container.parentElement) {
        // Jangan hitung tombol Hide all replies sebagai jumlah reply.
        const texts = Array.from(container.querySelectorAll('[role="button"], button, span, div'))
            .map(getText)
            .filter(t => isReplyExpandText(t) && !/^hide all replies$/i.test(t));

        if (texts.length > 0) {
            const parsed = parseMetricString(texts[0]);
            if (parsed > 0) return parsed;
        }
    }
    return 0;
}

// Helper: Ubah "10.5K" jadi 10500
function parseMetricString(str) {
    if (!str) return 0;
    str = String(str).toLowerCase()
        .replace(/,/g, '')
        .replace(/likes?/g, '')
        .replace(/replies?/g, '')
        .replace(/suka/g, '')
        .replace(/balasan/g, '')
        .replace(/[()]/g, '')
        .trim();

    let multiplier = 1;
    if (str.includes('k')) { multiplier = 1000; str = str.replace('k', ''); }
    if (str.includes('m')) { multiplier = 1000000; str = str.replace('m', ''); }

    const match = str.match(/\d+(\.\d+)?/);
    const num = match ? parseFloat(match[0]) : NaN;
    return isNaN(num) ? 0 : Math.floor(num * multiplier);
}

// --- SCROLL ENGINE ---
async function autoScrollSmart(options = {}) {
    return new Promise(async (resolve) => {
        let scrollTarget = window;
        const maxScrolls = options.maxScrolls ?? 150;
        const waitMs = options.waitMs ?? 1500;

        if (window.location.href.includes("tiktok.com")) {
            const startNode = document.querySelector('[data-e2e="comment-list"]') || document.querySelector('div[class*="DivCommentListContainer"]');
            if (startNode) {
                let currentNode = startNode;
                for (let i = 0; i < 10; i++) {
                    if (!currentNode) break;
                    const style = window.getComputedStyle(currentNode);
                    const overflowY = style.overflowY;
                    const isScrollable = (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay');
                    const hasHeight = currentNode.scrollHeight > currentNode.clientHeight;
                    if (isScrollable && hasHeight) {
                        scrollTarget = currentNode;
                        break;
                    }
                    currentNode = currentNode.parentElement;
                }
            }
        } else if (window.location.href.includes("instagram.com")) {
            const possibleTargets = document.querySelectorAll('div, ul');
            for (const el of possibleTargets) {
                const style = window.getComputedStyle(el);
                if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
                    scrollTarget = el;
                    break;
                }
            }
        }

        let lastHeight = getScrollHeight(scrollTarget);
        let scrollCount = 0;
        let sameHeightCount = 0;

        while (scrollCount < maxScrolls) {
            doScroll(scrollTarget);
            await sleep(waitMs);
            const newHeight = getScrollHeight(scrollTarget);

            if (Math.abs(newHeight - lastHeight) < 50) {
                sameHeightCount++;
                if (sameHeightCount >= 4) break;
            } else {
                sameHeightCount = 0;
                lastHeight = newHeight;
            }
            scrollCount++;
        }
        resolve();
    });
}

function doScroll(element) {
    if (element === window) {
        window.scrollTo(0, document.body.scrollHeight);
    } else {
        element.scrollTop = element.scrollHeight;
        element.dispatchEvent(new Event('scroll'));
        element.dispatchEvent(new WheelEvent('wheel', { deltaY: 500, bubbles: true }));
    }
}

function getScrollHeight(element) {
    if (element === window) return document.body.scrollHeight;
    return element.scrollHeight;
}

function scrollToComment(id) {
    const element = document.querySelector(`[data-ext-id="${id}"]`);
    if (!element) return;

    element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const originalBorder = element.style.border;
    element.style.transition = "all 0.5s";
    element.style.border = "2px solid #ff0050";
    element.style.padding = "5px";
    setTimeout(() => {
        element.style.border = originalBorder;
        element.style.padding = "";
    }, 2000);
}
