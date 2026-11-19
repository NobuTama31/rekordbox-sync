"""The monitor builds the resident UIA source by default (since the
2026-11-18 live validation) and the one-shot source only when
MonitorConfig.uia_resident opts out."""

import unittest
from unittest import mock

import rekordbox_sync.service as service
from rekordbox_sync import MonitorConfig, RekordboxMonitor


class SourceSelectionTests(unittest.TestCase):
    def test_default_constructs_resident_source(self):
        with (
            mock.patch.object(service, "UiaSource") as one_shot,
            mock.patch.object(service, "UiaResidentSource") as resident,
        ):
            RekordboxMonitor(MonitorConfig())
        resident.assert_called_once()
        self.assertIsNone(resident.call_args.kwargs["on_suspect"])
        one_shot.assert_not_called()

    def test_resident_flag_constructs_resident_source_with_suspect_hook(self):
        with (
            mock.patch.object(service, "UiaSource") as one_shot,
            mock.patch.object(service, "UiaResidentSource") as resident,
        ):
            RekordboxMonitor(MonitorConfig(uia_resident=True, save_suspect_captures=True))
        resident.assert_called_once()
        self.assertIsNotNone(resident.call_args.kwargs["on_suspect"])
        one_shot.assert_not_called()

    def test_one_shot_opt_out_constructs_one_shot_source(self):
        with (
            mock.patch.object(service, "UiaSource") as one_shot,
            mock.patch.object(service, "UiaResidentSource") as resident,
        ):
            RekordboxMonitor(MonitorConfig(uia_resident=False))
        one_shot.assert_called_once()
        self.assertIsNone(one_shot.call_args.kwargs["on_suspect"])
        resident.assert_not_called()


if __name__ == "__main__":
    unittest.main()
