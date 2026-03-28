let ws;

function connectWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/status`);

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updateInverterPage(data);
    };

    ws.onclose = () => setTimeout(connectWebSocket, 3000);
    ws.onerror = () => ws.close();
}

function updateInverterPage(data) {
    const s = data.solar;

    // Battery banks
    const banksEl = document.getElementById('battery-banks');
    banksEl.innerHTML = s.battery.banks.map(b => `
        <div class="battery-card">
            <h3>Battery ${b.id}</h3>
            <div class="stat"><span class="label">SoC</span><span>${b.soc}%</span></div>
            <div class="stat"><span class="label">Voltage</span><span>${b.voltage} V</span></div>
            <div class="stat"><span class="label">Current</span><span>${b.current} A</span></div>
            <div class="stat"><span class="label">Power</span><span>${b.power} W</span></div>
            <div class="stat"><span class="label">Temp</span><span>${b.temperature} °C</span></div>
            <div class="stat"><span class="label">Cycles</span><span>${b.cycles}</span></div>
            <div class="stat"><span class="label">Cell ΔV</span><span>${(b.cell_highest - b.cell_lowest).toFixed(3)} V</span></div>
        </div>
    `).join('');

    // Inverter
    document.getElementById('inv-temp').textContent = `${s.inverter.temperature} °C`;
    document.getElementById('inv-load').textContent = `${s.inverter.load_percentage}%`;
    document.getElementById('inv-mode').textContent = s.inverter.device_mode;
    document.getElementById('inv-ac').textContent =
        `${s.inverter.ac_output_voltage} V / ${s.inverter.ac_output_frequency} Hz`;

    // Energy today
    document.getElementById('energy-grid-in').textContent = `${s.grid.energy_in} kWh`;
    document.getElementById('energy-grid-out').textContent = `${s.grid.energy_out} kWh`;
    document.getElementById('energy-bat-in').textContent = `${s.battery.energy_in} kWh`;
    document.getElementById('energy-bat-out').textContent = `${s.battery.energy_out} kWh`;
}

async function fetchEnergyTotals() {
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();
        const s = data.solar;
        document.getElementById('energy-pv').textContent = `${(s.battery.energy_in + s.grid.energy_out).toFixed(1) || '--'} kWh`;
        document.getElementById('energy-grid-in').textContent = `${s.grid.energy_in} kWh`;
        document.getElementById('energy-grid-out').textContent = `${s.grid.energy_out} kWh`;
        document.getElementById('energy-bat-in').textContent = `${s.battery.energy_in} kWh`;
        document.getElementById('energy-bat-out').textContent = `${s.battery.energy_out} kWh`;
    } catch (e) {}
}

// Lux → PV model chart
let luxChart = null;

async function loadLuxChart() {
    let data;
    try {
        const resp = await fetch('/api/lux-model');
        data = await resp.json();
    } catch (e) { return; }

    const empty = document.getElementById('lux-chart-empty');
    const wrap = document.getElementById('lux-chart-wrap');
    const meta = document.getElementById('lux-model-meta');

    if (!data.model_ready || !data.buckets.length) {
        empty.style.display = '';
        wrap.style.display = 'none';
        return;
    }
    empty.style.display = 'none';
    wrap.style.display = '';

    const samples = data.buckets.reduce((s, b) => s + b.samples, 0);
    meta.textContent = `${data.buckets.length} buckets · ${samples.toLocaleString()} samples`;

    const labels = data.buckets.map(b => (b.lux / 1000).toFixed(0) + 'k');
    const isMobile = window.innerWidth <= 480;

    const datasets = [
        {
            label: 'P90 (model)',
            data: data.buckets.map(b => b.pv_p90),
            borderColor: '#f59e0b',
            backgroundColor: 'rgba(245,158,11,0.15)',
            fill: true,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 4,
            pointHitRadius: 10,
        },
        {
            label: 'Observed Max',
            data: data.buckets.map(b => b.pv_max),
            borderColor: '#94a3b8',
            borderDash: [5, 4],
            fill: false,
            tension: 0.3,
            borderWidth: 1.5,
            pointRadius: 3,
            pointHitRadius: 10,
        },
    ];

    if (luxChart) {
        luxChart.data.labels = labels;
        luxChart.data.datasets.forEach((ds, i) => { ds.data = datasets[i].data; });
        luxChart.update('none');
        return;
    }

    const ctx = document.getElementById('lux-chart').getContext('2d');
    luxChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    labels: {
                        color: '#475569',
                        usePointStyle: true,
                        pointStyle: 'line',
                        padding: isMobile ? 8 : 16,
                        font: { size: isMobile ? 10 : 12 },
                        boxWidth: isMobile ? 20 : 40,
                    },
                },
                tooltip: {
                    backgroundColor: 'rgba(15,23,42,0.9)',
                    titleColor: '#e2e8f0',
                    bodyColor: '#e2e8f0',
                    borderColor: '#334155',
                    borderWidth: 1,
                    padding: 10,
                    callbacks: {
                        title: (items) => `Lux: ${data.buckets[items[0].dataIndex].lux.toLocaleString()}`,
                        afterBody: (items) => {
                            const b = data.buckets[items[0].dataIndex];
                            return `Samples: ${b.samples}`;
                        },
                        label: (item) => `${item.dataset.label}: ${item.parsed.y.toLocaleString()} W`,
                    },
                },
            },
            scales: {
                x: {
                    title: { display: !isMobile, text: 'Solar Lux', color: '#475569' },
                    ticks: { color: '#64748b', font: { size: isMobile ? 9 : 11 } },
                    grid: { color: 'rgba(226,232,240,0.5)' },
                },
                y: {
                    title: { display: !isMobile, text: 'PV Power (W)', color: '#475569' },
                    ticks: { color: '#475569', font: { size: isMobile ? 9 : 11 } },
                    grid: { color: 'rgba(226,232,240,0.5)' },
                },
            },
        },
    });
}

// Init
connectWebSocket();
fetchEnergyTotals();
setInterval(fetchEnergyTotals, 30000);
loadLuxChart();
setInterval(loadLuxChart, 3600000); // refresh hourly (matches model refresh rate)
