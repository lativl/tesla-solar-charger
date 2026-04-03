// Load current settings
async function loadSettings() {
    const resp = await fetch('/api/settings');
    const data = await resp.json();
    const form = document.getElementById('settings-form');

    for (const [key, value] of Object.entries(data)) {
        const input = form.elements[key];
        if (!input) continue;
        if (input.type === 'range') {
            input.value = value;
            input.nextElementSibling.textContent = value + '%';
        } else if (input.tagName === 'SELECT') {
            input.value = String(value);
        } else {
            input.value = value;
        }
    }

    // Populate BLE config forms
    const bleForm = document.getElementById('ble-settings-form');
    const bleEntityForm = document.getElementById('ble-entity-form');
    for (const [key, value] of Object.entries(data)) {
        let input = bleForm.elements[key] || bleEntityForm.elements[key];
        if (input) input.value = value;
    }
}

// Load and apply tooltips from API
async function loadTooltips() {
    try {
        const resp = await fetch('/api/settings/tooltips');
        const tooltips = await resp.json();
        const form = document.getElementById('settings-form');
        for (const [key, text] of Object.entries(tooltips)) {
            const input = form.elements[key];
            if (!input) continue;
            input.title = text;
            const label = input.closest('.form-group')?.querySelector('label');
            if (label) label.title = text;
        }
    } catch (e) {
        // tooltips are non-critical
    }
}

// Save charger settings
document.getElementById('settings-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = {};
    for (const el of form.elements) {
        if (el.name && el.value !== undefined) {
            data[el.name] = el.type === 'number' || el.type === 'range'
                ? Number(el.value) : el.value;
        }
    }
    const resp = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    const status = document.getElementById('save-status');
    if (resp.ok) {
        status.textContent = 'Saved!';
        setTimeout(() => status.textContent = '', 3000);
    } else {
        status.textContent = 'Error saving';
        status.style.color = '#ef4444';
    }
});

// Tesla status
async function loadTeslaStatus() {
    try {
        const resp = await fetch('/api/tesla/status');
        const data = await resp.json();
        const channel = data.transport?.active_channel;

        // If BLE is the active channel, Fleet API auth is irrelevant here
        if (channel === 'ble') {
            document.getElementById('tesla-connected').textContent = 'Using BLE — Fleet API not in use';
            document.getElementById('tesla-connected').style.color = 'var(--text-muted)';
            document.getElementById('tesla-auth-section').style.display = 'none';
            document.getElementById('tesla-vehicle').style.display = 'none';
            return;
        }

        if (data.connected) {
            document.getElementById('tesla-connected').textContent = data.key_revoked ? 'Connected (key revoked)' : 'Connected';
            document.getElementById('tesla-connected').style.color = data.key_revoked ? '#f59e0b' : '#22c55e';
            document.getElementById('tesla-auth-section').style.display = 'none';
            const v = data.vehicle;
            if (v && (v.battery_level > 0 || v.name)) {
                document.getElementById('tesla-vehicle').style.display = 'grid';
                document.getElementById('tesla-name').textContent = v.name || v.vin || '—';
                document.getElementById('tesla-soc').textContent = v.battery_level > 0 ? `${v.battery_level}%` : 'Asleep';
                document.getElementById('tesla-state').textContent = v.charge_state;
            }
        } else {
            document.getElementById('tesla-connected').textContent = 'Not connected — authorize below';
            document.getElementById('tesla-connected').style.color = '#ef4444';
            document.getElementById('tesla-auth-section').style.display = 'block';
            document.getElementById('tesla-vehicle').style.display = 'none';
        }
    } catch (e) {
        document.getElementById('tesla-connected').textContent = 'Error checking status';
    }
}

// Tesla auth relay flow
async function startTeslaAuth() {
    const resp = await fetch('/api/tesla/auth');
    const data = await resp.json();
    window.open(data.url, '_blank');
}

async function exchangeTeslaCode() {
    const codeInput = document.getElementById('tesla-auth-code');
    const status = document.getElementById('tesla-exchange-status');
    const code = codeInput.value.trim();

    if (!code) {
        status.textContent = 'Please paste the authorization code';
        status.style.color = '#ef4444';
        return;
    }

    status.textContent = 'Connecting...';
    status.style.color = 'var(--text-muted)';

    try {
        const resp = await fetch('/api/tesla/exchange', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
        const data = await resp.json();
        if (data.ok) {
            status.textContent = 'Tesla connected successfully!';
            status.style.color = '#22c55e';
            codeInput.value = '';
            setTimeout(loadTeslaStatus, 1000);
        } else {
            status.textContent = `Failed: ${data.error}`;
            status.style.color = '#ef4444';
        }
    } catch (e) {
        status.textContent = `Error: ${e.message}`;
        status.style.color = '#ef4444';
    }
}

// Tesla commands
async function teslaCmd(cmd) {
    const body = { command: cmd };
    if (cmd === 'set_amps') {
        body.amps = Number(document.getElementById('manual-amps').value);
    }
    const resp = await fetch('/api/tesla/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const result = await resp.json();
    alert(result.ok ? `Command '${cmd}' succeeded` : `Command failed: ${result.error || JSON.stringify(result)}`);
    setTimeout(loadTeslaStatus, 2000);
}

// Channel management
async function loadChannelStatus() {
    try {
        const resp = await fetch('/api/tesla/channel');
        const data = await resp.json();

        document.getElementById('channel-select').value = data.active_channel;
        document.getElementById('active-channel-badge').textContent =
            data.active_channel === 'ble' ? 'ESPHome BLE' : 'Fleet API';
        document.getElementById('active-channel-badge').style.color =
            data.active_channel === 'ble' ? '#22c55e' : 'var(--text)';

        const fleet = data.fleet_api;
        let fleetText, fleetColor;
        if (fleet?.key_revoked) {
            fleetText = 'Key revoked';
            fleetColor = '#ef4444';
        } else if (!fleet?.has_token) {
            fleetText = 'No token — authorize in Tesla Connection';
            fleetColor = '#f59e0b';
        } else {
            fleetText = 'Token valid';
            fleetColor = '#22c55e';
        }
        document.getElementById('fleet-api-badge').textContent = fleetText;
        document.getElementById('fleet-api-badge').style.color = fleetColor;

        const keyRevokedWarning = document.getElementById('key-revoked-warning');
        if (keyRevokedWarning) keyRevokedWarning.style.display = fleet?.key_revoked ? 'block' : 'none';

        const bleAvail = data.ble?.available;
        const bleReach = data.ble?.reachable;
        document.getElementById('ble-badge').textContent =
            bleAvail ? (bleReach ? 'Connected' : 'Configured') : 'Not configured';
        document.getElementById('ble-badge').style.color =
            bleAvail ? (bleReach ? '#22c55e' : '#f59e0b') : 'var(--text-muted)';
    } catch (e) {
        // non-critical
    }
}

async function switchChannel(channel) {
    const resp = await fetch('/api/tesla/channel', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel }),
    });
    const data = await resp.json();
    if (data.error) {
        alert(data.error);
        loadChannelStatus(); // revert selector
    } else {
        loadChannelStatus();
    }
}

async function saveBleSettings() {
    const bleForm = document.getElementById('ble-settings-form');
    const bleEntityForm = document.getElementById('ble-entity-form');
    const data = {};
    for (const form of [bleForm, bleEntityForm]) {
        for (const el of form.elements) {
            if (el.name) data[el.name] = el.value;
        }
    }
    const resp = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    const result = await resp.json();
    const statusEl = document.getElementById('ble-test-result');
    if (result.status === 'ok') {
        statusEl.textContent = 'BLE config saved';
        statusEl.style.color = '#22c55e';
        setTimeout(() => { statusEl.textContent = ''; }, 3000);
        loadChannelStatus();
    } else {
        statusEl.textContent = 'Save failed';
        statusEl.style.color = '#ef4444';
    }
}

async function testBleConnection() {
    const statusEl = document.getElementById('ble-test-result');
    statusEl.textContent = 'Testing...';
    statusEl.style.color = 'var(--text-muted)';
    // Save first, then the manager will reinitialize BLE and we can check status
    await saveBleSettings();
    await new Promise(r => setTimeout(r, 500));
    const resp = await fetch('/api/tesla/channel');
    const data = await resp.json();
    if (data.ble?.available) {
        statusEl.textContent = data.ble.reachable ? 'BLE device reachable!' : 'BLE configured — reachability updated after next poll';
        statusEl.style.color = '#22c55e';
    } else {
        statusEl.textContent = 'BLE not configured — enter host IP above';
        statusEl.style.color = '#ef4444';
    }
}

// Strategy management
async function loadStrategies() {
    const resp = await fetch('/api/strategies');
    const strategies = await resp.json();
    const el = document.getElementById('strategies-list');
    if (!strategies.length) {
        el.innerHTML = '<p style="color:var(--text-muted)">No strategies found</p>';
        return;
    }
    el.innerHTML = strategies.map(s => `
        <div class="schedule-item" style="${s.is_active ? 'border-left:3px solid #22c55e' : ''}">
            <div class="schedule-info">
                <span class="name">${s.name}</span>
                ${s.is_active ? '<span style="font-size:0.75rem; color:#22c55e; margin-left:0.5rem; font-weight:600">ACTIVE</span>' : ''}
                <span class="time">${Object.keys(s.settings).length} settings</span>
            </div>
            <div>
                ${!s.is_active ? `<button onclick="activateStrategy(${s.id})" class="btn btn-solar" style="font-size:0.75rem">Activate</button>` : ''}
                <button onclick="duplicateStrategy(${s.id})" class="btn" style="font-size:0.75rem">Duplicate</button>
                ${!s.is_active ? `<button onclick="deleteStrategy(${s.id})" class="btn btn-pause" style="font-size:0.75rem">Delete</button>` : ''}
            </div>
        </div>
    `).join('');
}

async function activateStrategy(id) {
    const resp = await fetch(`/api/strategies/${id}/activate`, { method: 'PUT' });
    const data = await resp.json();
    if (data.error) {
        alert(data.error);
    } else {
        await loadStrategies();
        await loadSettings();
        const msg = data.applied > 0 ? `Strategy activated (${data.applied} settings updated)` : 'Strategy activated (no changes)';
        const statusEl = document.getElementById('save-status');
        if (statusEl) {
            statusEl.textContent = msg;
            statusEl.style.color = '#22c55e';
            setTimeout(() => { statusEl.textContent = ''; statusEl.style.color = ''; }, 4000);
        }
    }
}

async function duplicateStrategy(id) {
    await fetch(`/api/strategies/${id}/duplicate`, { method: 'POST' });
    loadStrategies();
}

async function deleteStrategy(id) {
    if (!confirm('Delete this strategy?')) return;
    const resp = await fetch(`/api/strategies/${id}`, { method: 'DELETE' });
    const data = await resp.json();
    if (data.error) {
        alert(data.error);
    } else {
        loadStrategies();
    }
}

async function createStrategyFromCurrent() {
    const name = prompt('Strategy name:');
    if (!name) return;

    const form = document.getElementById('settings-form');
    const settings = {};
    for (const el of form.elements) {
        if (el.name && el.value !== undefined && el.type !== 'submit') {
            settings[el.name] = el.type === 'number' || el.type === 'range'
                ? Number(el.value) : el.value;
        }
    }

    const resp = await fetch('/api/strategies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, settings }),
    });
    if (resp.ok) loadStrategies();
}

// Schedules
async function loadSchedules() {
    const resp = await fetch('/api/schedules');
    const schedules = await resp.json();
    const el = document.getElementById('schedules-list');
    if (!schedules.length) {
        el.innerHTML = '<p style="color: var(--text-muted)">No schedules configured</p>';
        return;
    }
    el.innerHTML = schedules.map(s => `
        <div class="schedule-item">
            <div class="schedule-info">
                <span class="name">${s.name}</span>
                <span class="time">${s.start_time} - ${s.end_time} | Target: ${s.target_soc}%
                    | Grid: ${s.allow_grid ? 'Yes (' + s.max_grid_amps + 'A)' : 'No'}
                    | Days: ${s.days_of_week}</span>
            </div>
            <div>
                <button onclick="toggleSchedule(${s.id}, ${!s.enabled})" class="btn" style="font-size:0.75rem">
                    ${s.enabled ? 'Disable' : 'Enable'}
                </button>
                <button onclick="deleteSchedule(${s.id})" class="btn btn-pause" style="font-size:0.75rem">Delete</button>
            </div>
        </div>
    `).join('');
}

async function deleteSchedule(id) {
    if (!confirm('Delete this schedule?')) return;
    await fetch(`/api/schedules/${id}`, { method: 'DELETE' });
    loadSchedules();
}

async function toggleSchedule(id, enabled) {
    const resp = await fetch('/api/schedules');
    const schedules = await resp.json();
    const sched = schedules.find(s => s.id === id);
    if (!sched) return;
    sched.enabled = enabled;
    await fetch(`/api/schedules/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sched),
    });
    loadSchedules();
}

document.getElementById('schedule-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = {
        name: form.name.value,
        start_time: form.start_time.value,
        end_time: form.end_time.value,
        target_soc: Number(form.target_soc.value),
        allow_grid: form.allow_grid.value === 'true',
        max_grid_amps: Number(form.max_grid_amps.value),
        days_of_week: form.days_of_week.value,
        enabled: true,
    };
    await fetch('/api/schedules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    form.reset();
    loadSchedules();
});

// Init
loadSettings();
loadTooltips();
loadTeslaStatus();
loadChannelStatus();
loadStrategies();
loadSchedules();
setInterval(loadTeslaStatus, 30000);
setInterval(loadChannelStatus, 30000);
