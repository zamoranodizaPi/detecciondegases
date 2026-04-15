#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from pymodbus.client import ModbusTcpClient


@dataclass(frozen=True)
class DecodedRegisters:
    oxygen_percent: float
    co_ppm: int
    no2_ppm: float
    nh3_ppm: int
    device_status: int
    alarm_status: int
    system_state: int
    error_code: int


DEVICE_STATUS = {
    0: "OK",
    1: "WARNING",
    2: "ALARM",
    3: "SENSOR FAULT",
}

SYSTEM_STATE = {
    0: "BOOT",
    1: "WARMUP",
    2: "NORMAL",
    3: "WARNING",
    4: "ALARM",
    5: "ERROR",
}

ERROR_CODES = {
    0: "NONE",
    1: "SENSOR FAILURE",
    2: "WATCHDOG TIMEOUT",
    3: "INVALID READING",
}


def read_holding_registers(client: ModbusTcpClient, address: int, count: int, unit: int):
    try:
        return client.read_holding_registers(address=address, count=count, slave=unit)
    except TypeError:
        try:
            return client.read_holding_registers(address=address, count=count, unit=unit)
        except TypeError:
            return client.read_holding_registers(address, count)


def decode(registers: list[int]) -> DecodedRegisters:
    if len(registers) < 8:
        raise ValueError("expected at least 8 holding registers")
    return DecodedRegisters(
        oxygen_percent=registers[0] / 10.0,
        co_ppm=registers[1],
        no2_ppm=registers[2] / 100.0,
        nh3_ppm=registers[3],
        device_status=registers[4],
        alarm_status=registers[5],
        system_state=registers[6],
        error_code=registers[7],
    )


def alarm_labels(mask: int) -> list[str]:
    labels = []
    if mask & 1:
        labels.append("OXYGEN LOW")
    if mask & 2:
        labels.append("OXYGEN HIGH")
    if mask & 4:
        labels.append("CO HIGH")
    if mask & 8:
        labels.append("SENSOR FAILURE")
    return labels or ["NONE"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read GasMonitor Modbus TCP registers.")
    parser.add_argument("--host", default="127.0.0.1", help="Modbus TCP host")
    parser.add_argument("--port", default=5020, type=int, help="Modbus TCP port")
    parser.add_argument("--unit", default=1, type=int, help="Modbus unit/slave id")
    parser.add_argument("--address", default=0, type=int, help="zero-based holding register address")
    parser.add_argument("--count", default=8, type=int, help="number of holding registers")
    args = parser.parse_args()

    client = ModbusTcpClient(args.host, port=args.port)
    if not client.connect():
        print(f"connection failed: {args.host}:{args.port}", file=sys.stderr)
        return 2

    try:
        response = read_holding_registers(client, args.address, args.count, args.unit)
        if response.isError():
            print(f"modbus error: {response}", file=sys.stderr)
            return 3
        registers = list(response.registers)
        print(f"raw registers @{args.address}: {registers}")
        if args.address == 0 and len(registers) >= 8:
            decoded = decode(registers)
            print(f"oxygen: {decoded.oxygen_percent:.1f} %")
            print(f"co: {decoded.co_ppm} ppm")
            print(f"no2: {decoded.no2_ppm:.2f} ppm")
            print(f"nh3: {decoded.nh3_ppm} ppm")
            print(f"device_status: {decoded.device_status} ({DEVICE_STATUS.get(decoded.device_status, 'UNKNOWN')})")
            print(f"alarm_status: {decoded.alarm_status} ({', '.join(alarm_labels(decoded.alarm_status))})")
            print(f"system_state: {decoded.system_state} ({SYSTEM_STATE.get(decoded.system_state, 'UNKNOWN')})")
            print(f"error_code: {decoded.error_code} ({ERROR_CODES.get(decoded.error_code, 'UNKNOWN')})")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
