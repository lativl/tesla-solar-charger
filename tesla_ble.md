# Tesla Local Control via BLE — Research

## Summary

There is **no local WiFi API** on Tesla vehicles. The only viable local control method is **Bluetooth Low Energy (BLE)**. Several mature solutions exist, all built on Tesla's official `vehicle-command` protocol. All support charging commands (`charge_start`, `charge_stop`, `set_charging_amps`), operate entirely without internet, and avoid Fleet API rate limits.

**BLE does not have the IV counter corruption issue** — BLE sessions are direct point-to-point between your device and the vehicle with no cloud relay in the middle. Counter desyncs auto-recover via handshake.

---

## Options

### 1. TeslaBleHttpProxy

**Repo:** [wimaha/TeslaBleHttpProxy](https://github.com/wimaha/TeslaBleHttpProxy) (108 stars, v2.3.0, Go)

Wraps Tesla BLE commands in an HTTP REST API — drop-in replacement for `tesla-http-proxy`.

- **Supported commands:** `charge_start`, `charge_stop`, `set_charging_amps`, `set_charge_limit`, `charge_port_door_open`, `charge_port_door_close`, climate, locks, sentry, wake, flash, honk
- **Hardware:** Linux only (RPi or x86). **Not ESP32** — requires D-Bus for Bluetooth stack. Docker supported.
- **No internet required.** Entirely local BLE.
- **BLE range:** ~5-10 meters line of sight.
- **Key pairing:** Web dashboard at `http://IP:8080/dashboard`. Generate key, confirm on vehicle with NFC card tap.
- **BLE key limit:** Tesla vehicles accept max 3 BLE keys.
- **Auto-wake:** Commands automatically wake sleeping vehicles.
- **IV counter issue:** Not applicable. BLE sessions are direct device-to-vehicle.
- **Maturity:** 25+ releases, 234+ commits, actively used with [evcc](https://github.com/evcc-io/evcc) solar charging systems.

### 2. ESPHome Tesla BLE (ESP32)

**Repo:** [yoziru/esphome-tesla-ble](https://github.com/yoziru/esphome-tesla-ble) (286 stars, C++)

- **Hardware:** ESP32 boards (~$8, e.g. M5Stack NanoC6, M5Stack AtomS3).
- **Supported commands:** `set_charging_amps`, `set_charge_limit`, `charge_start`, `charge_stop`, wake, pair key.
- **No internet required.** ESP32 connects to HA over WiFi; talks to car over BLE.
- **Key pairing:** Trigger from HA, then tap NFC card on center console.
- **BLE range:** 10-30m depending on ESP32 antenna.
- **Presented at FOSDEM 2025** with sub-second response times.
- **Quirk:** Tesla requires stepping to 5A before going lower (setting 3A requires first setting 5A, waiting 20s, then 3A).
- **Best for:** When server is far from car — place ESP32 near parking spot, bridges BLE→WiFi.

### 3. Tesla Local Control HA Add-on / MQTT Docker

**Repos:**
- [tesla-local-control/tesla_ble_mqtt_docker](https://github.com/tesla-local-control/tesla_ble_mqtt_docker) (77 stars)
- [tesla-local-control/tesla-local-control-addon](https://github.com/tesla-local-control/tesla-local-control-addon) (52 stars)

- **Hardware:** RPi 3+ with built-in Bluetooth, or any Linux with BLE adapter.
- **Architecture:** Runs Tesla's official `vehicle-command` CLI, exposes entities via MQTT with HA auto-discovery.
- **Recommended BLE range:** 3 metres (conservative).
- **Note:** 2.4GHz WiFi on RPi can interfere with BLE. Use Ethernet or 5GHz WiFi.

### 4. Tesla's Official `vehicle-command` CLI (Direct BLE)

**Repo:** [teslamotors/vehicle-command](https://github.com/teslamotors/vehicle-command) (627 stars)

- **Usage:** `tesla-control -ble -key-file private_key.pem charging-set-amps 16`
- **All charging commands supported over BLE.**
- **Platform:** macOS or Linux (Windows lacks BLE support).
- **Session cache:** `TESLA_CACHE_FILE` env var persists session state to disk.
- **Compatibility:** Post-2021 Model S/X and all Model 3/Y.

### 5. PyTeslaBLE (Python) — NOT RECOMMENDED

**Repo:** [kaedenbrinkman/PyTeslaBLE](https://github.com/kaedenbrinkman/PyTeslaBLE)

- **Abandoned.** Last release v0.1.4 from September 2022.
- No charging commands. Known cryptography issues. Superseded by official SDK.

---

## Comparison

| Option | Hardware | Integration | Maturity | Internet | IV Issue |
|---|---|---|---|---|---|
| **TeslaBleHttpProxy** | RPi/x86 | HTTP API (drop-in) | High | None | No |
| **ESPHome Tesla BLE** | ESP32 (~$8) | HA / WiFi | High | None | No |
| **Tesla Local Control** | RPi | MQTT / HA Add-on | Medium | None | No |
| **`tesla-control -ble`** | RPi/x86 | CLI / scripts | Official | None | No |

---

## Recommendation for This Project

**If `devsrv` is within ~10m of the car:** Add **TeslaBleHttpProxy** as a Docker container. Change `TESLA_PROXY_URL` to point at it instead of `tesla-http-proxy`. No more proxy recreation, no IV corruption, no rate limits.

**If the server is far from the car:** Get an **ESP32** (~$8), flash **esphome-tesla-ble**, place near parking spot. Bridges BLE→WiFi. App calls it via HTTP.

---

## Protocol Notes

- Tesla BLE uses AES-GCM encryption with monotonic counters for anti-replay
- Two domains: Infotainment (sliding window, tolerates out-of-order) and VCSEC (strict sequential)
- VCSEC is always powered — BLE wake works even when car is in deep sleep
- Counter desync triggers automatic re-handshake (self-healing)
- Max 3 BLE keys per vehicle

## Sources

- [teslamotors/vehicle-command](https://github.com/teslamotors/vehicle-command)
- [wimaha/TeslaBleHttpProxy](https://github.com/wimaha/TeslaBleHttpProxy)
- [yoziru/esphome-tesla-ble](https://github.com/yoziru/esphome-tesla-ble)
- [tesla-local-control/tesla_ble_mqtt_docker](https://github.com/tesla-local-control/tesla_ble_mqtt_docker)
- [Vehicle-command protocol spec](https://github.com/teslamotors/vehicle-command/blob/main/pkg/protocol/protocol.md)
- [FOSDEM 2025 talk](https://archive.fosdem.org/2025/schedule/event/fosdem-2025-4664-interacting-with-tesla-vehicles-locally-over-ble-using-esphome/)


I have ESPHome Tesla BLE ESP32 device accessiable by the IP 192.168.40.125 or from Home Assistant. That device is connected with Tesla via Bloetooth, can read its data and send commands and does not use external API so we can poll and call Tesla more often. Create the plan how to add this connectivity option as additional to exisiting API based connectivity and let user choose on UI available connection capable communication means. Each communication channel should has its own set of settings as in case of direct connectivity we do not have to fit into the API calls limit and can setup a bit different strategy of controlling Tesla. So maybe we need to create different strategies (set of settings and assign them to the communication channel or even change strategy manually or by specific rules in future). Create the plan how to integrate with "ESPHome Tesla BLE", what UI changes need to be done and how to implement the changes.