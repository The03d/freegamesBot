'''*******************************************************************
*************************                      ***********************
************************* ITAD WEBHOOKS BOT!!! ***********************
*************************                      ***********************
*******************************************************************'''

import requests
import json
from discord_webhooks import DiscordWebhooks
# from slack_webhook import Slack
# import os
# import pprint
import time
import string
allPunctuation = string.punctuation
ignorePunctuation = "," # + "_" + '"' + "/" + "."
excludePunctuation = "".join(ch for ch in allPunctuation if ch not in ignorePunctuation)

'''*******************************************************************
******************** Custom Webhooks Classes!!! **********************
*******************************************************************'''

## I added returns based on whethere there was an error or not
    # and I fixed a bug with the embeds
class DiscordWebhooks_SendErrorHandling(DiscordWebhooks):
    def format_payload(self):
        """
            Formats the data into a JSON object so it can be pushed
            as a payload to Discord.
        """
        # Initializes the default payload structure.
        payload = {}
        embed = None
        # embed = {
        # 'author': {},
        # 'footer': {},
        # 'image': {},
        # 'thumbnail': {},
        # 'fields': []
        # }

        # Attaches data to the payload if provided.
        if self.content:
            payload['content'] = self.content

        if self.title:
            embed['title'] = self.title

        if self.description:
            embed['description'] = self.description

        if self.url:
            embed['url'] = self.url

        if self.color:
            embed['color'] = self.color

        if self.timestamp:
            embed['timestamp'] = self.timestamp

        if self.author_name:
            embed['author']['name'] = self.author_name

        if self.author_url:
            embed['author']['url'] = self.author_url

        if self.author_icon:
            embed['author']['icon_url'] = self.author_icon

        if self.thumbnail_url:
            embed['thumbnail']['url'] = self.thumbnail_url

        if self.image:
            embed['image']['url'] = self.image

        if self.fields:
            embed['fields'] = self.fields

        if self.footer_icon:
            embed['footer']['icon_url'] = self.footer_icon

        if self.footer_text:
            embed['footer']['text'] = self.footer_text

        # If the embed object has content it gets appended to the payload
        if embed:
            payload['embeds'] = []
            payload['embeds'].append(embed)

        return payload

    def send(self):
        """
        Makes a POST request to Discord with the message payload.
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
                # pprint.pprint(payload)
                request = requests.post(self.webhook_url,
                    data=json.dumps(payload),
                    headers={'Content-Type': 'application/json'})
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


'''*******************************************************************
*************************** Bot Classes!!! ***************************
*******************************************************************'''

class Bot_Profile:
    def __init__(self, profile_info=None, profile_name="Unnamed_Profile"):
        print( "************************ STARTING ", profile_name, "************************" )

        self.game_filter_funcs = {
            "min_discount_percent" : lambda game :  game.price_cut >= self.game_filters[ 'min_discount_percent' ],
            "min_price_old" : lambda game :  game.price_old > self.game_filters[ 'min_price_old' ],
            "max_price_new" : lambda game :  game.price_new <= self.game_filters[ 'max_price_new' ],
            ## true if the set that is the words in the title and the set that is the list of keywords to exclude have no overlap... 
            "exclude_keywords" : lambda game : len(set(self.game_filters['exclude_keywords'].split(',')) & set(game.title.lower().translate( str.maketrans('', '', excludePunctuation) ).split(" "))) == 0
        }
            
        self.store_filter_funcs = {
            "include" : lambda : self.store_filter['include'],
            "exclude" : lambda : list( set(self.get_all_shops() ) - set(self.store_filter['exclude']))
        }

        if profile_info != None:
            self.api_params = profile_info['api_params']
            self.web_hook_address = profile_info['web_hook_address']

            self.store_filter = profile_info['store_filter']
            self.api_params['shops'] = self.filter_shops()

            self.game_filters = profile_info['game_filters']
            self.games = []

            self.tracking_json = profile_info['tracking_json']
            self.tracking_info = self.load_tracking_info()

        self.session_info = []


    def load_tracking_info( self ):
        tracking_info = {}
        try:
            tracking_info = json.loads(open(self.tracking_json, 'r').read())
            print( "...............Tracking JSON loaded.\n" )
        except:
            f = open(self.tracking_json, "x")
            f.write( str(tracking_info) )
            f.close
            print( "...............Tracking JSON created.\n" )

        return tracking_info

    def get_all_shops( self ):
        shop_data = requests.get("https://api.isthereanydeal.com/v01/web/stores/all/").content.decode()
        all_shops = json.loads(shop_data)
        shops_list = []
        for shop in all_shops['data']:
            shops_list.append(shop['id'])
        return shops_list

    def filter_shops( self ):        
        shops_to_query = ""
        ## There is only ever one store filter.  They are stored as a name as the key and a list as the value.
            ## In order to get the filter name we have to do some weird stuff with the list of keys. 
        store_filter = list( self.store_filter.keys() )[0]
        if store_filter in self.store_filter_funcs:
            shops = self.store_filter_funcs.get( store_filter )
            shops_to_query = str(shops()).translate( str.maketrans('', '', excludePunctuation)).replace(" ", "")
            return shops_to_query

    def game_filter_check( self, game ):
        for game_filter in self.game_filters:
            filter = self.game_filter_funcs.get( game_filter )
            if not filter(game):
                return False
        return True

    def game_tracking_check( self, game ):
        for past_game in self.tracking_info:
            ## Filtering against past games if they have the same URL and the same discount.
                ## Using the date added posted a lot of games that were simply added at the same discount every day.
            if game.url == past_game['url'] and game.price_cut == past_game.get('price_cut', 0.0):
                return True
        return False

    def get_games( self ):
        game_data = requests.get( 'https://api.isthereanydeal.com/v01/deals/list/', params=self.api_params ).content.decode()
        all_games = json.loads(game_data)
        candidate_games = all_games['data']['list']

        for item in candidate_games:
            game = Game( item )

            if not self.game_filter_check( game ):
                continue

            self.session_info.append( game.output_record() )

            if self.game_tracking_check( game ):
                print("{} from {} Already posted.".format(game.title, game.shop))
                continue

            # print( self.web_hook_address )
            game.create_web_hook( self.web_hook_address )
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
        if "discord.com/api" in web_hook_address:
            self.web_hook = DiscordWebhooks_SendErrorHandling( web_hook_address, content=self.announce_text )
            return

        if "slack.com/services" in web_hook_address:
            self.web_hook = SlackWebhooks( web_hook_address, content=self.announce_text )
            return

        print("No valid webhooks found for", web_hook_address, "?!?!?!")

    def send_web_hook( self ):
        print("*** Posting {} from {}.".format(self.title, self.shop))
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
                                "exclude": ["itchio"], 
                                "exclude_keywords" : "hentai,demo" 
                            },
            "game_filters": { "max_price_new" : 0.00 },
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


def validate_profiles(settings):
    for profile in settings:
        needs_setup = False
        if settings[profile]['api_params']['key'] == "":
            print( "WARNING: It looks like you don't have a ISTHEREANYDEAL.COM api key set up in {}.  Check the documentation for instructions.\n".format(profile) )
            needs_setup = True
        if settings[profile]['web_hook_address'] == "":
            print( "WARNING: It looks like you don't have a Webhooks destination set up in {}.  Check the documentation for instructions.\n".format(profile) )
            needs_setup = True

    if needs_setup:
        print("ITAB BOTS:  Quitting!")
        quit()

'''*******************************************************************
************************** Main Function!!! **************************
*******************************************************************'''

if __name__ == "__main__":
    print(  "********************************************************************\n" +
            "************************ STARTING ITAD BOTS ************************\n" +
            "********************************************************************")
    settings = load_json_settings( settings_json )

    validate_profiles(settings)

    for profile in settings:
        bot = Bot_Profile( settings[profile], profile )
        bot.get_games()
        bot.send_games()
        bot.update_tracking_json()