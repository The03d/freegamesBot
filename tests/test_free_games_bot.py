import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from src.delivery_state import DeliveryState
from src.free_games_bot import (
    Bot_Profile,
    Game,
    ITADClient,
    ITADRequestError,
    run_profile,
    manage_uncertain_deliveries,
    resolve_shops,
    run_api_check,
    validate_profiles,
)


class DeliveryStateTests(unittest.TestCase):
    def test_queue_and_daily_reservations_survive_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'delivery.sqlite3'
            state = DeliveryState(path)
            item = {'id': 'game', 'deal': {'shop': {'id': 61}, 'cut': 100}}
            state.enqueue('profile', item)
            state.enqueue('profile', item)
            entry_id, _ = state.queued('profile')[0]
            delivery_id = state.reserve('profile', entry_id, '2026-11-11', 1)
            self.assertEqual(state.counts('profile', '2026-11-11'), (1, 0))
            self.assertEqual(state.counts('other-profile', '2026-11-11'), (0, 0))
            state.close()

            state = DeliveryState(path)
            self.assertEqual(len(state.uncertain('profile')), 1)
            self.assertEqual(state.queued('profile'), [])
            state.confirm(delivery_id, {'url': 'deal', 'price_cut': 100})
            self.assertEqual(state.counts('profile', '2026-11-11'), (1, 1))
            self.assertEqual(state.counts('profile', '2026-11-12'), (0, 0))
            self.assertEqual(len(state.sent_records('profile')), 1)
            state.close()

    def test_uncertain_delivery_requires_confirmation_and_can_be_requeued(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict('os.environ', {'FREEGAMESBOT_DATA_DIR': temp_dir}):
            state = DeliveryState(Path(temp_dir) / 'delivery-state.sqlite3')
            item = {'id': 'game', 'title': 'Game', 'deal': {'shop': {'id': 61, 'name': 'Steam'}, 'cut': 100}}
            state.enqueue('profile', item)
            entry_id, _ = state.queued('profile')[0]
            changed_item = dict(item, deal=dict(item['deal'], cut=80))
            state.enqueue('profile', changed_item)
            delivery_id = state.reserve('profile', entry_id, '2026-11-11', 10)
            state.enqueue('profile', changed_item)
            self.assertTrue(state.blocked('profile', 'game', 61))
            self.assertEqual(len(state.queued('profile')), 1)
            state.close()
            with self.assertRaisesRegex(ValueError, 'confirm'):
                manage_uncertain_deliveries({}, 'profile', delivery_id, 'not-sent')
            manage_uncertain_deliveries({}, 'profile', delivery_id, 'not-sent', confirmed=True)
            state = DeliveryState(Path(temp_dir) / 'delivery-state.sqlite3')
            self.assertEqual(state.counts('profile', '2026-11-11'), (0, 0))
            self.assertEqual(len(state.queued('profile')), 2)
            self.assertFalse(state.blocked('profile', 'game', 61))
            state.close()

class QueuedRunTests(unittest.TestCase):
    def make_offer(self, index):
        return {
            'id': 'game-{}'.format(index), 'title': 'Game {}'.format(index), 'mature': False,
            'deal': {
                'timestamp': '2026-01-01T00:00:00Z', 'expiry': None, 'drm': [], 'cut': 100,
                'price': {'amount': 0, 'currency': 'CAD'},
                'regular': {'amount': 50, 'currency': 'CAD'},
                'shop': {'id': 61, 'name': 'Steam'}, 'url': 'https://example.test/{}'.format(index),
            },
        }

    def test_daily_cap_and_queue_across_runs(self):
        offers = [self.make_offer(index) for index in range(25)]
        client = Mock()
        client.get_all_shops.return_value = [{'id': 61, 'title': 'Steam'}]
        client.iter_deal_pages.side_effect = lambda params: iter([offers])
        client.get_current_prices.side_effect = lambda ids, params: [
            {'id': item['id'], 'deals': [item['deal']]} for item in offers if item['id'] in ids
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = {
                'api_params': {'key': 'key', 'country': 'CA', 'limit': None},
                'store_filter': {'include': ['Steam']}, 'game_filters': {'max_price_new': 0},
                'web_hook_address': 'https://discord.com/api/webhooks/test/token',
                'tracking_json': str(Path(temp_dir) / 'tracking.json'),
            }
            with patch.dict('os.environ', {'FREEGAMESBOT_DATA_DIR': temp_dir}), \
                    patch('src.free_games_bot.ITADClient', return_value=client) as client_class, \
                    patch('src.free_games_bot.requests.post', return_value=Mock()) as post, \
                    patch('src.free_games_bot.time.sleep'), \
                    patch('src.free_games_bot._local_day', return_value='2026-11-11') as day:
                client_class.PAGE_LIMIT = 200
                first = run_profile(profile, 'Test')
                Path(profile['tracking_json']).write_text('[]')
                second = run_profile(profile, 'Test')
                self.assertEqual((first['sent'], first['queued'], second['sent']), (10, 15, 0))
                self.assertEqual(len(json.loads(Path(profile['tracking_json']).read_text())), 10)
                self.assertEqual(post.call_count, 10)
                day.return_value = '2026-11-12'
                third = run_profile(profile, 'Test')
                self.assertEqual((third['sent'], third['queued']), (10, 5))
                self.assertEqual(post.call_count, 20)
                self.assertEqual(len(json.loads(Path(profile['tracking_json']).read_text())), 20)

    def test_expired_and_changed_price_are_not_posted(self):
        offers = [self.make_offer(0), self.make_offer(1)]
        refreshed = [self.make_offer(0), self.make_offer(1)]
        refreshed[0]['deal']['expiry'] = '2000-01-01T00:00:00Z'
        refreshed[1]['deal']['price']['amount'] = 5
        client = Mock()
        client.get_all_shops.return_value = [{'id': 61, 'title': 'Steam'}]
        client.iter_deal_pages.side_effect = lambda params: iter([offers])
        client.get_current_prices.return_value = [
            {'id': item['id'], 'deals': [item['deal']]} for item in refreshed
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = {
                'api_params': {'key': 'key', 'country': 'CA'}, 'store_filter': {'include': ['Steam']},
                'game_filters': {'max_price_new': 0}, 'tracking_json': str(Path(temp_dir) / 'tracking.json'),
                'web_hook_address': 'https://discord.com/api/webhooks/test/token',
            }
            with patch.dict('os.environ', {'FREEGAMESBOT_DATA_DIR': temp_dir}), \
                    patch('src.free_games_bot.ITADClient', return_value=client) as client_class, \
                    patch('src.free_games_bot.requests.post') as post:
                client_class.PAGE_LIMIT = 200
                result = run_profile(profile, 'Test')
            self.assertEqual((result['sent'], result['queued']), (0, 0))
            post.assert_not_called()

    def test_timeout_remains_uncertain_and_is_not_resent(self):
        offer = self.make_offer(0)
        client = Mock()
        client.get_all_shops.return_value = [{'id': 61, 'title': 'Steam'}]
        client.iter_deal_pages.side_effect = lambda params: iter([[offer]])
        client.get_current_prices.return_value = [{'id': offer['id'], 'deals': [offer['deal']]}]
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = {
                'api_params': {'key': 'key', 'country': 'CA'}, 'store_filter': {'include': ['Steam']},
                'game_filters': {'max_price_new': 0}, 'tracking_json': str(Path(temp_dir) / 'tracking.json'),
                'web_hook_address': 'https://discord.com/api/webhooks/test/token',
            }
            with patch.dict('os.environ', {'FREEGAMESBOT_DATA_DIR': temp_dir}), \
                    patch('src.free_games_bot.ITADClient', return_value=client) as client_class, \
                    patch('src.free_games_bot.requests.post', side_effect=requests.exceptions.ReadTimeout) as post:
                client_class.PAGE_LIMIT = 200
                first = run_profile(profile, 'Test')
                second = run_profile(profile, 'Test')
            self.assertEqual((first['status'], first['uncertain'], second['uncertain']), ('partial', 1, 1))
            self.assertEqual(post.call_count, 1)

    def test_partial_discovery_still_processes_saved_candidates(self):
        offer = self.make_offer(0)
        client = Mock()
        client.get_all_shops.return_value = [{'id': 61, 'title': 'Steam'}]
        def partial_pages(params):
            yield [offer]
            raise ITADRequestError('rate limited')
        client.iter_deal_pages.side_effect = partial_pages
        client.get_current_prices.return_value = [{'id': offer['id'], 'deals': [offer['deal']]}]
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = {
                'api_params': {'key': 'key', 'country': 'CA'}, 'store_filter': {'include': ['Steam']},
                'game_filters': {'max_price_new': 0}, 'tracking_json': str(Path(temp_dir) / 'tracking.json'),
                'web_hook_address': 'https://discord.com/api/webhooks/test/token',
            }
            with patch.dict('os.environ', {'FREEGAMESBOT_DATA_DIR': temp_dir}), \
                    patch('src.free_games_bot.ITADClient', return_value=client) as client_class, \
                    patch('src.free_games_bot.requests.post', return_value=Mock()) as post, \
                    patch('src.free_games_bot.time.sleep'):
                client_class.PAGE_LIMIT = 200
                result = run_profile(profile, 'Test')
            self.assertEqual((result['status'], result['sent']), ('partial', 1))
            self.assertTrue(result['discovery_incomplete'])
            post.assert_called_once()

    def test_price_verification_failure_keeps_queue_without_posting(self):
        offer = self.make_offer(0)
        client = Mock()
        client.get_all_shops.return_value = [{'id': 61, 'title': 'Steam'}]
        client.iter_deal_pages.side_effect = lambda params: iter([[offer]])
        client.get_current_prices.side_effect = ITADRequestError('prices unavailable')
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = {
                'api_params': {'key': 'key', 'country': 'CA'}, 'store_filter': {'include': ['Steam']},
                'game_filters': {'max_price_new': 0}, 'tracking_json': str(Path(temp_dir) / 'tracking.json'),
                'web_hook_address': 'https://discord.com/api/webhooks/test/token',
            }
            with patch.dict('os.environ', {'FREEGAMESBOT_DATA_DIR': temp_dir}), \
                    patch('src.free_games_bot.ITADClient', return_value=client) as client_class, \
                    patch('src.free_games_bot.requests.post') as post:
                client_class.PAGE_LIMIT = 200
                result = run_profile(profile, 'Test')
                state = DeliveryState(Path(temp_dir) / 'delivery-state.sqlite3')
                self.assertEqual(len(state.queued('Test')), 1)
                state.close()
            self.assertEqual(result['status'], 'failed')
            post.assert_not_called()


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

    def test_unlimited_deals_exceed_old_limit_with_bounded_pages(self):
        client = ITADClient()
        def page_response(url, params, endpoint, api_key):
            offset = params['offset']
            return {
                'list': [{'title': 'Deal'}] * (200 if offset < 4000 else 1),
                'hasMore': offset < 4000,
                'nextOffset': offset + 200,
            }
        with patch.object(client, '_get_json', side_effect=page_response) as request:
            self.assertEqual(len(client.get_deals({'limit': None})), 4001)
        self.assertEqual(request.call_count, 21)
        self.assertTrue(all(call.args[1]['limit'] == 200 for call in request.call_args_list))

    def test_deals_reject_zero_limit(self):
        with self.assertRaisesRegex(ValueError, 'at least 1'):
            ITADClient().get_deals({'limit': 0})

    @patch('src.free_games_bot.requests.post')
    def test_current_prices_use_game_ids_country_and_shops(self, post):
        post.return_value.json.return_value = [{'id': 'game', 'deals': []}]
        params = {'country': 'CA', 'key': 'private', 'shops': '61', 'nondeals': False}
        self.assertEqual(ITADClient().get_current_prices(['game'], params), [{'id': 'game', 'deals': []}])
        post.assert_called_once_with(
            ITADClient.PRICES_URL, params={'country': 'CA', 'deals': True, 'shops': '61'},
            json=['game'], headers={'ITAD-API-Key': 'private'}, timeout=15,
        )

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

    def test_include_keywords_matches_any_title_word_and_empty_allows_all(self):
        settings = self.profile_settings('unused.json')
        settings['game_filters']['include_keywords'] = 'adventure, PUZZLE'
        profile = Bot_Profile(settings, api_client=self.profile_api_client(), read_only=True)

        self.assertTrue(profile.game_filter_check(SimpleNamespace(title='A puzzle, game', price_new=1)))
        self.assertFalse(profile.game_filter_check(SimpleNamespace(title='A platform game', price_new=1)))
        profile.game_filters['include_keywords'] = ' , '
        self.assertTrue(profile.game_filter_check(SimpleNamespace(title='A platform game', price_new=1)))

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

    def test_only_successfully_sent_games_are_added_to_tracking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tracking_path = Path(temp_dir) / 'tracking.json'
            profile = Bot_Profile(
                self.profile_settings(tracking_path),
                api_client=self.profile_api_client(),
            )
            posted_record = {'url': 'https://example.test/posted', 'price_cut': 100}
            failed_record = {'url': 'https://example.test/failed', 'price_cut': 100}
            profile.games = [
                SimpleNamespace(
                    send_web_hook=Mock(return_value=True),
                    output_record=Mock(return_value=posted_record),
                ),
                SimpleNamespace(
                    send_web_hook=Mock(return_value=False),
                    output_record=Mock(return_value=failed_record),
                ),
            ]

            with patch('src.free_games_bot.time.sleep'):
                self.assertEqual(profile.send_games(), (1, 1))
            profile.update_tracking_json()

            self.assertEqual(json.loads(tracking_path.read_text()), [posted_record])

    def test_run_profile_records_stats_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_info = {
                'api_params': {'key': 'private-api-key'},
                'web_hook_address': 'https://discord.test/private-webhook',
            }
            bot = Mock()
            bot.candidate_count = 5
            bot.filtered_count = 2
            bot.duplicate_count = 1
            bot.games = [Mock(), Mock()]
            bot.sent_count = 1
            bot.failed_count = 1

            with patch.dict('os.environ', {'FREEGAMESBOT_DATA_DIR': temp_dir}), \
                    patch('src.free_games_bot.validate_profiles', return_value=True), \
                    patch('src.free_games_bot.Bot_Profile', side_effect=RuntimeError(
                        'failure private-api-key https://discord.test/private-webhook'
                    )):
                result = run_profile(profile_info, 'Test')

            history = (Path(temp_dir) / 'run-history.jsonl').read_text().splitlines()
            record = json.loads(history[0])
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('private-api-key', record['error'])
            self.assertNotIn('private-webhook', record['error'])

    def test_run_profile_records_success_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_info = {
                'api_params': {'key': 'test-key'},
                'web_hook_address': 'https://discord.test/webhook',
            }
            bot = Mock()
            bot.candidate_count = 5
            bot.filtered_count = 2
            bot.duplicate_count = 1
            bot.games = [Mock(), Mock()]
            bot.sent_count = 2
            bot.failed_count = 0

            def execute(bot, state, state_id, maximum, record):
                record.update(candidates=5, filtered=2, duplicates=1, eligible=2, sent=2, failed=0)
            with patch.dict('os.environ', {'FREEGAMESBOT_DATA_DIR': temp_dir}), \
                    patch('src.free_games_bot.validate_profiles', return_value=True), \
                    patch('src.free_games_bot.Bot_Profile', return_value=bot), \
                    patch('src.free_games_bot._execute_queued_profile', side_effect=execute):
                result = run_profile(profile_info, 'Test')

            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['sent'], 2)
            self.assertEqual(result['failed'], 0)

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
