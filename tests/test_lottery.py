import copy
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'lottery.py'


class CloudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if MODULE.exists():
            spec = importlib.util.spec_from_file_location('cloud_lottery', MODULE)
            cls.mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.assertTrue(MODULE.exists(), 'Cloud implementation is missing')
        self.accounts = [{'label': 'Edge', 'token': 'edge-test-token'},
                         {'label': 'Chrome', 'token': 'chrome-test-token'}]
        self.sites = {}

    def site(self, identity, joined=False, open_round=True, join_error=False,
             auth_error=False, history=None, history2=None):
        class Site:
            def __init__(self):
                self.calls = []
                self.joined = joined
            def request(self, path, body=None):
                self.calls.append((path, body))
                if path == '/me':
                    if auth_error:
                        raise RuntimeError('must-not-log-token')
                    return {'user': {'id': identity}}
                if path == '/lotteries/1':
                    return {'server_time': 1000, 'item': {
                        'public_id': 1, 'round_id': 61, 'draw_state': 'open',
                        'is_visible': True, 'status': 'active', 'drawn_at': None,
                        'draw_at_ts': 2000 if open_round else 999,
                        'my_entry': {'joined': self.joined, 'result': 'pending' if self.joined else 'not_joined'}}}
                if path == '/lotteries/1/join':
                    if join_error:
                        raise RuntimeError('must-not-log-token')
                    self.joined = True
                    return {}
                if path.startswith('/me/lotteries?'):
                    page = 2 if 'page=2&' in path else 1
                    return {'items': copy.deepcopy(history2 if page == 2 else history) or [],
                            'pagination': {'page': page, 'total_pages': 2 if history2 else 1}}
                raise AssertionError(path)
        return Site()

    def execute(self, mode='join'):
        return self.mod.run_accounts(self.accounts, mode, self.sites.__getitem__)

    def test_both_distinct_accounts_join_and_verify(self):
        self.sites = {'edge-test-token': self.site(7), 'chrome-test-token': self.site(8)}
        records = self.execute()
        self.assertEqual([r['participation'] for r in records], ['joined', 'joined'])
        for site in self.sites.values():
            self.assertIn(('/lotteries/1/join', {'round_id': 61}), site.calls)

    def test_first_invalid_account_does_not_block_second(self):
        self.sites = {'edge-test-token': self.site(7, auth_error=True), 'chrome-test-token': self.site(8)}
        records = self.execute()
        self.assertEqual(records[0]['errors'], ['authentication_failed'])
        self.assertEqual(records[1]['participation'], 'joined')
        self.assertNotIn('must-not-log-token', str(records))

    def test_missing_first_token_does_not_block_second(self):
        self.accounts[0]['token'] = ''
        self.sites = {'chrome-test-token': self.site(8)}
        records = self.execute()
        self.assertEqual(records[0]['errors'], ['missing_token'])
        self.assertEqual(records[1]['participation'], 'joined')

    def test_same_account_twice_is_rejected_before_join(self):
        self.sites = {'edge-test-token': self.site(7), 'chrome-test-token': self.site(7)}
        records = self.execute()
        self.assertTrue(all('duplicate_account' in r['errors'] for r in records))
        self.assertTrue(all(not site.joined for site in self.sites.values()))

    def test_join_failure_still_finds_historical_own_win(self):
        history = [{'id': 60, 'public_id': 1, 'result': 'won', 'card_content': 'PRIVATE-CARD'}]
        self.sites = {'edge-test-token': self.site(7, join_error=True, history=history),
                      'chrome-test-token': self.site(8)}
        records = self.execute()
        self.assertEqual(records[0]['wins'], [60])
        self.assertIn('participation_failed', records[0]['errors'])
        self.assertNotIn('PRIVATE-CARD', str(records))

    def test_closed_and_already_joined_are_not_submitted(self):
        self.sites = {'edge-test-token': self.site(7, open_round=False),
                      'chrome-test-token': self.site(8, joined=True)}
        records = self.execute()
        self.assertEqual([r['participation'] for r in records], ['not_open', 'already_joined'])
        self.assertFalse(any(path.endswith('/join') for site in self.sites.values() for path, _ in site.calls))

    def test_check_mode_never_submits(self):
        self.sites = {'edge-test-token': self.site(7), 'chrome-test-token': self.site(8)}
        self.execute('check')
        self.assertTrue(all(not site.joined for site in self.sites.values()))

    def test_verify_mode_only_checks_identity(self):
        self.sites = {'edge-test-token': self.site(7), 'chrome-test-token': self.site(8)}
        self.execute('verify')
        self.assertTrue(all(site.calls == [('/me', None)] for site in self.sites.values()))

    def test_page_two_win_is_found_and_other_activity_ignored(self):
        self.sites = {'edge-test-token': self.site(7, history=[{'id': 4, 'public_id': 2, 'result': 'won'}],
                                                       history2=[{'id': 60, 'public_id': 1, 'result': 'won'}]),
                      'chrome-test-token': self.site(8)}
        records = self.execute('check')
        self.assertEqual(records[0]['wins'], [60])

    def test_notification_retries_delivery_failure_and_deduplicates_after_success(self):
        class Store:
            def __init__(self): self.sent = set()
            def was_notified(self, key): return key in self.sent
            def mark_notified(self, key): self.sent.add(key)
        store = Store()
        calls = []
        def fail(message):
            calls.append(message)
            raise TimeoutError('unknown delivery')
        records = [{'label': 'Edge', 'wins': [60]}]
        first = self.mod.notify_once(records, store, fail, enabled=True)
        self.assertEqual(first, 'delivery_failed')
        self.assertEqual(len(store.sent), 0)
        second = self.mod.notify_once(records, store, calls.append, enabled=True)
        third = self.mod.notify_once(records, store, calls.append, enabled=True)
        self.assertEqual(second, 'sent')
        self.assertEqual(third, 'already_notified')
        self.assertEqual(len(calls), 2)
        self.assertIn('Edge', calls[1])

    def test_new_win_gets_a_separate_notification(self):
        class Store:
            def __init__(self): self.sent = set()
            def was_notified(self, key): return key in self.sent
            def mark_notified(self, key): self.sent.add(key)
        store = Store()
        records = [{'label': 'Edge', 'wins': [60]}]
        self.assertEqual(self.mod.notify_once(records, store, lambda _: None, True), 'sent')
        records[0]['wins'].append(61)
        self.assertEqual(self.mod.notify_once(records, store, lambda _: None, True), 'sent')

    def test_no_prize_or_no_channel_does_not_read_or_write_state(self):
        class Store:
            def was_notified(self, key): raise AssertionError('must not read state')
            def mark_notified(self, key): raise AssertionError('must not write state')
        self.assertEqual(self.mod.notify_once([{'label': 'Edge', 'wins': []}], Store(), print, True), 'no_win')
        self.assertEqual(self.mod.notify_once([{'label': 'Edge', 'wins': [60]}], Store(), print, False), 'channel_not_configured')

    def test_site_rejects_out_of_scope_operations_without_network(self):
        api = self.mod.SiteAPI('test')
        with self.assertRaises(ValueError): api.request('/orders', {'amount': 1})
        with self.assertRaises(ValueError): api.request('/lotteries/1/join')


if __name__ == '__main__':
    unittest.main()
