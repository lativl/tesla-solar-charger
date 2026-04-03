# Solar Assistant — MQTT Data Specification

[Solar Assistant](https://solar-assistant.io) runs on a Raspberry Pi connected to the inverter and publishes all inverter and battery data to a local MQTT broker.

## Topic Structure

```
solar_assistant/<device>/<metric>/state        # read-only values
solar_assistant/<device>/<metric>/set          # writable settings
solar_assistant/set/response_message/state     # response to setting changes
```

**Devices:**
- `inverter_1`, `inverter_2`, … — per-inverter data
- `battery_1`, `battery_2`, … — per-battery-bank data (BMS-level)
- `total` — system-wide aggregates

All values are published as plain UTF-8 strings (numeric or text). Retained messages are used, so a subscriber always receives the last known value on connect.

---

## Solar / PV

| Topic | Unit | Description |
|---|---|---|
| `solar_assistant/inverter_1/pv_power/state` | W | Total PV production (sum of all MPPT inputs) |
| `solar_assistant/inverter_1/pv_power_1/state` | W | PV string 1 power |
| `solar_assistant/inverter_1/pv_power_2/state` | W | PV string 2 power |
| `solar_assistant/inverter_1/pv_voltage_1/state` | V | PV string 1 voltage |
| `solar_assistant/inverter_1/pv_voltage_2/state` | V | PV string 2 voltage |
| `solar_assistant/inverter_1/pv_current_1/state` | A | PV string 1 current |
| `solar_assistant/inverter_1/pv_current_2/state` | A | PV string 2 current |
| `solar_assistant/total/pv_power/state` | W | System-wide PV total (multi-inverter setups) |

---

## Battery (Aggregate / Inverter View)

| Topic | Unit | Description |
|---|---|---|
| `solar_assistant/total/battery_power/state` | W | Net battery power — positive = charging, negative = discharging |
| `solar_assistant/total/battery_state_of_charge/state` | % | System SoC across all banks |
| `solar_assistant/total/battery_temperature/state` | °C | Battery temperature |
| `solar_assistant/total/battery_energy_in/state` | kWh | Energy charged today |
| `solar_assistant/total/battery_energy_out/state` | kWh | Energy discharged today |
| `solar_assistant/inverter_1/battery_voltage/state` | V | DC bus voltage (inverter side) |
| `solar_assistant/inverter_1/battery_current/state` | A | DC bus current (inverter side) |

## Battery Banks (BMS-level, per bank)

Replace `N` with the bank number (`1`, `2`, `3`, …):

| Topic | Unit | Description |
|---|---|---|
| `solar_assistant/battery_N/state_of_charge/state` | % | State of charge |
| `solar_assistant/battery_N/voltage/state` | V | Terminal voltage |
| `solar_assistant/battery_N/current/state` | A | Current (positive = charging) |
| `solar_assistant/battery_N/power/state` | W | Power |
| `solar_assistant/battery_N/temperature/state` | °C | Cell/pack temperature |
| `solar_assistant/battery_N/cycles/state` | — | Charge cycle count |
| `solar_assistant/battery_N/cell_voltage_-_highest/state` | V | Highest cell voltage |
| `solar_assistant/battery_N/cell_voltage_-_lowest/state` | V | Lowest cell voltage |

> BMS-level topics are only published if the battery communicates via a supported BMS protocol (e.g. CAN, RS485). Systems with dumb/AGM batteries will only have inverter-side voltage/current.

---

## Grid

| Topic | Unit | Description |
|---|---|---|
| `solar_assistant/inverter_1/grid_power/state` | W | Grid power as seen by inverter |
| `solar_assistant/inverter_1/grid_power_ct/state` | W | Grid power from external CT clamp — net import (+) / export (−) at the meter |
| `solar_assistant/inverter_1/grid_voltage/state` | V | Grid AC voltage |
| `solar_assistant/inverter_1/grid_frequency/state` | Hz | Grid frequency |
| `solar_assistant/total/grid_energy_in/state` | kWh | Energy imported from grid today |
| `solar_assistant/total/grid_energy_out/state` | kWh | Energy exported to grid today |

> `grid_power_ct` (CT clamp) measures the actual meter import/export for the whole premises and is more accurate than `grid_power` (inverter port only), especially when non-essential loads bypass the inverter.

---

## Load

| Topic | Unit | Description |
|---|---|---|
| `solar_assistant/inverter_1/load_power/state` | W | Total load power |
| `solar_assistant/inverter_1/load_power_essential/state` | W | Essential loads (on inverter output) |
| `solar_assistant/inverter_1/load_power_non-essential/state` | W | Non-essential loads (bypassing inverter) |
| `solar_assistant/total/load_power/state` | W | System-wide load total |

---

## Inverter Status

| Topic | Unit | Description |
|---|---|---|
| `solar_assistant/inverter_1/temperature/state` | °C | Inverter heat-sink temperature |
| `solar_assistant/inverter_1/load_percentage/state` | % | Inverter load as % of rated capacity |
| `solar_assistant/inverter_1/device_mode/state` | string | Operating mode, e.g. `"Discharge above 50%"`, `"Solar first"` |
| `solar_assistant/inverter_1/ac_output_voltage/state` | V | AC output voltage |
| `solar_assistant/inverter_1/ac_output_frequency/state` | Hz | AC output frequency |

---

## Writable Settings (inverter control)

Settings are changed by publishing to the `/set` topic. Solar Assistant forwards the command to the inverter over RS485/CAN and publishes a response on `solar_assistant/set/response_message/state`.

Available command topics vary by inverter model. Common examples:

| Topic | Accepted values | Description |
|---|---|---|
| `solar_assistant/inverter_1/output_source_priority/set` | `"Utility first"`, `"Solar first"`, `"SBU"` | Output source priority |
| `solar_assistant/inverter_1/charger_source_priority/set` | `"Solar only"`, `"Solar and utility simultaneously"`, etc. | Charger source priority |
| `solar_assistant/inverter_1/max_grid_charge_current/set` | numeric (A) | Max AC charge current |
| `solar_assistant/inverter_1/shutdown_battery_voltage/set` | numeric (V) | Low battery shutdown threshold |
| `solar_assistant/inverter_1/capacity_point_1/set` | numeric (%) | Work mode SoC threshold (Deye) |

To discover all writable topics for your specific inverter, enable Home Assistant Discovery in Solar Assistant and run:
```bash
mosquitto_sub -h <solar-assistant-ip> -v -t '#' | grep command_topic
```

---

## Notes

- **Topic availability depends on inverter model.** Not all topics are published by all inverters. If a metric is not supported, Solar Assistant simply does not publish that topic.
- **Multi-inverter setups** use `inverter_2`, `inverter_3`, … and `battery_2`, `battery_3`, … Additional `total/*` aggregates span all devices.
- **CT clamp** (`grid_power_ct`) requires an optional external current transformer and may not be present in all installations.
- **Publish rate** is approximately every 5 seconds, driven by the inverter polling cycle.
- **Retained messages** — all state topics are published with the MQTT `retain` flag, so new subscribers receive the last value immediately on connect.

## Discovery

To see all topics your specific installation publishes:
```bash
mosquitto_sub -h <solar-assistant-ip> -p 1883 -v -t 'solar_assistant/#'
```
