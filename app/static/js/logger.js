let currentCategory = 'all';
let autoRefreshInterval = null;

const LEVEL_ICONS = {
    info: '\u2139\uFE0F',
    warn: '\u26A0\uFE0F',
    error: '\u274C',
    success: '\u2705',
};

const CATEGORY_COLORS = {
    system: 'var(--load)',
    algorithm: 'var(--solar)',
    tesla: 'var(--ev)',
    mqtt: 'var(--grid)',
    schedule: 'var(--battery)',
    manual: 'var(--text-secondary)',
};

async function loadEvents() {
    const params = new URLSearchParams({ limit: '300' });
    if (currentCategory !== 'all') {
        params.set('category', currentCategory);
    }

    try {
        const resp = await fetch(`/api/events?${params}`);
        const events = await resp.json();
        renderEvents(events);
    } catch (e) {
        document.getElementById('log-container').innerHTML =
            '<div class="log-empty">Failed to load events</div>';
    }
}

function renderEvents(events) {
    const container = document.getElementById('log-container');

    if (!events.length) {
        container.innerHTML = '<div class="log-empty">No events recorded yet</div>';
        return;
    }

    container.innerHTML = events.map(e => {
        const icon = LEVEL_ICONS[e.level] || '';
        const catColor = CATEGORY_COLORS[e.category] || 'var(--text-muted)';
        const levelClass = `log-level-${e.level}`;
        const time = e.timestamp.split('T')[1] || e.timestamp;
        const date = e.timestamp.split('T')[0] || '';

        return `<div class="log-row ${levelClass}">
            <span class="log-time" title="${date}">${time}</span>
            <span class="log-cat" style="color:${catColor}">${e.category}</span>
            <span class="log-icon">${icon}</span>
            <span class="log-msg">${escapeHtml(e.message)}</span>
        </div>`;
    }).join('');
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

async function clearEvents() {
    if (!confirm('Clear all events?')) return;
    await fetch('/api/events', { method: 'DELETE' });
    loadEvents();
}

// Filter buttons
document.querySelectorAll('.log-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.log-filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentCategory = btn.dataset.cat;
        loadEvents();
    });
});

// Auto-refresh toggle
document.getElementById('auto-refresh').addEventListener('change', (e) => {
    if (e.target.checked) {
        startAutoRefresh();
    } else {
        stopAutoRefresh();
    }
});

function startAutoRefresh() {
    stopAutoRefresh();
    autoRefreshInterval = setInterval(loadEvents, 5000);
}

function stopAutoRefresh() {
    if (autoRefreshInterval) {
        clearInterval(autoRefreshInterval);
        autoRefreshInterval = null;
    }
}

// Init
loadEvents();
startAutoRefresh();
