#!/usr/bin/env python3
"""Command-line interface for Burst Control on Linux."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from burstcontrol.protocol import (
    LIFT_OFF_MODES,
    POLLING_RATES,
    HIDConnection,
    MockHIDConnection,
    MouseError,
    MouseService,
)


def parse_stage_dpi(spec: str) -> tuple[int, int]:
    if ":" not in spec:
        raise argparse.ArgumentTypeError("Expected STAGE:DPI (e.g. 1:800)")
    stage_s, dpi_s = spec.split(":", 1)
    stage = int(stage_s)
    dpi = int(dpi_s)
    if not (1 <= stage <= 5):
        raise argparse.ArgumentTypeError("Stage must be 1..5")
    if not (50 <= dpi <= 26000 and dpi % 50 == 0):
        raise argparse.ArgumentTypeError("DPI must be 50..26000 in steps of 50")
    return stage - 1, dpi


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure Turtle Beach Burst II Air on Linux")
    parser.add_argument("--mock", action="store_true", help="Use simulated mouse backed by Tests/Fixtures/settings.json")
    parser.add_argument("--read", action="store_true", help="Print full raw settings JSON")
    parser.add_argument("--status", action="store_true", help="Print battery and transport status JSON")
    parser.add_argument("--verify-writes", action="store_true", help="Run non-destructive round-trip write check")
    parser.add_argument("--dpi", type=parse_stage_dpi, action="append", metavar="STAGE:DPI", help="Set DPI for stage 1..5 (e.g. --dpi 2:1600)")
    parser.add_argument("--polling", type=int, choices=POLLING_RATES, help="Set polling rate in Hz")
    parser.add_argument("--debounce", type=int, choices=range(11), metavar="0..10", help="Set click debounce in ms")
    parser.add_argument("--lod", choices=("very-low", "low"), help="Set lift-off distance")
    parser.add_argument("--motion-sync", choices=("on", "off"), help="Enable or disable Motion Sync")
    parser.add_argument("--angle-snapping", choices=("on", "off"), help="Enable or disable Angle Snapping")
    parser.add_argument("--gui", action="store_true", help="Launch the GTK4 desktop interface")

    args = parser.parse_args(argv)

    if args.gui:
        from burstcontrol.gui import run_gui
        return run_gui(mock=args.mock)

    if args.mock:
        mock_conn = MockHIDConnection()
        service = MouseService(connection_factory=lambda **kw: mock_conn)
    else:
        service = MouseService(connection_factory=HIDConnection)

    try:
        if args.status:
            telem = service.telemetry()
            print(json.dumps(telem.to_dict(), indent=2))
            return 0

        if args.verify_writes:
            verified = service.verify_writes()
            print(json.dumps(verified.to_dict(), indent=2, sort_keys=True))
            return 0

        has_mutations = any(
            x is not None
            for x in (args.dpi, args.polling, args.debounce, args.lod, args.motion_sync, args.angle_snapping)
        )

        if args.read and not has_mutations:
            settings = service.read()
            print(json.dumps(settings.to_dict(), indent=2, sort_keys=True))
            return 0

        current, telem = service.read_live()
        if not has_mutations:
            print(f"Device: Burst II Air ({telem.transport})")
            if telem.battery_percent is not None:
                charge_str = " (Charging)" if telem.charging else ""
                print(f"Battery: {telem.battery_percent}%{charge_str}")
            print(f"Profile: {current.profile_index + 1} of {current.profile[1]}")
            stages = []
            for idx in range(5):
                marker = "*" if current.active_stage == idx else " "
                stages.append(f"  {marker} Stage {idx + 1}: {current.dpi(idx)} DPI")
            print("DPI Stages:\n" + "\n".join(stages))
            print(f"Polling Rate: {current.polling_hz} Hz")
            print(f"Debounce: {current.debounce_ms} ms")
            print(f"Lift-Off Distance: {LIFT_OFF_MODES.get(current.lift_off_mode, 'Custom')}")
            print(f"Angle Snapping: {'On' if current.angle_snapping_enabled else 'Off'}")
            print(f"Motion Sync: {'On' if current.motion_sync_enabled else 'Off'}")
            return 0

        desired = current.clone()
        if args.dpi:
            for stage_idx, dpi_val in args.dpi:
                desired.set_dpi(stage_idx, dpi_val)
        if args.polling is not None:
            desired.polling_index = POLLING_RATES.index(args.polling)
        if args.debounce is not None:
            desired.debounce_ms = args.debounce
        if args.lod is not None:
            desired.lift_off_mode = 0 if args.lod == "very-low" else 1
        if args.motion_sync is not None:
            desired.motion_sync_enabled = args.motion_sync == "on"
        if args.angle_snapping is not None:
            desired.angle_snapping_enabled = args.angle_snapping == "on"

        applied = service.apply(desired, current)
        print("Settings applied and verified.")
        if args.read:
            print(json.dumps(applied.to_dict(), indent=2, sort_keys=True))
        return 0

    except MouseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
