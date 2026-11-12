"""Offline protocol, fixture, validation, and mock-transport tests."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from burstcontrol.protocol import (
    DeviceTelemetry,
    MockHIDConnection,
    MouseError,
    MouseService,
    Settings,
    build_packet,
)

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "settings.json"


class ProtocolChecksTest(unittest.TestCase):
    def test_packet_building_and_checksums(self):
        acquire = build_packet(0x80, [])
        self.assertEqual(list(acquire[:6]), [3, 0x81, 0x80, 0, 0, 1])

        wired = build_packet(0x82, [64, 0, 0, 0, 0, 0, 0, 10, 0, 0], target=0)
        self.assertEqual(wired[5], 0)
        self.assertEqual(wired[1], sum(wired[2:]) & 0xFF)

        read = build_packet(0x82, [64, 0, 6, 0, 0, 0, 0, 1, 0, 0])
        self.assertEqual(
            list(read[:16]),
            [3, 0xD4, 0x82, 10, 0, 1, 64, 0, 6, 0, 0, 0, 0, 1, 0, 0],
        )

        with self.assertRaises(MouseError):
            build_packet(0x82, [0] * 59)

    def test_telemetry_decoding(self):
        power = DeviceTelemetry.from_status("USB cable", [0, 0, 0, 0, 0, 51, 0, 1, 0, 0])
        self.assertEqual(power.battery_percent, 51)
        self.assertTrue(power.charging)

        full = DeviceTelemetry.from_status("2.4 GHz receiver", [0, 0, 0, 0, 0, 100, 0, 0, 0, 0])
        self.assertEqual(full.battery_percent, 100)
        self.assertFalse(full.charging)

        invalid_pct = DeviceTelemetry.from_status("USB cable", [0, 0, 0, 0, 0, 255, 0, 0, 0, 0])
        self.assertIsNone(invalid_pct.battery_percent)

        short = DeviceTelemetry.from_status("USB cable", [51])
        self.assertIsNone(short.battery_percent)

    def test_fixture_dpi_motion_sync_and_merging(self):
        original = Settings.from_dict(json.loads(FIXTURE_PATH.read_text()))
        self.assertEqual([original.dpi(i) for i in range(5)], [400, 800, 1200, 1600, 3200])
        self.assertEqual(original.motion_sync, 255)
        self.assertTrue(original.motion_sync_enabled)

        motion = original.clone()
        motion.motion_sync_enabled = False
        self.assertEqual(motion.motion_sync, 0)
        motion.validate(original)

        motion.motion_sync_enabled = True
        self.assertEqual(motion.motion_sync, 1)
        self.assertEqual(motion.sensor[:-1], original.sensor[:-1])
        motion.validate(original)

        self.assertEqual(original.active_stage, 3)
        switched = original.clone()
        switched.sensor[1] = 4
        self.assertEqual(switched.active_stage, 4)
        self.assertTrue(switched.same_configuration(original))

        edits = original.clone()
        edits.set_dpi(0, 650)
        edits.debounce_ms = 2
        merged = switched.merging_edits(edits, original)
        self.assertEqual(merged.active_stage, 4)
        self.assertEqual(merged.dpi(0), 650)
        self.assertEqual(merged.debounce_ms, 2)
        merged.validate(switched)

        external = switched.clone()
        external.polling_index = 3
        self.assertFalse(external.same_configuration(original))
        kept = external.merging_edits(edits, original)
        self.assertEqual(kept.polling_index, 3)
        self.assertEqual(kept.dpi(0), 650)

        invalid = original.clone()
        invalid.sensor[1] = 255
        self.assertIsNone(invalid.active_stage)

    def test_bounds_and_unmapped_byte_protection(self):
        original = Settings.from_dict(json.loads(FIXTURE_PATH.read_text()))

        changed = original.clone()
        changed.set_dpi(4, 26000)
        self.assertEqual(changed.dpi(4), 26000)
        for idx, val in enumerate(changed.sensor):
            if idx not in (10, 11):
                self.assertEqual(val, original.sensor[idx])
        changed.validate(original)

        changed.sensor[20] ^= 1
        with self.assertRaises(MouseError):
            changed.validate(original)

        bad_profile = original.clone()
        bad_profile.profile[0] = 4
        with self.assertRaises(MouseError):
            bad_profile.validate(original)

        bad_polling = original.clone()
        bad_polling.polling_index = 4
        with self.assertRaises(MouseError):
            bad_polling.validate(original)

    def test_mock_service_apply_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            mock_conn = MockHIDConnection(FIXTURE_PATH, wired=False)
            service = MouseService(connection_factory=lambda **kw: mock_conn)

            current, telem = service.read_live()
            self.assertEqual(telem.battery_percent, 84)
            self.assertEqual(current.debounce_ms, 0)

            desired = current.clone()
            desired.debounce_ms = 1
            desired.set_dpi(1, 1600)
            desired.polling_index = 3  # 1000 Hz
            applied = service.apply(desired, current)

            self.assertEqual(applied.debounce_ms, 1)
            self.assertEqual(applied.dpi(1), 1600)
            self.assertEqual(applied.polling_hz, 1000)

            backup = service.load_backup()
            self.assertEqual(backup["settings_obj"].debounce_ms, 0)
            self.assertEqual(backup["settings_obj"].dpi(1), 800)


if __name__ == "__main__":
    unittest.main(verbosity=2)
