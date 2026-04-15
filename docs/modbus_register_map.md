# GasMonitor Modbus TCP Register Map

GasMonitor exposes a Modbus TCP server intended for SCADA polling. The default endpoint is:

- Host: `0.0.0.0`
- Port: `5020`
- Unit ID: `1`
- Encoding: unsigned 16-bit registers, big-endian Modbus standard
- Update interval: `1 s`
- Default write policy: read-only

Most SCADA tools display holding registers as `4xxxx` addresses. Internally, Modbus requests use zero-based offsets, so `40001` is request address `0`.

## Holding Registers

| SCADA Address | Request Offset | Description | Format | Engineering Value |
| --- | ---: | --- | --- | --- |
| `40001` | `0` | Oxygen | `UINT16` | value / 10 = `%` |
| `40002` | `1` | CO | `UINT16` | `ppm` |
| `40003` | `2` | NO2 | `UINT16` | value / 100 = `ppm` |
| `40004` | `3` | NH3 | `UINT16` | `ppm` |
| `40005` | `4` | Device status | `UINT16` | status code |
| `40006` | `5` | Alarm status | `UINT16` | bitmask |
| `40007` | `6` | System state | `UINT16` | state code |
| `40008` | `7` | Error code | `UINT16` | error code |

## Device Status

| Code | Meaning |
| ---: | --- |
| `0` | OK |
| `1` | Warning |
| `2` | Alarm |
| `3` | Sensor fault |

## Alarm Status Bitmask

| Bit | Mask | Meaning |
| ---: | ---: | --- |
| `0` | `1` | Oxygen low |
| `1` | `2` | Oxygen high |
| `2` | `4` | CO high |
| `3` | `8` | Sensor failure |

Example: `40006 = 5` means oxygen low (`1`) and CO high (`4`).

## System State

| Code | Meaning |
| ---: | --- |
| `0` | BOOT |
| `1` | WARMUP |
| `2` | NORMAL |
| `3` | WARNING |
| `4` | ALARM |
| `5` | ERROR / SENSOR_ERROR |

## Error Code

| Code | Meaning |
| ---: | --- |
| `0` | No error |
| `1` | Sensor failure |
| `2` | Watchdog timeout |
| `3` | Invalid reading |

## Control Registers

Control writes are disabled while `[modbus] read_only = true`. To enable controls, set `read_only = false` in the runtime configuration and restart the service.

Write `1` to execute a command. Other values are rejected and logged.

| SCADA Address | Request Offset | Function |
| --- | ---: | --- |
| `40100` | `99` | Reset latched alarm/fault hooks |
| `40101` | `100` | Reboot device |
| `40102` | `101` | Force calibration hook |

## Configuration

Runtime configuration is stored at `/var/lib/gasmonitor/config.ini` after installation. Repository `config.ini` is the template.

```ini
[modbus]
enabled = true
host = 0.0.0.0
port = 5020
max_clients = 5
timeout = 10
read_only = true
whitelist =
debug = false
```

`whitelist` accepts comma-separated IP addresses or CIDR ranges for audit logging, for example:

```ini
whitelist = 192.168.1.20,192.168.1.0/24
```

## Validation

From the Raspberry Pi:

```bash
cd /opt/gasmonitor
.venv/bin/python tools/modbus_client.py --host 127.0.0.1 --port 5020
```

From a SCADA client:

- Function: Read Holding Registers
- Start address: `0`
- Count: `8`
- Unit ID: `1`
- Poll rate: `1000 ms` or slower
