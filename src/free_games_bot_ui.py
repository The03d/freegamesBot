import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import tkinter.messagebox as messagebox

import customtkinter as ctk

try:
    from .free_games_bot import DeliveryState, acquire_profile_run_lock, get_app_data_directory, migrate_project_data, project_settings_path, release_profile_run_lock, run_history_path
except ImportError:
    from free_games_bot import DeliveryState, acquire_profile_run_lock, get_app_data_directory, migrate_project_data, project_settings_path, release_profile_run_lock, run_history_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOT_SCRIPT = Path(__file__).resolve().with_name('free_games_bot.py')
TASKS_DIRECTORY = get_app_data_directory() / 'tasks'
SCHEDULES_PATH = get_app_data_directory() / 'schedules.json'
HISTORY_LIMIT = 40


def build_profile(values, existing=None):
    profile = json.loads(json.dumps(existing or {}))
    api_params = profile.setdefault('api_params', {})
    store_filter = profile.setdefault('store_filter', {})
    game_filters = profile.setdefault('game_filters', {})

    for key in ('key', 'country', 'sort', 'region'):
        api_params[key] = values[key].strip()
    if not api_params['country']:
        raise ValueError('Country is required.')
    try:
        api_params['limit'] = int(values['limit']) if values['limit'].strip() else None
        api_params['offset'] = int(values['offset'] or '0')
        profile['max_posts_per_day'] = int(values['max_posts_per_day'])
    except ValueError:
        raise ValueError('Limit and offset must be whole numbers.') from None
    if (api_params['limit'] is not None and api_params['limit'] < 1) or api_params['offset'] < 0 or profile['max_posts_per_day'] < 1:
        raise ValueError('Limits must be positive and offset must be nonnegative.')
    api_params.setdefault('shops', '')
    api_params['nondeals'] = values['nondeals']
    api_params['mature'] = values['mature']
    profile['web_hook_address'] = values['web_hook_address'].strip()
    profile['tracking_json'] = values['tracking_json'].strip()
    if not profile['tracking_json']:
        raise ValueError('Tracking file is required.')

    shops = [shop.strip() for shop in values['shops'].split(',') if shop.strip()]
    mode = values['store_mode'].lower()
    if mode not in ('include', 'exclude'):
        raise ValueError('Choose an include or exclude shop filter.')
    if mode == 'include' and not shops:
        raise ValueError('Enter at least one shop to include.')
    store_filter.pop('include', None)
    store_filter.pop('exclude', None)
    store_filter.pop('exclude_keywords', None)
    store_filter[mode] = shops

    for key in ('min_discount_percent', 'min_price_old', 'max_price_new'):
        value = values[key].strip()
        if not value:
            game_filters.pop(key, None)
            continue
        try:
            number = float(value)
        except ValueError:
            raise ValueError('{} must be a number.'.format(key)) from None
        if not math.isfinite(number) or number < 0 or (key == 'min_discount_percent' and number > 100):
            raise ValueError('{} is out of range.'.format(key))
        game_filters[key] = number
    for key in ('exclude_keywords', 'include_keywords'):
        game_filters[key] = values[key].strip()
    return profile


class ProfileManager(ctk.CTkToplevel):
    FIELDS = (
        ('name', 'Profile name'),
        ('key', 'ITAD API key'),
        ('web_hook_address', 'Webhook URL'),
        ('tracking_json', 'Tracking file'),
        ('country', 'Country'),
        ('limit', 'Deal limit (blank for unlimited)'),
        ('max_posts_per_day', 'Maximum posts per day'),
        ('offset', 'Offset'),
        ('sort', 'Sort'),
        ('region', 'Region'),
        ('shops', 'Shops (comma-separated names or IDs)'),
        ('min_discount_percent', 'Minimum discount (%)'),
        ('min_price_old', 'Minimum original price'),
        ('max_price_new', 'Maximum sale price'),
        ('exclude_keywords', 'Exclude title keywords'),
        ('include_keywords', 'Include title keywords'),
    )

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.original_name = None
        self.title('Profile Manager')
        self.geometry('620x760')
        self.minsize(480, 500)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(self, fg_color='transparent')
        toolbar.grid(row=0, column=0, sticky='ew', padx=16, pady=12)
        toolbar.grid_columnconfigure(0, weight=1)
        self.selection = ctk.StringVar(value='')
        self.profile_menu = ctk.CTkOptionMenu(toolbar, variable=self.selection, values=['No profiles'], command=self._select)
        self.profile_menu.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        ctk.CTkButton(toolbar, text='New', width=70, command=self._new).grid(row=0, column=1, padx=4)
        ctk.CTkButton(toolbar, text='Remove', width=80, command=self._remove).grid(row=0, column=2, padx=4)

        form = ctk.CTkScrollableFrame(self)
        form.grid(row=1, column=0, sticky='nsew', padx=16, pady=(0, 8))
        form.grid_columnconfigure(1, weight=1)
        self.entries = {}
        for row, (key, label) in enumerate(self.FIELDS):
            ctk.CTkLabel(form, text=label, anchor='w').grid(row=row, column=0, sticky='w', padx=8, pady=5)
            entry = ctk.CTkEntry(form, show='*' if key in ('key', 'web_hook_address') else '')
            entry.grid(row=row, column=1, sticky='ew', padx=8, pady=5)
            self.entries[key] = entry
        row = len(self.FIELDS)
        ctk.CTkLabel(form, text='Shop filter').grid(row=row, column=0, sticky='w', padx=8, pady=5)
        self.store_mode = ctk.StringVar(value='Exclude')
        ctk.CTkSegmentedButton(form, values=['Include', 'Exclude'], variable=self.store_mode).grid(
            row=row, column=1, sticky='w', padx=8, pady=5
        )
        self.nondeals = ctk.BooleanVar(value=False)
        self.mature = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text='Include non-deals', variable=self.nondeals).grid(
            row=row + 1, column=1, sticky='w', padx=8, pady=5
        )
        ctk.CTkCheckBox(form, text='Include mature games', variable=self.mature).grid(
            row=row + 2, column=1, sticky='w', padx=8, pady=5
        )
        ctk.CTkButton(self, text='Save Profile', command=self._save).grid(row=2, column=0, sticky='e', padx=16, pady=(0, 16))
        self._refresh_menu()
        selected = app.profile_var.get()
        self._select(selected) if selected in app.settings else self._new()

    def _refresh_menu(self):
        self.profile_menu.configure(values=list(self.app.settings) or ['No profiles'])

    def _populate(self, name, profile):
        api = profile.get('api_params', {})
        stores = profile.get('store_filter', {})
        filters = profile.get('game_filters', {})
        mode = 'Include' if 'include' in stores else 'Exclude'
        values = {
            'name': name,
            'key': api.get('key', ''),
            'web_hook_address': profile.get('web_hook_address', ''),
            'tracking_json': profile.get('tracking_json', ''),
            'country': api.get('country', 'CA'),
            'limit': api.get('limit', 4000) if api.get('limit', 4000) is not None else '',
            'max_posts_per_day': profile.get('max_posts_per_day', 10),
            'offset': api.get('offset', 0),
            'sort': api.get('sort', '-cut'),
            'region': api.get('region', ''),
            'shops': ', '.join(str(shop) for shop in stores.get(mode.lower(), [])),
            'exclude_keywords': filters.get('exclude_keywords', stores.get('exclude_keywords', '')),
            'include_keywords': filters.get('include_keywords', ''),
        }
        for key in ('min_discount_percent', 'min_price_old', 'max_price_new'):
            values[key] = filters.get(key, '' if profile else (0 if key == 'max_price_new' else ''))
        for key, entry in self.entries.items():
            entry.delete(0, 'end')
            entry.insert(0, str(values[key]))
        self.store_mode.set(mode)
        self.nondeals.set(api.get('nondeals', False))
        self.mature.set(api.get('mature', False))

    def _new(self):
        self.original_name = None
        self.selection.set('New profile')
        self._populate('', {})

    def _select(self, name):
        if name in self.app.settings:
            self.original_name = name
            self.selection.set(name)
            self._populate(name, self.app.settings[name])

    def _save(self):
        try:
            values = {key: entry.get() for key, entry in self.entries.items()}
            name = values['name'].strip()
            if not name:
                raise ValueError('Profile name is required.')
            if name != self.original_name and name in self.app.settings:
                raise ValueError('A profile with that name already exists.')
            if self.original_name and name != self.original_name and self.original_name in self.app._read_schedules():
                raise ValueError('Remove this profile\'s schedule before renaming it.')
            values.update(store_mode=self.store_mode.get(), nondeals=self.nondeals.get(), mature=self.mature.get())
            profile = build_profile(values, self.app.settings.get(self.original_name))
            if self.original_name:
                profile['state_id'] = profile.get('state_id', self.original_name)
            else:
                state = DeliveryState(get_app_data_directory() / 'delivery-state.sqlite3')
                try:
                    profile['state_id'] = state.removed_profile_id(name) or str(uuid.uuid4())
                finally:
                    state.close()
                if any(entry.get('state_id', other_name) == profile['state_id']
                       for other_name, entry in self.app.settings.items()):
                    raise ValueError('This profile\'s retained state belongs to another active profile.')
            settings = dict(self.app.settings)
            if self.original_name and name != self.original_name:
                del settings[self.original_name]
            settings[name] = profile
            self.app._save_profiles(settings, name)
            self.original_name = name
            self._refresh_menu()
            self.selection.set(name)
        except (OSError, ValueError, TypeError, sqlite3.Error) as error:
            messagebox.showerror('Could not save profile', str(error), parent=self)

    def _remove(self):
        name = self.original_name
        if not name:
            return
        if name in self.app._read_schedules():
            messagebox.showerror('Schedule exists', 'Remove this profile\'s schedule first.', parent=self)
            return
        if not messagebox.askyesno('Remove profile', 'Remove profile {}? Tracking, queue and daily state will be kept.'.format(name), parent=self):
            return
        lock_file = None
        try:
            settings = dict(self.app.settings)
            state_id = settings[name].get('state_id', name)
            lock_file = acquire_profile_run_lock(state_id)
            if lock_file is None:
                raise ValueError('This profile is running. Try again after it completes.')
            state = DeliveryState(get_app_data_directory() / 'delivery-state.sqlite3')
            try:
                state.remember_profile(name, state_id)
            finally:
                state.close()
            del settings[name]
            self.app._save_profiles(settings)
            self._refresh_menu()
            self._new()
        except (OSError, ValueError, sqlite3.Error) as error:
            messagebox.showerror('Could not remove profile', str(error), parent=self)
        finally:
            if lock_file is not None:
                release_profile_run_lock(lock_file)


class FreeGamesBotApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Free Games Bot')
        self.geometry('1020x760')
        self.minsize(860, 620)
        ctk.set_appearance_mode('system')
        ctk.set_default_color_theme('blue')

        self.settings = {}
        self.settings_loaded = False
        self.running = False
        self.schedules = self._read_schedules()
        self.profile_var = ctk.StringVar(value='')
        self.frequency_var = ctk.StringVar(value='Daily')
        self.weekday_var = ctk.StringVar(value='SUN')
        self.status_var = ctk.StringVar(value='Ready')

        self._build_ui()
        self._load_profiles()
        self._refresh_all()
        self.after(5000, self._poll_history)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self.grid_rowconfigure(5, weight=1)

        header = ctk.CTkFrame(self, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew', padx=20, pady=(18, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text='Free Games Bot', font=ctk.CTkFont(size=24, weight='bold')).grid(
            row=0, column=0, sticky='w'
        )
        ctk.CTkLabel(header, textvariable=self.status_var, text_color=('gray35', 'gray75')).grid(
            row=0, column=1, sticky='e'
        )

        actions = ctk.CTkFrame(self)
        actions.grid(row=1, column=0, sticky='ew', padx=20, pady=8)
        actions.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(actions, text='Profile').grid(row=0, column=0, padx=(14, 8), pady=14)
        self.profile_menu = ctk.CTkOptionMenu(actions, variable=self.profile_var, values=['No profiles'])
        self.profile_menu.grid(row=0, column=1, sticky='ew', padx=8, pady=14)
        self.run_button = ctk.CTkButton(actions, text='Run Profile', command=self._run_profile)
        self.run_button.grid(row=0, column=2, padx=6, pady=14)
        self.webhook_button = ctk.CTkButton(
            actions,
            text='Test Webhook',
            fg_color='#167d76',
            hover_color='#126a64',
            command=self._test_webhook,
        )
        self.webhook_button.grid(row=0, column=3, padx=(6, 14), pady=14)
        self.run_all_button = ctk.CTkButton(
            actions,
            text='Run All Profiles',
            fg_color='transparent',
            border_width=1,
            command=self._run_all,
        )
        self.run_all_button.grid(row=1, column=1, sticky='w', padx=8, pady=(0, 12))
        self.manage_button = ctk.CTkButton(actions, text='Manage Profiles', command=self._manage_profiles)
        self.manage_button.grid(row=1, column=2, padx=6, pady=(0, 12))

        schedule = ctk.CTkFrame(self)
        schedule.grid(row=2, column=0, sticky='ew', padx=20, pady=8)
        schedule.grid_columnconfigure(7, weight=1)
        ctk.CTkLabel(schedule, text='Schedule', font=ctk.CTkFont(weight='bold')).grid(
            row=0, column=0, padx=(14, 12), pady=(12, 4), sticky='w'
        )
        ctk.CTkSegmentedButton(
            schedule,
            values=['Daily', 'Weekly'],
            variable=self.frequency_var,
            command=self._frequency_changed,
            width=150,
        ).grid(row=0, column=1, padx=6, pady=(12, 4))
        ctk.CTkLabel(schedule, text='At').grid(row=0, column=2, padx=(10, 4), pady=(12, 4))
        self.time_entry = ctk.CTkEntry(schedule, width=76, placeholder_text='12:00')
        self.time_entry.insert(0, '12:00')
        self.time_entry.grid(row=0, column=3, padx=4, pady=(12, 4))
        ctk.CTkLabel(schedule, text='Day').grid(row=0, column=4, padx=(10, 4), pady=(12, 4))
        self.weekday_menu = ctk.CTkOptionMenu(
            schedule,
            variable=self.weekday_var,
            values=['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'],
            width=84,
        )
        self.weekday_menu.grid(row=0, column=5, padx=4, pady=(12, 4))
        self.save_schedule_button = ctk.CTkButton(
            schedule, text='Save', width=78, command=self._save_schedule
        )
        self.save_schedule_button.grid(row=0, column=6, padx=(10, 5), pady=(12, 4))
        self.remove_schedule_button = ctk.CTkButton(
            schedule,
            text='Remove',
            width=82,
            fg_color='transparent',
            border_width=1,
            command=self._remove_schedule,
        )
        self.remove_schedule_button.grid(row=0, column=7, padx=(5, 14), pady=(12, 4), sticky='w')
        self.schedule_list = ctk.CTkScrollableFrame(schedule, height=94, label_text='Configured tasks')
        self.schedule_list.grid(row=1, column=0, columnspan=8, sticky='ew', padx=12, pady=(6, 12))
        self.schedule_list.grid_columnconfigure(0, weight=1)

        runs = ctk.CTkFrame(self)
        runs.grid(row=3, column=0, sticky='nsew', padx=20, pady=8)
        runs.grid_columnconfigure(0, weight=1)
        runs.grid_rowconfigure(1, weight=1)
        run_header = ctk.CTkFrame(runs, fg_color='transparent')
        run_header.grid(row=0, column=0, sticky='ew', padx=12, pady=(10, 4))
        ctk.CTkLabel(run_header, text='Recent Runs', font=ctk.CTkFont(size=16, weight='bold')).pack(side='left')
        ctk.CTkButton(run_header, text='Refresh', width=78, command=self._refresh_all).pack(side='right')
        self.history_list = ctk.CTkScrollableFrame(runs, label_text='')
        self.history_list.grid(row=1, column=0, sticky='nsew', padx=12, pady=(4, 12))
        self.history_list.grid_columnconfigure(0, weight=1)

        output_frame = ctk.CTkFrame(self)
        output_frame.grid(row=4, column=0, sticky='ew', padx=20, pady=(8, 18))
        output_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(output_frame, text='Latest Output', font=ctk.CTkFont(weight='bold')).grid(
            row=0, column=0, padx=12, pady=(10, 4), sticky='w'
        )
        self.output = ctk.CTkTextbox(output_frame, height=120, wrap='word')
        self.output.grid(row=1, column=0, sticky='ew', padx=12, pady=(4, 12))
        self.output.configure(state='disabled')

        self._frequency_changed(self.frequency_var.get())

    def _load_profiles(self):
        try:
            migrate_project_data(PROJECT_ROOT)
            settings_path = project_settings_path(PROJECT_ROOT)
            self.settings = json.loads(settings_path.read_text(encoding='utf-8'))
            if not isinstance(self.settings, dict):
                raise ValueError('Settings must contain a profile object.')
            self.settings_loaded = True
        except FileNotFoundError:
            self.settings = {}
            self.settings_loaded = True
        except (OSError, ValueError) as error:
            messagebox.showerror('Settings unavailable', '{}: {}'.format(type(error).__name__, error))
            self.settings = {}
            self.settings_loaded = False
        self._refresh_profile_menu()

    def _refresh_profile_menu(self, selected=None):
        profiles = list(self.settings)
        self.profile_menu.configure(values=profiles or ['No profiles'])
        self.profile_var.set(selected if selected is not None and selected in profiles else (profiles[0] if profiles else 'No profiles'))

    def _save_profiles(self, settings, selected=None):
        settings_path = project_settings_path(PROJECT_ROOT)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(settings, indent=4) + '\n', encoding='utf-8')
        temporary.replace(settings_path)
        self.settings = settings
        self._refresh_profile_menu(selected)
        self.status_var.set('Profiles updated')

    def _manage_profiles(self):
        if not self.settings_loaded:
            messagebox.showerror('Settings unavailable', 'Fix the settings file before managing profiles.')
            return
        manager = ProfileManager(self)
        manager.transient(self)
        manager.grab_set()

    def _selected_profile(self):
        profile = self.profile_var.get()
        if profile not in self.settings:
            raise ValueError('Select a configured profile first.')
        return profile

    def _set_busy(self, busy, text):
        self.running = busy
        self.status_var.set(text)
        state = 'disabled' if busy else 'normal'
        self.run_button.configure(state=state)
        self.webhook_button.configure(state=state)
        self.run_all_button.configure(state=state)
        self.save_schedule_button.configure(state=state)
        self.manage_button.configure(state=state)

    def _write_output(self, text):
        self.output.configure(state='normal')
        self.output.insert('end', text)
        self.output.see('end')
        self.output.configure(state='disabled')

    def _launch(self, mode=None):
        if self.running:
            return
        if mode is None:
            profile = 'all profiles'
            command = [sys.executable, str(BOT_SCRIPT)]
        else:
            try:
                profile = self._selected_profile()
            except ValueError as error:
                messagebox.showwarning('Profile required', str(error))
                return
            command = [sys.executable, str(BOT_SCRIPT), mode, profile]

        self._set_busy(True, 'Running {}'.format(profile))
        self._write_output('\n> {}\n'.format(' '.join(command)))

        def worker():
            try:
                result = subprocess.run(
                    command,
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.after(0, self._finish_process, result.returncode, result.stdout + result.stderr)
            except (OSError, subprocess.SubprocessError) as error:
                self.after(0, self._finish_process, 1, '{}: {}\n'.format(type(error).__name__, error))

        threading.Thread(target=worker, daemon=True).start()

    def _run_profile(self):
        self._launch('--run-profile')

    def _test_webhook(self):
        self._launch('--test-webhook')

    def _run_all(self):
        if messagebox.askyesno(
            'Run all profiles',
            'Run all profiles with their daily posting limits and update their tracking files?',
        ):
            self._launch()

    def _finish_process(self, return_code, output):
        if output:
            self._write_output(output)
        self._set_busy(False, 'Completed' if return_code == 0 else 'Failed; see output')
        self._refresh_all()

    def _frequency_changed(self, value):
        self.weekday_menu.configure(state='normal' if value == 'Weekly' else 'disabled')

    @staticmethod
    def _profile_slug(profile):
        readable = re.sub(r'[^A-Za-z0-9_-]+', '-', profile).strip('-') or 'profile'
        digest = hashlib.sha256(profile.encode('utf-8')).hexdigest()[:8]
        return '{}-{}'.format(readable[:44], digest)

    def _task_name(self, profile):
        return 'FreeGamesBot-{}'.format(self._profile_slug(profile))

    @staticmethod
    def _read_schedules():
        try:
            return json.loads(SCHEDULES_PATH.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_schedules(schedules):
        temporary = SCHEDULES_PATH.with_suffix('.tmp')
        temporary.write_text(json.dumps(schedules, indent=2), encoding='utf-8')
        temporary.replace(SCHEDULES_PATH)

    def _launcher_path(self, profile):
        TASKS_DIRECTORY.mkdir(parents=True, exist_ok=True)
        slug = self._profile_slug(profile)
        launcher = TASKS_DIRECTORY / (slug + '.py')
        log_path = get_app_data_directory() / ('scheduled-' + slug + '.log')
        content = '\n'.join((
            'import subprocess',
            'import sys',
            '',
            'with open({}, "a", encoding="utf-8") as log_file:'.format(repr(str(log_path))),
            '    subprocess.run(',
            '        [{}, {}, "--run-profile", {}],'.format(
                repr(sys.executable),
                repr(str(BOT_SCRIPT)),
                repr(profile),
            ),
            '        cwd={},'.format(repr(str(PROJECT_ROOT))),
            '        stdout=log_file,',
            '        stderr=subprocess.STDOUT,',
            '        check=False,',
            '    )',
            '',
        ))
        launcher.write_text(content, encoding='utf-8')
        return launcher

    def _save_schedule(self):
        if os.name != 'nt':
            messagebox.showerror('Windows required', 'Task Scheduler management is available on Windows.')
            return
        try:
            profile = self._selected_profile()
            run_time = self.time_entry.get().strip()
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', run_time):
                raise ValueError('Enter time in 24-hour HH:MM format.')
            frequency = self.frequency_var.get()
            weekday = self.weekday_var.get() if frequency == 'Weekly' else ''
            task_name = self._task_name(profile)
            launcher = self._launcher_path(profile)
            trigger_args = ['/SC', frequency.upper(), '/ST', run_time]
            if frequency == 'Weekly':
                trigger_args.extend(['/D', weekday])
            task_command = subprocess.list2cmdline([sys.executable, str(launcher)])
            result = subprocess.run(
                [
                    'schtasks.exe', '/Create', '/F', '/TN', task_name,
                    '/TR', task_command,
                    *trigger_args,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Task Scheduler rejected the task.')

            schedules = self._read_schedules()
            schedules[profile] = {
                'profile': profile,
                'frequency': frequency,
                'time': run_time,
                'weekday': weekday,
                'task_name': task_name,
                'launcher': str(launcher),
            }
            self._write_schedules(schedules)
            self._refresh_schedules()
            self.status_var.set('Schedule saved for {}'.format(profile))
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror('Could not save schedule', str(error))

    def _remove_schedule(self):
        try:
            profile = self._selected_profile()
        except ValueError as error:
            messagebox.showwarning('Profile required', str(error))
            return
        schedules = self._read_schedules()
        entry = schedules.get(profile)
        if not entry:
            messagebox.showinfo('No schedule', 'No scheduled task for {}.'.format(profile))
            return
        if not messagebox.askyesno('Remove schedule', 'Remove the scheduled task for {}?'.format(profile)):
            return
        result = subprocess.run(
            ['schtasks.exe', '/Delete', '/F', '/TN', entry['task_name']],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            messagebox.showerror('Could not remove schedule', result.stderr.strip() or result.stdout.strip())
            return
        Path(entry['launcher']).unlink(missing_ok=True)
        schedules.pop(profile, None)
        self._write_schedules(schedules)
        self._refresh_schedules()
        self.status_var.set('Schedule removed for {}'.format(profile))

    def _refresh_schedules(self):
        for child in self.schedule_list.winfo_children():
            child.destroy()
        schedules = self._read_schedules()
        if not schedules:
            ctk.CTkLabel(self.schedule_list, text='No scheduled profiles').grid(
                row=0, column=0, sticky='w', padx=8, pady=4
            )
            return
        for row, (profile, entry) in enumerate(sorted(schedules.items())):
            day = ' {}'.format(entry['weekday']) if entry.get('weekday') else ''
            next_run = self._next_run(entry)
            text = '{}  |  {}{} at {}  |  {}'.format(
                profile,
                entry['frequency'],
                day,
                entry['time'],
                'next ' + next_run,
            )
            ctk.CTkLabel(self.schedule_list, text=text, anchor='w').grid(
                row=row, column=0, sticky='ew', padx=8, pady=3
            )

    @staticmethod
    def _next_run(entry, now=None):
        now = now or datetime.now()
        run_time = datetime.strptime(entry['time'], '%H:%M').time()
        candidate = now.replace(
            hour=run_time.hour,
            minute=run_time.minute,
            second=0,
            microsecond=0,
        )
        if entry['frequency'] == 'Daily':
            if candidate <= now:
                candidate += timedelta(days=1)
        else:
            weekdays = {'MON': 0, 'TUE': 1, 'WED': 2, 'THU': 3, 'FRI': 4, 'SAT': 5, 'SUN': 6}
            target_weekday = weekdays[entry['weekday']]
            days_ahead = (target_weekday - now.weekday()) % 7
            if days_ahead == 0 and candidate <= now:
                days_ahead = 7
            candidate += timedelta(days=days_ahead)
        return candidate.strftime('%a %Y-%m-%d %H:%M')

    def _refresh_history(self):
        for child in self.history_list.winfo_children():
            child.destroy()

        headings = ('Started', 'Profile', 'Result', 'Deals', 'Eligible', 'Sent', 'Queued', 'Left', 'Uncertain', 'Failed', 'Seconds')
        for column, heading in enumerate(headings):
            ctk.CTkLabel(self.history_list, text=heading, font=ctk.CTkFont(weight='bold')).grid(
                row=0, column=column, sticky='w', padx=5, pady=4
            )

        try:
            lines = run_history_path().read_text(encoding='utf-8').splitlines()
        except FileNotFoundError:
            lines = []
        except OSError as error:
            self._write_output('Could not read run history ({}).\n'.format(type(error).__name__))
            return

        records = []
        for line in lines[-HISTORY_LIMIT:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        for row, record in enumerate(reversed(records), start=1):
            values = (
                record.get('started_at', '').replace('T', ' ')[:19],
                record.get('profile', ''),
                record.get('status', ''),
                record.get('candidates', 0),
                record.get('eligible', 0),
                record.get('sent', 0),
                record.get('queued', ''),
                record.get('remaining_today', ''),
                record.get('uncertain', ''),
                record.get('failed', 0),
                record.get('duration_seconds', ''),
            )
            for column, value in enumerate(values):
                ctk.CTkLabel(self.history_list, text=str(value), anchor='w').grid(
                    row=row, column=column, sticky='w', padx=5, pady=3
                )

    def _refresh_all(self):
        self._refresh_schedules()
        self._refresh_history()

    def _poll_history(self):
        self._refresh_history()
        self.after(5000, self._poll_history)


def main():
    app = FreeGamesBotApp()
    app.mainloop()


if __name__ == '__main__':
    main()
