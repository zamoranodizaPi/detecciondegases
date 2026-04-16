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
- `[display]`: brightness, dark theme, local HMI inactivity timeout
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
- enables SPI and configures a sensor I2C GPIO bus on GPIO20/GPIO21
- configures real sensor mode by default: `mock_sensors=false`
- adds the runtime user to `i2c`, `spi`, `video`, and `input`
- installs the generic 3.5 inch SPI LCD driver from `goodtft/LCD-show` when `/dev/fb1` is not present
- applies the touchscreen transform through one central mapping layer: `touch_rotation=90`, `touch_swap_xy=true`, `touch_invert_x=false`, `touch_invert_y=true`
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

# Override the sensor I2C GPIO bus if needed
sudo I2C_BUS=3 I2C_SDA_GPIO=20 I2C_SCL_GPIO=21 ./install.sh

# Install in demo mode if sensors are not connected
sudo MOCK_SENSORS=true ./install.sh
```

The known working display defaults are:

- Driver script: `LCD35-show`
- Framebuffer: `/dev/fb1`
- App geometry: `320x480`
- App rotation: `0`
- Touch: `rotation=90`, `swap_xy=true`, `invert_x=false`, `invert_y=true`

Touch debug can be enabled in `/var/lib/gasmonitor/config.ini`:

```ini
[hardware]
touch_debug = true
```

When enabled, the display draws the mapped touch point and the service logs:

```text
touch RAW=(x,y) MAPPED=(x,y)
BUTTON HIT: true/false
```

Touch calibration is available from the local display:

```text
MENU -> CAL TOUCH
```

On a new install, if no calibration is saved yet, the service opens this calibration screen automatically before the normal HMI. The screen presents 10 targets. Touch the center of each target once. The system stores an affine RAW-to-UI calibration in:

```ini
[hardware]
touch_calibration = ax,bx,cx,ay,by,cy
```

After calibration, every touch event uses the saved calibration before button hit testing. To force calibration again without relying on the menu:

```bash
sudo sed -i 's|touch_force_calibration = .*|touch_force_calibration = true|' /var/lib/gasmonitor/config.ini
sudo systemctl restart gasmonitor.service
```

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

- `mock_sensors = false` enables physical sensor reads. Set it to `true` only for demo mode without sensors.
- MHS-3.5inch RPi Display uses SPI for LCD and touch: GPIO17, GPIO24, GPIO10, GPIO9, GPIO25, GPIO11, GPIO8, and GPIO7.
- Sensor I2C is moved away from physical pins 3 and 5 because the 3.5 inch display blocks that area:
  - SDA: physical pin 38 / GPIO20
  - SCL: physical pin 40 / GPIO21
  - `i2c_bus = 3`, exposed as `/dev/i2c-3`
  - installer overlay: `dtoverlay=i2c-gpio,bus=3,i2c_gpio_sda=20,i2c_gpio_scl=21`
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
