/**
 * AI Chat Widget for fasthx-admin
 */
(function () {
    const STORAGE_KEY_EXPANDED = 'ai_chat_expanded';
    const STORAGE_KEY_SIZE = 'ai_chat_size';

    const widget = document.getElementById('ai-chat-widget');
    const toggle = document.getElementById('ai-chat-toggle');
    const panel = document.getElementById('ai-chat-panel');
    const minimize = document.getElementById('ai-chat-minimize');
    const clearBtn = document.getElementById('ai-chat-clear');
    const form = document.getElementById('ai-chat-form');
    const input = document.getElementById('ai-chat-input');
    const messagesEl = document.getElementById('ai-chat-messages');
    const typingEl = document.getElementById('ai-chat-typing');

    if (!widget) return;

    // --- State ---
    let expanded = localStorage.getItem(STORAGE_KEY_EXPANDED) === 'true';

    // --- Markdown rendering ---
    function renderMarkdown(text) {
        if (typeof marked !== 'undefined') {
            let html = marked.parse(text);
            if (typeof DOMPurify !== 'undefined') {
                html = DOMPurify.sanitize(html);
            }
            return html;
        }
        // Fallback: basic escaping + newlines
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');
    }

    // --- UI helpers ---
    function setExpanded(state) {
        expanded = state;
        localStorage.setItem(STORAGE_KEY_EXPANDED, state);
        toggle.style.display = state ? 'none' : 'flex';
        panel.style.display = state ? 'flex' : 'none';
        if (state) {
            restoreSize();
            input.focus();
            scrollToBottom();
        }
    }

    function scrollToBottom() {
        requestAnimationFrame(function () {
            messagesEl.scrollTop = messagesEl.scrollHeight;
        });
    }

    function showTyping() {
        typingEl.style.display = 'block';
        scrollToBottom();
    }

    function hideTyping() {
        typingEl.style.display = 'none';
    }

    function addMessage(role, content) {
        // Remove welcome message if present
        const welcome = messagesEl.querySelector('.ai-chat-welcome');
        if (welcome) welcome.remove();

        const bubble = document.createElement('div');
        bubble.className = 'ai-chat-bubble ' +
            (role === 'user' ? 'ai-chat-bubble-user' : 'ai-chat-bubble-ai');

        if (role === 'user') {
            bubble.textContent = content;
        } else {
            bubble.innerHTML = renderMarkdown(content);
        }

        messagesEl.appendChild(bubble);
        scrollToBottom();
    }

    function addToolCall(name, result) {
        const el = document.createElement('div');
        el.className = 'ai-chat-tool-call';
        el.innerHTML = '<i class="bi bi-tools"></i> <strong>' +
            escapeHtml(name) + '</strong>: ' + escapeHtml(truncate(result, 120));
        messagesEl.appendChild(el);
        scrollToBottom();
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function truncate(text, max) {
        return text.length > max ? text.substring(0, max) + '...' : text;
    }

    // --- Size persistence ---
    function restoreSize() {
        const saved = localStorage.getItem(STORAGE_KEY_SIZE);
        if (saved) {
            try {
                const size = JSON.parse(saved);
                panel.style.width = size.width + 'px';
                panel.style.height = size.height + 'px';
            } catch (e) { /* ignore */ }
        }
    }

    // Save size on resize
    const resizeObserver = new ResizeObserver(function (entries) {
        for (const entry of entries) {
            if (entry.target === panel && panel.style.display !== 'none') {
                const rect = entry.contentRect;
                localStorage.setItem(STORAGE_KEY_SIZE, JSON.stringify({
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                }));
            }
        }
    });
    resizeObserver.observe(panel);

    // --- Events ---
    toggle.addEventListener('click', function () {
        setExpanded(true);
    });

    minimize.addEventListener('click', function () {
        setExpanded(false);
    });

    clearBtn.addEventListener('click', async function () {
        try {
            await fetch('/ai/clear', { method: 'POST' });
        } catch (e) { /* ignore */ }
        messagesEl.innerHTML =
            '<div class="ai-chat-welcome text-center text-muted p-3">' +
            '<i class="bi bi-robot" style="font-size: 2rem;"></i>' +
            '<p class="mt-2 mb-0 small">Ask me anything about the admin data.</p>' +
            '</div>';
    });

    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        const message = input.value.trim();
        if (!message) return;

        input.value = '';
        addMessage('user', message);
        showTyping();
        input.disabled = true;

        try {
            const resp = await fetch('/ai/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: message })
            });

            hideTyping();

            if (!resp.ok) {
                const err = await resp.json().catch(function () { return {}; });
                addMessage('ai', 'Error: ' + (err.error || resp.statusText));
                return;
            }

            const data = await resp.json();

            // Show tool calls if any
            if (data.tool_calls && data.tool_calls.length > 0) {
                data.tool_calls.forEach(function (tc) {
                    addToolCall(tc.name, tc.result || '');
                });
            }

            addMessage('ai', data.response);
        } catch (err) {
            hideTyping();
            addMessage('ai', 'Error: Could not reach the AI service.');
        } finally {
            input.disabled = false;
            input.focus();
        }
    });

    // --- Load history on init ---
    async function loadHistory() {
        try {
            const resp = await fetch('/ai/history');
            if (!resp.ok) return;
            const data = await resp.json();
            if (data.messages && data.messages.length > 0) {
                const welcome = messagesEl.querySelector('.ai-chat-welcome');
                if (welcome) welcome.remove();
                data.messages.forEach(function (msg) {
                    addMessage(msg.role === 'user' ? 'user' : 'ai', msg.content);
                });
            }
        } catch (e) { /* ignore */ }
    }

    // --- Init ---
    setExpanded(expanded);
    loadHistory();
})();
