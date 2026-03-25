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

// Init
connectWebSocket();
fetchEnergyTotals();
setInterval(fetchEnergyTotals, 30000);
