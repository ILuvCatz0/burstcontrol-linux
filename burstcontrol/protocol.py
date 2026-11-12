"""Linux USB HID protocol and settings engine for Turtle Beach Burst II Air."""

from __future__ import annotations

import copy
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

try:
    import hid
except ImportError:  # Allows offline testing even before hidapi is installed
    hid = None


VENDOR_ID = 0x10F5
PID_WIRED = 0x5002
PID_RECEIVER = 0x5004
PRODUCTS = (PID_WIRED, PID_RECEIVER)

POLLING_RATES = (125, 250, 500, 1000)
LIFT_OFF_MODES = {0: "Very low", 1: "Low"}


class MouseError(Exception):
    """Raised when HID communication, validation, or permissions fail."""


def xdg_data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "burst-control"


def last_backup_path() -> Path:
    return xdg_data_dir() / "last-backup.json"


def build_packet(command: int, payload: bytes | list[int] = b"", target: int = 1) -> bytearray:
    raw = bytes(payload)
    if len(raw) > 58:
        raise MouseError("Packet too large")
    pkt = bytearray(64)
    pkt[0] = 3
    pkt[2] = command & 0xFF
    pkt[3] = len(raw)
    pkt[5] = target & 0xFF
    pkt[6 : 6 + len(raw)] = raw
    pkt[1] = sum(pkt[2:]) & 0xFF
    return pkt


@dataclass
class Settings:
    profile: list[int]
    sensor: list[int]
    debounce: list[int]
    liftOff: list[int]
    wireless: list[int]

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        return cls(
            profile=list(data["profile"]),
            sensor=list(data["sensor"]),
            debounce=list(data["debounce"]),
            liftOff=list(data["liftOff"]),
            wireless=list(data["wireless"]),
        )

    def to_dict(self) -> dict:
        return {
            "profile": list(self.profile),
            "sensor": list(self.sensor),
            "debounce": list(self.debounce),
            "liftOff": list(self.liftOff),
            "wireless": list(self.wireless),
        }

    def clone(self) -> Settings:
        return copy.deepcopy(self)

    @property
    def profile_index(self) -> int:
        return int(self.profile[0])

    @property
    def active_stage(self) -> Optional[int]:
        return int(self.sensor[1]) if self.sensor[1] < 5 else None

    def same_configuration(self, other: Settings) -> bool:
        a = self.clone()
        b = other.clone()
        a.sensor[1] = 0
        b.sensor[1] = 0
        return a == b

    def merging_edits(self, draft: Settings, baseline: Settings) -> Settings:
        result = self.clone()
        for i in range(5):
            if draft.dpi(i) != baseline.dpi(i):
                result.set_dpi(i, draft.dpi(i))
        if draft.debounce_ms != baseline.debounce_ms:
            result.debounce_ms = draft.debounce_ms
        if draft.polling_index != baseline.polling_index:
            result.polling_index = draft.polling_index
        if draft.angle_snapping != baseline.angle_snapping:
            result.angle_snapping = draft.angle_snapping
        if draft.motion_sync != baseline.motion_sync:
            result.motion_sync = draft.motion_sync
        if draft.lift_off_mode != baseline.lift_off_mode:
            result.lift_off_mode = draft.lift_off_mode
        return result

    @property
    def polling_index(self) -> int:
        return int(self.sensor[38])

    @polling_index.setter
    def polling_index(self, value: int) -> None:
        self.sensor[38] = value & 0xFF

    @property
    def polling_hz(self) -> int:
        return POLLING_RATES[self.polling_index] if 0 <= self.polling_index < 4 else 0

    @property
    def motion_sync(self) -> int:
        return int(self.sensor[39])

    @motion_sync.setter
    def motion_sync(self, value: int) -> None:
        self.sensor[39] = value & 0xFF

    @property
    def motion_sync_enabled(self) -> bool:
        return self.motion_sync != 0

    @motion_sync_enabled.setter
    def motion_sync_enabled(self, enabled: bool) -> None:
        self.motion_sync = 1 if enabled else 0

    @property
    def angle_snapping(self) -> int:
        return int(self.sensor[37])

    @angle_snapping.setter
    def angle_snapping(self, value: int) -> None:
        self.sensor[37] = value & 0xFF

    @property
    def angle_snapping_enabled(self) -> bool:
        return self.angle_snapping != 0

    @angle_snapping_enabled.setter
    def angle_snapping_enabled(self, enabled: bool) -> None:
        self.angle_snapping = 1 if enabled else 0

    @property
    def debounce_ms(self) -> int:
        return int(self.debounce[0])

    @debounce_ms.setter
    def debounce_ms(self, value: int) -> None:
        self.debounce[0] = value & 0xFF

    @property
    def lift_off_mode(self) -> int:
        return int(self.liftOff[0])

    @lift_off_mode.setter
    def lift_off_mode(self, value: int) -> None:
        self.liftOff[0] = value & 0xFF

    def dpi(self, index: int) -> int:
        raw = self.sensor[2 + index * 2] | (self.sensor[3 + index * 2] << 8)
        return raw * 50 + 50

    def set_dpi(self, index: int, value: int) -> None:
        if not (0 <= index < 5 and 50 <= value <= 26000 and value % 50 == 0):
            return
        encoded = (value // 50) - 1
        self.sensor[2 + index * 2] = encoded & 0xFF
        self.sensor[3 + index * 2] = (encoded >> 8) & 0xFF

    def validate(self, original: Settings) -> None:
        if (
            self.profile != original.profile
            or len(self.sensor) != 40
            or len(self.debounce) != 1
            or len(self.liftOff) != 4
            or self.wireless != original.wireless
            or not (0 <= self.debounce_ms <= 10)
            or not (0 <= self.polling_index <= 3)
        ):
            raise MouseError("Invalid settings")

        if not (
            (self.motion_sync in (0, 1) or self.motion_sync == original.motion_sync)
            and (self.angle_snapping in (0, 1) or self.angle_snapping == original.angle_snapping)
            and (self.lift_off_mode in (0, 1) or self.lift_off_mode == original.lift_off_mode)
        ):
            raise MouseError("Unsupported setting value")

        for idx in range(5):
            if not (50 <= self.dpi(idx) <= 26000):
                raise MouseError("DPI must be 50–26000")

        editable = set(range(2, 12)) | {37, 38, 39}
        for idx, val in enumerate(self.sensor):
            if idx not in editable and val != original.sensor[idx]:
                raise MouseError("Attempt to alter an unmapped sensor field")

        if self.liftOff[1:] != original.liftOff[1:]:
            raise MouseError("Unmapped lift-off bytes changed")


@dataclass
class DeviceTelemetry:
    transport: str
    battery_percent: Optional[int] = None
    charging: Optional[bool] = None

    @classmethod
    def from_status(cls, transport: str, status: Optional[list[int]]) -> DeviceTelemetry:
        if status is not None and len(status) == 10 and 0 <= status[5] <= 100:
            return cls(
                transport=transport,
                battery_percent=int(status[5]),
                charging=status[7] != 0,
            )
        return cls(transport=transport, battery_percent=None, charging=None)

    def to_dict(self) -> dict:
        return {
            "transport": self.transport,
            "batteryPercent": self.battery_percent,
            "charging": self.charging,
        }


def find_matching_interfaces() -> list[dict]:
    if hid is None:
        return []
    found = []
    for pid in PRODUCTS:
        for dev in hid.enumerate(VENDOR_ID, pid):
            up = dev.get("usage_page", 0)
            usage = dev.get("usage", 0)
            iface = dev.get("interface_number", -1)
            # Vendor configuration endpoint is Usage Page 0xFF00 / Usage 1 (interface 1 on Linux hidraw)
            if (up == 0xFF00 and usage in (0, 1)) or (up == 0 and iface == 1):
                found.append(dev)
    # Deduplicate by device path
    unique = {d["path"]: d for d in found}
    return list(unique.values())


class HIDConnection:
    def __init__(self, fast_reads: bool = False):
        if hid is None:
            raise MouseError("Python 'hidapi' package is not installed. Run: pip install -r requirements.txt")
        self.fast_reads = fast_reads
        devices = find_matching_interfaces()
        cables = [d for d in devices if d.get("product_id") == PID_WIRED]
        candidates = cables if cables else devices
        if len(candidates) != 1:
            raise MouseError(
                "Connect one Burst II Air by USB cable or receiver. "
                "Multiple devices of the same connection type cannot be configured together."
            )
        info = candidates[0]
        self.wired = info.get("product_id") == PID_WIRED
        self.product_id = info.get("product_id", PID_RECEIVER)
        self.device = hid.device()
        try:
            self.device.open_path(info["path"])
        except OSError as exc:
            raise MouseError(
                "Linux udev permission denied opening /dev/hidraw. "
                "Install udev/99-burst-ii-air.rules, reload udevadm, and replug your mouse/dongle."
            ) from exc

    @property
    def transport_name(self) -> str:
        return "USB cable" if self.wired else "2.4 GHz receiver"

    @property
    def target(self) -> int:
        return 0 if self.wired else 1

    def close(self) -> None:
        if getattr(self, "device", None) is not None:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None

    def __enter__(self) -> HIDConnection:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def exchange(self, command: int, payload: bytes | list[int] = b"") -> list[int]:
        pkt = build_packet(command, payload, target=self.target)
        sent = self.device.send_feature_report(pkt)
        if sent < 64:
            raise MouseError(f"USB send failed ({sent}). Reconnect the dongle and refresh.")
        time.sleep(0.01 if (self.wired and self.fast_reads) else 0.05)
        reply = list(self.device.get_feature_report(3, 64))
        if (
            len(reply) < 5
            or reply[0] != 3
            or reply[1] != command
            or reply[2] != 0
            or len(reply) < 5 + reply[3]
        ):
            raise MouseError("Mouse returned an unexpected response; no further commands sent.")
        body = reply[5 : 5 + reply[3]]
        if body == [0xC0, 2]:
            raise MouseError("Mouse is busy or rejected the command.")
        return body

    def _transaction(self, work: Callable):
        if self.wired:
            return work()
        self.exchange(0x80)
        try:
            return work()
        finally:
            try:
                self.exchange(0x81)
            except Exception:
                pass

    def read(self, kind: int, profile: int = 0, sub: int = 0, size: int = 1) -> list[int]:
        if not (1 <= size <= 128):
            raise MouseError("Invalid read size")

        def _do_read() -> list[int]:
            request = [64, 0, kind, profile, sub, 0, 0, size, 0, 0]
            header = self.exchange(0x82, request)
            if (
                len(header) != 11
                or header[:9] != request[:9]
                or not (1 <= header[10] <= 57)
            ):
                raise MouseError("Unexpected settings header")
            result: list[int] = []
            for seq in range(1, 9):
                chunk = self.exchange(0x82, [0x80 | seq] + [0] * 57)
                if (
                    len(chunk) <= 1
                    or chunk[0] != (0x80 | seq)
                    or len(result) + len(chunk) - 1 > size
                ):
                    raise MouseError("Invalid settings chunk")
                result.extend(chunk[1:])
                if len(result) == size:
                    break
            if len(result) != size or (sum(result) & 0xFF) != header[9]:
                raise MouseError("Settings checksum mismatch")
            return result

        return self._transaction(_do_read)

    def write(self, kind: int, profile: int = 0, sub: int = 0, data: list[int] = ()) -> None:
        data_list = list(data)
        if not data_list or len(data_list) > 40 or kind not in (1, 2, 6, 7, 11):
            raise MouseError("Unsupported write")

        def _do_write() -> None:
            checksum = sum(data_list) & 0xFF
            request = [64, 1, kind, profile, sub, 0, 0, len(data_list), 0, checksum]
            header = self.exchange(0x82, request)
            if (
                len(header) != 11
                or header[:10] != request
                or not (1 <= header[10] <= 57)
            ):
                raise MouseError("Unexpected write header")
            chunk_size = header[10]
            for idx, offset in enumerate(range(0, len(data_list), chunk_size)):
                chunk = data_list[offset : offset + chunk_size]
                self.exchange(0x82, [0x81 + idx] + chunk)
            if self.exchange(0x82, [0xC0]) != [0xC0, 0]:
                raise MouseError("Write was not acknowledged. Refresh before retrying.")

        self._transaction(_do_write)


class MockHIDConnection:
    """In-memory Burst II Air simulator initialized from Tests/Fixtures/settings.json."""

    def __init__(self, fixture_path: Optional[Path] = None, wired: bool = False):
        self.wired = wired
        self.product_id = PID_WIRED if wired else PID_RECEIVER
        path = fixture_path or (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "settings.json")
        data = json.loads(path.read_text())
        self._state = Settings.from_dict(data)
        self._status = [0, 0, 0, 0, 0, 84, 0, 1 if wired else 0, 0, 0]

    @property
    def transport_name(self) -> str:
        return "USB cable (Simulated)" if self.wired else "2.4 GHz receiver (Simulated)"

    def __enter__(self) -> MockHIDConnection:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def read(self, kind: int, profile: int = 0, sub: int = 0, size: int = 1) -> list[int]:
        if kind == 0:
            return list(self._status[:size])
        if kind == 1:
            return list(self._state.profile[:size])
        if kind == 2:
            return list(self._state.sensor[:size])
        if kind == 6:
            return list(self._state.debounce[:size])
        if kind == 7:
            return list(self._state.liftOff[:size])
        if kind == 11:
            return list(self._state.wireless[:size])
        raise MouseError(f"Unsupported mock read kind {kind}")

    def write(self, kind: int, profile: int = 0, sub: int = 0, data: list[int] = ()) -> None:
        vals = list(data)
        if kind == 2:
            self._state.sensor = vals
        elif kind == 6:
            self._state.debounce = vals
        elif kind == 7:
            self._state.liftOff[0] = vals[0]
        else:
            raise MouseError(f"Unsupported mock write kind {kind}")


class MouseService:
    def __init__(self, connection_factory: Callable[..., HIDConnection | MockHIDConnection] = HIDConnection):
        self._connect = connection_factory

    def _snapshot(self, conn) -> Settings:
        profile = conn.read(kind=1, size=2)
        if len(profile) != 2 or not (0 < profile[1] <= 5) or not (profile[0] < profile[1]):
            raise MouseError("Unexpected mouse profile")
        settings = Settings(
            profile=profile,
            sensor=conn.read(kind=2, profile=profile[0], sub=1, size=40),
            debounce=conn.read(kind=6, size=1),
            liftOff=conn.read(kind=7, size=4),
            wireless=conn.read(kind=11, size=2),
        )
        if settings.debounce_ms > 10 or settings.polling_index > 3:
            raise MouseError("Unrecognized mouse settings format")
        if conn.read(kind=1, size=2) != profile:
            raise MouseError("Profile changed while reading. Refresh again.")
        return settings

    def read(self) -> Settings:
        with self._connect() as conn:
            return self._snapshot(conn)

    def active_stage(self) -> tuple[list[int], int]:
        with self._connect(fast_reads=True) as conn:
            profile = conn.read(kind=1, size=2)
            if len(profile) != 2 or not (profile[0] < profile[1] <= 5):
                raise MouseError("Unexpected profile")
            sensor = conn.read(kind=2, profile=profile[0], sub=1, size=40)
            if sensor[1] >= 5 or conn.read(kind=1, size=2) != profile:
                raise MouseError("Stage changed during read")
            return profile, sensor[1]

    def read_live(self) -> tuple[Settings, DeviceTelemetry]:
        with self._connect(fast_reads=True) as conn:
            settings = self._snapshot(conn)
            try:
                status = conn.read(kind=0, size=10)
            except Exception:
                status = None
            return settings, DeviceTelemetry.from_status(conn.transport_name, status)

    def telemetry(self) -> DeviceTelemetry:
        with self._connect() as conn:
            status = conn.read(kind=0, size=10)
            return DeviceTelemetry.from_status(conn.transport_name, status)

    def verify_writes(self) -> Settings:
        with self._connect() as conn:
            before = self._snapshot(conn)
            conn.write(kind=2, profile=before.profile[0], sub=1, data=before.sensor)
            if self._snapshot(conn) != before:
                raise MouseError("Sensor round-trip changed data")
            conn.write(kind=6, data=before.debounce)
            if self._snapshot(conn) != before:
                raise MouseError("Debounce round-trip changed data")
            conn.write(kind=7, data=[before.liftOff[0]])
            after = self._snapshot(conn)
            if after != before:
                raise MouseError("Lift-off round-trip changed data")
            return after

    def load_backup(self) -> dict:
        raw = json.loads(last_backup_path().read_text())
        s = Settings.from_dict(raw["settings"])
        if (
            len(s.profile) != 2
            or len(s.sensor) != 40
            or len(s.debounce) != 1
            or len(s.liftOff) != 4
            or len(s.wireless) != 2
        ):
            raise MouseError("Backup has invalid field lengths")
        s.validate(s)
        raw["settings_obj"] = s
        return raw

    def apply(self, desired: Settings, expected: Settings) -> Settings:
        desired.validate(expected)
        with self._connect() as conn:
            current = self._snapshot(conn)
            if not current.same_configuration(expected):
                raise MouseError(
                    "The mouse changed since your last refresh. Refresh to load its current settings before applying."
                )
            desired = desired.clone()
            desired.sensor[1] = current.sensor[1]
            if desired == current:
                return current

            backup_dir = xdg_data_dir()
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_payload = {
                "created": datetime.now(timezone.utc).isoformat(),
                "device": f"10f5:{conn.product_id:04x}",
                "settings": current.to_dict(),
            }
            encoded = json.dumps(backup_payload, indent=2, sort_keys=True)
            (backup_dir / f"backup-{uuid.uuid4()}.json").write_text(encoded)
            last_backup_path().write_text(encoded)

            try:
                if desired.sensor != current.sensor:
                    conn.write(kind=2, profile=current.profile[0], sub=1, data=desired.sensor)
                    if conn.read(kind=2, profile=current.profile[0], sub=1, size=40) != desired.sensor:
                        raise MouseError("Sensor settings read-back mismatch")
                if desired.debounce != current.debounce:
                    conn.write(kind=6, data=desired.debounce)
                    if conn.read(kind=6, size=1) != desired.debounce:
                        raise MouseError("Debounce read-back mismatch")
                if desired.liftOff != current.liftOff:
                    conn.write(kind=7, data=[desired.liftOff[0]])
                    if conn.read(kind=7, size=4) != desired.liftOff:
                        raise MouseError("Lift-off read-back mismatch")
                result = self._snapshot(conn)
                if result != desired:
                    raise MouseError("Final read-back differs from requested settings")
                return result
            except Exception as exc:
                raise MouseError(
                    f"{exc} Some changes may have applied. Refresh to inspect; the previous values are saved in Backups."
                ) from exc
