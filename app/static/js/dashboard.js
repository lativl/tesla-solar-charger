let ws;

// Arc length for 270-degree gauge (r=80, 270/360 * 2*pi*80 ≈ 376.99)
const ARC_LENGTH = 377;

function connectWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/status`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updateDashboard(data);
    };

    ws.onclose = () => setTimeout(connectWebSocket, 3000);
    ws.onerror = () => ws.close();
}

function updateGauge(id, valueW, maxKw, color) {
    const absW = Math.abs(valueW);
    const kw = absW / 1000;
    const clamped = Math.min(kw, maxKw);
    const ratio = clamped / maxKw;
    const dashLen = ratio * ARC_LENGTH;

    const arc = document.getElementById(`gauge-${id}-arc`);
    arc.style.strokeDasharray = `${dashLen} ${ARC_LENGTH}`;
    if (color) arc.style.stroke = color;

    document.getElementById(`gauge-${id}-val`).textContent = kw.toFixed(1);
    document.getElementById(`gauge-${id}-watts`).textContent = `${absW} W`;
}

function updateDashboard(data) {
    const s = data.solar;
    const ev = data.ev;

    // Mode
    document.getElementById('mode').textContent = data.mode.replace('_', ' ').toUpperCase();

    // Gauges
    updateGauge('solar', s.pv.power, 12);
    updateGauge('grid', s.grid.power_ct, 12);
    updateGauge('load', s.load.power, 12);

    // Tesla: charger_power is in kW from API, but we get watts from ev
    const teslaW = (ev.charger_power || 0) * 1000;
    updateGauge('tesla', teslaW, 7);

    // Battery: green if charging (power > 0), red if discharging (power < 0)
    const bp = s.battery.power;
    const batteryColor = bp >= 0 ? 'var(--battery)' : 'var(--ev)';
    updateGauge('battery', bp, 12, batteryColor);
    const batteryWatts = document.getElementById('gauge-battery-watts');
    if (bp >= 0) {
        batteryWatts.textContent = `${bp} W charging`;
        batteryWatts.style.color = 'var(--battery)';
    } else {
        batteryWatts.textContent = `${Math.abs(bp)} W discharging`;
        batteryWatts.style.color = 'var(--ev)';
    }

    // PV
    document.getElementById('pv-power').textContent = `${s.pv.power} W`;
    document.getElementById('pv1').textContent = s.pv.power_1;
    document.getElementById('pv2').textContent = s.pv.power_2;

    // Battery
    document.getElementById('battery-soc').textContent = `${s.battery.soc} %`;
    document.getElementById('battery-power').textContent =
        bp < 0 ? `${Math.abs(bp)} W discharge` : `${bp} W charge`;
    document.getElementById('battery-voltage').textContent = `${s.battery.voltage} V`;

    // Grid
    document.getElementById('grid-power').textContent = `${s.grid.power_ct} W`;
    document.getElementById('grid-voltage').textContent = `${s.grid.voltage} V`;
    document.getElementById('grid-freq').textContent = `${s.grid.frequency} Hz`;

    // EV
    document.getElementById('ev-soc').textContent = `${ev.battery_level} %`;
    document.getElementById('ev-amps').textContent = `${ev.charging_amps} A`;
    document.getElementById('ev-state').textContent = ev.charge_state;

    // Sync amps slider with Tesla's current setting (only if user isn't dragging)
    const slider = document.getElementById('amps-slider');
    const requestedAmps = ev.charge_amps_request || ev.charging_amps || 0;
    if (requestedAmps > 0 && document.activeElement !== slider) {
        slider.value = requestedAmps;
        document.getElementById('amps-display').textContent = requestedAmps;
    }
}

function setMode(mode) {
    fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode })
    });
}

function teslaCmd(cmd) {
    const body = { command: cmd };
    if (cmd === 'set_amps') {
        body.amps = parseInt(document.getElementById('amps-slider').value, 10);
    }
    fetch('/api/tesla/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

// --- Power History Chart ---
let powerChart = null;

async function loadChart() {
    const hours = document.getElementById('chart-range').value;
    const resp = await fetch(`/api/history?hours=${hours}`);
    const data = await resp.json();
    if (!data.length) return;

    // Downsample for large ranges to keep chart responsive
    const maxPoints = 500;
    let points = data;
    if (data.length > maxPoints) {
        const step = Math.ceil(data.length / maxPoints);
        points = data.filter((_, i) => i % step === 0);
    }

    const labels = points.map(d => {
        const t = new Date(d.timestamp);
        return hours > 24
            ? t.toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
            : t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    });

    const datasets = [
        {
            label: 'Solar (W)',
            data: points.map(d => d.pv_power),
            borderColor: '#f59e0b',
            backgroundColor: 'rgba(245,158,11,0.08)',
            fill: true,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 8,
        },
        {
            label: 'Battery (W)',
            data: points.map(d => d.battery_power),
            borderColor: '#22c55e',
            fill: false,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 8,
        },
        {
            label: 'Grid (W)',
            data: points.map(d => d.grid_power),
            borderColor: '#3b82f6',
            fill: false,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 8,
        },
        {
            label: 'Load (W)',
            data: points.map(d => d.load_power),
            borderColor: '#a855f7',
            fill: false,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 8,
        },
        {
            label: 'EV Charge (W)',
            data: points.map(d => Math.round((d.ev_charging_amps || 0) * 230)),
            borderColor: '#ef4444',
            fill: false,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 8,
        },
    ];

    if (powerChart) {
        powerChart.data.labels = labels;
        powerChart.data.datasets.forEach((ds, i) => { ds.data = datasets[i].data; });
        powerChart.options.scales.x.ticks.maxTicksLimit = hours > 24 ? 12 : 20;
        powerChart.update('none');
        return;
    }

    const ctx = document.getElementById('power-chart').getContext('2d');
    powerChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    labels: { color: '#475569', usePointStyle: true, pointStyle: 'line', padding: 16 },
                },
                tooltip: {
                    backgroundColor: 'rgba(15,23,42,0.9)',
                    titleColor: '#e2e8f0',
                    bodyColor: '#e2e8f0',
                    borderColor: '#334155',
                    borderWidth: 1,
                    padding: 10,
                    callbacks: {
                        label: function(ctx) {
                            return `${ctx.dataset.label.split(' (')[0]}: ${ctx.parsed.y} W`;
                        }
                    }
                },
            },
            scales: {
                x: {
                    ticks: { color: '#64748b', maxTicksLimit: 20, maxRotation: 45 },
                    grid: { color: 'rgba(226,232,240,0.5)' },
                },
                y: {
                    ticks: { color: '#475569' },
                    grid: { color: 'rgba(226,232,240,0.5)' },
                    title: { display: true, text: 'Watts', color: '#475569' },
                },
            },
        },
    });
}

// Init — fetch current status to seed slider, then start WebSocket
fetch('/api/status').then(r => r.json()).then(data => {
    const amps = data.ev.charge_amps_request || data.ev.charging_amps || 0;
    if (amps > 0) {
        document.getElementById('amps-slider').value = amps;
        document.getElementById('amps-display').textContent = amps;
    }
}).catch(() => {});
connectWebSocket();
loadChart();
// Refresh chart every 5 minutes
setInterval(loadChart, 300000);
