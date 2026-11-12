import json
import tempfile
import tkinter
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import customtkinter as ctk

from src.free_games_bot import acquire_profile_run_lock, release_profile_run_lock
from src.free_games_bot_ui import FreeGamesBotApp, ProfileManager, build_profile


class DesktopAppTests(unittest.TestCase):
    def profile_values(self):
        return {
            'name': 'Example', 'key': 'key', 'country': 'CA', 'sort': '-cut',
            'limit': '4000', 'max_posts_per_day': '10', 'offset': '', 'nondeals': False, 'mature': False,
            'web_hook_address': 'https://example.test', 'tracking_json': 'posted.json',
            'shops': 'Steam, 42', 'store_mode': 'Include', 'min_discount_percent': '',
            'min_price_old': '', 'max_price_new': '0', 'exclude_keywords': 'demo',
            'include_keywords': 'puzzle',
        }

    def test_build_profile_preserves_extra_settings(self):
        existing = {
            'api_params': {'extra': 'kept'},
            'store_filter': {'exclude': ['itchio']},
            'game_filters': {'extra_filter': 7},
            'extra': True,
        }
        result = build_profile(self.profile_values(), existing)
        self.assertEqual(result['store_filter'], {'include': ['Steam', '42']})
        self.assertEqual(result['game_filters']['exclude_keywords'], 'demo')
        self.assertEqual(result['game_filters']['include_keywords'], 'puzzle')
        self.assertEqual(result['game_filters']['extra_filter'], 7)
        self.assertEqual(result['api_params']['extra'], 'kept')
        self.assertTrue(result['extra'])
        self.assertEqual(existing['store_filter'], {'exclude': ['itchio']})

    def test_build_profile_rejects_invalid_values(self):
        for key, value in (('limit', '0'), ('max_posts_per_day', '0'), ('offset', '-1'), ('max_price_new', 'nan'),
                           ('min_discount_percent', '101'), ('shops', ''), ('tracking_json', '')):
            with self.subTest(key=key):
                values = self.profile_values()
                values[key] = value
                with self.assertRaises(ValueError):
                    build_profile(values)

    def test_build_profile_accepts_unlimited_and_large_limits(self):
        values = self.profile_values()
        values['limit'] = ''
        self.assertIsNone(build_profile(values)['api_params']['limit'])
        values['limit'] = '25000'
        values['max_posts_per_day'] = '15'
        profile = build_profile(values)
        self.assertEqual(profile['api_params']['limit'], 25000)
        self.assertEqual(profile['max_posts_per_day'], 15)

    def test_save_profiles_writes_settings_and_updates_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch('src.free_games_bot_ui.PROJECT_ROOT', Path(temp_dir)):
            app = Mock(spec=FreeGamesBotApp)
            app.status_var = Mock()
            settings = {'Example': build_profile(self.profile_values())}
            FreeGamesBotApp._save_profiles(app, settings, 'Example')
            self.assertEqual(json.loads((Path(temp_dir) / 'data' / 'settings.json').read_text(encoding='utf-8')), settings)
            self.assertEqual(app.settings, settings)
            app._refresh_profile_menu.assert_called_once_with('Example')

    def test_missing_settings_allows_creating_first_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch('src.free_games_bot_ui.PROJECT_ROOT', Path(temp_dir)):
            app = Mock(spec=FreeGamesBotApp)
            FreeGamesBotApp._load_profiles(app)
            self.assertTrue(app.settings_loaded)
            self.assertEqual(app.settings, {})

    def test_invalid_settings_cannot_be_overwritten_by_manager(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch('src.free_games_bot_ui.PROJECT_ROOT', Path(temp_dir)), \
                patch('src.free_games_bot_ui.messagebox.showerror'):
            settings_path = Path(temp_dir) / 'data' / 'settings.json'
            settings_path.parent.mkdir()
            settings_path.write_text('{invalid', encoding='utf-8')
            app = Mock(spec=FreeGamesBotApp)
            FreeGamesBotApp._load_profiles(app)
            self.assertFalse(app.settings_loaded)

    def test_removing_scheduled_profile_is_blocked(self):
        app = Mock(spec=FreeGamesBotApp)
        app._read_schedules.return_value = {'Example': {}}
        manager = Mock(spec=ProfileManager)
        manager.original_name = 'Example'
        manager.app = app
        with patch('src.free_games_bot_ui.messagebox.showerror') as showerror:
            ProfileManager._remove(manager)
        showerror.assert_called_once()
        app._save_profiles.assert_not_called()

    def test_profile_manager_create_edit_rename_and_remove(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch('src.free_games_bot_ui.PROJECT_ROOT', Path(temp_dir)), \
                patch.object(FreeGamesBotApp, '_read_schedules', return_value={}), \
                patch.object(FreeGamesBotApp, '_refresh_all'), \
                patch('src.free_games_bot_ui.messagebox.askyesno', return_value=True):
            settings_path = Path(temp_dir) / 'data' / 'settings.json'
            settings_path.parent.mkdir()
            settings_path.write_text('{}', encoding='utf-8')
            try:
                app = FreeGamesBotApp()
            except tkinter.TclError as error:
                self.skipTest('GUI unavailable: {}'.format(error))
            try:
                app.withdraw()
                manager = ProfileManager(app)
                manager.withdraw()
                values = self.profile_values()
                for key, entry in manager.entries.items():
                    entry.delete(0, 'end')
                    entry.insert(0, values[key])
                manager.store_mode.set('Include')
                manager._save()
                self.assertIn('Example', app.settings)
                self.assertEqual(app.profile_var.get(), 'Example')
                manager.entries['name'].delete(0, 'end')
                manager.entries['name'].insert(0, 'Renamed')
                manager.entries['include_keywords'].delete(0, 'end')
                manager.entries['include_keywords'].insert(0, 'strategy')
                manager._save()
                self.assertNotIn('Example', app.settings)
                self.assertEqual(app.settings['Renamed']['game_filters']['include_keywords'], 'strategy')
                state_id = app.settings['Renamed']['state_id']
                self.assertEqual(json.loads(settings_path.read_text(encoding='utf-8')), app.settings)
                manager._remove()
                self.assertEqual(app.settings, {})
                self.assertEqual(app.profile_var.get(), 'No profiles')
                self.assertEqual(json.loads(settings_path.read_text(encoding='utf-8')), {})
                manager.entries['name'].insert(0, 'Renamed')
                manager.entries['tracking_json'].insert(0, 'posted.json')
                manager._save()
                self.assertEqual(app.settings['Renamed']['state_id'], state_id)
                manager.destroy()
            finally:
                app.destroy()

    def test_ui_uses_customtkinter(self):
        self.assertTrue(issubclass(FreeGamesBotApp, ctk.CTk))

    def test_profile_lock_prevents_overlapping_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            'src.free_games_bot.PROJECT_ROOT', Path(temp_dir)
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
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            'src.free_games_bot_ui.PROJECT_ROOT', Path(temp_dir)
        ), patch('src.free_games_bot_ui.TASKS_DIRECTORY', Path(temp_dir) / 'data' / 'tasks'):
            app = object.__new__(FreeGamesBotApp)
            launcher = app._launcher_path(profile)
            source = launcher.read_text(encoding='utf-8')

            compile(source, str(launcher), 'exec')
            self.assertIn(repr(profile), source)
            expected_log_path = Path(temp_dir) / 'data' / ('scheduled-' + app._profile_slug(profile) + '.log')
            self.assertIn(repr(str(expected_log_path)), source)
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
