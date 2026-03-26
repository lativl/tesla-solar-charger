let refreshTimer;

// Sections not available via BLE (no GPS/drive data, no vehicle config)
const BLE_HIDDEN_SECTIONS = ['section-drive', 'section-config'];

function setBleModeUI(isBle) {
    const banner = document.getElementById('ble-mode-banner');
    if (banner) banner.style.display = isBle ? '' : 'none';
    for (const id of BLE_HIDDEN_SECTIONS) {
        const el = document.getElementById(id);
        if (el) el.style.display = isBle ? 'none' : '';
    }
    // Show sections that BLE now supports
    for (const id of ['section-climate', 'section-security', 'section-tires']) {
        const el = document.getElementById(id);
        if (el) el.style.display = '';
    }
    // Hide fleet-only controls in BLE mode
    for (const el of document.querySelectorAll('.fleet-only')) {
        el.style.display = isBle ? 'none' : '';
    }
}

async function loadVehicleData() {
    try {
        const resp = await fetch('/api/tesla/vehicle_data');
        const json = await resp.json();
        const isBle = json.channel === 'ble';

        if (!json.connected) {
            document.getElementById('vehicle-offline').style.display = '';
            document.getElementById('vehicle-offline-msg').innerHTML =
                'Vehicle not connected. Go to <a href="/settings.html">Settings</a> to connect your Tesla.';
            document.getElementById('vehicle-content').style.display = 'none';
            return;
        }

        if (!json.data) {
            const vs = json.vehicle_state || 'unknown';
            document.getElementById('vehicle-offline').style.display = '';
            document.getElementById('vehicle-offline-msg').textContent =
                vs === 'asleep' ? 'Vehicle is sleeping. Data will appear when it wakes up.'
                : vs === 'offline' ? 'Vehicle is offline. Data will appear when it comes online.'
                : 'Vehicle is unavailable. Waiting for data...';
            document.getElementById('vehicle-content').style.display = 'none';
            return;
        }

        document.getElementById('vehicle-offline').style.display = 'none';
        document.getElementById('vehicle-content').style.display = '';
        setBleModeUI(isBle);

        const d = json.data;
        const cs = d.charge_state || {};
        const vs = d.vehicle_state || {};
        const cl = d.climate_state || {};
        const ds = d.drive_state || {};
        const vc = d.vehicle_config || {};
        const gs = d.gui_settings || {};

        // Vehicle tab name
        const name = vs.vehicle_name || d.display_name || 'Vehicle';
        document.getElementById('vehicle-tab').textContent = name;
        document.getElementById('vehicle-title').textContent = name;
        document.title = `${name} - Tesla Solar Charger`;

        // Overview
        setText('v-state', d.state || '--');
        setText('v-odometer', vs.odometer ? `${Math.round(vs.odometer * 1.60934).toLocaleString()} km` : '--');
        setText('v-software', vs.car_version ? vs.car_version.split(' ')[0] : '--');
        setText('v-car-type', formatCarType(vc));

        // Charging
        setText('v-battery', cs.battery_level != null ? `${cs.battery_level}%` : '--');
        setText('v-charge-limit', cs.charge_limit_soc != null ? `${cs.charge_limit_soc}%` : '--');
        setText('v-charge-state', cs.charging_state || '--');
        setText('v-charge-rate', cs.charge_rate != null ? `${cs.charge_rate} km/h` : '--');
        setText('v-charger-power', cs.charger_power != null ? `${cs.charger_power} kW` : '--');
        setText('v-time-full', cs.time_to_full_charge ? formatTimeToFull(cs.time_to_full_charge) : '--');
        setText('v-range', cs.battery_range != null ? `${Math.round(cs.battery_range * 1.60934)} km` : '--');
        setText('v-energy-added', cs.charge_energy_added != null ? `${cs.charge_energy_added} kWh` : '--');
        setText('v-charge-port', formatChargePort(cs));

        // Sync charge limit slider
        if (cs.charge_limit_soc) {
            const slider = document.getElementById('charge-limit-slider');
            if (document.activeElement !== slider) {
                slider.value = cs.charge_limit_soc;
                document.getElementById('charge-limit-display').textContent = cs.charge_limit_soc;
            }
        }

        // Climate
        setText('v-inside-temp', cl.inside_temp != null ? `${cl.inside_temp.toFixed(1)}\u00B0C` : '--');
        setText('v-outside-temp', cl.outside_temp != null ? `${cl.outside_temp.toFixed(1)}\u00B0C` : '--');
        setText('v-driver-temp', cl.driver_temp_setting != null ? `${cl.driver_temp_setting.toFixed(1)}\u00B0C` : '--');
        setText('v-hvac', cl.is_climate_on ? 'On' : 'Off');
        setText('v-fan', cl.fan_status != null ? `Level ${cl.fan_status}` : '--');
        setText('v-seat-heater-l', seatLevel(cl.seat_heater_left));
        setText('v-seat-heater-r', seatLevel(cl.seat_heater_right));
        setText('v-steering-heater', cl.steering_wheel_heater ? 'On' : 'Off');

        // Sync temp input
        if (cl.driver_temp_setting) {
            document.getElementById('climate-temp').value = cl.driver_temp_setting;
        }

        // Security & Access
        setText('v-locked', vs.locked ? 'Locked' : 'Unlocked');
        setText('v-sentry', vs.sentry_mode ? 'Active' : 'Off');
        setText('v-frunk', vs.ft ? 'Open' : 'Closed');
        setText('v-trunk', vs.rt ? 'Open' : 'Closed');
        setText('v-door-df', vs.df ? 'Open' : 'Closed');
        setText('v-door-pf', vs.pf ? 'Open' : 'Closed');
        setText('v-door-dr', vs.dr ? 'Open' : 'Closed');
        setText('v-door-pr', vs.pr ? 'Open' : 'Closed');

        // Windows
        const anyWindowOpen = (vs.fd_window || vs.fp_window || vs.rd_window || vs.rp_window);
        setText('v-windows', anyWindowOpen ? 'Open' : 'Closed');

        // Drive
        setText('v-speed', ds.speed != null ? `${ds.speed} km/h` : 'Parked');
        setText('v-shift', ds.shift_state || 'P');
        setText('v-power', ds.power != null ? `${ds.power} kW` : '--');
        setText('v-heading', ds.heading != null ? `${ds.heading}\u00B0` : '--');
        setText('v-lat', ds.latitude != null ? ds.latitude.toFixed(6) : '--');
        setText('v-lon', ds.longitude != null ? ds.longitude.toFixed(6) : '--');

        // Tires (psi to bar)
        setText('v-tire-fl', formatPressure(vs.tpms_pressure_fl));
        setText('v-tire-fr', formatPressure(vs.tpms_pressure_fr));
        setText('v-tire-rl', formatPressure(vs.tpms_pressure_rl));
        setText('v-tire-rr', formatPressure(vs.tpms_pressure_rr));

        // Vehicle config
        buildConfigGrid(vc, gs);

    } catch (e) {
        console.error('Failed to load vehicle data:', e);
    }
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function formatCarType(vc) {
    if (!vc.car_type) return '--';
    const type = vc.car_type.replace('model', 'Model ').replace(/\b\w/g, c => c.toUpperCase());
    const trim = vc.trim_badging || '';
    return trim ? `${type} ${trim}` : type;
}

function formatChargePort(cs) {
    if (!cs.charge_port_door_open && !cs.charge_port_latch) return 'Closed';
    const door = cs.charge_port_door_open ? 'Open' : 'Closed';
    const latch = cs.charge_port_latch === 'Engaged' ? ', Engaged' : '';
    return `${door}${latch}`;
}

function seatLevel(val) {
    if (val == null) return '--';
    return ['Off', 'Low', 'Med', 'High'][val] || `${val}`;
}

function formatTimeToFull(hours) {
    if (hours <= 0) return '--';
    const h = Math.floor(hours);
    const m = Math.round((hours - h) * 60);
    if (h === 0) return `${m}m`;
    if (m === 0) return `${h}h`;
    return `${h}h ${m}m`;
}

function formatPressure(psi) {
    if (psi == null) return '--';
    return `${(psi * 0.0689476).toFixed(2)} bar`;
}

function buildConfigGrid(vc, gs) {
    const grid = document.getElementById('v-config-grid');
    const items = [
        ['Exterior Color', vc.exterior_color],
        ['Wheel Type', vc.wheel_type],
        ['Roof Color', vc.roof_color],
        ['Charge Port Type', vc.charge_port_type],
        ['EU Vehicle', vc.eu_vehicle ? 'Yes' : 'No'],
        ['Right-Hand Drive', vc.rhd ? 'Yes' : 'No'],
        ['Motorized Port', vc.motorized_charge_port ? 'Yes' : 'No'],
        ['Plaid', vc.plaid ? 'Yes' : 'No'],
        ['Distance Unit', gs.gui_distance_units],
        ['Temp Unit', gs.gui_temperature_units],
        ['Range Display', gs.gui_range_display],
    ];

    grid.innerHTML = items
        .filter(([, v]) => v != null && v !== '' && v !== undefined)
        .map(([label, val]) => `
            <div class="info-item">
                <span class="info-label">${label}</span>
                <span class="info-value">${val}</span>
            </div>
        `).join('');
}

async function cmd(command, extra) {
    const body = { command, ...extra };
    try {
        showToast(`Sending ${command}...`, 'info');
        const resp = await fetch('/api/tesla/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const json = await resp.json();
        if (json.ok) {
            showToast(`${command}: success`, 'success');
            setTimeout(loadVehicleData, 2000);
        } else {
            showToast(`${command}: failed`, 'error');
        }
    } catch (e) {
        showToast(`${command}: error`, 'error');
    }
}

function setChargeLimit() {
    const percent = parseInt(document.getElementById('charge-limit-slider').value, 10);
    cmd('set_charge_limit', { percent });
}

function setTemp() {
    const temp = parseFloat(document.getElementById('climate-temp').value);
    cmd('set_temps', { driver_temp: temp, passenger_temp: temp });
}

function setSeatHeater() {
    const seat = parseInt(document.getElementById('seat-select').value, 10);
    const level = parseInt(document.getElementById('seat-level').value, 10);
    cmd('seat_heater', { seat, level });
}

let toastTimeout;
function showToast(msg, type) {
    const toast = document.getElementById('cmd-toast');
    toast.textContent = msg;
    toast.className = `cmd-toast cmd-toast-${type} cmd-toast-show`;
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => toast.classList.remove('cmd-toast-show'), 3000);
}

// Load on page open, refresh every 60s
loadVehicleData();
refreshTimer = setInterval(loadVehicleData, 60000);
