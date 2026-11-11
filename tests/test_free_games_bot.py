import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from src.free_games_bot import (
    Bot_Profile,
    Game,
    ITADClient,
    ITADRequestError,
    resolve_shops,
    run_api_check,
    validate_profiles,
)


class ITADClientTests(unittest.TestCase):
    @patch('src.free_games_bot.requests.get')
    def test_get_all_shops_parses_ids(self, get):
        response = Mock()
        shops = [{'id': 61, 'title': 'Steam'}, {'id': 35, 'title': 'GOG'}]
        response.json.return_value = shops
        get.return_value = response

        self.assertEqual(ITADClient().get_all_shops('CA'), shops)
        get.assert_called_once_with(
            ITADClient.STORES_URL,
            params={'country': 'CA'},
            headers=None,
            timeout=15,
        )

    @patch('src.free_games_bot.requests.get')
    def test_get_deals_checks_status_parses_data_and_uses_timeout(self, get):
        response = Mock()
        response.json.return_value = {
            'nextOffset': 1,
            'hasMore': False,
            'list': [{'title': 'A game'}],
        }
        get.return_value = response

        deals = ITADClient().get_deals({'key': 'secret', 'limit': 10})

        self.assertEqual(deals, [{'title': 'A game'}])
        get.assert_called_once_with(
            ITADClient.DEALS_URL,
            params={
                'limit': 10,
                'nondeals': False,
                'mature': False,
                'offset': 0,
            },
            headers={'ITAD-API-Key': 'secret'},
            timeout=15,
        )
        response.raise_for_status.assert_called_once_with()

    @patch('src.free_games_bot.requests.get')
    def test_get_deals_paginates_and_normalizes_legacy_parameters(self, get):
        first_page = Mock()
        first_page.json.return_value = {
            'nextOffset': 210,
            'hasMore': True,
            'list': [{'title': 'Deal'} for _ in range(200)],
        }
        second_page = Mock()
        second_page.json.return_value = {
            'nextOffset': 211,
            'hasMore': False,
            'list': [{'title': 'Third'}],
        }
        get.side_effect = [first_page, second_page]

        deals = ITADClient().get_deals({
            'key': 'secret',
            'country': 'CA',
            'offset': 10,
            'limit': 201,
            'region': '',
            'shops': '35,61',
            'sort': 'cut:desc',
        })

        self.assertEqual(len(deals), 201)
        self.assertEqual(get.call_args_list[0].kwargs['params'], {
            'country': 'CA',
            'shops': '35,61',
            'sort': '-cut',
            'nondeals': False,
            'mature': False,
            'offset': 10,
            'limit': 200,
        })
        self.assertEqual(get.call_args_list[1].kwargs['params']['offset'], 210)
        self.assertEqual(get.call_args_list[1].kwargs['params']['limit'], 1)
        self.assertEqual(get.call_args_list[0].kwargs['headers'], {'ITAD-API-Key': 'secret'})

    @patch('src.free_games_bot.requests.get')
    def test_request_errors_do_not_expose_api_key(self, get):
        get.side_effect = requests.exceptions.ConnectionError('failed key=secret')

        with self.assertRaises(ITADRequestError) as error:
            ITADClient().get_deals({'key': 'secret'})

        self.assertNotIn('secret', str(error.exception))
        self.assertIn('ConnectionError', str(error.exception))
        self.assertIn('Check network access', str(error.exception))

    @patch('src.free_games_bot.requests.get')
    def test_http_errors_include_redacted_response_and_action(self, get):
        response = Mock()
        response.status_code = 401
        response.reason = 'Unauthorized'
        response.headers = {}
        response.json.return_value = {
            'status_code': 401,
            'reason_phrase': 'Rejected key secret-key',
            'details': {'submitted': 'secret-key'},
        }
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
        get.return_value = response

        with self.assertRaises(ITADRequestError) as error:
            ITADClient().get_deals({'key': 'secret-key'})

        message = str(error.exception)
        self.assertIn('HTTP 401 Unauthorized', message)
        self.assertIn('Verify the ITAD API key', message)
        self.assertIn('[REDACTED]', message)
        self.assertNotIn('secret-key', message)

    @patch('src.free_games_bot.requests.get')
    def test_bad_request_includes_api_details_and_parameter_advice(self, get):
        response = Mock()
        response.status_code = 400
        response.reason = 'Bad Request'
        response.headers = {}
        response.json.return_value = {
            'status_code': 400,
            'reason_phrase': 'Invalid shops',
            'details': {'shops': 'Unknown shop ID'},
        }
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
        get.return_value = response

        with self.assertRaises(ITADRequestError) as error:
            ITADClient().get_deals({'key': 'test-key'})

        message = str(error.exception)
        self.assertIn('HTTP 400 Bad Request', message)
        self.assertIn('Unknown shop ID', message)
        self.assertIn('numeric shop IDs', message)

    @patch('src.free_games_bot.requests.get')
    def test_rate_limit_reports_retry_after(self, get):
        response = Mock()
        response.status_code = 429
        response.reason = 'Too Many Requests'
        response.headers = {'Retry-After': '37'}
        response.json.return_value = {'reason_phrase': 'Rate limit exceeded'}
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
        get.return_value = response

        with self.assertRaises(ITADRequestError) as error:
            ITADClient().get_deals({'key': 'test-key'})

        message = str(error.exception)
        self.assertIn('Retry-After: 37 seconds', message)
        self.assertIn('Wait for the indicated Retry-After interval', message)

    @patch('src.free_games_bot.requests.get')
    def test_invalid_json_and_response_shapes_are_reported(self, get):
        response = Mock()
        response.json.side_effect = ValueError('bad json')
        get.return_value = response
        with self.assertRaisesRegex(ITADRequestError, 'invalid JSON'):
            ITADClient().get_deals({})

        response.json.side_effect = None
        response.json.return_value = {'data': {}}
        with self.assertRaisesRegex(ITADRequestError, 'unexpected format'):
            ITADClient().get_deals({})


class ProfileDiagnosticTests(unittest.TestCase):
    def profile_settings(self, tracking_path):
        return {
            'api_params': {'key': 'test-key', 'limit': 10, 'shops': ''},
            'web_hook_address': '',
            'store_filter': {
                'include': ['steam'],
                'exclude_keywords': 'demo, hentai',
            },
            'game_filters': {'max_price_new': 10},
            'tracking_json': str(tracking_path),
        }

    def profile_api_client(self):
        api_client = Mock()
        api_client.get_all_shops.return_value = [{'id': 61, 'title': 'Steam'}]
        return api_client

    def test_api_only_validation_does_not_require_webhook(self):
        settings = {'Test': self.profile_settings('unused.json')}

        self.assertTrue(validate_profiles(settings, require_webhook=False))
        self.assertFalse(validate_profiles(settings, require_webhook=True))

    def test_legacy_keyword_setting_is_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Bot_Profile(
                self.profile_settings(Path(temp_dir) / 'tracking.json'),
                api_client=self.profile_api_client(),
                read_only=True,
            )

        rejected_game = SimpleNamespace(title='A demo, game', price_new=1)
        accepted_game = SimpleNamespace(title='A complete game', price_new=1)
        self.assertFalse(profile.game_filter_check(rejected_game))
        self.assertTrue(profile.game_filter_check(accepted_game))

    def test_v2_free_steam_offer_normalizes_and_passes_filters(self):
        api_client = self.profile_api_client()
        profile_info = self.profile_settings('unused.json')
        profile_info['game_filters'] = {
            'max_price_new': 0,
            'exclude_keywords': 'hentai',
        }
        profile_info['store_filter'] = {'include': ['steam']}
        profile = Bot_Profile(profile_info, api_client=api_client, read_only=True)
        offer = {
            'id': 'game-id',
            'slug': 'sample-free-game',
            'title': 'Sample Free Game',
            'type': 'game',
            'mature': False,
            'deal': {
                'shop': {'id': 61, 'name': 'Steam'},
                'price': {'amount': 0, 'currency': 'CAD'},
                'regular': {'amount': 19.99, 'currency': 'CAD'},
                'cut': 100,
                'timestamp': '2026-11-11T00:00:00Z',
                'expiry': None,
                'url': 'https://itad.link/preserved-affiliate-url/',
                'drm': [],
                'platforms': [],
            },
        }

        game = Game(offer)

        self.assertEqual((game.price_new, game.price_old, game.price_cut), (0, 19.99, 100))
        self.assertEqual(game.currency, 'CAD')
        self.assertEqual(game.url, 'https://itad.link/preserved-affiliate-url/')
        self.assertTrue(profile.game_filter_check(game))

        mature_offer = dict(offer, mature=True)
        hentai_offer = dict(offer, title='Hentai Sample')
        self.assertFalse(profile.game_filter_check(Game(mature_offer)))
        self.assertFalse(profile.game_filter_check(Game(hentai_offer)))

    def test_dry_run_discovery_does_not_create_tracking_or_webhooks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tracking_path = Path(temp_dir) / 'tracking.json'
            api_client = Mock()
            api_client.get_all_shops.return_value = [{'id': 61, 'title': 'Steam'}]
            api_client.get_deals.return_value = [
                {
                    'slug': 'sample-game',
                    'title': 'Sample Game',
                    'mature': False,
                    'deal': {
                        'timestamp': '2026-01-01T00:00:00Z',
                        'drm': [],
                        'expiry': None,
                        'cut': 100,
                        'price': {'amount': 0, 'currency': 'CAD'},
                        'regular': {'amount': 10, 'currency': 'CAD'},
                        'shop': {'id': 61, 'name': 'Steam'},
                        'url': 'https://example.test/game',
                    },
                }
            ]
            profile = Bot_Profile(
                self.profile_settings(tracking_path),
                api_client=api_client,
                read_only=True,
            )

            profile.get_games(prepare_webhooks=False)

            self.assertEqual(profile.candidate_count, 1)
            self.assertEqual(profile.filtered_count, 0)
            self.assertEqual(profile.duplicate_count, 0)
            self.assertEqual(len(profile.games), 1)
            self.assertIsNone(profile.games[0].web_hook)
            self.assertFalse(tracking_path.exists())

    def test_tracking_comparison_matches_url_and_discount(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tracking_path = Path(temp_dir) / 'tracking.json'
            tracking_path.write_text('[{"url": "https://example.test/game", "price_cut": 50}]')
            original_tracking = tracking_path.read_text()
            profile = Bot_Profile(
                self.profile_settings(tracking_path),
                api_client=self.profile_api_client(),
                read_only=True,
            )

            duplicate = SimpleNamespace(url='https://example.test/game', price_cut=50)
            changed_discount = SimpleNamespace(url='https://example.test/game', price_cut=75)

            self.assertTrue(profile.game_tracking_check(duplicate))
            self.assertFalse(profile.game_tracking_check(changed_discount))
            self.assertEqual(tracking_path.read_text(), original_tracking)

    def test_api_check_uses_configured_shop_filter_without_webhook(self):
        api_client = Mock()
        api_client.get_deals.return_value = [{'title': 'Sample Game'}]
        api_client.get_all_shops.return_value = [
            {'id': 61, 'title': 'Steam'},
            {'id': 35, 'title': 'GOG'},
        ]
        profile = self.profile_settings('unused.json')

        count = run_api_check(profile, 'Test', api_client=api_client)

        self.assertEqual(count, 1)
        api_client.get_deals.assert_called_once_with({
            'key': 'test-key',
            'limit': 10,
            'shops': '61',
        })

    def test_exclude_shop_filter_uses_store_list(self):
        api_client = Mock()
        api_client.get_all_shops.return_value = [
            {'id': 61, 'title': 'Steam'},
            {'id': 3, 'title': 'itch.io'},
            {'id': 35, 'title': 'GOG'},
        ]

        self.assertEqual(resolve_shops({'exclude': ['itchio']}, api_client, 'CA'), '35,61')
        api_client.get_all_shops.assert_called_once_with('CA')

    def test_exclude_filter_ignores_shop_not_active_in_country(self):
        api_client = Mock()
        api_client.get_all_shops.return_value = [
            {'id': 61, 'title': 'Steam'},
            {'id': 35, 'title': 'GOG'},
        ]

        self.assertEqual(resolve_shops({'exclude': ['itchio']}, api_client, 'CA'), '35,61')

    def test_include_filter_rejects_unknown_shop(self):
        api_client = Mock()
        api_client.get_all_shops.return_value = [{'id': 61, 'title': 'Steam'}]

        with self.assertRaisesRegex(ValueError, 'Unknown active ITAD shop: itchio'):
            resolve_shops({'include': ['itchio']}, api_client, 'CA')

    def test_invalid_shop_filter_fails_before_network_access(self):
        api_client = Mock()

        with self.assertRaisesRegex(ValueError, 'list of shop IDs'):
            resolve_shops({'exclude': 'itchio'}, api_client)

        api_client.get_all_shops.assert_not_called()


if __name__ == '__main__':
    unittest.main()
