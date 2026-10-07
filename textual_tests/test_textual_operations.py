import asyncio
import unittest
from unittest.mock import Mock
from textual.widgets import SelectionList, Input
from auton_client.textual_demo import demo_app
from auton_client.preparation_model import PreparationModel

class OperationUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def app(self):
        app = demo_app()
        session = Mock(running=False)
        session.poll.return_value = None
        session.progress.snapshot.return_value = {"revision": 0, "rows": [], "operation_id": None}
        app.preparation = PreparationModel({'one': 'http://one'}, {}, {}, {}, session, {})
        return app

    async def test_preview_cancel_and_exactly_once_submission(self):
        app = self.app()
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(); await pilot.press('e'); await pilot.pause()
            screen = app.screen
            screen.query_one('#op-select-0', SelectionList).select('one')
            screen.query_one('#op-select-4', SelectionList).select('inventory')
            await pilot.click('#op-preview'); await pilot.pause()
            app.preparation.session.start.assert_not_called()
            await pilot.press('escape'); await pilot.pause()
            app.preparation.session.start.assert_not_called()
            await pilot.click('#op-preview'); await pilot.pause()
            await pilot.click('#dw-confirm'); await pilot.pause()
            app.preparation.session.start.assert_called_once()
            screen.confirmed(True)
            app.preparation.session.start.assert_called_once()
            self.assertEqual(app.preparation.mode, 'running')

    async def test_input_dialog_return_and_active_exit_guard(self):
        app = self.app()
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause(); await pilot.press('e'); await pilot.pause()
            screen = app.screen
            await pilot.click('#op-inputs'); await pilot.pause()
            app.screen.query_one(Input).value = '{"args": ["hello"], "env": {}}'
            await pilot.click('#form-submit'); await pilot.pause()
            self.assertIn('hello', app.preparation.input)
            app.preparation.session.running = True
            await pilot.press('escape'); await pilot.pause()
            self.assertIs(app.screen, screen)
            app.preparation.session.running = False
            await pilot.press('escape'); await pilot.pause()
            self.assertIsNot(app.screen, screen)
