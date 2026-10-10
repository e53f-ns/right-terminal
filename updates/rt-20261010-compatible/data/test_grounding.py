"""Offline tests: requests is stubbed BEFORE imports; sockets forbidden."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


def forbidden(*args, **kwargs):
    raise AssertionError('Network forbidden in offline tests')

requests_stub = types.ModuleType('requests')
requests_stub.Session = object
requests_stub.get = forbidden
sys.modules['requests'] = requests_stub
socket.socket = forbidden
socket.create_connection = forbidden
import news_feed as news
import macro_calendar as macro

XML = '''<rss><channel><item><title>Rates headline</title><source url="https://publisher.example">Publisher</source><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate><link>https://feed.example/story</link></item></channel></rss>'''

class Response:
    def __init__(self, text=XML, error=None):
        self.text, self.error = text, error
    def raise_for_status(self):
        if self.error:
            raise self.error

class Session:
    def __init__(self, response=None, error=None):
        self.response = response or Response()
        self.error, self.calls = error, 0
    def get(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response

class NewsTests(unittest.TestCase):
    def setUp(self):
        news.clear_cache()
    def fetch(self, at, session=None, **kwargs):
        with patch.object(news.time, 'time', return_value=at):
            return news.fetch_result('q', session=session or Session(), **kwargs)
    def test_success_and_publication_provenance(self):
        result = self.fetch(1000)
        self.assertEqual(result['status'], 'fresh')
        item = result['items'][0]
        self.assertEqual(item['source'], 'Publisher')
        self.assertEqual(item['url'], 'https://feed.example/story')
        self.assertEqual(item['source_url'], 'https://publisher.example')
        self.assertEqual(item['published_at'], item['ts'])
        self.assertEqual(item['fetched_at'], 1000)
    def test_ttl_cache_no_request(self):
        session = Session()
        self.fetch(1000, session)
        self.fetch(1599, session)
        self.assertEqual(session.calls, 1)
    def test_failures_do_not_refresh_success_time(self):
        self.fetch(1000)
        session = Session(error=TimeoutError())
        for at in (1600, 1800, 3000):
            result = self.fetch(at, session)
            self.assertEqual(result['status'], 'stale')
            self.assertEqual(result['fetched_at'], 1000)
            self.assertEqual(result['age_seconds'], at - 1000)
        self.assertEqual(session.calls, 3)
        self.assertEqual(news._cache['q'][0], 1000)
    def test_initial_failure_unknown_not_cached(self):
        result = self.fetch(1000, Session(error=TimeoutError()))
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['fetched_at'])
        self.assertEqual(result['items'], [])
        self.assertNotIn('q', news._cache)
    def test_malformed_xml_keeps_old_timestamp(self):
        self.fetch(1000)
        result = self.fetch(2000, Session(Response('not xml')))
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['fetched_at'], 1000)
    def test_http_error_unknown(self):
        self.assertEqual(self.fetch(1000, Session(Response(error=RuntimeError())))['status'], 'unknown')
    def test_successful_empty_is_distinct_from_unknown(self):
        result = self.fetch(1000, Session(Response('<rss><channel/></rss>')))
        self.assertEqual(result['status'], 'fresh')
        self.assertEqual(result['items'], [])
    def test_recovery_refreshes_success_time(self):
        self.fetch(1000)
        self.fetch(2000, Session(error=TimeoutError()))
        self.assertEqual(self.fetch(2100)['fetched_at'], 2100)
    def test_completion_time_not_start_time(self):
        with patch.object(news.time, 'time', side_effect=[1000, 1008]):
            self.assertEqual(news.fetch_result('q', session=Session())['fetched_at'], 1008)
    def test_missing_or_unsafe_urls_not_invented(self):
        for link in ('', '<link>javascript:evil</link>', '<link>/relative</link>'):
            item = news._parse('<rss><item><title>X</title>' + link + '</item></rss>', 1)[0]
            self.assertIsNone(item['url'])
            self.assertIsNone(item['source_url'])
            self.assertIsNone(item['published_at'])
    def test_returned_items_do_not_mutate_cache(self):
        result = self.fetch(1000)
        result['items'][0]['title'] = 'tampered'
        self.assertEqual(self.fetch(1001)['items'][0]['title'], 'Rates headline')
    def test_clock_rollback_not_marked_fresh(self):
        self.fetch(1000)
        self.assertEqual(self.fetch(900, Session(error=TimeoutError()))['status'], 'stale')
    def test_prompt_retains_provenance_and_unknown(self):
        for query in news.TOPICS.values():
            news._cache[query] = (1000, news._parse(XML, 12))
        with patch.object(news.time, 'time', return_value=1001):
            text = news.prompt_block_query('EUR USD')
        payload = json.loads(text[text.index('{'):])
        self.assertEqual(payload['feeds'][-1]['status'], 'unknown')
        item = payload['headlines'][0]
        self.assertEqual(item['source'], 'Publisher')
        self.assertIn('2026-10-01', item['published_at_utc'])
        self.assertEqual(item['url'], 'https://feed.example/story')
        self.assertIn('do not invent', text)
    def test_stale_prompt_and_legacy_list(self):
        for query in news.TOPICS.values():
            news._cache[query] = (1000, news._parse(XML, 12))
        with patch.object(news.time, 'time', return_value=2000):
            text = news.prompt_block()
            items = news.topic('fed')
        self.assertIn('"status":"stale"', text)
        self.assertIsInstance(items, list)
        self.assertEqual(items[0]['fetched_at'], 1000)

class MacroTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'calendar.json'
        self.env = patch.dict(os.environ, {'MACRO_CALENDAR_FILE': str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)
        macro._cache.update(path=None, mtime=None, events=[])
        macro._status.update(status='unknown', loaded_at=None, error=None)
        macro._warned = False
    def write(self, events):
        self.path.write_text(json.dumps(events))
    def test_missing_calendar_unknown(self):
        self.assertEqual(macro.calendar_snapshot()['status'], 'unknown')
        self.assertEqual(macro.blackout_status(1000, 1, 1)['status'], 'unknown')
        self.assertIn('status=unknown', macro.prompt_block(1000))
        self.assertIsNone(macro.blackout(1000, 1, 1))  # documented legacy ambiguity
    def test_available_unverified_absolute_utc(self):
        self.write([['FOMC', 4600]])
        with patch.object(macro.time, 'time', return_value=1000):
            snap = macro.calendar_snapshot()
        self.assertFalse(snap['verified'])
        self.assertIsNone(snap['source_url'])
        self.assertEqual(snap['loaded_at'], 1000)
        self.assertIn('1970-01-01T01:16:40+00:00', macro.prompt_block(1000))
        self.assertEqual(macro.upcoming(1000), [('FOMC', 4600)])
    def test_corruption_and_recovery(self):
        self.write([['CPI', 2000]])
        macro.events()
        self.path.write_text('{broken')
        self.assertEqual(macro.calendar_snapshot()['status'], 'unknown')
        self.assertIn('status=unknown', macro.prompt_block(1000))
        self.write([['FOMC', 4000]])
        self.assertEqual(macro.calendar_snapshot()['status'], 'available_unverified')
    def test_empty_calendar_not_proof_of_no_events(self):
        self.write([])
        self.assertEqual(macro.calendar_snapshot()['status'], 'available_unverified')
        self.assertIn('coverage unknown', macro.prompt_block(1000))
        self.assertEqual(macro.blackout_status(1000, 1, 1)['status'], 'none_in_unverified_calendar')
    def test_blackout_inclusive_edges_disabled(self):
        self.write([['CPI', 10000]])
        for at in (6400, 10000, 13600):
            self.assertEqual(macro.blackout_status(at, 1, 1)['status'], 'active')
            self.assertEqual(macro.blackout(at, 1, 1), ('CPI', 10000))
        self.assertEqual(macro.blackout_status(13601, 1, 1)['status'], 'none_in_unverified_calendar')
        self.path.unlink()
        self.assertEqual(macro.blackout_status(10000, 0, 0)['status'], 'disabled')
    def test_deleted_after_load_no_stale_calendar_claim(self):
        self.write([['CPI', 2000]])
        macro.events()
        self.path.unlink()
        snap = macro.calendar_snapshot()
        self.assertEqual(snap['events'], [])
        self.assertEqual(snap['status'], 'unknown')

if __name__ == '__main__':
    unittest.main(verbosity=2)
