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
├── sensors/
│   ├── oxygen.py
│   └── mics6814.py
├── display/
│   └── display.py
├── web/
│   ├── index.html
│   └── app.js
├── gasmonitor.service
├── install.sh
├── update.sh
└── requirements.txt
```

## Configuration

The service auto-creates `config.ini` if it does not exist. Main sections:

- `[hardware]`: I2C, MICS path, framebuffer, display geometry
- `[network]`: DHCP/static network profile values
- `[web]`: dashboard port and credentials
- `[modbus]`: enable flag and TCP port
- `[sampling]`: moving-average depth and loop interval
- `[calibration]`: gas calibration multipliers
- `[alarms]`: threshold configuration
- `[system]`: first-run state, device name, log file

On first boot:

- `first_run = true` keeps the system in configuration mode
- the display shows the current IP
- the dashboard forces a password change before first-run can be cleared

## Modbus Register Map

- `0`: oxygen percent x10
- `1`: CO ppm x10
- `2`: NO2 ppm x10
- `3`: NH3 ppm x10

Default Modbus TCP port is `5020`.

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
- copies the application to `/opt/gasmonitor`
- creates `.venv`
- installs Python dependencies
- installs `gasmonitor.service`
- starts the service

## Manual Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py --config config.ini
```

## Hardware Notes

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

The framebuffer display is designed for landscape `480x320`.

- First row: Gas Monitor title, branding icon, and status
- Measurement rows: CO, Oxygen, NH3, NO2
- Bottom row: IP address and touch menu button
- Colors: green for normal, yellow for attention, orange for warning, red for alarm/danger

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
