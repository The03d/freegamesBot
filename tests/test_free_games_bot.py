import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from src.free_games_bot import (
    Bot_Profile,
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
        response.json.return_value = {'data': [{'id': 'steam'}, {'id': 'gog'}]}
        get.return_value = response

        self.assertEqual(ITADClient().get_all_shops(), ['steam', 'gog'])
        get.assert_called_once_with(ITADClient.STORES_URL, params=None, timeout=15)

    @patch('src.free_games_bot.requests.get')
    def test_get_deals_checks_status_parses_data_and_uses_timeout(self, get):
        response = Mock()
        response.json.return_value = {'data': {'list': [{'title': 'A game'}]}}
        get.return_value = response

        deals = ITADClient().get_deals({'key': 'secret', 'limit': 10})

        self.assertEqual(deals, [{'title': 'A game'}])
        get.assert_called_once_with(
            ITADClient.DEALS_URL,
            params={'key': 'secret', 'limit': 10},
            timeout=15,
        )
        response.raise_for_status.assert_called_once_with()

    @patch('src.free_games_bot.requests.get')
    def test_request_errors_do_not_expose_api_key(self, get):
        get.side_effect = requests.exceptions.ConnectionError('failed key=secret')

        with self.assertRaises(ITADRequestError) as error:
            ITADClient().get_deals({'key': 'secret'})

        self.assertNotIn('secret', str(error.exception))
        self.assertIn('ConnectionError', str(error.exception))

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

    def test_api_only_validation_does_not_require_webhook(self):
        settings = {'Test': self.profile_settings('unused.json')}

        self.assertTrue(validate_profiles(settings, require_webhook=False))
        self.assertFalse(validate_profiles(settings, require_webhook=True))

    def test_legacy_keyword_setting_is_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Bot_Profile(
                self.profile_settings(Path(temp_dir) / 'tracking.json'),
                api_client=Mock(),
                read_only=True,
            )

        rejected_game = SimpleNamespace(title='A demo, game', price_new=1)
        accepted_game = SimpleNamespace(title='A complete game', price_new=1)
        self.assertFalse(profile.game_filter_check(rejected_game))
        self.assertTrue(profile.game_filter_check(accepted_game))

    def test_dry_run_discovery_does_not_create_tracking_or_webhooks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tracking_path = Path(temp_dir) / 'tracking.json'
            api_client = Mock()
            api_client.get_deals.return_value = [
                {
                    'added': '2026-01-01',
                    'drm': 'steam',
                    'expiry': None,
                    'plain': 'sample-game',
                    'price_cut': 100,
                    'price_new': 0,
                    'price_old': 10,
                    'shop': {'name': 'Steam'},
                    'title': 'Sample Game',
                    'urls': {'buy': 'https://example.test/game'},
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
                api_client=Mock(),
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
        profile = self.profile_settings('unused.json')

        count = run_api_check(profile, 'Test', api_client=api_client)

        self.assertEqual(count, 1)
        api_client.get_deals.assert_called_once_with({
            'key': 'test-key',
            'limit': 10,
            'shops': 'steam',
        })

    def test_exclude_shop_filter_uses_store_list(self):
        api_client = Mock()
        api_client.get_all_shops.return_value = ['steam', 'itchio', 'gog']

        self.assertEqual(resolve_shops({'exclude': ['itchio']}, api_client), 'steam,gog')
        api_client.get_all_shops.assert_called_once_with()

    def test_invalid_shop_filter_fails_before_network_access(self):
        api_client = Mock()

        with self.assertRaisesRegex(ValueError, 'list of shop IDs'):
            resolve_shops({'exclude': 'itchio'}, api_client)

        api_client.get_all_shops.assert_not_called()


if __name__ == '__main__':
    unittest.main()
