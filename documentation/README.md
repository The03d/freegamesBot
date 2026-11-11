# FGF-Bot
A WebHook bot that finds free games on IsThereAnyDeal.com using it's API and posts it directly to your [Discord](https://discord.com/) or [Slack](https://slack.com/) server in a particular channel <br />
## Instructions:
Make sure you have [Python 3](https://www.python.org/downloads/windows/) installed, and download the code to your machine. <br />

Install the needed libraries using [pip](https://pypi.org/project/pip/):<br /> 
* `py -m pip install requests`<br />
* `py -m pip install Discord-Webhooks`<br />

Consult the [pip documentation](https://pip.pypa.io/en/stable/) for further detail.

Create an "app" to get an API Key from [IsThereAnyDeal](https://isthereanydeal.com/apps/).  The API is [well documented](https://itad.docs.apiary.io/#).  Add the API key you get to the `settings.json` under `Bot_Name['api_params']['key']`. <br />

[Get a Discord webhook set up on your server](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks) and add the url you receive to the `settings.json` . <br />

## Recommended:
You can also optionally use [Task Scheduler](https://docs.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-start-page) in Windows to run this script as often as you like. <br />

## Bot Settings:
The settings JSON may contain multiple bot profiles, each with their own unique settings and discord or slack channel targets.

### API Paramters
The API Parameters are key value pairs that are sent to the ITAD web API to specify what data you want to receive in the payload.

* `key : ` The API Key from ITAD.
* `offset : ` The position at which to start the search. ie. From the 500th entry.
* `limit : ` The number after which to stop. ie. After 4000 entries. 
* `region : ` Filters results by countries within a region.
* `country : ` Filters deals by [country](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2#Officially_assigned_code_elements), as well as determines the currency conversion.
* `shops : ` Filter by specific shops.  This is set programmatically by the script based on the `store_filter`.
* `sort : ` Criteria to sort by. ie. By price cut in descending order.

### Other Parameters
* `web_hook_address : `The address of the webhook from your discord channel.
* `store_filter : `Can either `exclude` or `include` shops from the complete list of shops.  The value here will inform what gets sent to the ITAD API.
* `game_filters : `The script will go through the data downloaded from ITAD and filter it based on criteria.
    * `min_discount_percent : `The minimum discount to report in percent, e.g., `80.0`.
    * `min_price_old : `The minimum before-sale price to report in dollars, e.g., `60.00`.
    * `max_price_new : `The maximum sale price to report, e.g., `20.00`.
    * `exclude_keywords : `Any problem keywords in game titles you don't want to hear about, e.g., `"demo,schweet,minion"`.
* `tracking_json : `The filename of the tracking json to be created.  It's so you can give a unique name to your record per profile.  The record is the bot's memory of what it posted yesterday, e.g., `"posted_all_free.json"` or `posted_steam_discount.json`, named for their filters.
