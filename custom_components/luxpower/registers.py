#!/usr/bin/env python3
"""Verified LuxPower input-register map for a single-phase hybrid inverter.

Every entry here was confirmed against the LuxPower app. Registers the community
(lxp-bridge) map defines but which do NOT decode correctly on this hardware are listed
in EXCLUDED with the reason, so they don't get copied back in by accident.

Verification evidence for the framing itself:
  * CRC16-Modbus valid on blocks 0-39, 40-79, 80-119.
  * Register alignment proven: regs 115-119 hold the inverter's own serial as
    little-endian ASCII byte pairs, which only lines up if the offset and endianness
    are right (see protocol.decode_serial).
  * Reg 18 cross-checks: 950/100 = 9.50 A vs p_inv/v_ac = 2263/242.7 = 9.32 A.

Your inverter may differ. Check the values against the app before trusting them, and
see EXCLUDED for the ones that are known to lie.

No framing or I/O here on purpose -- this module is pure data plus scale helpers, so a
native HA integration and an MQTT bridge can share it unchanged.

Keys are HA-friendly: `unit`, `device_class`, `state_class` map straight onto a
SensorEntityDescription, and onto MQTT discovery payload fields of the same name.
"""

# --------------------------------------------------------------- scale helpers --

def s16(v: int) -> int:
    """Interpret a register as signed 16-bit."""
    return v - 0x10000 if v & 0x8000 else v


def u32(regs: dict, lo: int) -> int:
    """Combine two consecutive registers, low word first."""
    return regs.get(lo, 0) | (regs.get(lo + 1, 0) << 16)


# ------------------------------------------------------------------- the map --
# (key, label, register, kind, scale, unit, device_class, state_class)
#
# kind:  "u16"  single register, unsigned
#        "s16"  single register, signed
#        "u32"  this register + the next one, low word first
#        "low8" low byte of the register
#        "calc" derived, see DERIVED below
#
# value = raw / scale

MEASUREMENT = "measurement"
TOTAL_INCREASING = "total_increasing"

SENSORS = [
    # --- live power and voltage -------------------------------------------------
    ("pv1_voltage",            "PV1 voltage",             1, "u16",  10, "V",   "voltage",     MEASUREMENT),
    ("pv2_voltage",            "PV2 voltage",             2, "u16",  10, "V",   "voltage",     MEASUREMENT),
    ("pv1_power",              "PV1 power",               7, "u16",   1, "W",   "power",       MEASUREMENT),
    ("pv2_power",              "PV2 power",               8, "u16",   1, "W",   "power",       MEASUREMENT),
    ("pv_power",               "PV total power",       None, "calc",  1, "W",   "power",       MEASUREMENT),

    ("battery_voltage",        "Battery voltage",         4, "u16",  10, "V",   "voltage",     MEASUREMENT),
    ("battery_soc",            "Battery SOC",             5, "low8",  1, "%",   "battery",     MEASUREMENT),
    ("battery_charge_power",   "Battery charge power",    10, "u16",   1, "W",   "power",       MEASUREMENT),
    ("battery_discharge_power","Battery discharge power", 11, "u16",   1, "W",   "power",       MEASUREMENT),

    ("grid_voltage",           "Grid voltage",           12, "u16",  10, "V",   "voltage",     MEASUREMENT),
    ("grid_frequency",         "Grid frequency",         15, "u16", 100, "Hz",  "frequency",   MEASUREMENT),
    ("grid_import_power",      "Grid import power",      27, "u16",   1, "W",   "power",       MEASUREMENT),
    ("grid_export_power",      "Grid export power",      26, "u16",   1, "W",   "power",       MEASUREMENT),

    ("inverter_power",         "Inverter output power",  16, "u16",   1, "W",   "power",       MEASUREMENT),
    ("inverter_current",       "Inverter current",       18, "u16", 100, "A",   "current",     MEASUREMENT),
    ("ac_charge_power",        "AC charge power",        17, "u16",   1, "W",   "power",       MEASUREMENT),

    ("eps_voltage",            "EPS voltage",            20, "u16",  10, "V",   "voltage",     MEASUREMENT),
    ("eps_frequency",          "EPS frequency",          23, "u16", 100, "Hz",  "frequency",   MEASUREMENT),
    ("eps_power",              "EPS power",              24, "u16",   1, "W",   "power",       MEASUREMENT),

    # --- energy today (resets at midnight) --------------------------------------
    ("pv1_today",              "PV1 today",              28, "u16",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("pv2_today",              "PV2 today",              29, "u16",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("inverter_out_today",     "Inverter output today",  31, "u16",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("ac_charge_today",        "AC charge today",        32, "u16",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("battery_charged_today",  "Battery charged today",  33, "u16",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("battery_discharged_today","Battery discharged today",34,"u16", 10, "kWh", "energy",      TOTAL_INCREASING),
    ("eps_today",              "EPS today",              35, "u16",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("exported_today",         "Exported today",         36, "u16",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("imported_today",         "Imported today",         37, "u16",  10, "kWh", "energy",      TOTAL_INCREASING),

    # --- lifetime energy (u32, low word first) ----------------------------------
    ("pv1_total",              "PV1 total",              40, "u32",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("pv2_total",              "PV2 total",              42, "u32",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("inverter_out_total",     "Inverter output total",  46, "u32",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("ac_charge_total",        "AC charge total",        48, "u32",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("battery_charged_total",  "Battery charged total",  50, "u32",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("battery_discharged_total","Battery discharged total",52,"u32", 10, "kWh", "energy",      TOTAL_INCREASING),
    ("eps_total",              "EPS total",              54, "u32",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("exported_total",         "Exported total",         56, "u32",  10, "kWh", "energy",      TOTAL_INCREASING),
    ("imported_total",         "Imported total",         58, "u32",  10, "kWh", "energy",      TOTAL_INCREASING),

    # --- diagnostics -------------------------------------------------------------
    ("internal_temp",          "Inverter internal temp", 64, "s16",   1, "°C",  "temperature", MEASUREMENT),
    ("radiator1_temp",         "Radiator 1 temp",        65, "s16",   1, "°C",  "temperature", MEASUREMENT),
    ("radiator2_temp",         "Radiator 2 temp",        66, "s16",   1, "°C",  "temperature", MEASUREMENT),
    ("bus1_voltage",           "DC bus 1 voltage",       38, "u16",  10, "V",   "voltage",     MEASUREMENT),
    ("bus2_voltage",           "DC bus 2 voltage",       39, "u16",  10, "V",   "voltage",     MEASUREMENT),
    ("runtime",                "Runtime",                69, "u32",   1, "s",   "duration",    TOTAL_INCREASING),
    ("fault_code",             "Fault code",             60, "u32",   1, "",    None,          None),
    ("warning_code",           "Warning code",           62, "u32",   1, "",    None,          None),
    ("status",                 "Status",                  0, "u16",   1, "",    None,          None),
]

# Sensors computed from others rather than read straight from a register.
DERIVED = {
    "pv_power": ("pv1_power", "pv2_power"),  # PV3 deliberately not included, see EXCLUDED
}

# Status register 0 is a bitfield. Only code 20 has been observed on this unit; the
# rest come from the community map and are NOT verified here. Treat an unknown code as
# "Unknown (<n>)" rather than guessing.
STATUS_CODES = {
    0:   "Standby",
    1:   "Fault",
    2:   "Programming",
    4:   "PV on grid",
    8:   "PV charge",
    12:  "PV charge, on grid",
    16:  "Battery on grid",
    20:  "PV and battery on grid",   # <- observed 2026-10-18, matches app
    32:  "AC charge",
    40:  "PV and AC charge",
    64:  "Battery off grid",
    68:  "PV off grid",
    72:  "PV charge off grid",
}

# Registers the lxp-bridge map defines that are wrong or unpopulated on THIS unit.
# Do not re-add without fresh evidence from the app.
EXCLUDED = {
    3:   "'PV3 voltage': reads 419-421 V at 0 W and tracks the DC bus (regs 38/39). "
         "Not a real third string -- this unit has two MPPTs.",
    9:   "'PV3 power': always 0, no third string.",
    13:  "'Grid voltage phase S': unused on a single-phase unit.",
    14:  "'Grid voltage phase T': unused, reads ~204.9 V garbage.",
    19:  "'Power factor': raw 0 while exporting 2.2 kW. Not PF on this firmware. "
         "Use reg 18 (inverter current) instead.",
    21:  "'EPS voltage phase S': unused, reads ~513.6 V garbage.",
    22:  "'EPS voltage phase T': unused, reads ~418.6 V garbage.",
    30:  "'PV3 today': no third string.",
    44:  "'PV3 total': no third string.",
    67:  "'Battery temp': mirrors radiator-2 exactly in every sample, on a battery idle "
         "at 100% SOC. A real 73 degC battery would be in BMS protection.",
}

# Register 5's high byte is documented as battery SOH but is never set on this unit.
EXCLUDED_FIELDS = {
    "battery_soh": "High byte of reg 5, always 0 -- not reported by this BMS.",
}

# The whole 80-119 block does not match the lxp-bridge ReadInputs3 map here:
# regs 82/83 give 50.0 V and 56.5 V only if charge-reference and discharge-cutoff are
# swapped versus the documented order, and regs 90-93 (max/min cell voltage and
# temperature) read 12912 / 43967 / 782 / 24800 -- nonsense at any scale factor.
# Per-cell data needs its own reverse-engineering pass before it can be trusted.
BMS_BLOCK_UNVERIFIED = range(80, 120)


# ------------------------------------------------------------------- decoding --

def decode(regs: dict) -> dict:
    """Turn a {register: raw_value} dict into {sensor_key: scaled_value}.

    Only sensors whose registers are present are included, so a partial read (one
    block only) decodes fine.
    """
    out = {}
    for key, _label, reg, kind, scale, _unit, _dc, _sc in SENSORS:
        if kind == "calc":
            continue
        if reg not in regs:
            continue
        if kind == "u32":
            if reg + 1 not in regs:
                continue
            raw = u32(regs, reg)
        elif kind == "s16":
            raw = s16(regs[reg])
        elif kind == "low8":
            raw = regs[reg] & 0xFF
        else:
            raw = regs[reg]
        out[key] = raw / scale if scale != 1 else raw

    for key, parts in DERIVED.items():
        if all(p in out for p in parts):
            out[key] = sum(out[p] for p in parts)

    return out


def status_text(code: int) -> str:
    return STATUS_CODES.get(code, f"Unknown ({code})")


if __name__ == "__main__":
    # Self-check against the verified 2026-10-18 sample.
    sample = {
        0: 20, 1: 2583, 2: 0, 4: 540, 5: 100, 7: 2407, 8: 0, 10: 0, 11: 0,
        12: 2427, 15: 5013, 16: 2263, 17: 0, 18: 950, 20: 2421, 23: 5014,
        24: 0, 26: 0, 27: 0, 28: 141, 31: 85, 37: 104, 38: 4171, 39: 3754,
        40: 40102, 41: 1, 58: 62944, 59: 1, 64: 47, 65: 54, 66: 73,
    }
    values = decode(sample)
    expected = {
        "pv1_voltage": 258.3, "pv1_power": 2407, "pv_power": 2407,
        "battery_voltage": 54.0, "battery_soc": 100, "grid_voltage": 242.7,
        "grid_frequency": 50.13, "inverter_power": 2263, "inverter_current": 9.5,
        "pv1_total": 10563.8, "imported_total": 12848.0, "radiator2_temp": 73,
    }
    bad = [(k, values.get(k), v) for k, v in expected.items()
           if round(values.get(k, -1), 2) != v]
    print(f"status: {status_text(sample[0])}")
    print(f"{len(values)} sensors decoded from {len(sample)} registers")
    if bad:
        for k, got, want in bad:
            print(f"  MISMATCH {k}: got {got}, want {want}")
        raise SystemExit(1)
    print("self-check OK")
