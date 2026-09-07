// inject.js - Mata-mata React TikTok

// Fungsi untuk menggali data JSON dari elemen DOM
function findReactProps(dom) {
    const key = Object.keys(dom).find(k => k.startsWith('__reactProps'));
    if (!key) return null;
    return dom[key];
}

function extractCommentData(props, depth = 0) {
    if (!props || depth > 3) return null;
    
    // Cek apakah ini objek komentar yang kita cari (punya cid & create_time)
    if (props.cid && props.create_time && props.user) {
        return props;
    }
    
    // Cek properti umum tempat TikTok menyimpan data
    if (props.comment) return extractCommentData(props.comment, depth + 1);
    if (props.children && props.children.props) return extractCommentData(props.children.props, depth + 1);
    
    return null;
}

// Dengarkan perintah dari Content Script
window.addEventListener('message', (event) => {
    if (event.data.type === 'TT_SCRAPE_REQUEST') {
        let results = [];
        
        // Cari semua wrapper komentar
        const wrappers = document.querySelectorAll('div[class*="DivCommentObjectWrapper"]');
        
        wrappers.forEach((el, index) => {
            try {
                const props = findReactProps(el);
                if (props) {
                    const data = extractCommentData(props);
                    if (data) {
                        results.push({
                            id: data.cid, // ID Unik dari server
                            user: data.user.unique_id,
                            text: data.text,
                            timestamp: data.create_time, // Unix Timestamp!
                            link: data.share_info?.url || window.location.href, // Link Deep!
                            reply_count: data.reply_comment_total || 0,
                            is_reply: false // Ini komentar utama
                        });
                    }
                }
            } catch (e) { }
        });

        // Kirim balik hasil curian ke Content Script
        window.postMessage({ type: 'TT_SCRAPE_RESPONSE', data: results }, '*');
    }
});