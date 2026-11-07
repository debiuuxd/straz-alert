import json
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reliability as rules
import worker

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


class ReliabilityTests(unittest.TestCase):
    def incident(self, **kwargs):
        return {'external_id': 'new', 'title': 'Pożar budynku mieszkalnego w Strykowie',
                'location': 'Stryków', 'category': 'fire', 'lat': 51.901, 'lng': 19.602,
                'sources': [], 'updated_at': NOW.isoformat(), **kwargs}

    def test_same_local_rules_as_browser(self):
        cases = json.loads(Path(__file__).with_name('local-cases.json').read_text())
        for case in cases:
            expected = case.pop('expected')
            with self.subTest(location=case['location']):
                self.assertEqual(rules.is_local_incident(**case), expected)

    def test_nearby_incidents_not_merged_without_event_time(self):
        old = self.incident(external_id='old', id=1)
        self.assertIsNone(rules.find_duplicate(self.incident(), [old]))

    def test_same_place_on_different_days_is_separate(self):
        old = self.incident(external_id='old', id=1, event_date='2026-11-06', event_time='10:00')
        new = self.incident(event_date='2026-11-07', event_time='10:00')
        self.assertIsNone(rules.find_duplicate(new, [old]))

    def test_matching_explicit_event_can_merge_sources(self):
        old = self.incident(external_id='old', id=1, event_date='2026-11-07', event_time='10:00')
        new = self.incident(event_date='2026-11-07', event_time='10:00', sources=[{'url':'https://example.org/new'}])
        self.assertEqual(rules.find_duplicate(new, [old])['id'], 1)
        self.assertEqual(len(rules.merge_incident(old, new)['sources']), 1)

    def test_completed_incident_not_reopened_by_new_article(self):
        old = self.incident(status='Zakończone', status_observed_at='2026-11-07T10:00:00Z')
        new = self.incident(status='Trwa akcja', status_observed_at='2026-11-07T11:00:00Z')
        self.assertNotIn('status', rules.merge_incident(old, new))

    def test_unknown_event_time_is_not_detection_time(self):
        self.assertEqual(rules.event_fields({}, 'Strażacy interweniowali.', NOW, NOW),
                         {'event_date':None,'event_time':None})

    def test_yesterday_uses_publication_date_in_poland(self):
        publication = datetime(2026,11,6,23,30,tzinfo=timezone.utc)
        data={'date_evidence':'wczoraj','event_time':'09:15','time_evidence':'o 09:15'}
        self.assertEqual(rules.event_fields(data,'Wczoraj o 09:15 doszło do pożaru.',publication,NOW),
                         {'event_date':'2026-11-06','event_time':'09:15'})

    def test_invented_date_evidence_is_rejected(self):
        data={'event_date':'2026-11-07','date_evidence':'7 listopada','event_time':'10:00'}
        self.assertIsNone(rules.event_fields(data,'Wypadek w Polsce',NOW,NOW)['event_date'])

    def test_generic_straz_does_not_establish_psp_osp_or_police(self):
        data={'units':'PSP, OSP, Policja','units_evidence':'Straż interweniowała'}
        self.assertEqual(rules.confirmed_units(data,'Straż interweniowała przy pożarze.'),'Brak danych')

    def test_units_require_matching_source_fragment(self):
        data={'units':'PSP, Policja, ZRM','units_evidence':'Na miejscu pracują PSP i ZRM'}
        self.assertEqual(rules.confirmed_units(data,'Na miejscu pracują PSP i ZRM.'),'PSP, ZRM')

    def test_first_fetch_and_old_articles_do_not_alert(self):
        new=self.incident(published_at=NOW.isoformat())
        self.assertFalse(rules.notification_eligible(new,NOW,False))
        self.assertTrue(rules.notification_eligible(new,NOW,True))
        new['published_at']='2026-11-06T10:00:00Z'
        self.assertFalse(rules.notification_eligible(new,NOW,True))

    def test_unknown_location_cannot_alert(self):
        self.assertFalse(rules.notification_eligible(self.incident(lat=None,lng=None,published_at=NOW.isoformat()),NOW,True))

    @patch.object(worker, 'geocode_place', return_value=None)
    def test_ai_coordinates_and_invented_units_are_ignored(self, _):
        row=worker.build_incident({'external_id':'test','payload':{'title':'Pożar budynku',
            'summary':'Straż interweniowała.', 'link':'https://example.org', 'published_at':None}},
            {'location':'Nieznana','estimated_lat':51.901,'estimated_lng':19.602,
             'units':'PSP, OSP, Policja','status':'Trwa akcja','event_time':'12:00'})
        self.assertIsNone(row['lat'])
        self.assertEqual(row['units'],'Brak danych')
        self.assertEqual(row['status'],'Nieustalony')
        self.assertEqual(row['time_str'],'')
        self.assertFalse(row['local_alert'])

    def test_notification_timeout_is_not_retried(self):
        class Duplicate(Exception):
            code='23505'
        class FakeDatabase:
            def __init__(self): self.keys=set();self.row=None
            def table(self,name): return self
            def insert(self,row): self.row=row;return self
            def update(self,row): self.row=None;return self
            def eq(self,*args): return self
            def execute(self):
                if self.row:
                    key=(self.row['incident_id'],self.row['topic'])
                    if key in self.keys: raise Duplicate()
                    self.keys.add(key)
        db=FakeDatabase()
        row=self.incident(id=1,units='Brak danych',description='Opis',local_alert=False)
        with patch.object(worker,'supabase',db), patch.object(worker.urllib.request,'urlopen',side_effect=TimeoutError()) as request:
            worker.notify_once(row)
            worker.notify_once(row)
            self.assertEqual(request.call_count,1)


if __name__ == '__main__':
    unittest.main()
