// interceptor.js - V19 TikTok main comment + reply endpoint sniffer
(function() {
    function parseUrl(rawUrl) {
        try {
            return new URL(rawUrl, location.origin);
        } catch (e) {
            return null;
        }
    }

    function classifyCommentEndpoint(rawUrl) {
        const u = parseUrl(rawUrl);
        if (!u) return null;

        const path = u.pathname;
        const isReply = path.includes('/comment/list/reply/') || path.includes('/api/comment/list/reply/');
        const isMain = !isReply && (path.includes('/comment/list/') || path.includes('/api/comment/list/'));

        if (!isReply && !isMain) return null;

        return {
            kind: isReply ? 'reply' : 'main',
            parentCid:
                u.searchParams.get('comment_id') ||
                u.searchParams.get('root_comment_id') ||
                u.searchParams.get('reply_to_comment_id') ||
                ''
        };
    }

    function postComments(url, responseTextOrJson) {
        const meta = classifyCommentEndpoint(url);
        if (!meta) return;

        try {
            const response = typeof responseTextOrJson === 'string' ? JSON.parse(responseTextOrJson) : responseTextOrJson;
            const comments = response?.comments || response?.reply_comments || [];
            if (!Array.isArray(comments) || comments.length === 0) return;

            window.postMessage({
                type: 'TT_NET_INTERCEPT',
                payload: {
                    kind: meta.kind,
                    parentCid: meta.parentCid,
                    comments
                }
            }, '*');
        } catch (e) {}
    }

    const XHR = XMLHttpRequest.prototype;
    const open = XHR.open;
    const send = XHR.send;

    XHR.open = function(method, url) {
        this._url = url;
        return open.apply(this, arguments);
    };

    XHR.send = function(postData) {
        this.addEventListener('load', function() {
            if (this._url) postComments(this._url, this.responseText);
        });
        return send.apply(this, arguments);
    };

    const originalFetch = window.fetch;
    window.fetch = async function(...args) {
        const response = await originalFetch(...args);
        const clone = response.clone();
        const url = response.url || (typeof args[0] === 'string' ? args[0] : args[0]?.url);

        if (classifyCommentEndpoint(url)) {
            clone.json().then(data => postComments(url, data)).catch(e => {});
        }

        return response;
    };
})();
