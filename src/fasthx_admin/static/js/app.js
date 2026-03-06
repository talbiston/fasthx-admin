// fasthx-admin - Minimal JS (HTMX handles most interactions)

// Theme switcher
function toggleTheme() {
    var html = document.documentElement;
    var current = html.getAttribute('data-bs-theme');
    var next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-bs-theme', next);
    localStorage.setItem('theme', next);
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
function initTomSelect(root) {
    if (typeof TomSelect === 'undefined') return;
    var container = root || document;
    container.querySelectorAll('select.form-select').forEach(function (el) {
        if (el.tomselect) return; // already initialized
        new TomSelect(el, {
            create: false,
            sortField: { field: 'text', direction: 'asc' },
            allowEmptyOption: true
        });
    });
}

// Sync Tom Select when HTMX swaps options into an existing select
function syncTomSelect(target) {
    if (typeof TomSelect === 'undefined') return;
    // If HTMX swapped content into a select element, rebuild its Tom Select
    var selects = target.matches && target.matches('select.form-select')
        ? [target]
        : [];
    selects.forEach(function (el) {
        if (el.tomselect) {
            el.tomselect.destroy();
            new TomSelect(el, {
                create: false,
                sortField: { field: 'text', direction: 'asc' },
                allowEmptyOption: true
            });
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
