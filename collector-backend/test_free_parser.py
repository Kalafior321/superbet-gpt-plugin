import json
import unittest
from free_parser import parse_public_html, MAX_HTML

URL = 'https://superscore.live/pl-PL'

def html(event):
    return '<script type="application/ld+json">' + json.dumps(event) + '</script>'

EVENT = {'@type':'SportsEvent','homeTeam':{'name':'A'},'awayTeam':{'name':'B'},'startDate':'2026-11-27T20:00:00+01:00'}

class ParserTest(unittest.TestCase):
    def test_event(self):
        self.assertEqual(parse_public_html(html(EVENT), URL)['events'][0]['home_team'], 'A')
    def test_timezone(self):
        self.assertEqual(parse_public_html(html(EVENT), URL)['events'][0]['start_at'], EVENT['startDate'])
    def test_no_timezone(self):
        self.assertEqual(parse_public_html(html(dict(EVENT,startDate='2026-11-27T20:00:00')), URL)['events'], [])
    def test_duplicate(self):
        self.assertEqual(len(parse_public_html(html({'@graph':[EVENT,EVENT]}), URL)['events']), 1)
    def test_missing_team(self):
        self.assertEqual(parse_public_html(html(dict(EVENT,awayTeam=None)), URL)['events'], [])
    def test_no_guess(self):
        self.assertEqual(parse_public_html('<div>A B 1.50 4.00 6.00</div>', URL)['events'], [])
    def test_no_odds_claim(self):
        self.assertFalse(parse_public_html(html(EVENT), URL)['events'][0]['odds_verified_as_superbet'])
    def test_table(self):
        self.assertEqual(parse_public_html('<table><tr><th>1</th><th>X</th></tr><tr><td>1.50</td><td>4.00</td></tr></table>',URL)['tables'][0]['rows'][1],['1.50','4.00'])
    def test_bad_json(self):
        self.assertEqual(parse_public_html('<script type="application/ld+json">{invalid}</script>',URL)['events'],[])
    def test_size(self):
        self.assertEqual(parse_public_html('a'*(MAX_HTML+1),URL)['extraction_status'],'document_too_large')
    def test_no_cost(self):
        self.assertFalse(parse_public_html(html(EVENT),URL)['paid_api_used'])
    def test_unverified(self):
        self.assertFalse(parse_public_html(html(EVENT),URL)['structured_data_verified'])

if __name__ == '__main__': unittest.main()
