# GasMonitor for Raspberry Pi 3

GasMonitor is a modular gas monitoring service for Raspberry Pi 3 with:

- oxygen sensing through DFRobot SEN0322 over I2C
- multi-gas support for MICS-6814/CJMCU through ADS1115 over I2C
- Modbus TCP server
- FastAPI web dashboard for monitoring and configuration
- framebuffer-driven 3.5 inch SPI display
- persistent `config.ini`
- first-run commissioning mode
- structured rotating logs in `logs/system.log`

## Project Structure

```text
.
├── main.py
├── core.py
├── config.py
├── shared_state.py
├── logging_utils.py
├── auth.py
├── config.ini
├── modbus_server.py
├── register_map.py
├── sensors/
│   ├── oxygen.py
│   └── mics6814.py
├── display/
│   └── display.py
├── web/
│   ├── index.html
│   └── app.js
├── docs/
│   └── modbus_register_map.md
├── tools/
│   └── modbus_client.py
├── gasmonitor.service
├── install.sh
├── update.sh
└── requirements.txt
```

## Configuration

The installed service stores runtime configuration at `/var/lib/gasmonitor/config.ini` so touchscreen and web changes are not overwritten by Git updates. The repository `config.ini` is only the installation template. Main sections:

- `[hardware]`: I2C, MICS path, framebuffer, display geometry
- `[network]`: DHCP/static network profile values
- `[web]`: dashboard port and credentials
- `[modbus]`: enable flag, TCP host/port, read-only policy, client limits, debug, whitelist
- `[sampling]`: moving-average depth and loop interval
- `[calibration]`: gas calibration multipliers
- `[alarms]`: threshold configuration
- `[system]`: first-run state, device name, log file

On first boot:

- `first_run = true` keeps the system in configuration mode
- the display shows the current IP
- the dashboard forces a password change before first-run can be cleared

## Modbus Register Map

GasMonitor exposes an industrial Modbus TCP map compatible with Ignition, Node-RED, and Modbus Poll. Use function code `03` to read holding registers.

| SCADA Address | Request Offset | Description | Format |
| --- | ---: | --- | --- |
| `40001` | `0` | Oxygen (%) x10 | `UINT16` |
| `40002` | `1` | CO (ppm) | `UINT16` |
| `40003` | `2` | NO2 (ppm x100) | `UINT16` |
| `40004` | `3` | NH3 (ppm) | `UINT16` |
| `40005` | `4` | Device status | `UINT16` |
| `40006` | `5` | Alarm status bitmask | `UINT16` |
| `40007` | `6` | System state | `UINT16` |
| `40008` | `7` | Error code | `UINT16` |

Control registers are available only when `[modbus] read_only = false`:

| SCADA Address | Request Offset | Function |
| --- | ---: | --- |
| `40100` | `99` | Reset alarms |
| `40101` | `100` | Reboot device |
| `40102` | `101` | Force calibration hook |

Default Modbus TCP port is `5020`.

Full SCADA documentation is in [`docs/modbus_register_map.md`](docs/modbus_register_map.md).

Quick test:

```bash
.venv/bin/python tools/modbus_client.py --host 127.0.0.1 --port 5020
```

## Web API

- `POST /login`
- `GET /api/measurements`
- `GET /api/config`
- `POST /api/config`
- `POST /api/reboot`

The dashboard serves at `/` and static assets are under `/web`.

## Install on Raspberry Pi

Install the MHS-3.5inch framebuffer driver first if `/dev/fb1` is not present:

```bash
git clone https://github.com/goodtft/LCD-show.git
chmod -R 755 LCD-show
cd LCD-show
sudo ./MHS35-show
```

The driver install reboots the Raspberry Pi. After it comes back:

```bash
cd /opt
sudo git clone https://github.com/zamoranodizaPi/detecciondegases.git gasmonitor
cd /opt/gasmonitor
sudo ./install.sh
```

The installer:

- installs required OS packages
- enables I2C and SPI when `raspi-config` is present
- adds the runtime user to `i2c`, `spi`, `video`, and `input`
- installs the generic 3.5 inch SPI LCD driver from `goodtft/LCD-show` when `/dev/fb1` is not present
- applies the known working touchscreen mapping: `touch_swap_xy=true`, `touch_invert_x=true`, `touch_invert_y=true`
- stops the existing `gasmonitor.service` when present
- backs up existing config/service files under `/var/backups/gasmonitor/<timestamp>`
- copies the application to `/opt/gasmonitor`
- stores editable runtime configuration in `/var/lib/gasmonitor/config.ini`
- migrates legacy `/opt/gasmonitor/config.ini` into `/var/lib/gasmonitor/config.ini` when no runtime config exists
- validates and auto-repairs missing or invalid config keys
- creates `.venv`
- installs Python dependencies
- installs `gasmonitor.service`
- starts the service

LCD driver options:

```bash
# Default: install only if /dev/fb1 is missing
sudo ./install.sh

# Force reinstall of the generic LCD35 driver
sudo INSTALL_LCD_DRIVER=1 ./install.sh

# Skip LCD driver install
sudo INSTALL_LCD_DRIVER=0 ./install.sh

# Override driver script or rotation if a different board needs it
sudo LCD_DRIVER_SCRIPT=LCD35-show LCD_ROTATION=0 ./install.sh
```

The known working display defaults are:

- Driver script: `LCD35-show`
- Framebuffer: `/dev/fb1`
- App geometry: `320x480`
- App rotation: `0`
- Touch: `swap_xy=true`, `invert_x=true`, `invert_y=true`

The LCD driver script may reboot the Raspberry Pi. The installer enables the service before running the LCD driver so `gasmonitor.service` starts after reboot.

## Migration to a New Raspberry Pi

On a clean system:

```bash
cd /opt
sudo git clone https://github.com/zamoranodizaPi/detecciondegases.git gasmonitor
cd /opt/gasmonitor
sudo ./install.sh
```

To migrate an existing configuration, copy your old runtime file before running the installer:

```bash
sudo mkdir -p /var/lib/gasmonitor
sudo cp config.ini /var/lib/gasmonitor/config.ini
sudo ./install.sh
```

After installation, user-editable settings must live in `/var/lib/gasmonitor/config.ini`. The repository `config.ini` remains only as the default template so Git updates do not overwrite field settings.

## Manual Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py --config config.ini
```

## Hardware Notes

- `mock_sensors = true` disables physical sensor reads and shows simulated measurements. Set it to `false` when SEN0322/ADS1115 hardware is connected.
- MHS-3.5inch RPi Display uses SPI for LCD and touch: GPIO17, GPIO24, GPIO10, GPIO9, GPIO25, GPIO11, GPIO8, and GPIO7.
- The MHS page marks physical pins 3 and 5 as NC, so sensors stay on the normal Raspberry Pi I2C bus:
  - SDA: physical pin 3 / GPIO2
  - SCL: physical pin 5 / GPIO3
  - `i2c_bus = 1`, exposed as `/dev/i2c-1`
- Keep sensor power on 3.3V/GND. Do not power I2C pullups from 5V.
- SEN0322 oxygen sensor defaults to I2C address `0x73`
- ADS1115 defaults to I2C address `0x48`
- MICS-6814/CJMCU channels are mapped as CO -> ADS1115 A0, NH3 -> ADS1115 A1, NO2/OX -> ADS1115 A2
- the SPI TFT must already be exposed as a framebuffer such as `/dev/fb1`

If your ADS1115 address is different, update `mics_address` in `config.ini`.

## Touchscreen UI

The framebuffer display is designed for portrait `320x480`.

- Header: device name, status, time
- Measurement panels: oxygen, CO, NH3, NO2
- Footer: IP address and touch menu button
- Colors: green for normal, yellow for warning, red for alarm, gray for inactive or sensor fault

The touch menu can edit the same commissioning values exposed in the web dashboard:

- system device name
- web port, username, and password
- network mode, static IP, gateway, and DNS
- oxygen and CO alarm thresholds

## Logs and Service

Logs are written to `logs/system.log` and to `journald`.

Useful commands:

```bash
sudo systemctl status gasmonitor.service
sudo journalctl -u gasmonitor.service -f
```
