// fasthx-admin - Minimal JS (HTMX handles most interactions)

// Theme switcher
function toggleTheme() {
    var html = document.documentElement;
    var current = html.getAttribute('data-bs-theme');
    var next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
    if (typeof restyleAllTomSelects === 'function') restyleAllTomSelects();
}

// Show global loading indicator for HTMX requests
document.addEventListener('htmx:beforeRequest', function (event) {
    var indicator = document.getElementById('global-indicator');
    if (indicator) indicator.style.display = 'inline-block';
});

document.addEventListener('htmx:afterRequest', function (event) {
    var indicator = document.getElementById('global-indicator');
    if (indicator) indicator.style.display = 'none';
});

// Toast notifications — triggered via HX-Trigger: {"showToast": {"message": "...", "type": "success"}}
function showToast(detail) {
    var data = typeof detail === 'string' ? { message: detail } : detail;
    var message = data.message || '';
    var type = data.type || 'info';
    var title = data.title || type.charAt(0).toUpperCase() + type.slice(1);
    var delay = data.delay || 5000;

    var icons = {
        success: 'check-circle-fill',
        danger: 'exclamation-triangle-fill',
        warning: 'exclamation-triangle-fill',
        info: 'info-circle-fill'
    };
    var icon = icons[type] || 'info-circle-fill';

    var toastEl = document.createElement('div');
    toastEl.className = 'toast';
    toastEl.setAttribute('role', 'alert');
    toastEl.innerHTML =
        '<div class="toast-header">' +
        '<i class="bi bi-' + icon + ' text-' + type + ' me-2"></i>' +
        '<strong class="me-auto">' + title + '</strong>' +
        '<button type="button" class="btn-close" data-bs-dismiss="toast"></button>' +
        '</div>' +
        '<div class="toast-body">' + message + '</div>';

    var container = document.getElementById('toast-container');
    if (container) {
        container.appendChild(toastEl);
        var toast = new bootstrap.Toast(toastEl, { delay: delay });
        toast.show();
        toastEl.addEventListener('hidden.bs.toast', function () {
            toastEl.remove();
        });
    }
}

// Modal — triggered via HX-Trigger: {"showModal": {}}
function showModal(detail) {
    var modalEl = document.getElementById('admin-modal');
    if (!modalEl) return;
    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    var dialog = modalEl.querySelector('.modal-dialog');
    if (dialog) {
        dialog.classList.remove('modal-lg', 'modal-xl', 'modal-sm');
        if (detail && detail.size) {
            dialog.classList.add(detail.size);
        }
    }
    modal.show();
}

// ---------------------------------------------------------------------------
// Rich confirm dialogs for row actions
//
// Reads data-confirm-* attributes emitted by partials/row_actions.html and
// shows a Bootstrap modal instead of the native confirm() popup. Buttons flow
// through HTMX's htmx:confirm event; links are intercepted on click. Both fall
// back to native confirm() (via hx-confirm / this handler) if the modal element
// is missing, so behavior degrades gracefully without JS.
// ---------------------------------------------------------------------------

// Extract a normalized confirm config from an element's data-confirm-* attrs.
// Returns null when no confirm is configured (caller should not intercept).
function readConfirmConfig(el) {
    if (!el || !el.getAttribute) return null;
    var title = el.getAttribute('data-confirm-title');
    var prompt = el.getAttribute('data-confirm-prompt');
    var simple = el.getAttribute('data-confirm');
    var linesRaw = el.getAttribute('data-confirm-lines');
    var lines = [];
    if (linesRaw) {
        try {
            var parsed = JSON.parse(linesRaw);
            lines = Array.isArray(parsed) ? parsed : [String(parsed)];
        } catch (e) {
            lines = [linesRaw];
        }
    }
    // Nothing configured on this element — leave it to default handling.
    if (title == null && prompt == null && simple == null && !lines.length) return null;
    return {
        title: title || 'Confirm',
        // Fall back to the simple one-liner when no explicit lines were given.
        lines: lines.length ? lines : (simple ? [simple] : []),
        prompt: prompt || '',
        danger: el.hasAttribute('data-confirm-danger'),
        okLabel: el.getAttribute('data-confirm-ok') || 'Confirm',
        cancelLabel: el.getAttribute('data-confirm-cancel') || 'Cancel'
    };
}

// Show the confirm modal for cfg, invoking onConfirm() when the user accepts.
// Falls back to a native confirm() if the modal element isn't in the DOM.
function showConfirmDialog(cfg, onConfirm) {
    var modalEl = document.getElementById('confirm-modal');
    if (!modalEl || typeof bootstrap === 'undefined') {
        var msg = [cfg.title]
            .concat(cfg.lines, cfg.prompt ? [cfg.prompt] : [])
            .filter(Boolean)
            .join('\n');
        if (confirm(msg)) onConfirm();
        return;
    }

    modalEl.querySelector('.confirm-modal-heading').textContent = cfg.title;

    var icon = modalEl.querySelector('.confirm-modal-icon');
    if (icon) {
        icon.className = 'bi confirm-modal-icon me-2 ' + (cfg.danger
            ? 'bi-exclamation-triangle-fill text-danger'
            : 'bi-question-circle-fill text-primary');
    }

    var body = modalEl.querySelector('.confirm-modal-body');
    body.innerHTML = '';
    cfg.lines.forEach(function (line) {
        var p = document.createElement('p');
        p.className = 'mb-2';
        p.textContent = line;
        body.appendChild(p);
    });
    if (cfg.prompt) {
        var pr = document.createElement('p');
        pr.className = 'fw-semibold mb-0 mt-3';
        pr.textContent = cfg.prompt;
        body.appendChild(pr);
    }

    var cancelBtn = modalEl.querySelector('.confirm-modal-cancel');
    if (cancelBtn) cancelBtn.textContent = cfg.cancelLabel;

    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);

    // Replace the OK button to drop any listener bound by a previous open.
    var okBtn = modalEl.querySelector('.confirm-modal-ok');
    var freshOk = okBtn.cloneNode(true);
    freshOk.className = 'btn confirm-modal-ok ' + (cfg.danger ? 'btn-danger' : 'btn-primary');
    freshOk.textContent = cfg.okLabel;
    okBtn.parentNode.replaceChild(freshOk, okBtn);
    freshOk.addEventListener('click', function () {
        modal.hide();
        onConfirm();
    });

    modal.show();
}

// Buttons: HTMX fires htmx:confirm before every request. Intercept when the
// element carries our data-confirm-* config; otherwise let HTMX proceed (which
// still honors a plain hx-confirm for e.g. the Delete action).
document.addEventListener('htmx:confirm', function (evt) {
    var cfg = readConfirmConfig(evt.detail.elt);
    if (!cfg) return;
    evt.preventDefault();
    showConfirmDialog(cfg, function () { evt.detail.issueRequest(true); });
});

// Links: plain <a> navigations don't go through HTMX, so intercept the click.
document.addEventListener('click', function (evt) {
    if (!evt.target.closest) return;
    var el = evt.target.closest('a[data-confirm], a[data-confirm-title], a[data-confirm-lines], a[data-confirm-prompt]');
    if (!el) return;
    var cfg = readConfirmConfig(el);
    if (!cfg) return;
    evt.preventDefault();
    showConfirmDialog(cfg, function () {
        // Re-issue the navigation/download without re-triggering the confirm.
        var a = document.createElement('a');
        a.href = el.href;
        if (el.hasAttribute('download')) a.download = el.getAttribute('download') || '';
        if (el.getAttribute('target')) a.target = el.getAttribute('target');
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    });
});

// Dedup guard — prevent double-firing from both native event and afterSettle fallback.
// Both paths must build the key with _toastKey; differing key formats defeat the guard.
var _toastHandled = null;

// htmx 2.x adds a circular `elt` (the triggering element) to event.detail, so the key
// must be built from just the toast fields — JSON.stringify would throw on the cycle.
function _toastKey(d) {
    return d ? (d.message || '') + '|' + (d.type || '') : '';
}

// HTMX natively dispatches events from HX-Trigger headers on the body.
// This is the primary listener — works even when the target element is removed from the DOM.
document.body.addEventListener('showToast', function (event) {
    var d = (event.detail && event.detail.value) ? event.detail.value : event.detail;
    var key = _toastKey(d);
    if (_toastHandled === key) return;
    _toastHandled = key;
    setTimeout(function () { _toastHandled = null; }, 200);
    showToast(d);
});
document.body.addEventListener('showModal', function (event) {
    var d = (event.detail && event.detail.value) ? event.detail.value : event.detail;
    showModal(d);
});
document.body.addEventListener('showConsole', function (event) {
    var d = (event.detail && event.detail.value) ? event.detail.value : event.detail;
    showConsole(d);
});

// Fallback: manually parse HX-Trigger header after swap settles.
// Catches edge cases where the native event might not fire as expected.
document.addEventListener('htmx:afterSettle', function (event) {
    var xhr = event.detail.xhr;
    if (!xhr) return;
    var trigger = xhr.getResponseHeader('HX-Trigger');
    if (!trigger) return;
    try {
        var data = JSON.parse(trigger);
        if (data.showToast) {
            var key = _toastKey(data.showToast);
            if (_toastHandled === key) return;
            _toastHandled = key;
            setTimeout(function () { _toastHandled = null; }, 200);
            showToast(data.showToast);
        }
        if (data.showModal) {
            showModal(data.showModal);
        }
        if (data.showConsole) {
            showConsole(data.showConsole);
        }
    } catch (e) {}
});

// Auto-dismiss alerts after 5 seconds
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.alert-dismissible').forEach(function (alert) {
        setTimeout(function () {
            var bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            bsAlert.close();
        }, 5000);
    });

    // Show any toast passed via cookie (set by server before HX-Redirect)
    var match = document.cookie.match(/(^|;\s*)_toast=([^;]*)/);
    if (match) {
        document.cookie = '_toast=; max-age=0; path=/; samesite=lax';
        try {
            showToast(JSON.parse(decodeURIComponent(match[2])));
        } catch (e) {}
    }
});

// Tom Select - searchable dropdowns for all select.form-select elements
function getTomSelectOptions(el) {
    // Find placeholder text from the empty option
    var emptyOption = el.querySelector('option[value=""]');
    var placeholder = emptyOption ? emptyOption.textContent.trim() : 'Select...';
    // Remove the empty option so it doesn't show as a selectable item
    if (emptyOption) emptyOption.remove();
    // Preserve any pre-selected value (edit forms), otherwise start empty for placeholder
    var selectedOption = el.querySelector('option[selected]');
    var items = selectedOption && selectedOption.value ? [selectedOption.value] : [];
    return {
        create: false,
        sortField: { field: 'text', direction: 'asc' },
        placeholder: placeholder,
        allowEmptyOption: false,
        items: items
    };
}

function getTomSelectColors() {
    var isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
    return {
        bg: isDark ? '#1f1f1f' : '#f3f4f6',
        border: isDark ? '#404040' : '#d1d5db',
        color: isDark ? '#ffffff' : '#1f2937'
    };
}

function styleTomSelect(tsInstance) {
    var control = tsInstance.control;
    if (!control) return;
    var c = getTomSelectColors();
    control.style.setProperty('background', c.bg, 'important');
    control.style.setProperty('border', '1px solid ' + c.border, 'important');
    control.style.setProperty('border-radius', '0.375rem');
    control.style.setProperty('color', c.color, 'important');
}

function restyleAllTomSelects() {
    document.querySelectorAll('select.form-select').forEach(function (el) {
        if (el.tomselect) styleTomSelect(el.tomselect);
    });
}

function getAjaxTomSelectOptions(el) {
    var ajaxUrl = el.getAttribute('data-ajax-url');
    var placeholder = el.getAttribute('data-placeholder') || 'Type to search...';
    return {
        plugins: ['virtual_scroll'],
        create: false,
        placeholder: placeholder,
        allowEmptyOption: false,
        // Load the (empty-query) first page once on initial focus. Using the
        // built-in preload guard fixes edit forms where the preselected value
        // already counts as an option and would otherwise suppress the load.
        preload: 'focus',
        // Default maxOptions (50) makes virtual_scroll's canLoadMore bail after
        // 5 pages and truncates the list, so large datasets can never be fully
        // scrolled. null removes the cap so paging continues on scroll.
        maxOptions: null,
        valueField: 'value',
        labelField: 'label',
        searchField: 'label',
        firstUrl: function (query) {
            return ajaxUrl + '?q=' + encodeURIComponent(query) + '&page=1';
        },
        shouldLoad: function () { return true; },
        load: function (query, callback) {
            var self = this;
            var url = self.getUrl(query);
            fetch(url)
                .then(function (resp) { return resp.json(); })
                .then(function (data) {
                    // Backwards-compat: handler may return a bare array (no pagination).
                    if (Array.isArray(data)) {
                        callback(data);
                        return;
                    }
                    if (data && data.next) {
                        self.setNextUrl(query, data.next);
                    }
                    callback((data && data.items) || []);
                })
                .catch(function () { callback(); });
        }
    };
}

// Clean up stale Tom Select instances and orphaned wrappers.
// Called before re-initialization after full-page HTMX swaps (hx-boost).
function cleanupTomSelects(root) {
    if (typeof TomSelect === 'undefined') return;
    var container = root || document;
    container.querySelectorAll('select.form-select').forEach(function (el) {
        // Destroy stale instance if the property survived the swap
        if (el.tomselect) {
            try { el.tomselect.destroy(); } catch (e) {}
        }
        // Remove orphaned wrapper siblings left behind by the swap
        var next = el.nextElementSibling;
        if (next && next.classList.contains('ts-wrapper')) {
            next.remove();
        }
        // Remove Tom Select classes from the raw select so it starts clean
        el.classList.remove('tomselected', 'ts-hidden-accessible');
    });
}

function initTomSelect(root) {
    if (typeof TomSelect === 'undefined') return;
    var container = root || document;
    container.querySelectorAll('select.form-select').forEach(function (el) {
        if (el.tomselect) return; // already initialized
        if (el.classList.contains('no-tomselect')) return; // opt-out
        var opts;
        if (el.hasAttribute('data-ajax-url')) {
            opts = getAjaxTomSelectOptions(el);
        } else {
            opts = getTomSelectOptions(el);
        }
        var ts = new TomSelect(el, opts);
        styleTomSelect(ts);
        // Initial ajax load is handled by preload:'focus' in getAjaxTomSelectOptions.
    });
}

// Sync Tom Select when HTMX swaps options into an existing select
function syncTomSelect(target) {
    if (typeof TomSelect === 'undefined') return;
    var selects = target.matches && target.matches('select.form-select')
        ? [target]
        : [];
    selects.forEach(function (el) {
        if (el.tomselect) {
            // Save the new options HTMX just swapped in before destroying,
            // because destroy() reverts innerHTML to the original state.
            var newHTML = el.innerHTML;
            el.tomselect.destroy();
            el.innerHTML = newHTML;
            var ts = new TomSelect(el, getTomSelectOptions(el));
            styleTomSelect(ts);
        }
    });
}

// Conditional field visibility — show/hide fields based on a checkbox value
function initDependsOn(root) {
    var container = root || document;
    // Find all fields that depend on another field
    var dependents = container.querySelectorAll('[data-depends-on]');
    var controllers = {};
    dependents.forEach(function (el) {
        var key = el.getAttribute('data-depends-on');
        if (!controllers[key]) controllers[key] = [];
        controllers[key].push(el);
    });

    Object.keys(controllers).forEach(function (key) {
        var ctrl = document.getElementById(key);
        if (!ctrl) return;
        var toggle = function () {
            var checked = ctrl.checked;
            controllers[key].forEach(function (el) {
                if (checked) {
                    el.style.overflow = 'hidden';
                    el.style.maxHeight = '0';
                    el.style.opacity = '0';
                    el.style.removeProperty('margin');
                    el.style.removeProperty('padding');
                    // Force reflow so the transition triggers from 0
                    el.offsetHeight;
                    el.style.transition = 'max-height .3s ease, opacity .3s ease, margin .3s ease';
                    el.style.maxHeight = el.scrollHeight + 'px';
                    el.style.opacity = '1';
                    // Clean up after transition so content isn't clipped
                    var onEnd = function () {
                        el.style.maxHeight = '';
                        el.style.overflow = '';
                        el.style.transition = '';
                        el.removeEventListener('transitionend', onEnd);
                    };
                    el.addEventListener('transitionend', onEnd);
                } else {
                    // Snap max-height to current size, then transition to 0
                    el.style.maxHeight = el.scrollHeight + 'px';
                    el.style.overflow = 'hidden';
                    el.offsetHeight;
                    el.style.transition = 'max-height .3s ease, opacity .3s ease, margin .3s ease';
                    el.style.maxHeight = '0';
                    el.style.opacity = '0';
                    el.style.margin = '0';
                    el.style.padding = '0';
                }
            });
        };
        toggle(); // set initial state
        ctrl.addEventListener('change', toggle);
    });
}

// Auto-fill — a checkbox sets (and locks) other fields' values from a JSON map.
// Config: data-autofill='{"target_field": "value", ...}' on the checkbox input.
// Checked -> remember each target's current value, then fill with the mapped
// value and make it read-only. Unchecked (by the user) -> restore the remembered
// value and re-enable the field. On initial render an unchecked box leaves
// existing values untouched (don't wipe real data on edit).
function initAutofill(root) {
    var container = root || document;
    container.querySelectorAll('input[type="checkbox"][data-autofill]').forEach(function (ctrl) {
        if (ctrl._autofillInit) return; // guard against double-binding on re-init
        ctrl._autofillInit = true;
        var map;
        try {
            map = JSON.parse(ctrl.getAttribute('data-autofill'));
        } catch (e) {
            return;
        }
        var targets = Object.keys(map);
        var saved = {}; // last user-entered value per target, captured on check
        var apply = function (isUserToggle) {
            var checked = ctrl.checked;
            targets.forEach(function (key) {
                var field = document.getElementById(key);
                if (!field) return;
                if (checked) {
                    if (isUserToggle) saved[key] = field.value; // remember before overwriting
                    field.value = map[key];
                    field.readOnly = true;
                    field.classList.add('bg-body-secondary');
                } else {
                    field.readOnly = false;
                    field.classList.remove('bg-body-secondary');
                    if (isUserToggle && saved.hasOwnProperty(key)) {
                        field.value = saved[key]; // restore what was there before
                    }
                }
            });
        };
        apply(false); // initial: lock when checked, but never touch values on load
        ctrl.addEventListener('change', function () { apply(true); });
    });
}

// Mutual exclusion — checking one checkbox unchecks the others in its group.
// Config: data-exclusive-with='["other_field", ...]' on the checkbox input.
// Links are undirected and transitive: declaring it on either side is enough,
// and A<->B plus A<->C puts all three in one group. Only checking clears the
// peers; unchecking leaves everything alone, so "none selected" stays reachable.
// Programmatic unchecks dispatch a 'change' event so depends_on / autofill bound
// to those boxes react exactly as they would to a real click.
// Initial render is never modified — stored data is shown as-is, even if two
// boxes in a group somehow arrive checked.
function initExclusive(root) {
    var container = root || document;
    var boxes = container.querySelectorAll('input[type="checkbox"][data-exclusive-with]');
    if (!boxes.length) return;

    // Build undirected adjacency from every declaration...
    var adj = {};
    var link = function (a, b) {
        (adj[a] = adj[a] || []).push(b);
        (adj[b] = adj[b] || []).push(a);
    };
    boxes.forEach(function (ctrl) {
        var peers;
        try {
            peers = JSON.parse(ctrl.getAttribute('data-exclusive-with'));
        } catch (e) {
            return;
        }
        if (typeof peers === 'string') peers = [peers];
        if (!Array.isArray(peers)) return;
        peers.forEach(function (p) { if (p && p !== ctrl.id) link(ctrl.id, p); });
    });

    // ...then flood-fill: each connected component is one exclusive group.
    var groupOf = {};
    Object.keys(adj).forEach(function (start) {
        if (groupOf[start]) return;
        var group = [];
        var queue = [start];
        groupOf[start] = group;
        while (queue.length) {
            var cur = queue.shift();
            group.push(cur);
            (adj[cur] || []).forEach(function (next) {
                if (!groupOf[next]) {
                    groupOf[next] = group;
                    queue.push(next);
                }
            });
        }
    });

    Object.keys(groupOf).forEach(function (key) {
        var ctrl = document.getElementById(key);
        if (!ctrl || ctrl._exclusiveInit) return; // guard against double-binding
        ctrl._exclusiveInit = true;
        var group = groupOf[key];
        ctrl.addEventListener('change', function () {
            if (!ctrl.checked) return;
            group.forEach(function (peerKey) {
                if (peerKey === key) return;
                var peer = document.getElementById(peerKey);
                if (!peer || !peer.checked) return;
                peer.checked = false;
                // Peer handlers see checked=false and bail, so this can't loop.
                peer.dispatchEvent(new Event('change', { bubbles: true }));
            });
        });
    });
}

// Bootstrap tooltips
function initTooltips(root) {
    var container = root || document;
    container.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
        if (!bootstrap.Tooltip.getInstance(el)) {
            new bootstrap.Tooltip(el);
        }
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function () {
    initTomSelect();
    initDependsOn();
    initAutofill();
    initExclusive();
    initTooltips();
});

// Destroy Tom Select instances before HTMX replaces the DOM (prevents orphaned wrappers)
document.addEventListener('htmx:beforeSwap', function (event) {
    var target = event.detail.target;
    if (target === document.body || target === document.documentElement) {
        document.querySelectorAll('select.form-select').forEach(function (el) {
            if (el.tomselect) {
                try { el.tomselect.destroy(); } catch (e) {}
            }
        });
    }
});

// Re-initialize after HTMX swaps new content in
document.addEventListener('htmx:afterSwap', function (event) {
    syncTomSelect(event.detail.target);
    initTomSelect(event.detail.target);
    initDependsOn(event.detail.target);
    initAutofill(event.detail.target);
    initExclusive(event.detail.target);
    initTooltips(event.detail.target);
    // Auto-open modal when content is swapped into it
    var target = event.detail.target;
    if (target && target.closest && target.closest('#admin-modal')) {
        var modalEl = document.getElementById('admin-modal');
        if (modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }
});

// Re-initialize after boosted full-page swaps settle (e.g. form validation errors)
document.addEventListener('htmx:afterSettle', function (event) {
    var target = event.detail.target;
    if (target === document.body || target === document.documentElement) {
        cleanupTomSelects();
        initTomSelect();
        initDependsOn();
        initAutofill();
        initExclusive();
        initTooltips();
    }
});

// Handle out-of-band swaps (dependent dropdowns with multiple targets)
document.addEventListener('htmx:oobAfterSwap', function (event) {
    syncTomSelect(event.detail.target);
    initTomSelect(event.detail.target);
    initDependsOn(event.detail.target);
    initAutofill(event.detail.target);
    initExclusive(event.detail.target);
    initTooltips(event.detail.target);
});

// ---------------------------------------------------------------------------
// Terminal Console
// ---------------------------------------------------------------------------

var _consoleObserver = null;
var _consoleMaxLines = 5000;

function showConsole(detail) {
    showModal(detail);
    // Set up auto-scroll after the modal content is swapped in
    setTimeout(function () {
        var outputEl = document.querySelector('#console-output');
        if (!outputEl) return;
        // Scroll to bottom initially
        outputEl.scrollTop = outputEl.scrollHeight;
        // Watch for new content and auto-scroll
        if (_consoleObserver) _consoleObserver.disconnect();
        _consoleObserver = new MutationObserver(function () {
            // Trim old lines if over max
            while (outputEl.children.length > _consoleMaxLines) {
                outputEl.removeChild(outputEl.firstChild);
            }
            // Auto-scroll only if user is near the bottom
            var atBottom = outputEl.scrollHeight - outputEl.scrollTop - outputEl.clientHeight < 50;
            if (atBottom) {
                outputEl.scrollTop = outputEl.scrollHeight;
            }
        });
        _consoleObserver.observe(outputEl, { childList: true });
        // Focus input if present
        var input = document.querySelector('.console-input-form input');
        if (input) input.focus();
    }, 100);
}

// Clean up SSE connections and observer when modal closes
(function () {
    var modalEl = document.getElementById('admin-modal');
    if (modalEl) {
        modalEl.addEventListener('hidden.bs.modal', function () {
            if (_consoleObserver) {
                _consoleObserver.disconnect();
                _consoleObserver = null;
            }
            // Disconnect any SSE connections inside the modal
            var sseEl = modalEl.querySelector('[sse-connect]');
            if (sseEl && typeof htmx !== 'undefined') {
                htmx.remove(sseEl);
            }
        });
    }
})();

