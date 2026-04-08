# Raspberry Pi Oxygen Monitor

This project provides a single-file Python 3 application for a Raspberry Pi 3 that:

- reads oxygen concentration from a DFRobot Gravity SEN0322 over I2C
- exposes the value through a Modbus TCP server on port `5020`
- renders a simple status screen to a framebuffer-backed SPI TFT

The main application file is `oxygen_monitor.py`.

## Files

- `oxygen_monitor.py`: main application
- `requirements.txt`: Python dependencies
- `install.sh`: automated installer for Raspberry Pi
- `update.sh`: update script for code and dependencies
- `oxygen-monitor.service`: systemd service template

## Hardware Notes

- Oxygen sensor: DFRobot Gravity `SEN0322`
- Default I2C address: `0x73`
- Display: 3.5 inch ILI9486-compatible SPI TFT configured as a Linux framebuffer, typically `/dev/fb1`

The script assumes the TFT is already working as a framebuffer device. For many Raspberry Pi display driver stacks, that means `/dev/fb1` at `480x320`.

## Enable I2C and SPI

Use `raspi-config`:

```bash
sudo raspi-config
```

Then enable:

1. `Interface Options` -> `I2C` -> `Yes`
2. `Interface Options` -> `SPI` -> `Yes`

Reboot after enabling them:

```bash
sudo reboot
```

`install.sh` also tries to enable `I2C` and `SPI` automatically using `raspi-config nonint` when available.

## Verify Devices

Check that the sensor appears on I2C bus 1:

```bash
sudo apt update
sudo apt install -y i2c-tools
i2cdetect -y 1
```

You should normally see the SEN0322 at `0x73`.

Check that the display driver created a framebuffer:

```bash
ls -l /dev/fb*
fbset -fb /dev/fb1
```

If `/dev/fb1` does not exist yet, install and configure the correct SPI TFT driver or overlay for your specific 3.5 inch ILI9486-compatible panel first.

## Installation

Quick install on Raspberry Pi:

```bash
cd /home/pi/detecciondegases
chmod +x install.sh
sudo ./install.sh
```

The installer:

- installs OS packages
- enables `I2C` and `SPI` automatically when possible
- adds the runtime user to `i2c`, `spi`, and `video`
- copies the app to `/opt/oxygen-monitor`
- creates the virtual environment
- installs dependencies
- writes the `systemd` service
- enables and starts the service

Useful installer overrides:

```bash
sudo APP_USER=pi INSTALL_DIR=/opt/oxygen-monitor FRAMEBUFFER=/dev/fb1 WIDTH=480 HEIGHT=320 ROTATE=0 ./install.sh
sudo APP_USER=pi I2C_ADDRESS=0x73 MODBUS_PORT=5020 ./install.sh
sudo ENABLE_INTERFACES=0 ./install.sh
```

If this is the first time you enabled `I2C` or `SPI`, reboot after installation:

```bash
sudo reboot
```

## Update Existing Installation

For later code changes, use:

```bash
cd /home/pi/detecciondegases
chmod +x update.sh
sudo ./update.sh
```

This updates:

- `oxygen_monitor.py`
- `requirements.txt`
- Python packages in `.venv`
- the `systemd` unit
- the running service via restart

Useful updater overrides:

```bash
sudo FRAMEBUFFER=/dev/fb1 ROTATE=90 ./update.sh
sudo I2C_ADDRESS=0x73 MODBUS_PORT=5020 ./update.sh
```

Manual package installation:

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv python3-dev libjpeg-dev libopenjp2-7 zlib1g-dev
```

Create a virtual environment:

```bash
mkdir -p /home/pi/oxygen-monitor
cd /home/pi/oxygen-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Run Manually

```bash
cd /home/pi/oxygen-monitor
source .venv/bin/activate
python3 oxygen_monitor.py
```

Useful options:

```bash
python3 oxygen_monitor.py --framebuffer /dev/fb1 --width 480 --height 320 --rotate 0
python3 oxygen_monitor.py --i2c-address 0x73 --modbus-port 5020
```

## Modbus Mapping

- Holding register address: `0`
- Value format: oxygen percent multiplied by 10
- Example: `20.9%` is exposed as `209`

## Startup with systemd

Template file included in the repository:

```ini
[Unit]
Description=Raspberry Pi Oxygen Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/oxygen-monitor
ExecStart=/home/pi/oxygen-monitor/.venv/bin/python /home/pi/oxygen-monitor/oxygen_monitor.py --framebuffer /dev/fb1 --width 480 --height 320
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

If you use `install.sh`, this is done automatically. For manual setup, copy and enable it:

```bash
sudo cp oxygen-monitor.service /etc/systemd/system/oxygen-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable oxygen-monitor.service
sudo systemctl start oxygen-monitor.service
```

Check logs:

```bash
sudo journalctl -u oxygen-monitor.service -f
```

## Behavior

- Sensor thread reads oxygen every second
- Modbus thread serves holding registers over TCP
- Display thread redraws the screen every second
- Sensor failures are logged and shown on screen without crashing the process

## Display Colors

- Black background
- Green text for `NORMAL`
- Red text for `LOW`, `HIGH`, or sensor fault

## Notes

- The SEN0322 needs time to stabilize after power-up.
- If the sensor is disconnected, the Modbus holding register is set to `0` until readings recover.
- If your display orientation is wrong, change `--rotate` to `90`, `180`, or `270`.
