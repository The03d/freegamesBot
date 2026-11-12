# FGF-Bot
A WebHook bot that finds free games on IsThereAnyDeal.com using it's API and posts it directly to your [Discord](https://discord.com/) or [Slack](https://slack.com/) server in a particular channel <br />
## Instructions:
Make sure you have [Python 3](https://www.python.org/downloads/windows/) installed, and download the code to your machine. <br />

Install the needed libraries using [pip](https://pypi.org/project/pip/):<br /> 
* `py -m pip install -r requirements.txt`<br />

Consult the [pip documentation](https://pip.pypa.io/en/stable/) for further detail.

Create an "app" to get an API Key from [IsThereAnyDeal](https://isthereanydeal.com/apps/).  The API is [well documented](https://itad.docs.apiary.io/#).  Add the API key you get to `data/settings.json` under `Bot_Name['api_params']['key']`. <br />

[Get a Discord webhook set up on your server](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks) and add the url you receive to the `settings.json` . <br />

Settings, tracking files, delivery state, run history, locks, schedules, launcher scripts, and scheduled-task logs are stored in the project's `data` folder. Startup migrates eligible legacy root-level settings and tracking files into `data` without overwriting conflicting files. Tracking paths must be relative to `data` and cannot escape it. Existing AppData files are not imported; legacy `FreeGamesBot-*` scheduled tasks are removed once when the dashboard starts. The `data` folder is Git-ignored, but its settings are not encrypted.

## Testing
Run the offline component tests from the project root. They use mocked HTTP requests and do not contact ITAD, Discord, or Slack:

```powershell
py -m unittest discover -s tests -v
```

To check the ITAD API for one configured profile without setting up a webhook:

```powershell
py src\free_games_bot.py --check-api Bot_Name
```

This requires that profile's ITAD API key. It checks the store list when needed by the shop filter and then requests deals. It does not send webhooks or create/update tracking files.

If the API rejects a request, the diagnostic reports the HTTP status, a short response detail, and a suggested action. For `429` responses it also reports `Retry-After`. You can share that diagnostic output for troubleshooting, but never share the API key, request headers, or the full settings file.

To fetch and filter real deals without posting or saving tracking state:

```powershell
py src\free_games_bot.py --dry-run Bot_Name
```

The dry run reads an existing tracking file if present, prints a summary and up to five eligible games, and does not create or modify tracking files. Running the script without either diagnostic option is the production flow: it can post to configured webhooks and update tracking files.

## Desktop Dashboard
Install the requirements, including CustomTkinter, then start the Windows dashboard:

```powershell
py -m pip install -r requirements.txt
py src\free_games_bot_ui.py
```

Select a profile and use **Test Webhook** to send one timestamped probe without fetching deals or changing tracking. **Run Profile** executes that profile's normal deal workflow. The dashboard can also create or remove daily and weekly Windows Task Scheduler jobs for the selected profile; scheduled tasks continue to run when the dashboard is closed.

Use **Manage Profiles** to add, edit, rename, or remove profiles. Leave the deal limit blank to search all available pages, and set the maximum posts per day for each profile. Remove a profile's scheduled task before renaming or deleting it. Removing a profile retains its tracking file, queue and daily state; recreating it with the same name restores that state. Saving a profile moves legacy `store_filter.exclude_keywords` settings into `game_filters`.

Each profile defaults to 10 deal posts per local calendar day across all its runs. Profiles using the same webhook have separate limits. The allowance resets at local midnight, but queued deals are only sent when the profile next runs. Existing tracking files do not contain posting dates, so the daily count starts with the new delivery ledger.

Matching deals are saved in a persistent queue and checked against current ITAD prices and profile filters before sending. Expired, unavailable, or no longer eligible offers are discarded. Unlimited searches can take longer and trigger ITAD rate limits; already discovered candidates remain queued. Queue and delivery state are stored in `data/delivery-state.sqlite3`. Keep this database with the tracking files: deleting it resets the queue and daily counts. Rename profiles through the dashboard to preserve their state.

Only confirmed deliveries are added to tracking. If a webhook times out or the process stops during a send, the delivery may already have reached the channel. The bot reserves a daily slot and does not retry that game until it is reviewed. List uncertain deliveries, check the channel, and confirm the outcome:

```powershell
py src\free_games_bot.py --list-uncertain Bot_Name
py src\free_games_bot.py --resolve-uncertain Bot_Name --delivery-id 1 --outcome sent --confirm
py src\free_games_bot.py --resolve-uncertain Bot_Name --delivery-id 1 --outcome not-sent --confirm
```

Use exactly one outcome after checking. `sent` records the delivery; `not-sent` releases the reservation and returns the deal to the queue. An explicit **Test Webhook** probe is not counted as a deal post. Run statistics, schedules, launcher scripts, and scheduled-task logs are stored in `data/`. The existing no-argument script invocation continues to run all configured profiles.

## Recommended:
You can also optionally use [Task Scheduler](https://docs.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-start-page) in Windows to run this script as often as you like. <br />

## Bot Settings:
The settings JSON may contain multiple bot profiles, each with their own unique settings and discord or slack channel targets.

### API Paramters
The API Parameters are key value pairs that are sent to the ITAD web API to specify what data you want to receive in the payload.

* `key : ` The ITAD API key, sent in the `ITAD-API-Key` request header.
* `offset : ` The starting position for the deals list; defaults to `0`.
* `limit : ` The total number of deals to inspect, or `null` for unlimited. ITAD returns at most 200 per request; the script fetches additional pages until the limit or the end of the list.
* `country : ` Filters deals by [country](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2#Officially_assigned_code_elements) and determines the returned currency.
* `shops : ` Numeric ITAD shop IDs, resolved from the `store_filter` names or IDs.
* `sort : ` V2 sort expression, e.g. `-cut` for largest discounts first.
* `nondeals : ` Set to `false` to return sale prices only.
* `mature : ` Set to `false` to exclude games ITAD marks as mature.

### Other Parameters
* `web_hook_address : `The address of the webhook from your discord channel.
* `max_posts_per_day : `Maximum confirmed deal posts per profile per local calendar day; defaults to `10`. Uncertain deliveries reserve a slot until reviewed.
* `store_filter : `Can either `exclude` or `include` shops from the complete list of shops.  The value here will inform what gets sent to the ITAD API.
* `game_filters : `The script will go through the data downloaded from ITAD and filter it based on criteria.
    * `min_discount_percent : `The minimum discount to report in percent, e.g., `80.0`.
    * `min_price_old : `The minimum before-sale price to report in dollars, e.g., `60.00`.
    * `max_price_new : `The maximum sale price to report, e.g., `20.00`.
    * `exclude_keywords : `Any problem keywords in game titles you don't want to hear about, e.g., `"demo,schweet,minion"`.
    * `include_keywords : `Comma-separated title words to include, e.g., `"puzzle,adventure"`. At least one must match; an empty string allows all titles. Both keyword filters match whole words case-insensitively after removing punctuation.
* `tracking_json : `A filename or subpath relative to the project `data` folder. Absolute paths and paths that escape `data` are rejected. The record is the bot's memory of previously posted deals, e.g., `"posted_all_free.json"` or `"tracking/steam_discount.json"`.

For example, to find free Steam games while excluding titles containing `hentai`, set the shop and game filters like this:

```json
{
    "store_filter": { "include": ["Steam"] },
    "game_filters": {
        "max_price_new": 0,
        "exclude_keywords": "hentai"
    }
}
```

The shop name is resolved to the active numeric ITAD shop ID for the configured country. Excluded shops that are no longer active in that country are ignored with a warning; unknown included shops are rejected to avoid accidentally broadening the results. The title keyword filter is applied locally in addition to ITAD's `mature=false` request filter.
