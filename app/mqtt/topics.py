# Key MQTT topics from Solar Assistant / Deya inverter
# All topics are prefixed with solar_assistant/

# Inverter real-time measurements
INVERTER_PV_POWER = "inverter_1/pv_power"
INVERTER_PV_POWER_1 = "inverter_1/pv_power_1"
INVERTER_PV_POWER_2 = "inverter_1/pv_power_2"
INVERTER_PV_VOLTAGE_1 = "inverter_1/pv_voltage_1"
INVERTER_PV_VOLTAGE_2 = "inverter_1/pv_voltage_2"
INVERTER_PV_CURRENT_1 = "inverter_1/pv_current_1"
INVERTER_PV_CURRENT_2 = "inverter_1/pv_current_2"

INVERTER_LOAD_POWER = "inverter_1/load_power"
INVERTER_LOAD_ESSENTIAL = "inverter_1/load_power_essential"
INVERTER_LOAD_NON_ESSENTIAL = "inverter_1/load_power_non-essential"

INVERTER_GRID_POWER = "inverter_1/grid_power"
INVERTER_GRID_POWER_CT = "inverter_1/grid_power_ct"
INVERTER_GRID_VOLTAGE = "inverter_1/grid_voltage"
INVERTER_GRID_FREQUENCY = "inverter_1/grid_frequency"

INVERTER_BATTERY_VOLTAGE = "inverter_1/battery_voltage"
INVERTER_BATTERY_CURRENT = "inverter_1/battery_current"
INVERTER_TEMPERATURE = "inverter_1/temperature"
INVERTER_LOAD_PERCENTAGE = "inverter_1/load_percentage"
INVERTER_DEVICE_MODE = "inverter_1/device_mode"
INVERTER_AC_OUTPUT_VOLTAGE = "inverter_1/ac_output_voltage"
INVERTER_AC_OUTPUT_FREQUENCY = "inverter_1/ac_output_frequency"

# Totals
TOTAL_BATTERY_POWER = "total/battery_power"
TOTAL_BATTERY_SOC = "total/battery_state_of_charge"
TOTAL_BATTERY_TEMPERATURE = "total/battery_temperature"
TOTAL_PV_ENERGY = "total/pv_energy"
TOTAL_LOAD_ENERGY = "total/load_energy"
TOTAL_GRID_ENERGY_IN = "total/grid_energy_in"
TOTAL_GRID_ENERGY_OUT = "total/grid_energy_out"
TOTAL_BATTERY_ENERGY_IN = "total/battery_energy_in"
TOTAL_BATTERY_ENERGY_OUT = "total/battery_energy_out"

# Per-battery topics (battery_1 through battery_4)
BATTERY_SOC = "battery_{n}/state_of_charge"
BATTERY_VOLTAGE = "battery_{n}/voltage"
BATTERY_CURRENT = "battery_{n}/current"
BATTERY_POWER = "battery_{n}/power"
BATTERY_TEMPERATURE = "battery_{n}/temperature"
BATTERY_CYCLES = "battery_{n}/cycles"
BATTERY_CELL_HIGHEST = "battery_{n}/cell_voltage_-_highest"
BATTERY_CELL_LOWEST = "battery_{n}/cell_voltage_-_lowest"

# Topics critical for charging algorithm
ALGORITHM_TOPICS = [
    INVERTER_PV_POWER,
    INVERTER_LOAD_POWER,
    INVERTER_GRID_POWER_CT,
    TOTAL_BATTERY_POWER,
    TOTAL_BATTERY_SOC,
    INVERTER_BATTERY_VOLTAGE,
    INVERTER_BATTERY_CURRENT,
]
