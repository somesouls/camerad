// sidepanel.js - V19 (Main comments + reply thread columns)

let allComments = [];
let targetComments = [];

document.addEventListener('DOMContentLoaded', () => {
    setDefaultDates();

    document.getElementById('btnRefresh').addEventListener('click', () => sendAction("SCRAPE_COMMENTS"));
    document.getElementById('btnLoadAll').addEventListener('click', () => {
        showStatus("🤖 Robot berjalan: komentar utama + balasan...", true);
        sendAction("AUTO_SCROLL_AND_SCRAPE");
    });
    document.getElementById('btnExport').addEventListener('click', exportToCSV);

    document.getElementById('searchInput').addEventListener('input', applyFilters);
    document.getElementById('sortSelect').addEventListener('change', applyFilters);
    document.getElementById('agentSelect').addEventListener('change', applyFilters);
    document.getElementById('totalAgents').addEventListener('change', updateAgentDropdown);
    document.getElementById('startDate').addEventListener('change', applyFilters);
    document.getElementById('endDate').addEventListener('change', applyFilters);

    updateAgentDropdown();
});

function setDefaultDates() {
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    const yStr = getIsoDate(yesterday);
    document.getElementById('startDate').value = yStr;
    document.getElementById('endDate').value = yStr;
}

function getIsoDate(dateObj) {
    if (!dateObj) return "";
    const year = dateObj.getFullYear();
    const month = String(dateObj.getMonth() + 1).padStart(2, '0');
    const day = String(dateObj.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function updateAgentDropdown() {
    const total = parseInt(document.getElementById('totalAgents').value) || 3;
    const select = document.getElementById('agentSelect');
    const currentVal = select.value;
    select.innerHTML = '';

    const optRaw = document.createElement('option');
    optRaw.value = 'raw';
    optRaw.innerText = '📂 Semua Data (Raw)';
    select.appendChild(optRaw);

    const optCycle = document.createElement('option');
    optCycle.value = 'cycle_all';
    optCycle.innerText = '📅 Filter Tanggal Saja';
    select.appendChild(optCycle);

    for (let i = 1; i <= total; i++) {
        const opt = document.createElement('option');
        opt.value = i;
        opt.innerText = `👤 Agent ${i}`;
        select.appendChild(opt);
    }

    if (currentVal && (currentVal === 'raw' || currentVal === 'cycle_all' || parseInt(currentVal) <= total)) {
        select.value = currentVal;
    } else {
        select.value = 'raw';
    }
    applyFilters();
}

async function sendAction(actionName) {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    if (!tab || !tab.url) return;
    if (actionName === "SCRAPE_COMMENTS") showStatus("Sedang mengambil data komentar + balasan...", true);

    try {
        chrome.tabs.sendMessage(tab.id, { action: actionName }, (response) => {
            if (chrome.runtime.lastError) {
                renderEmpty("Error: refresh halaman TikTok/Instagram dulu, lalu buka ulang side panel.");
                showStatus("", false);
                return;
            }
            if (response && response.data) {
                processRawData(response.data);
                showStatus("", false);
            }
        });
    } catch (e) {
        renderEmpty("Gagal komunikasi dengan tab aktif.");
    }
}

function showStatus(msg, isVisible) {
    const el = document.getElementById('statusMsg');
    el.innerText = msg;
    el.className = isVisible ? 'status-bar' : 'status-bar hidden';
}

function processRawData(data) {
    const badge = document.getElementById('platform-indicator');
    badge.innerText = data.platform.toUpperCase();
    badge.className = `platform-badge bg-${data.platform}`;
    badge.classList.remove('hidden');

    allComments = (data.comments || []).map(c => ({ ...c, replyItems: Array.isArray(c.replyItems) ? c.replyItems : [] }));
    document.getElementById('totalRawBadge').innerText = allComments.length;
    document.getElementById('agentSelect').value = 'raw';
    applyFilters();
}

function applyFilters() {
    const filterText = document.getElementById('searchInput').value.toLowerCase();
    const sortMode = document.getElementById('sortSelect').value;
    const viewMode = document.getElementById('agentSelect').value;
    const totalAgents = parseInt(document.getElementById('totalAgents').value);

    const startStr = document.getElementById('startDate').value;
    const endStr = document.getElementById('endDate').value;

    targetComments = allComments.filter(c => {
        const cDate = parseFriendlyDate(c.timestamp);
        if (!cDate) return false;
        const cStr = getIsoDate(cDate);
        return cStr >= startStr && cStr <= endStr;
    });

    let baseData = viewMode === 'raw' ? [...allComments] : [...targetComments];

    const stableSort = (a, b) => {
        const tA = parseFriendlyDate(a.timestamp) || new Date(0);
        const tB = parseFriendlyDate(b.timestamp) || new Date(0);
        const rA = getReplyCount(a);
        const rB = getReplyCount(b);

        if (sortMode === 'most_likes') return (b.likes || 0) - (a.likes || 0);
        if (sortMode === 'most_replies') return rB - rA;

        if (tA.getTime() === tB.getTime()) {
            const idA = parseInt(String(a.id || '').split('-').pop()) || 0;
            const idB = parseInt(String(b.id || '').split('-').pop()) || 0;
            return sortMode === 'newest' ? idB - idA : idA - idB;
        }
        return sortMode === 'newest' ? tB - tA : tA - tB;
    };

    if (sortMode !== 'original') baseData.sort(stableSort);

    let finalSlice = baseData;
    if (viewMode !== 'raw' && viewMode !== 'cycle_all') {
        const agentIdx = parseInt(viewMode);
        const consistentList = [...baseData].sort((a, b) => {
            const tA = parseFriendlyDate(a.timestamp) || new Date(0);
            const tB = parseFriendlyDate(b.timestamp) || new Date(0);
            if (tA.getTime() === tB.getTime()) {
                const idA = parseInt(String(a.id || '').split('-').pop()) || 0;
                const idB = parseInt(String(b.id || '').split('-').pop()) || 0;
                return idA - idB;
            }
            return tA - tB;
        });

        const totalItems = consistentList.length;
        const itemsPerAgent = Math.ceil(totalItems / totalAgents);
        const startIndex = (agentIdx - 1) * itemsPerAgent;
        const endIndex = startIndex + itemsPerAgent;
        const agentIds = consistentList.slice(startIndex, endIndex).map(c => c.id);
        finalSlice = baseData.filter(c => agentIds.includes(c.id));
    }

    const finalData = finalSlice.filter(c => matchesSearch(c, filterText));
    document.getElementById('shownBadge').innerText = finalData.length;
    renderList(finalData);
}

function getReplyCount(c) {
    return Math.max(Number(c.replies || 0), Array.isArray(c.replyItems) ? c.replyItems.length : 0);
}

function matchesSearch(c, filterText) {
    if (!filterText) return true;
    const mainMatch = String(c.text || '').toLowerCase().includes(filterText) || String(c.user || '').toLowerCase().includes(filterText);
    const replyMatch = (c.replyItems || []).some(r =>
        String(r.text || '').toLowerCase().includes(filterText) || String(r.user || '').toLowerCase().includes(filterText)
    );
    return mainMatch || replyMatch;
}

function parseFriendlyDate(timeVal) {
    if (!timeVal) return null;
    const now = new Date();

    if (!isNaN(timeVal) && String(timeVal).length >= 9) {
        let ts = parseInt(timeVal);
        if (String(ts).length === 10) ts = ts * 1000;
        return new Date(ts);
    }

    const str = String(timeVal).trim().toLowerCase();
    const cleanStr = str.replace('ago', '').replace('lalu', '').trim();

    if (str.match(/^\d{1,2}-\d{1,2}$/)) {
        const parts = str.split('-');
        let d = new Date(now.getFullYear(), parseInt(parts[0]) - 1, parseInt(parts[1]));
        if (d > now) d.setFullYear(now.getFullYear() - 1);
        return d;
    }
    if (str.match(/^\d{4}-\d{1,2}-\d{1,2}$/)) return new Date(str);

    if (cleanStr.match(/^\d+[hmsdw]$/)) {
        const num = parseInt(cleanStr);
        const unit = cleanStr.slice(-1);
        let d = new Date();
        if (unit === 'm') d.setMinutes(now.getMinutes() - num);
        if (unit === 'h') d.setHours(now.getHours() - num);
        if (unit === 'd') d.setDate(now.getDate() - num);
        if (unit === 'w') d.setDate(now.getDate() - (num * 7));
        return d;
    }

    if (str.includes('ago') || str.includes('lalu') || str.includes('hours')) {
        const parts = str.split(' ');
        const num = parseInt(parts[0]);
        const unit = parts[1];
        let d = new Date();
        if (unit?.startsWith('min')) d.setMinutes(now.getMinutes() - num);
        if (unit?.startsWith('hour')) d.setHours(now.getHours() - num);
        if (unit?.startsWith('day')) d.setDate(now.getDate() - num);
        if (unit?.startsWith('week')) d.setDate(now.getDate() - (num * 7));
        return d;
    }

    let parsed = new Date(timeVal);
    if (!isNaN(parsed.getTime())) {
        if (parsed.getFullYear() < 2020) parsed.setFullYear(now.getFullYear());
        return parsed;
    }
    return null;
}

function renderList(data) {
    const container = document.getElementById('commentsList');
    container.innerHTML = '';

    if (data.length === 0) {
        const viewMode = document.getElementById('agentSelect').value;
        if (viewMode === 'raw' && allComments.length === 0) {
            container.innerHTML = `<div class="empty-state"><p>Data belum diambil.<br>Klik Robot/Refresh.</p></div>`;
        } else if (targetComments.length === 0) {
            container.innerHTML = `<div class="empty-state"><p>Tidak ada komentar pada rentang tanggal:<br><b>${document.getElementById('startDate').value} s.d. ${document.getElementById('endDate').value}</b></p></div>`;
        } else {
            container.innerHTML = `<div class="empty-state"><p>Tidak ada data di filter ini.</p></div>`;
        }
        return;
    }

    data.forEach(c => {
        const dateObj = parseFriendlyDate(c.timestamp);
        const dateDisplay = dateObj ? dateObj.toLocaleDateString('id-ID', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : c.timestamp;
        const debugDate = dateObj ? getIsoDate(dateObj) : "ERR";
        const replies = Array.isArray(c.replyItems) ? c.replyItems : [];

        let metricsHtml = '';
        if ((c.likes || 0) > 0) metricsHtml += `<span style="margin-right:8px;"><i class="fa-solid fa-heart" style="color:#ef4444;"></i> ${c.likes}</span>`;
        if (getReplyCount(c) > 0) metricsHtml += `<span><i class="fa-solid fa-comment-dots" style="color:#3b82f6;"></i> ${getReplyCount(c)}</span>`;

        const replyColumns = replies.map((r, idx) => {
            const rDate = parseFriendlyDate(r.timestamp);
            const rDateDisplay = rDate ? rDate.toLocaleDateString('id-ID', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : (r.timestamp || '');
            return `
                <div class="reply-column">
                    <div class="reply-title">Balasan ${idx + 1}</div>
                    <div class="reply-user">@${escapeHtml(r.user || 'Anonim')}</div>
                    <div class="reply-text">${escapeHtml(r.text || '')}</div>
                    <div class="reply-meta">${escapeHtml(rDateDisplay)}${r.likes ? ` · ❤️ ${r.likes}` : ''}</div>
                </div>
            `;
        }).join('');

        const div = document.createElement('div');
        div.className = 'comment-card thread-card';
        div.innerHTML = `
            <div class="thread-grid">
                <div class="main-column">
                    <div class="comment-header">
                        <span class="comment-user">@${escapeHtml(c.user || 'Anonim')}</span>
                        <div style="text-align:right;">
                            <span class="comment-date">${escapeHtml(dateDisplay || '')}</span>
                            <br><span style="font-size:9px; color:#aaa;">[Tgl: ${escapeHtml(debugDate)}]</span>
                        </div>
                    </div>
                    <div class="comment-text">${escapeHtml(c.text || '')}</div>
                    <div style="margin-top:8px; font-size:11px; font-weight:600; color:#64748b; border-top:1px solid #f1f5f9; padding-top:4px;">
                        ${metricsHtml || '<span style="color:#cbd5e1;">No interaction</span>'}
                    </div>
                </div>
                ${replyColumns || '<div class="reply-column empty-reply">Belum ada balasan terbaca</div>'}
            </div>
        `;

        div.addEventListener('click', async () => {
            document.querySelectorAll('.comment-card').forEach(el => el.classList.remove('active'));
            div.classList.add('active');
            const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
            chrome.tabs.sendMessage(tabs[0].id, { action: "SCROLL_TO_COMMENT", id: c.id });
        });
        container.appendChild(div);
    });
}

function renderEmpty(msg) {
    document.getElementById('commentsList').innerHTML = `<div class="empty-state"><p>${escapeHtml(msg)}</p></div>`;
}

function exportToCSV() {
    const viewMode = document.getElementById('agentSelect').value;
    const dataToExport = viewMode === 'raw' ? allComments : targetComments;

    if (!dataToExport || dataToExport.length === 0) {
        alert("Tidak ada data untuk di-export pada rentang tanggal ini.");
        return;
    }

    const maxReplies = dataToExport.reduce((max, c) => Math.max(max, (c.replyItems || []).length), 0);
    let csvContent = "\uFEFF";

    const headers = ["Tanggal Scrape", "Username", "Waktu Komentar", "Isi Komentar", "Likes", "Jumlah Balasan Terbaca", "Link Komentar"];
    for (let i = 1; i <= maxReplies; i++) headers.push(`Balasan ${i}`);
    csvContent += headers.join(',') + "\n";

    const scrapeDate = new Date().toLocaleString('id-ID');

    dataToExport.forEach(c => {
        let safeLink = c.link || "N/A";
        if (safeLink.includes("tiktok.com") && !safeLink.includes("is_from_webapp")) {
            safeLink += (safeLink.includes("?") ? "&" : "?") + "is_from_webapp=1&sender_device=pc";
        }

        let safeTime = c.timestamp || '';
        const dateObj = parseFriendlyDate(c.timestamp);
        if (dateObj) safeTime = dateObj.toLocaleString('id-ID');

        const row = [
            scrapeDate,
            c.user || '',
            safeTime,
            String(c.text || '').replace(/(\r\n|\n|\r)/gm, ' '),
            c.likes || 0,
            (c.replyItems || []).length,
            safeLink
        ];

        for (let i = 0; i < maxReplies; i++) {
            const r = (c.replyItems || [])[i];
            row.push(r ? `@${r.user || 'Anonim'}: ${String(r.text || '').replace(/(\r\n|\n|\r)/gm, ' ')}` : '');
        }

        csvContent += row.map(csvEscape).join(',') + "\n";
    });

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const startStr = document.getElementById('startDate').value;
    const endStr = document.getElementById('endDate').value;
    const filename = `SocialData_ThreadReplies_${startStr}_to_${endStr}.csv`;

    link.setAttribute("href", url);
    link.setAttribute("download", filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

function csvEscape(value) {
    return `"${String(value ?? '').replace(/"/g, '""')}"`;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}
