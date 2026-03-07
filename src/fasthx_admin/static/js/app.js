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

// For non-redirect responses, show the toast after the DOM swap.
var _lastToastXhr = null;
document.addEventListener('htmx:afterSettle', function (event) {
    var xhr = event.detail.xhr;
    if (!xhr || xhr === _lastToastXhr) return;
    var trigger = xhr.getResponseHeader('HX-Trigger');
    if (!trigger) return;
    try {
        var data = JSON.parse(trigger);
        if (data.showToast) {
            _lastToastXhr = xhr;
            setTimeout(function () { showToast(data.showToast); }, 50);
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
    return {
        create: false,
        sortField: { field: 'text', direction: 'asc' },
        placeholder: placeholder,
        allowEmptyOption: false,
        items: []  // Start with nothing selected so placeholder shows
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
        create: false,
        placeholder: placeholder,
        allowEmptyOption: false,
        valueField: 'value',
        labelField: 'label',
        searchField: 'label',
        firstUrl: function (query) {
            return ajaxUrl + '?q=' + encodeURIComponent(query);
        },
        shouldLoad: function () { return true; },
        load: function (query, callback) {
            var url = ajaxUrl + '?q=' + encodeURIComponent(query);
            fetch(url)
                .then(function (resp) { return resp.json(); })
                .then(function (data) { callback(data); })
                .catch(function () { callback(); });
        },
        score: function () { return function () { return 1; }; }
    };
}

function initTomSelect(root) {
    if (typeof TomSelect === 'undefined') return;
    var container = root || document;
    container.querySelectorAll('select.form-select').forEach(function (el) {
        if (el.tomselect) return; // already initialized
        var opts;
        if (el.hasAttribute('data-ajax-url')) {
            opts = getAjaxTomSelectOptions(el);
        } else {
            opts = getTomSelectOptions(el);
        }
        var ts = new TomSelect(el, opts);
        styleTomSelect(ts);
        if (el.hasAttribute('data-ajax-url')) {
            ts.on('focus', function () {
                if (!Object.keys(ts.options).length) {
                    ts.load('');
                }
            });
        }
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

// Initialize on page load
document.addEventListener('DOMContentLoaded', function () {
    initTomSelect();
});

// Re-initialize after HTMX swaps new content in
document.addEventListener('htmx:afterSwap', function (event) {
    syncTomSelect(event.detail.target);
    initTomSelect(event.detail.target);
});

