import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import customtkinter as ctk

from src.free_games_bot import acquire_profile_run_lock, release_profile_run_lock
from src.free_games_bot_ui import FreeGamesBotApp


class DesktopAppTests(unittest.TestCase):
    def test_ui_uses_customtkinter(self):
        self.assertTrue(issubclass(FreeGamesBotApp, ctk.CTk))

    def test_profile_lock_prevents_overlapping_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {'FREEGAMESBOT_DATA_DIR': temp_dir},
        ):
            first_lock = acquire_profile_run_lock('Daily Profile')
            self.assertIsNotNone(first_lock)
            self.assertIsNone(acquire_profile_run_lock('Daily Profile'))

            release_profile_run_lock(first_lock)
            second_lock = acquire_profile_run_lock('Daily Profile')
            self.assertIsNotNone(second_lock)
            release_profile_run_lock(second_lock)

    def test_schedule_launcher_is_valid_python_and_quotes_profile_safely(self):
        profile = 'Profile "with" spaces; & symbols'
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {'FREEGAMESBOT_DATA_DIR': temp_dir},
        ), patch('src.free_games_bot_ui.TASKS_DIRECTORY', Path(temp_dir) / 'tasks'):
            app = object.__new__(FreeGamesBotApp)
            launcher = app._launcher_path(profile)
            source = launcher.read_text(encoding='utf-8')

            compile(source, str(launcher), 'exec')
            self.assertIn(repr(profile), source)
            self.assertNotEqual(launcher.suffix, '.cmd')

    def test_next_run_calculates_daily_and_weekly_times(self):
        now = datetime(2026, 11, 11, 13, 30)

        daily = FreeGamesBotApp._next_run({'frequency': 'Daily', 'time': '12:00'}, now)
        weekly = FreeGamesBotApp._next_run(
            {'frequency': 'Weekly', 'time': '12:00', 'weekday': 'SUN'},
            now,
        )

        self.assertEqual(daily, 'Thu 2026-11-12 12:00')
        self.assertEqual(weekly, 'Sun 2026-11-15 12:00')


if __name__ == '__main__':
    unittest.main()
