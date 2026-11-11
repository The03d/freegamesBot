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
        except requests.exceptions.RequestException as error:
            print('Error: %s' % error)
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
    STORES_URL = "https://api.isthereanydeal.com/v01/web/stores/all/"
    DEALS_URL = "https://api.isthereanydeal.com/v01/deals/list/"
    TIMEOUT = 15

    def _get_json(self, url, params, endpoint):
        try:
            response = requests.get(url, params=params, timeout=self.TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as error:
            raise ITADRequestError(
                "ITAD {} request failed ({}).".format(endpoint, type(error).__name__)
            ) from None

        try:
            return response.json()
        except ValueError:
            raise ITADRequestError("ITAD {} returned invalid JSON.".format(endpoint)) from None

    def get_all_shops(self):
        response = self._get_json(self.STORES_URL, None, "store list")
        try:
            shops = response['data']
            if not isinstance(shops, list):
                raise TypeError
            return [shop['id'] for shop in shops]
        except (KeyError, TypeError):
            raise ITADRequestError("ITAD store list response has an unexpected format.") from None

    def get_deals(self, params):
        response = self._get_json(self.DEALS_URL, params, "deals list")
        try:
            deals = response['data']['list']
            if not isinstance(deals, list):
                raise TypeError
            return deals
        except (KeyError, TypeError):
            raise ITADRequestError("ITAD deals list response has an unexpected format.") from None


def resolve_shops(store_filter, api_client):
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

    if 'exclude' in store_filter:
        excluded_shops = set(shops)
        shops = [shop for shop in api_client.get_all_shops() if shop not in excluded_shops]
    return ','.join(str(shop) for shop in shops)


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
            self.api_params['shops'] = self.filter_shops()

            self.games = []

            self.tracking_json = profile_info.get('tracking_json', '')
            self.tracking_info = self.load_tracking_info(create_if_missing=not read_only)

        self.session_info = []
        self.candidate_count = 0
        self.filtered_count = 0
        self.duplicate_count = 0


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

    def filter_shops(self):
        return resolve_shops(self.store_filter, self.api_client)

    def game_filter_check(self, game):
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

            self.session_info.append( game.output_record() )

            if self.game_tracking_check( game ):
                self.duplicate_count += 1
                continue

            if prepare_webhooks:
                game.create_web_hook(self.web_hook_address)
            self.games.append( game )

    def send_games( self ):
        for game in self.games:
            succeeded = game.send_web_hook()
            # self.session_info.append( game.output_record() )
            print("Succeeded?", succeeded)
            time.sleep(2.0)
            # if succeeded:

                
    def update_tracking_json( self ):
        f = open(self.tracking_json, 'w')
        f.write(json.dumps(self.session_info, indent=4))
        print( str(self.tracking_json) + " updated!")

class Game:
    def __init__(self, game_info=None, currency="CAD" ):
        if game_info != None:
            self.added = game_info['added']
            self.drm = game_info['drm']
            self.expiry = game_info['expiry']
            self.plain = game_info['plain']
            self.price_cut = game_info['price_cut']
            self.price_new = game_info['price_new']
            self.price_old = game_info['price_old']
            self.shop = game_info['shop']['name']
            self.title = game_info['title']
            self.plain = game_info['plain']
            self.url = game_info['urls']['buy']

            self.web_hook = None

            # self.output_record = {  "title":    self.title,
            #                         "shop":     self.shop,
            #                         "url":      self.url,
            #                         "added":    self.added,
            #                         "price_cut":    self.price_cut }

        price_str = str(self.price_new) + " " + currency
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
                    "offset": "",
                    "limit": 4000,
                    "region": "",
                    "country": "CA",
                    "shops": "",
                    "sort": "cut:desc"
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
    api_params['shops'] = resolve_shops(profile_info.get('store_filter', {}), api_client)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find deals and post them to configured webhooks.")
    diagnostic_modes = parser.add_mutually_exclusive_group()
    diagnostic_modes.add_argument("--check-api", metavar="PROFILE", help="Test ITAD connectivity without a webhook.")
    diagnostic_modes.add_argument("--dry-run", metavar="PROFILE", help="Fetch and filter deals without sending or saving.")
    args = parser.parse_args(argv)

    print(
        "********************************************************************\n"
        "************************ STARTING ITAD BOTS ************************\n"
        "********************************************************************"
    )
    settings = load_json_settings(settings_json)

    if args.check_api or args.dry_run:
        profile_name = args.check_api or args.dry_run
        profile_info = settings.get(profile_name)
        if profile_info is None:
            print("Unknown profile '{}'.".format(profile_name))
            return 1
        if not validate_profiles({profile_name: profile_info}, require_webhook=False):
            return 1
        try:
            if args.check_api:
                run_api_check(profile_info, profile_name)
            else:
                run_dry_run(profile_info, profile_name)
        except (ITADRequestError, OSError, ValueError, KeyError) as error:
            print("Diagnostic failed: {}".format(error))
            return 1
        return 0

    if not validate_profiles(settings):
        return 1

    for profile in settings:
        bot = Bot_Profile(settings[profile], profile)
        bot.get_games()
        bot.send_games()
        bot.update_tracking_json()
    return 0

'''*******************************************************************
************************** Main Function!!! **************************
*******************************************************************'''

if __name__ == "__main__":
    raise SystemExit(main())
