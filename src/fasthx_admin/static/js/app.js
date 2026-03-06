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

// Auto-dismiss alerts after 5 seconds
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.alert-dismissible').forEach(function (alert) {
        setTimeout(function () {
            var bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            bsAlert.close();
        }, 5000);
    });
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
        allowEmptyOption: false
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
        load: function (query, callback) {
            var url = ajaxUrl + '?q=' + encodeURIComponent(query);
            fetch(url)
                .then(function (resp) { return resp.json(); })
                .then(function (data) { callback(data); })
                .catch(function () { callback(); });
        }
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
            el.tomselect.destroy();
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
