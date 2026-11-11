import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

import requests

from src.free_games_bot import DiscordWebhookSender, Game, SlackWebhooks


class DiscordWebhookSenderTests(unittest.TestCase):
    def test_content_payload_omits_embeds(self):
        sender = DiscordWebhookSender('https://discord.com/api/webhooks/test', 'New game')

        self.assertEqual(sender.format_payload(), {'content': 'New game'})

    @patch('src.free_games_bot.requests.post')
    def test_send_posts_json_with_timeout(self, post):
        response = Mock()
        post.return_value = response
        sender = DiscordWebhookSender('https://discord.com/api/webhooks/test', 'New game')

        self.assertTrue(sender.send())

        post.assert_called_once_with(
            'https://discord.com/api/webhooks/test',
            json={'content': 'New game'},
            timeout=10,
        )
        response.raise_for_status.assert_called_once_with()

    @patch('src.free_games_bot.requests.post')
    def test_send_returns_false_for_request_errors(self, post):
        post.side_effect = requests.exceptions.ConnectionError('offline')
        sender = DiscordWebhookSender('https://discord.com/api/webhooks/test', 'New game')

        self.assertFalse(sender.send())

    @patch('src.free_games_bot.requests.post')
    def test_send_returns_false_for_http_errors(self, post):
        response = Mock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError('bad response')
        post.return_value = response
        sender = DiscordWebhookSender('https://discord.com/api/webhooks/test', 'New game')

        self.assertFalse(sender.send())

    @patch('src.free_games_bot.requests.post')
    def test_send_rejects_missing_url_or_content(self, post):
        self.assertFalse(DiscordWebhookSender(None, 'New game').send())
        self.assertFalse(DiscordWebhookSender('https://discord.com/api/webhooks/test').send())

        post.assert_not_called()

    def test_game_routes_discord_and_slack_to_their_senders(self):
        game = Game.__new__(Game)
        game.announce_text = 'New game'

        game.create_web_hook('https://discord.com/api/webhooks/test')
        self.assertIsInstance(game.web_hook, DiscordWebhookSender)

        game.create_web_hook('https://hooks.slack.com/services/test')
        self.assertIsInstance(game.web_hook, SlackWebhooks)

    def test_invalid_webhook_address_is_not_printed_or_sent(self):
        secret_url = 'https://hooks.example.test/private-token'
        game = Game.__new__(Game)
        game.announce_text = 'New game'
        game.title = 'New game'
        game.shop = 'Steam'
        game.web_hook = None
        output = StringIO()

        with redirect_stdout(output):
            game.create_web_hook(secret_url)
            succeeded = game.send_web_hook()

        self.assertFalse(succeeded)
        self.assertNotIn(secret_url, output.getvalue())


if __name__ == '__main__':
    unittest.main()