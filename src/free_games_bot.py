'''*******************************************************************
*************************                      ***********************
************************* ITAD WEBHOOKS BOT!!! ***********************
*************************                      ***********************
*******************************************************************'''


import argparse
import requests
import json
import time
import string
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

'''*******************************************************************
******************** Custom Webhooks Classes!!! **********************
*******************************************************************'''

class DiscordWebhookSender:
    def __init__(self, webhook_url, content=None):
        self.webhook_url = webhook_url
        self.content = content

    def format_payload(self):
        if not self.content:
            return {}
        return {'content': self.content}

    def send(self):
        payload = self.format_payload()

        if not self.webhook_url:
            print('Error: Webhook URL is required.')
            return False

        if not payload:
            print('Error: Message payload cannot be empty.')
            return False

        try:
            request = requests.post(self.webhook_url, json=payload, timeout=10)
            request.raise_for_status()
            return True
        except requests.exceptions.HTTPError as error:
            response = error.response
            if response is None:
                print("Error: Discord webhook rejected the request (HTTP status unavailable).")
            else:
                try:
                    body = response.json()
                except ValueError:
                    body = getattr(response, 'text', '') or ''
                body = str(body).replace(self.webhook_url, '[REDACTED_WEBHOOK_URL]')[:500]
                print(
                    "Error: Discord webhook rejected the request (HTTP {} {}). {}".format(
                        response.status_code,
                        response.reason or '',
                        body,
                    )
                )
            return False
        except requests.exceptions.RequestException as error:
            print(
                "Error: Discord webhook request failed ({}). Check network access and try again.".format(
                    type(error).__name__
                )
            )
            return False

## Slack webhooks was a little minimal, so I made my own based on Discord webhooks
class SlackWebhooks:
    def __init__(self, webhook_url, **kwargs ):
        self.webhook_url = webhook_url
        self.content = kwargs.get('content')
        # self.blocks = None

    def set_content(self, **kwargs):
        self.content = kwargs.get('content')

    def format_payload(self):
        """
        Formats the data into a JSON object so it can be pushed
        as a payload to Discord.
        """
        # Initializes the default payload structure.
        payload = {}
        # Attaches data to the payload if provided.
        if self.content:
            payload['text'] = self.content

        return payload

    def send(self):
        """
        Makes a POST request to Slack with the message payload.
        """
        payload = self.format_payload()

        # Makes sure that the required fields are provided before
        # sending the payload.
        if not self.webhook_url:
            print ('Error: Webhook URL is required.')

        elif not payload:
            print ('Error: Message payload cannot be empty.')

        else:
            try:
                request = requests.post(self.webhook_url,
                    data=json.dumps(payload),
                    headers={'Content-Type': 'application/json'})

                request.raise_for_status()

                return True

            except requests.exceptions.RequestException as error:
                print('Error: %s' % error)
                return False


class ITADRequestError(RuntimeError):
    pass


class ITADClient:
    STORES_URL = "https://api.isthereanydeal.com/service/shops/v1"
    DEALS_URL = "https://api.isthereanydeal.com/deals/v2"
    TIMEOUT = 15
    PAGE_LIMIT = 200
    TOTAL_LIMIT = 4000

    @staticmethod
    def _http_error_message(endpoint, response, api_key):
        status_code = response.status_code
        reason = getattr(response, 'reason', '')
        message = "ITAD {} request failed: HTTP {}{}.".format(
            endpoint,
            status_code,
            " " + reason if reason else '',
        )

        try:
            body = response.json()
            if isinstance(body, dict):
                reason_phrase = body.get('reason_phrase') or body.get('message')
                details = body.get('details')
                if reason_phrase:
                    body = "{}{}".format(
                        reason_phrase,
                        "; details={}".format(details) if details is not None else '',
                    )
                else:
                    body = json.dumps(body, ensure_ascii=True)
            else:
                body = json.dumps(body, ensure_ascii=True)
        except ValueError:
            body = getattr(response, 'text', '') or ''

        body = str(body)
        if api_key:
            body = body.replace(api_key, '[REDACTED]')
        if body:
            message += " Response: {}".format(body[:500])

        retry_after = response.headers.get('Retry-After')
        if retry_after:
            message += " Retry-After: {} seconds.".format(retry_after)

        suggestions = {
            400: "Check the V2 query parameters: country, active numeric shop IDs, offset, and a per-request limit from 1 to 200.",
            401: "Verify the ITAD API key is current and sent in the ITAD-API-Key header.",
            403: "Check that the API key is valid and the app has access to this endpoint.",
            429: "Wait for the indicated Retry-After interval before trying again; avoid rapid repeated checks.",
        }
        suggestion = suggestions.get(status_code)
        if status_code is not None and status_code >= 500:
            suggestion = "ITAD may be temporarily unavailable; retry later and check the API service status."
        if suggestion:
            message += " Suggested action: {}".format(suggestion)
        return message

    def _get_json(self, url, params, endpoint, api_key=None):
        headers = {'ITAD-API-Key': api_key} if api_key else None
        try:
            response = requests.get(url, params=params, headers=headers, timeout=self.TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.HTTPError as error:
            response = error.response
            if response is None:
                raise ITADRequestError(
                    "ITAD {} request failed with HTTPError and no response details.".format(endpoint)
                ) from None
            raise ITADRequestError(
                self._http_error_message(endpoint, response, api_key)
            ) from None
        except requests.exceptions.RequestException as error:
            raise ITADRequestError(
                "ITAD {} request failed before an HTTP response ({}, timeout={}s). "
                "Check network access, DNS, firewall/proxy settings, then retry.".format(
                    endpoint,
                    type(error).__name__,
                    self.TIMEOUT,
                )
            ) from None

        try:
            return response.json()
        except ValueError:
            raise ITADRequestError("ITAD {} returned invalid JSON.".format(endpoint)) from None

    def get_all_shops(self, country='US'):
        response = self._get_json(self.STORES_URL, {'country': country}, "store list")
        try:
            shops = response
            if not isinstance(shops, list):
                raise TypeError
            if not all(isinstance(shop, dict) and 'id' in shop and 'title' in shop for shop in shops):
                raise TypeError
            return shops
        except TypeError:
            raise ITADRequestError("ITAD store list response has an unexpected format.") from None

    def get_deals(self, params):
        query = dict(params)
        api_key = query.pop('key', None)
        requested_limit = int(query.pop('limit', self.PAGE_LIMIT) or self.PAGE_LIMIT)
        total_limit = min(requested_limit, self.TOTAL_LIMIT)
        if total_limit < 1:
            raise ValueError("The deals limit must be at least 1.")

        offset = int(query.pop('offset', 0) or 0)
        if offset < 0:
            raise ValueError("The deals offset cannot be negative.")
        query.pop('region', None)
        if query.get('sort') == 'cut:desc':
            query['sort'] = '-cut'
        query.setdefault('nondeals', False)
        query.setdefault('mature', False)
        query = {key: value for key, value in query.items() if value not in (None, '')}

        deals = []
        while len(deals) < total_limit:
            page_limit = min(self.PAGE_LIMIT, total_limit - len(deals))
            page_params = dict(query, offset=offset, limit=page_limit)
            response = self._get_json(self.DEALS_URL, page_params, "deals list", api_key)
            try:
                page = response['list']
                has_more = response['hasMore']
                next_offset = response['nextOffset']
                if not isinstance(page, list) or not isinstance(has_more, bool) or not isinstance(next_offset, int):
                    raise TypeError
            except (KeyError, TypeError):
                raise ITADRequestError("ITAD deals list response has an unexpected format.") from None

            deals.extend(page[:total_limit - len(deals)])
            if not has_more or len(deals) >= total_limit:
                break
            if next_offset <= offset:
                raise ITADRequestError("ITAD deals pagination returned a non-advancing offset.")
            offset = next_offset

        return deals


def resolve_shops(store_filter, api_client, country='US'):
    if not isinstance(store_filter, dict):
        raise ValueError("store_filter must be an object.")
    if 'include' in store_filter and 'exclude' in store_filter:
        raise ValueError("store_filter must use either 'include' or 'exclude', not both.")

    if 'include' in store_filter:
        shops = store_filter['include']
    elif 'exclude' in store_filter:
        shops = store_filter['exclude']
    else:
        raise ValueError("store_filter must define 'include' or 'exclude'.")

    if not isinstance(shops, (list, tuple, set)):
        raise ValueError("store_filter values must be a list of shop IDs.")

    available_shops = api_client.get_all_shops(country)
    shops_by_name = {
        ''.join(character for character in shop['title'].casefold() if character.isalnum()): shop['id']
        for shop in available_shops
    }
    shop_ids = {int(shop['id']) for shop in available_shops}

    def resolve_shop(shop):
        try:
            shop_id = int(shop)
        except (TypeError, ValueError):
            shop_id = shops_by_name.get(
                ''.join(character for character in str(shop).casefold() if character.isalnum())
            )
        if shop_id not in shop_ids:
            if 'exclude' in store_filter:
                print("Ignoring excluded ITAD shop not active for {}: {}".format(country, shop))
                return None
            raise ValueError("Unknown active ITAD shop: {}".format(shop))
        return shop_id

    selected_ids = set()
    for shop in shops:
        shop_id = resolve_shop(shop)
        if shop_id is not None:
            selected_ids.add(shop_id)
    if 'exclude' in store_filter:
        selected_ids = shop_ids - selected_ids
    return ','.join(str(shop_id) for shop_id in sorted(selected_ids))


'''*******************************************************************
*************************** Bot Classes!!! ***************************
*******************************************************************'''

class Bot_Profile:
    def __init__(self, profile_info=None, profile_name="Unnamed_Profile", *, api_client=None, read_only=False):
        print( "************************ STARTING ", profile_name, "************************" )

        self.api_client = api_client or ITADClient()
        self.read_only = read_only
        self.game_filter_funcs = {
            "min_discount_percent" : lambda game :  game.price_cut >= self.game_filters[ 'min_discount_percent' ],
            "min_price_old" : lambda game :  game.price_old > self.game_filters[ 'min_price_old' ],
            "max_price_new" : lambda game :  game.price_new <= self.game_filters[ 'max_price_new' ],
            "exclude_keywords" : self.passes_exclude_keywords
        }

        if profile_info != None:
            self.api_params = dict(profile_info['api_params'])
            self.web_hook_address = profile_info.get('web_hook_address', '')

            self.store_filter = dict(profile_info.get('store_filter', {}))
            self.game_filters = dict(profile_info.get('game_filters', {}))
            if 'exclude_keywords' not in self.game_filters and 'exclude_keywords' in self.store_filter:
                self.game_filters['exclude_keywords'] = self.store_filter['exclude_keywords']
            self.api_params['shops'] = self.filter_shops(self.api_params.get('country') or 'US')

            self.games = []

            self.tracking_json = profile_info.get('tracking_json', '')
            self.tracking_info = self.load_tracking_info(create_if_missing=not read_only)

        self.session_info = []
        self.candidate_count = 0
        self.filtered_count = 0
        self.duplicate_count = 0
        self.posted_records = []
        self.sent_count = 0
        self.failed_count = 0


    def passes_exclude_keywords(self, game):
        keywords = {
            keyword.strip().lower()
            for keyword in self.game_filters.get('exclude_keywords', '').split(',')
            if keyword.strip()
        }
        title_words = set(
            game.title.lower().translate(str.maketrans('', '', string.punctuation)).split()
        )
        return keywords.isdisjoint(title_words)

    def load_tracking_info(self, create_if_missing=True):
        try:
            with open(self.tracking_json, 'r') as tracking_file:
                return json.load(tracking_file)
        except FileNotFoundError:
            if not create_if_missing:
                return []
            with open(self.tracking_json, 'w') as tracking_file:
                json.dump([], tracking_file)
            print("...............Tracking JSON created.\n")
            return []

    def get_all_shops(self):
        return self.api_client.get_all_shops()

    def filter_shops(self, country='US'):
        return resolve_shops(self.store_filter, self.api_client, country)

    def game_filter_check(self, game):
        if not self.api_params.get('mature', False) and getattr(game, 'mature', False):
            return False
        for game_filter in self.game_filters:
            filter_game = self.game_filter_funcs.get(game_filter)
            if filter_game is None:
                raise ValueError("Unknown game filter: {}".format(game_filter))
            if not filter_game(game):
                return False
        return True

    def game_tracking_check(self, game):
        for past_game in self.tracking_info:
            if game.url == past_game.get('url') and game.price_cut == past_game.get('price_cut', 0.0):
                return True
        return False

    def get_games(self, prepare_webhooks=True):
        candidate_games = self.api_client.get_deals(self.api_params)
        self.candidate_count = len(candidate_games)
        self.filtered_count = 0
        self.duplicate_count = 0

        for item in candidate_games:
            game = Game(item)

            if not self.game_filter_check( game ):
                self.filtered_count += 1
                continue

            if self.game_tracking_check( game ):
                self.duplicate_count += 1
                continue

            if prepare_webhooks:
                game.create_web_hook(self.web_hook_address)
            self.games.append( game )

    def send_games( self ):
        self.posted_records = []
        self.sent_count = 0
        self.failed_count = 0
        for index, game in enumerate(self.games):
            succeeded = game.send_web_hook()
            print("Succeeded?", succeeded)
            if succeeded:
                self.sent_count += 1
                self.posted_records.append(game.output_record())
            else:
                self.failed_count += 1
            if index < len(self.games) - 1:
                time.sleep(2.0)
        return self.sent_count, self.failed_count

                
    def update_tracking_json( self ):
        tracked_records = list(self.tracking_info)
        known_records = {
            (record.get('url'), record.get('price_cut', 0.0))
            for record in tracked_records
        }
        for record in self.posted_records:
            key = (record.get('url'), record.get('price_cut', 0.0))
            if key not in known_records:
                tracked_records.append(record)
                known_records.add(key)

        with open(self.tracking_json, 'w') as tracking_file:
            json.dump(tracked_records, tracking_file, indent=4)
        self.tracking_info = tracked_records
        print(str(self.tracking_json) + " updated!")

class Game:
    def __init__(self, game_info=None, currency=None):
        if game_info != None:
            deal = game_info['deal']
            price = deal['price']
            regular = deal['regular']

            self.added = deal['timestamp']
            self.drm = deal.get('drm', [])
            self.expiry = deal['expiry']
            self.plain = game_info.get('slug', '')
            self.price_cut = deal['cut']
            self.price_new = price['amount']
            self.price_old = regular['amount']
            self.currency = price.get('currency', currency or 'CAD')
            self.shop = deal['shop']['name']
            self.title = game_info['title']
            self.url = deal['url']
            self.mature = game_info.get('mature', False)

            self.web_hook = None

            # self.output_record = {  "title":    self.title,
            #                         "shop":     self.shop,
            #                         "url":      self.url,
            #                         "added":    self.added,
            #                         "price_cut":    self.price_cut }

        price_str = str(self.price_new) + " " + self.currency
        if self.price_new == 0.00:
            price_str = "Free"
        self.announce_text = "`{}` Is now on {} for {}! ({} % Off)\nGet it now: {}".format( self.title, 
                                                                                    self.shop,  
                                                                                    price_str,
                                                                                    self.price_cut,
                                                                                    self.url )

    def create_web_hook( self, web_hook_address ):
        if not isinstance(web_hook_address, str):
            print("No valid webhook provider configured.")
            return

        if "discord.com/api" in web_hook_address:
            self.web_hook = DiscordWebhookSender( web_hook_address, content=self.announce_text )
            return

        if "slack.com/services" in web_hook_address:
            self.web_hook = SlackWebhooks( web_hook_address, content=self.announce_text )
            return

        print("No valid webhook provider configured.")

    def send_web_hook( self ):
        print("*** Posting {} from {}.".format(self.title, self.shop))
        if self.web_hook is None:
            print("No valid webhook configured for this game.")
            return False
        succeeded = self.web_hook.send()
        return succeeded

    def output_record( self ):
        return {
            "title":    self.title,
            "shop":     self.shop,
            "url":      self.url,
            "added":    self.added,
            "price_cut":    self.price_cut
        }

'''*******************************************************************
********************** Default Bot Settings!!! ***********************
*******************************************************************'''

default_settings = json.dumps( {
        "Default_Profile" : {
            "api_params": 
                {
                    "key": "",
                    "offset": 0,
                    "limit": 4000,
                    "country": "CA",
                    "shops": "",
                    "sort": "-cut",
                    "nondeals": False,
                    "mature": False
                },
            "web_hook_address": "",
            "store_filter": { 
                                "exclude": ["itchio"]
                            },
            "game_filters": {
                "max_price_new": 0.00,
                "exclude_keywords": "hentai,demo"
            },
            "tracking_json" : "posted_default.json"
  } }, sort_keys=True, indent=4 )

settings_json = "settings.json"

## try to read the settings JSON
def load_json_settings( json_file ):
    settings = {}
    try:
        settings = json.loads(open(json_file, 'r').read())
        print( "...............JSON settings loaded.\n" )
    except:
        settings = json.loads(default_settings)
        f = open("settings.json", "x")
        f.write( default_settings )
        f.close
        print( "...............DEFAULT settings JSON created.\n" )

    return settings


def validate_profiles(settings, require_webhook=True):
    is_valid = True
    needs_setup = False
    for profile in settings:
        api_key = settings[profile].get('api_params', {}).get('key', '')
        if not api_key:
            print( "WARNING: It looks like you don't have a ISTHEREANYDEAL.COM api key set up in {}.  Check the documentation for instructions.\n".format(profile) )
            needs_setup = True
            is_valid = False
        if require_webhook and not settings[profile].get('web_hook_address'):
            print( "WARNING: It looks like you don't have a Webhooks destination set up in {}.  Check the documentation for instructions.\n".format(profile) )
            needs_setup = True
            is_valid = False

    if needs_setup:
        print("ITAD BOT: Configuration is incomplete.")
    return is_valid


def run_api_check(profile_info, profile_name, api_client=None):
    api_client = api_client or ITADClient()
    api_params = dict(profile_info['api_params'])
    api_params['shops'] = resolve_shops(
        profile_info.get('store_filter', {}),
        api_client,
        api_params.get('country') or 'US',
    )
    deals = api_client.get_deals(api_params)
    print("ITAD API check succeeded for '{}': received {} deals.".format(profile_name, len(deals)))
    return len(deals)


def run_dry_run(profile_info, profile_name, api_client=None):
    bot = Bot_Profile(profile_info, profile_name, api_client=api_client, read_only=True)
    bot.get_games(prepare_webhooks=False)
    eligible_count = len(bot.games)
    print(
        "Dry run '{}': {} received, {} filtered, {} already tracked, {} eligible.".format(
            profile_name,
            bot.candidate_count,
            bot.filtered_count,
            bot.duplicate_count,
            eligible_count,
        )
    )
    for game in bot.games[:5]:
        print("  {} ({})".format(game.title, game.shop))
    return bot


def get_app_data_directory():
    configured_directory = os.environ.get('FREEGAMESBOT_DATA_DIR')
    if configured_directory:
        directory = Path(configured_directory)
    elif os.name == 'nt':
        directory = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'FreeGamesBot'
    else:
        directory = Path.home() / '.local' / 'share' / 'FreeGamesBot'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def run_history_path():
    return get_app_data_directory() / 'run-history.jsonl'


def append_run_history(run_record):
    with run_history_path().open('a', encoding='utf-8') as history_file:
        history_file.write(json.dumps(run_record, ensure_ascii=True) + '\n')


def acquire_profile_run_lock(profile_name):
    lock_directory = get_app_data_directory() / 'run-locks'
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(profile_name.encode('utf-8')).hexdigest() + '.lock'
    lock_file = (lock_directory / lock_name).open('a+b')
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b'0')
        lock_file.flush()
    lock_file.seek(0)

    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def release_profile_run_lock(lock_file):
    try:
        lock_file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _redact_run_error(error, profile_info):
    message = "{}: {}".format(type(error).__name__, error)
    secrets = [
        profile_info.get('api_params', {}).get('key'),
        profile_info.get('web_hook_address'),
    ]
    for secret in secrets:
        if secret:
            message = message.replace(secret, '[REDACTED]')
    return message


def run_profile(profile_info, profile_name):
    started = datetime.now(timezone.utc)
    start_time = time.monotonic()
    run_record = {
        'profile': profile_name,
        'started_at': started.isoformat(timespec='seconds'),
        'status': 'failed',
        'candidates': 0,
        'filtered': 0,
        'duplicates': 0,
        'eligible': 0,
        'sent': 0,
        'failed': 0,
    }

    lock_file = None
    try:
        lock_file = acquire_profile_run_lock(profile_name)
        if lock_file is None:
            run_record['status'] = 'skipped'
            run_record['error'] = 'Another run for this profile is already active.'
            print("{} Skipping duplicate profile run.".format(run_record['error']))
        elif not validate_profiles({profile_name: profile_info}):
            raise ValueError("Profile configuration is incomplete.")
        else:
            bot = Bot_Profile(profile_info, profile_name)
            bot.get_games()
            run_record['candidates'] = bot.candidate_count
            run_record['filtered'] = bot.filtered_count
            run_record['duplicates'] = bot.duplicate_count
            run_record['eligible'] = len(bot.games)
            bot.send_games()
            run_record['sent'] = bot.sent_count
            run_record['failed'] = bot.failed_count

            if bot.sent_count:
                bot.update_tracking_json()
            run_record['status'] = 'partial' if bot.failed_count else 'success'
    except Exception as error:
        run_record['error'] = _redact_run_error(error, profile_info)
        print("Run failed: {}".format(run_record['error']))
    finally:
        if lock_file is not None:
            release_profile_run_lock(lock_file)
        run_record['completed_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        run_record['duration_seconds'] = round(time.monotonic() - start_time, 2)
        try:
            append_run_history(run_record)
        except OSError as error:
            print("Could not save run history ({}).".format(type(error).__name__))

    print(
        "Run '{}' {}: {} candidates, {} filtered, {} duplicates, {} eligible, {} sent, {} failed.".format(
            profile_name,
            run_record['status'],
            run_record['candidates'],
            run_record['filtered'],
            run_record['duplicates'],
            run_record['eligible'],
            run_record['sent'],
            run_record['failed'],
        )
    )
    return run_record


def run_webhook_test(profile_info, profile_name):
    webhook_url = profile_info.get('web_hook_address', '')
    message = "FreeGamesBot webhook test for '{}' at {}.".format(
        profile_name,
        datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z'),
    )

    if 'discord.com/api/webhooks/' in webhook_url:
        sender = DiscordWebhookSender(webhook_url, content=message)
    elif 'slack.com/services/' in webhook_url:
        sender = SlackWebhooks(webhook_url, content=message)
    else:
        raise ValueError("Profile does not contain a supported Discord or Slack webhook URL.")

    succeeded = sender.send()
    print("Webhook test for '{}' {}.".format(profile_name, 'succeeded' if succeeded else 'failed'))
    return succeeded


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find deals and post them to configured webhooks.")
    diagnostic_modes = parser.add_mutually_exclusive_group()
    diagnostic_modes.add_argument("--check-api", metavar="PROFILE", help="Test ITAD connectivity without a webhook.")
    diagnostic_modes.add_argument("--dry-run", metavar="PROFILE", help="Fetch and filter deals without sending or saving.")
    diagnostic_modes.add_argument("--run-profile", metavar="PROFILE", help="Run one configured profile and record its result.")
    diagnostic_modes.add_argument("--test-webhook", metavar="PROFILE", help="Send one test message without fetching deals or updating tracking.")
    args = parser.parse_args(argv)

    print(
        "********************************************************************\n"
        "************************ STARTING ITAD BOTS ************************\n"
        "********************************************************************"
    )
    settings = load_json_settings(settings_json)

    if args.check_api or args.dry_run or args.run_profile or args.test_webhook:
        profile_name = args.check_api or args.dry_run or args.run_profile or args.test_webhook
        profile_info = settings.get(profile_name)
        if profile_info is None:
            print("Unknown profile '{}'.".format(profile_name))
            return 1
        try:
            if args.test_webhook:
                return 0 if run_webhook_test(profile_info, profile_name) else 1
            if args.check_api:
                if not validate_profiles({profile_name: profile_info}, require_webhook=False):
                    return 1
                run_api_check(profile_info, profile_name)
            elif args.dry_run:
                if not validate_profiles({profile_name: profile_info}, require_webhook=False):
                    return 1
                run_dry_run(profile_info, profile_name)
            else:
                result = run_profile(profile_info, profile_name)
                return 0 if result['status'] in ('success', 'partial') else 1
        except (ITADRequestError, OSError, ValueError, KeyError) as error:
            print("Diagnostic failed: {}".format(error))
            return 1
        return 0

    if not validate_profiles(settings):
        return 1

    results = [run_profile(settings[profile], profile) for profile in settings]
    return 1 if any(result['status'] == 'failed' for result in results) else 0

'''*******************************************************************
************************** Main Function!!! **************************
*******************************************************************'''

if __name__ == "__main__":
    raise SystemExit(main())
