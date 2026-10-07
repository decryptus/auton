import asyncio
import unittest
from unittest.mock import Mock, patch
from textual.widgets import DataTable, Static
from dwho.tui.textual import StatusLine
from auton_client.textual_demo import demo_app
from auton_client.textual_tui import OperatorApp, run


class TextualTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    async def test_views_output_errors_filters_and_all_daemons(self):
        app = demo_app()
        async with app.run_test(size=(156, 46)) as pilot:
            await pilot.pause(); app.tick(); await pilot.pause()
            self.assertEqual(app.query_one(DataTable).row_count, 2)
            await pilot.press('enter'); app.tick(); app.tick(); await pilot.pause()
            self.assertEqual(app.view, 'output')
            self.assertIn('3 resources', str(app.query_one('.dw-detail-text', Static).render()))
            await pilot.press('v'); await pilot.pause()
            self.assertIn('no remote command', str(app.query_one('.dw-detail-text', Static).render()))
            app.action_all_daemons(); app.tick(); app.tick(); await pilot.pause()
            self.assertEqual(app.query_one(DataTable).row_count, 3)
            app.action_state(); await pilot.pause()
            self.assertEqual(app.query_one(DataTable).row_count, 0)
            app.action_clear_filters()
            await pilot.click('#dw-nav-3'); app.tick(); app.tick(); await pilot.pause()
            self.assertEqual(app.query_one(DataTable).row_count, 2)

    async def test_old_selection_response_cannot_replace_current_view(self):
        monitor = Mock(); monitor.clients = {'one': None, 'two': None}
        monitor.poll.return_value = None; monitor.refresh.return_value = False
        app = OperatorApp(monitor)
        async with app.run_test() as pilot:
            app.switch_daemon('two')
            monitor.poll.return_value = ('one', None, {'jobs': [{'uid': 'secret:old'}]})
            app.tick()
            self.assertEqual(app.data, {})
            self.assertEqual(app.query_one(DataTable).row_count, 0)

    async def test_partial_errors_and_pause_are_visible(self):
        app = demo_app()
        async with app.run_test() as pilot:
            app.data = {'errors': {'jobs': 'HTTP 403'}, 'partial': True}
            app.update_notice()
            self.assertEqual(app.query_one('#dw-notice', StatusLine).state, 'warning')
            app.action_pause()
            self.assertIn('PAUSED', app.query_one('#dw-notice', StatusLine).render().plain)

    async def test_unchanged_output_refresh_preserves_reader_scroll(self):
        app = demo_app()
        async with app.run_test(size=(140, 40)) as pilot:
            app.monitor.close()
            app.monitor.result = None
            app.paused = True
            app.next_refresh = float('inf')
            app.view = 'output'
            app.data = {'detail': dict(uid='read:example', endpoint='read', status='complete',
                                      return_code=0, stream=['line\n' * 200], errors=[])}
            app.render_snapshot()
            await pilot.pause()
            panel = app.query_one('#dw-details')
            panel.scroll_to(y=50, animate=False)
            await pilot.pause()
            offset = panel.scroll_y
            self.assertGreater(offset, 0)
            app.render_snapshot()
            await pilot.pause()
            self.assertEqual(panel.scroll_y, offset)

    def test_terminal_rejected_before_clients(self):
        with patch('sys.stdin.isatty', return_value=False), patch('auton_client.textual_tui.DaemonClient') as client:
            with self.assertRaisesRegex(ValueError, 'interactive terminal'):
                run([], ['https://example.invalid'])
            client.assert_not_called()

    def test_read_only_launcher_preserves_transport_and_closes_monitor(self):
        with patch('dwho.cli.require_terminal'), patch('auton_client.textual_tui.DaemonClient') as client, patch('auton_client.textual_tui.FleetMonitor') as monitor, patch('auton_client.textual_tui.OperatorApp') as app:
            self.assertEqual(run([], ['https://example.invalid'], transport={'verify': True}), 0)
            self.assertEqual(client.call_args.kwargs['transport'], {'verify': True})
            app.return_value.run.assert_called_once()
            monitor.return_value.close.assert_called_once()

    async def test_refresh_does_not_access_monitor_after_app_shutdown(self):
        app = demo_app()
        async with app.run_test() as pilot:
            await pilot.pause()
        with patch.object(app.monitor, 'poll') as poll, patch.object(app.monitor, 'refresh') as refresh:
            app.tick()
            poll.assert_not_called()
            refresh.assert_not_called()
