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
}

// Save settings
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
        if (data.connected && data.vehicle) {
            document.getElementById('tesla-connected').textContent = 'Connected';
            document.getElementById('tesla-connected').style.color = '#22c55e';
            document.getElementById('tesla-auth-section').style.display = 'none';
            document.getElementById('tesla-vehicle').style.display = 'grid';
            document.getElementById('tesla-name').textContent = data.vehicle.name || data.vehicle.vin;
            document.getElementById('tesla-soc').textContent = `${data.vehicle.battery_level}%`;
            document.getElementById('tesla-state').textContent = data.vehicle.charge_state;
        } else {
            document.getElementById('tesla-connected').textContent = 'Not connected';
            document.getElementById('tesla-connected').style.color = '#ef4444';
            document.getElementById('tesla-auth-section').style.display = 'block';
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
    alert(result.ok ? `Command '${cmd}' succeeded` : `Command failed: ${JSON.stringify(result)}`);
    setTimeout(loadTeslaStatus, 2000);
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
    // Get current schedule data first, then update
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
loadTeslaStatus();
loadSchedules();
setInterval(loadTeslaStatus, 30000);
