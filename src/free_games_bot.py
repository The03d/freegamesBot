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

try:
    from .delivery_state import DeliveryState
except ImportError:
    from delivery_state import DeliveryState

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_data_directory(project_root=None):
    return Path(project_root if project_root is not None else PROJECT_ROOT) / 'data'


def project_settings_path(project_root=None, allow_legacy=False):
    root = Path(project_root if project_root is not None else PROJECT_ROOT)
    current = project_data_directory(root) / 'settings.json'
    legacy = root / 'settings.json'
    if current.exists() and legacy.exists() and current.read_bytes() != legacy.read_bytes():
        raise ValueError('Conflicting settings files: {} and {}'.format(legacy, current))
    if allow_legacy and not current.exists() and legacy.exists():
        return legacy
    return current


def resolve_tracking_path(value, project_root=None, allow_legacy=False):
    root = Path(project_root if project_root is not None else PROJECT_ROOT)
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parent != Path('.'):
        return root / path
    current = project_data_directory(root) / path
    legacy = root / path
    if current.exists() and legacy.exists() and current.read_bytes() != legacy.read_bytes():
        raise ValueError('Conflicting tracking files: {} and {}'.format(legacy, current))
    if allow_legacy and not current.exists() and legacy.exists():
        return legacy
    return current

'''*******************************************************************
******************** Custom Webhooks Classes!!! **********************
*******************************************************************'''

class DiscordWebhookSender:
    def __init__(self, webhook_url, content=None):
        self.webhook_url = webhook_url
        self.content = content
        self.last_result = 'rejected'

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
            self.last_result = 'uncertain'
            request = requests.post(self.webhook_url, json=payload, timeout=10)
            request.raise_for_status()
            self.last_result = 'sent'
            return True
        except requests.exceptions.HTTPError as error:
            response = error.response
            if response is None:
                print("Error: Discord webhook rejected the request (HTTP status unavailable).")
            else:
                if 400 <= response.status_code < 500 and response.status_code != 408:
                    self.last_result = 'rejected'
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
        self.last_result = 'rejected'
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
                self.last_result = 'uncertain'
                request = requests.post(self.webhook_url,
                    data=json.dumps(payload),
                    headers={'Content-Type': 'application/json'}, timeout=10)

                request.raise_for_status()

                self.last_result = 'sent'
                return True

            except requests.exceptions.RequestException as error:
                response = getattr(error, 'response', None)
                if response is not None and 400 <= response.status_code < 500 and response.status_code != 408:
                    self.last_result = 'rejected'
                print('Error: Slack webhook request failed ({}).'.format(type(error).__name__))
                return False


class ITADRequestError(RuntimeError):
    pass


class ITADClient:
    STORES_URL = "https://api.isthereanydeal.com/service/shops/v1"
    DEALS_URL = "https://api.isthereanydeal.com/deals/v2"
    PRICES_URL = "https://api.isthereanydeal.com/games/prices/v3"
    TIMEOUT = 15
    PAGE_LIMIT = 200

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

    def get_current_prices(self, game_ids, params):
        query = {'country': params.get('country') or 'US', 'deals': not params.get('nondeals', False)}
        if params.get('shops'):
            query['shops'] = params['shops']
        api_key = params.get('key')
        try:
            response = requests.post(
                self.PRICES_URL, params=query, json=game_ids,
                headers={'ITAD-API-Key': api_key} if api_key else None, timeout=self.TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as error:
            if error.response is None:
                raise ITADRequestError('ITAD prices request failed with no response details.') from None
            raise ITADRequestError(self._http_error_message('prices', error.response, api_key)) from None
        except requests.exceptions.RequestException as error:
            raise ITADRequestError('ITAD prices request failed ({}).'.format(type(error).__name__)) from None
        try:
            data = response.json()
            if not isinstance(data, list) or not all(
                isinstance(item, dict) and isinstance(item.get('id'), str) and isinstance(item.get('deals'), list)
                for item in data
            ):
                raise ValueError
            return data
        except ValueError:
            raise ITADRequestError('ITAD prices response has an unexpected format.') from None

    def get_deals(self, params):
        return [item for page in self.iter_deal_pages(params) for item in page]

    def iter_deal_pages(self, params):
        query = dict(params)
        api_key = query.pop('key', None)
        requested_limit = query.pop('limit', self.PAGE_LIMIT)
        total_limit = None if requested_limit is None else int(self.PAGE_LIMIT if requested_limit == '' else requested_limit)
        if total_limit is not None and total_limit < 1:
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

        received = 0
        while total_limit is None or received < total_limit:
            page_limit = self.PAGE_LIMIT if total_limit is None else min(self.PAGE_LIMIT, total_limit - received)
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

            page = page[:page_limit]
            received += len(page)
            yield page
            if not has_more or (total_limit is not None and received >= total_limit):
                break
            if next_offset <= offset:
                raise ITADRequestError("ITAD deals pagination returned a non-advancing offset.")
            offset = next_offset

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
            "exclude_keywords" : self.passes_exclude_keywords,
            "include_keywords" : self.passes_include_keywords
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

            self.tracking_json = resolve_tracking_path(profile_info.get('tracking_json', ''), allow_legacy=read_only)
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

    def passes_include_keywords(self, game):
        keywords = {
            keyword.strip().lower()
            for keyword in self.game_filters.get('include_keywords', '').split(',')
            if keyword.strip()
        }
        title_words = set(
            game.title.lower().translate(str.maketrans('', '', string.punctuation)).split()
        )
        return not keywords or not keywords.isdisjoint(title_words)

    def load_tracking_info(self, create_if_missing=True):
        try:
            with open(self.tracking_json, 'r') as tracking_file:
                return json.load(tracking_file)
        except FileNotFoundError:
            if not create_if_missing:
                return []
            Path(self.tracking_json).parent.mkdir(parents=True, exist_ok=True)
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

        tracking_path = Path(self.tracking_json)
        temporary = tracking_path.with_name(tracking_path.name + '.tmp')
        with temporary.open('w') as tracking_file:
            json.dump(tracked_records, tracking_file, indent=4)
        temporary.replace(tracking_path)
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
            "max_posts_per_day": 10,
            "store_filter": { 
                                "exclude": ["itchio"]
                            },
            "game_filters": {
                "max_price_new": 0.00,
                "exclude_keywords": "hentai,demo",
                "include_keywords": ""
            },
            "tracking_json" : "posted_default.json"
  } }, sort_keys=True, indent=4 )

def load_json_settings(json_file, create_if_missing=True):
    path = Path(json_file)
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(settings, dict):
            raise ValueError('Settings must contain a profile object.')
        print( "...............JSON settings loaded.\n" )
    except FileNotFoundError:
        if not create_if_missing:
            raise
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as settings_file:
            settings_file.write(default_settings)
        settings = json.loads(default_settings)
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


def migrate_project_data(project_root=None):
    root = Path(project_root if project_root is not None else PROJECT_ROOT)
    data_directory = project_data_directory(root)
    migration_lock = acquire_profile_run_lock('__project_data_migration__:' + str(root.resolve()))
    if migration_lock is None:
        raise ValueError('Project data migration is already running.')
    profile_locks = []
    try:
        legacy_settings = root / 'settings.json'
        new_settings = data_directory / 'settings.json'
        if legacy_settings.exists() and new_settings.exists() and legacy_settings.read_bytes() != new_settings.read_bytes():
            raise ValueError('Conflicting settings files: {} and {}'.format(legacy_settings, new_settings))
        settings_path = new_settings if new_settings.exists() else legacy_settings
        settings = {}
        settings_content = None
        if settings_path.exists():
            settings_content = settings_path.read_bytes()
            settings = json.loads(settings_content)
            if not isinstance(settings, dict) or not all(isinstance(profile, dict) for profile in settings.values()):
                raise ValueError('Settings must contain a profile object.')

        sources = set(root.glob('posted_*.json'))
        absolute_paths = set()
        bare_paths = set()
        for profile in settings.values():
            tracking = profile.get('tracking_json')
            if tracking:
                path = Path(tracking)
                if path.is_absolute():
                    absolute_paths.add(path)
                elif path.parent == Path('.'):
                    bare_paths.add(root / path)
        if absolute_paths & bare_paths:
            raise ValueError('A tracking file is shared by absolute and bare paths; resolve it before migration.')
        sources = (sources | bare_paths) - absolute_paths
        if legacy_settings.exists():
            sources.add(legacy_settings)
        sources = {path for path in sources if path.is_file()}
        if not sources:
            return

        for state_id in sorted({profile.get('state_id', name) for name, profile in settings.items()}):
            lock_file = acquire_profile_run_lock(state_id)
            if lock_file is None:
                raise ValueError('A profile is running; retry project data migration after it finishes.')
            profile_locks.append(lock_file)
        if settings_content is not None and settings_path.read_bytes() != settings_content:
            raise ValueError('Settings changed during project data migration.')
        snapshots = {path: path.read_bytes() for path in sources}
        for source, content in snapshots.items():
            destination = data_directory / source.name
            if destination.exists() and destination.read_bytes() != content:
                raise ValueError('Conflicting project data files: {} and {}'.format(source, destination))

        if snapshots:
            data_directory.mkdir(parents=True, exist_ok=True)
        for source in sorted(snapshots, key=lambda path: path.name == 'settings.json'):
            destination = data_directory / source.name
            content = snapshots[source]
            if source.read_bytes() != content:
                raise ValueError('Project data changed during migration: {}'.format(source))
            if not destination.exists():
                try:
                    with destination.open('xb') as target:
                        target.write(content)
                        target.flush()
                        os.fsync(target.fileno())
                except FileExistsError:
                    pass
            if destination.read_bytes() != content:
                raise ValueError('Could not verify migrated project data: {}'.format(destination))
        for source, content in snapshots.items():
            if source.read_bytes() != content:
                raise ValueError('Project data changed during migration: {}'.format(source))
        for source in snapshots:
            source.unlink()
    finally:
        for lock_file in reversed(profile_locks):
            release_profile_run_lock(lock_file)
        release_profile_run_lock(migration_lock)


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


def _local_day():
    return datetime.now().astimezone().date().isoformat()


def _sync_delivery_tracking(bot, state, state_id):
    records = state.sent_records(state_id)
    tracked = {(record.get('url'), record.get('price_cut', 0.0)) for record in bot.tracking_info}
    if any((record.get('url'), record.get('price_cut', 0.0)) not in tracked for record in records):
        bot.posted_records = records
        bot.update_tracking_json()


def _execute_queued_profile(bot, state, state_id, maximum, run_record):
    _sync_delivery_tracking(bot, state, state_id)
    used, sent_today = state.counts(state_id, _local_day())
    if used < maximum:
        try:
            for page in bot.api_client.iter_deal_pages(bot.api_params):
                for item in page:
                    run_record['candidates'] += 1
                    game = Game(item)
                    if not bot.game_filter_check(game):
                        run_record['filtered'] += 1
                    elif bot.game_tracking_check(game):
                        run_record['duplicates'] += 1
                    else:
                        state.enqueue(state_id, item)
                        run_record['eligible'] += 1
        except ITADRequestError as error:
            message = str(error)
            api_key = bot.api_params.get('key')
            if api_key:
                message = message.replace(api_key, '[REDACTED]')
            print('Discovery incomplete: {}. Processing saved candidates.'.format(message))
            run_record['discovery_incomplete'] = True

        queued = state.queued(state_id)
        for start in range(0, len(queued), ITADClient.PAGE_LIMIT):
            batch = queued[start:start + ITADClient.PAGE_LIMIT]
            game_ids = list(dict.fromkeys(item['id'] for _, item in batch))
            prices = bot.api_client.get_current_prices(game_ids, bot.api_params)
            by_id = {item['id']: item['deals'] for item in prices}
            if any(game_id not in by_id for game_id in game_ids):
                raise ITADRequestError('ITAD prices response omitted queued games; queue retained.')
            for entry_id, item in batch:
                day = _local_day()
                used, _ = state.counts(state_id, day)
                if used >= maximum:
                    break
                shop_id = item['deal']['shop']['id']
                if state.blocked(state_id, item['id'], shop_id):
                    continue
                allowed_shops = set(bot.api_params.get('shops', '').split(','))
                current = next((deal for deal in by_id[item['id']]
                                if deal.get('shop', {}).get('id') == shop_id), None)
                if current is None or str(shop_id) not in allowed_shops:
                    state.remove(entry_id)
                    continue
                item['deal'] = current
                game = Game(item)
                if game.expiry:
                    expiry = datetime.fromisoformat(game.expiry.replace('Z', '+00:00'))
                    if expiry.tzinfo is None:
                        raise ValueError('ITAD returned a deal expiry without a time zone.')
                    if expiry <= datetime.now(timezone.utc):
                        state.remove(entry_id)
                        continue
                if not bot.game_filter_check(game) or bot.game_tracking_check(game):
                    state.remove(entry_id)
                    continue
                game.create_web_hook(bot.web_hook_address)
                if game.web_hook is None:
                    raise ValueError('No valid webhook configured.')
                state.update_payload(entry_id, item)
                delivery_id = state.reserve(state_id, entry_id, _local_day(), maximum)
                if delivery_id is None:
                    break
                if game.send_web_hook():
                    state.confirm(delivery_id, game.output_record())
                    run_record['sent'] += 1
                    _sync_delivery_tracking(bot, state, state_id)
                else:
                    run_record['failed'] += 1
                    if game.web_hook.last_result == 'rejected':
                        state.reject(delivery_id)
                    else:
                        print('Delivery {} is uncertain and requires review.'.format(delivery_id))
                    break
                time.sleep(2.0)
            else:
                continue
            break
    used, sent_today = state.counts(state_id, _local_day())
    run_record['queued'] = len(state.queued(state_id))
    run_record['uncertain'] = len(state.uncertain(state_id))
    run_record['sent_today'] = sent_today
    run_record['remaining_today'] = max(0, maximum - used)
    print('Queue: {}; sent today: {}; uncertain: {}; remaining: {}.'.format(
        run_record['queued'], sent_today, run_record['uncertain'], run_record['remaining_today']
    ))


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
        state_id = profile_info.get('state_id', profile_name)
        lock_file = acquire_profile_run_lock(state_id)
        if lock_file is None:
            run_record['status'] = 'skipped'
            run_record['error'] = 'Another run for this profile is already active.'
            print("{} Skipping duplicate profile run.".format(run_record['error']))
        elif not validate_profiles({profile_name: profile_info}):
            raise ValueError("Profile configuration is incomplete.")
        else:
            maximum = profile_info.get('max_posts_per_day', 10)
            if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
                raise ValueError('Daily post limit must be a positive whole number.')
            bot = Bot_Profile(profile_info, profile_name)
            state = DeliveryState(get_app_data_directory() / 'delivery-state.sqlite3')
            try:
                _execute_queued_profile(bot, state, state_id, maximum, run_record)
            finally:
                state.close()
            run_record['status'] = 'partial' if run_record['failed'] or run_record.get('discovery_incomplete') else 'success'
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


def manage_uncertain_deliveries(profile_info, profile_name, delivery_id=None, outcome=None, confirmed=False):
    state_id = profile_info.get('state_id', profile_name)
    lock_file = acquire_profile_run_lock(state_id)
    if lock_file is None:
        raise ValueError('Another run for this profile is already active.')
    try:
        state = DeliveryState(get_app_data_directory() / 'delivery-state.sqlite3')
        try:
            uncertain = state.uncertain(state_id)
            if delivery_id is None:
                for entry_id, day, item in uncertain:
                    print('{} | {} | {} | {}'.format(entry_id, day, item['title'], item['deal']['shop']['name']))
                print('{} uncertain deliveries.'.format(len(uncertain)))
                return
            if not confirmed or outcome not in ('sent', 'not-sent'):
                raise ValueError('Specify --outcome sent or not-sent and --confirm after checking the channel.')
            item = next((item for entry_id, _, item in uncertain if entry_id == delivery_id), None)
            if item is None:
                raise ValueError('No uncertain delivery with that ID for this profile.')
            if outcome == 'sent':
                state.confirm(delivery_id, Game(item).output_record())
                print('Delivery marked sent; tracking will sync on the next profile run.')
            else:
                state.reject(delivery_id)
                print('Delivery returned to the queue.')
        finally:
            state.close()
    finally:
        release_profile_run_lock(lock_file)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find deals and post them to configured webhooks.")
    diagnostic_modes = parser.add_mutually_exclusive_group()
    diagnostic_modes.add_argument("--check-api", metavar="PROFILE", help="Test ITAD connectivity without a webhook.")
    diagnostic_modes.add_argument("--dry-run", metavar="PROFILE", help="Fetch and filter deals without sending or saving.")
    diagnostic_modes.add_argument("--run-profile", metavar="PROFILE", help="Run one configured profile and record its result.")
    diagnostic_modes.add_argument("--test-webhook", metavar="PROFILE", help="Send one test message without fetching deals or updating tracking.")
    diagnostic_modes.add_argument("--list-uncertain", metavar="PROFILE", help="List deliveries requiring review.")
    diagnostic_modes.add_argument("--resolve-uncertain", metavar="PROFILE", help="Resolve a reviewed delivery.")
    parser.add_argument('--delivery-id', type=int, help='ID of the uncertain delivery to resolve.')
    parser.add_argument('--outcome', choices=['sent', 'not-sent'], help='Confirmed delivery outcome.')
    parser.add_argument('--confirm', action='store_true', help='Confirm the delivery outcome after checking the destination.')
    args = parser.parse_args(argv)

    print(
        "********************************************************************\n"
        "************************ STARTING ITAD BOTS ************************\n"
        "********************************************************************"
    )
    try:
        diagnostic = bool(args.check_api or args.dry_run)
        if not diagnostic:
            migrate_project_data()
        settings_path = project_settings_path(allow_legacy=diagnostic)
        settings = load_json_settings(settings_path, create_if_missing=not diagnostic)
    except (OSError, ValueError) as error:
        print('Settings unavailable ({}): {}'.format(type(error).__name__, error))
        return 1

    if args.check_api or args.dry_run or args.run_profile or args.test_webhook or args.list_uncertain or args.resolve_uncertain:
        profile_name = args.check_api or args.dry_run or args.run_profile or args.test_webhook or args.list_uncertain or args.resolve_uncertain
        profile_info = settings.get(profile_name)
        if profile_info is None:
            print("Unknown profile '{}'.".format(profile_name))
            return 1
        try:
            if args.list_uncertain:
                manage_uncertain_deliveries(profile_info, profile_name)
                return 0
            if args.resolve_uncertain:
                if args.delivery_id is None:
                    raise ValueError('Specify --delivery-id when resolving a delivery.')
                manage_uncertain_deliveries(profile_info, profile_name, args.delivery_id, args.outcome, args.confirm)
                return 0
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
