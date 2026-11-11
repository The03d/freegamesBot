import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
import tkinter.messagebox as messagebox

import customtkinter as ctk

try:
    from .free_games_bot import get_app_data_directory, run_history_path
except ImportError:
    from free_games_bot import get_app_data_directory, run_history_path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOT_SCRIPT = Path(__file__).resolve().with_name('free_games_bot.py')
TASKS_DIRECTORY = get_app_data_directory() / 'tasks'
SCHEDULES_PATH = get_app_data_directory() / 'schedules.json'
HISTORY_LIMIT = 40


class FreeGamesBotApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Free Games Bot')
        self.geometry('1020x760')
        self.minsize(860, 620)
        ctk.set_appearance_mode('system')
        ctk.set_default_color_theme('blue')

        self.settings = {}
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
        settings_path = PROJECT_ROOT / 'settings.json'
        try:
            self.settings = json.loads(settings_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            messagebox.showerror('Settings unavailable', '{}: {}'.format(type(error).__name__, error))
            self.settings = {}
        profiles = list(self.settings)
        self.profile_menu.configure(values=profiles or ['No profiles'])
        if profiles:
            self.profile_var.set(profiles[0])

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
                    timeout=1800,
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
            'This can post every eligible game for every configured profile and update their tracking files. Continue?',
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

        headings = ('Started', 'Profile', 'Result', 'Deals', 'Eligible', 'Sent', 'Failed', 'Seconds')
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
