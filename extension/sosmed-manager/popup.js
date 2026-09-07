document.addEventListener("DOMContentLoaded", () => {
    chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
        chrome.tabs.sendMessage(tabs[0].id, { action: "getComments" }, comments => {
            
            let list = comments || [];

            function render() {
                const result = document.getElementById("result");
                result.innerHTML = "";
                list.forEach(c => {
                    const li = document.createElement("li");
                    li.textContent = `[${c.username}] ${c.text}`;
                    result.appendChild(li);
                });
            }

            document.getElementById("sortNewest").onclick = () => {
                list.sort((a, b) => new Date(b.time) - new Date(a.time));
                render();
            };

            document.getElementById("sortOldest").onclick = () => {
                list.sort((a, b) => new Date(a.time) - new Date(b.time));
                render();
            };

            document.getElementById("search").oninput = (e) => {
                const keyword = e.target.value.toLowerCase();
                list = comments.filter(c =>
                    c.text.toLowerCase().includes(keyword) ||
                    c.username.toLowerCase().includes(keyword)
                );
                render();
            };

            render();
        });
    });
});
