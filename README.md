# LuxPower Inverter — Home Assistant integration

A **read-only** native Home Assistant integration for LuxPower hybrid inverters,
talking directly to the WiFi/LAN dongle over TCP on your own network. No cloud,
no LuxPower account, no MQTT broker.

Config flow, a single shared connection, and 46 sensors built from a register map
that was verified value-by-value against the LuxPower app.

> **Status:** works against the author's inverter, but has not been widely
> tested. Treat the register map as verified for one single-phase hybrid unit and
> check the values against the app on yours. Issues and reports from other models
> are welcome.

## Read-only, on purpose

Only Modbus read functions are implemented — `0x03` (read holding) and `0x04`
(read input). `protocol.build_read()` raises `ValueError` for anything else, so a
write function cannot be constructed through it by accident, and that guard is
covered by a test. Nothing here changes an inverter setting.

If write support is ever added it should be a conscious, separately reviewed
change — a mistake in an inverter holding register can alter how your house is
powered.

## Install

### HACS (custom repository)

1. HACS → Integrations → ⋮ → **Custom repositories**.
2. Add `https://github.com/murendie/luxpower-ha`, category **Integration**.
3. Install, then restart Home Assistant.

### Manual

1. Copy `custom_components/luxpower/` into your config directory so you end up
   with `<config>/custom_components/luxpower/manifest.json`.
2. Restart Home Assistant.

Then: **Settings → Devices & services → Add integration → LuxPower Inverter**.

## Setting it up

You need the dongle's IP, its port (`8000`) and two serials.

**The dongle serial is the one that matters.** It is printed on the dongle's
sticker — 10 characters, usually starting `BA`. Enter that, choose *Detect the
inverter serial*, and the inverter serial is read straight off the inverter.

That works because of a quirk worth knowing: the dongle **ignores the inverter
serial** in a request — blank, zero-filled and plainly wrong values all get
answered — while it **does validate its own serial**. So the inverter serial can
simply be read out of input registers 115–119, where it sits as little-endian
ASCII byte pairs. A wrong dongle serial, by contrast, gets no reply at all. If
detection fails, that is the field to check.

If you don't have the sticker, leave the dongle serial blank and detection falls
back to listening for the dongle to announce itself. Treat that as a coin flip:
the dongle volunteers heartbeats on its own schedule and in testing once said
nothing at all for 30 seconds. The reliable fallback is the bundled probe:

```bash
python3 lux_read.py --listen 90
```

Polling interval is configurable per entry (**Configure**), default 30 s.

## The probe script

`lux_read.py` is a standalone, dependency-free, read-only probe — useful for
getting the serials, for sanity-checking the register map against your own app
before trusting the integration, and for debugging:

```bash
python3 lux_read.py --listen 90                      # learn the serials
python3 lux_read.py --dongle BA12345678 --inverter 1234567890 --raw
```

## How it talks to the dongle

Two dongle behaviours shape the design:

- **One client at a time.** The coordinator holds a single connection open for
  the lifetime of the config entry rather than connecting per poll, and the
  config flow always closes its validating connection before setup opens its
  own. If a read fails, the socket is dropped so the next cycle reconnects
  cleanly — a wedged dongle keeps answering TCP while refusing reads.
- **Heartbeats must be echoed.** A background reader task echoes the dongle's
  `0xC1` frames even when no read is pending, otherwise it hangs up.

The dongle also pushes unsolicited register banks. Those are logged and dropped:
their layout does not match the polled-read layout and has not been verified, so
polled reads are the only path into state. Read waiters are keyed on
`(function, start, count)` precisely so a push frame starting at the same
register cannot satisfy a pending read.

## What is verified, and what is not

Verified against the hardware:

- Framing, CRC16-Modbus and register alignment — regs 115–119 carry the inverter
  serial as little-endian ASCII byte pairs, which only lines up if the offset and
  endianness are right.
- The decoded values matched the LuxPower app.
- Reg 18 cross-checks as inverter RMS current ÷100 against `p_inv ÷ v_ac`, in two
  different operating modes.
- Energy balance closes to ~4% conversion loss (PV + battery discharge versus
  inverter output).
- The async client: repeated polls on one connection, reconnect after a forced
  disconnect, push frames not corrupting reads, and write functions refused.
- `read_inverter_serial()` reading the real serial live, returning `None` for a
  wrong dongle serial, raising for an unreachable host, and leaving the dongle
  free afterwards.

**Not verified:** this has not been exercised against every Home Assistant
version. `coordinator.py` deliberately does not pass `config_entry` to
`DataUpdateCoordinator`, since that parameter only exists on newer HA releases;
`DeviceInfo(serial_number=...)` needs HA ≥ 2023.11.

## Registers deliberately left out

`registers.py` carries an `EXCLUDED` dict naming every register the community
[lxp-bridge](https://github.com/celsworth/lxp-bridge) map defines that decodes
wrongly on this hardware, each with its reason:

| register | documented as | what it actually does |
|---|---|---|
| 3, 9, 30, 44 | PV3 voltage / power / energy | no third string; the voltage tracks the DC bus at 0 W |
| 5 (high byte) | battery SOH | never set, always 0 |
| 19 | power factor | reads 0 while exporting 2.2 kW — use reg 18 (current) |
| 67 | battery temperature | mirrors radiator 2 exactly, on an idle battery |
| 13, 14, 21, 22 | grid/EPS phases S and T | unused on a single-phase unit, hold garbage |
| 80–119 | BMS / per-cell data | does not match the documented layout at all |

Don't re-add any of these without fresh evidence from your own app. If they
decode correctly on your model, that's a useful issue to open.

## Entity naming

Entity IDs are deterministic, and dashboards depend on them: with
`has_entity_name` and a device named `LuxPower`, each description's `name` (the
map's label) yields `sensor.luxpower_<slug(label)>` — for example
`sensor.luxpower_pv_total_power`. Swapping `name` for `translation_key`, or
renaming a label, renames entities and breaks existing dashboards.

Three cases where the internal key and the entity ID differ:

| key | entity ID |
|---|---|
| `inverter_power` | `sensor.luxpower_inverter_output_power` |
| `radiator2_temp` | `sensor.luxpower_radiator_2_temp` |
| `bus1_voltage` | `sensor.luxpower_dc_bus_1_voltage` |

`status` is a text state, and `fault_code` / `warning_code` are hex strings — so
none of the three belong in a numeric chart or a `numeric_state` condition.

## Licence

MIT. Not affiliated with or endorsed by LuxPower.
