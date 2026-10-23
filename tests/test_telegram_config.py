import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from channels.telegram import main


class TelegramConfigTests(unittest.TestCase):
    def run_with_config(self, contents, environment=None, arguments=None):
        with TemporaryDirectory() as directory:
            env_file = Path(directory) / '.env'
            env_file.write_text(contents)
            with (
                patch('channels.telegram.ENV_FILE', env_file),
                patch.dict(os.environ, environment or {}, clear=True),
                patch('sys.argv', ['telegram'] + (arguments or [])),
                patch('channels.telegram.run_bot', new_callable=AsyncMock) as bot,
                patch('channels.telegram.getpass.getpass') as prompt,
            ):
                self.assertEqual(main(), 0)
                prompt.assert_not_called()
                return bot.call_args.args

    def test_token_and_owner_are_loaded_from_project_env(self):
        token, _, owner = self.run_with_config('TELEGRAM_BOT_TOKEN=fixture-token\nTELEGRAM_OWNER_ID=100\n')
        self.assertEqual(token, 'fixture-token')
        self.assertEqual(owner, 100)

    def test_exported_token_and_cli_owner_take_precedence(self):
        token, _, owner = self.run_with_config(
            'TELEGRAM_BOT_TOKEN=file-token\nTELEGRAM_OWNER_ID=100\n',
            environment={'TELEGRAM_BOT_TOKEN': 'environment-token'},
            arguments=['--owner-id', '200'],
        )
        self.assertEqual(token, 'environment-token')
        self.assertEqual(owner, 200)

    def test_empty_owner_keeps_capture_disabled(self):
        _, _, owner = self.run_with_config('TELEGRAM_BOT_TOKEN=fixture-token\nTELEGRAM_OWNER_ID=\n')
        self.assertIsNone(owner)


if __name__ == '__main__':
    unittest.main()
