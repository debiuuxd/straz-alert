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

    def krakow_reports(self):
        # Regression: two articles, no event hour, category drift and legacy GPS.
        old = self.incident(id=488, external_id='https://example.org/?p=1',
            title='Strzelanina na Rynku Głównym w Krakowie', category='hazard',
            location='Kraków, Rynek Główny', date_str='2026-11-07',
            created_at='2026-11-07T15:01:57Z', lat=50.05, lng=20.00,
            source_url='https://example.org/first-report')
        new = self.incident(external_id='https://example.org/?p=2',
            title=old['title'], category='accident', location=old['location'],
            date_str='2026-11-07', published_at='2026-11-07T15:23:36Z',
            lat=50.061, lng=19.938, source_url='https://example.org/update',
            sources=[{'url':'https://example.org/update','title':'Drugi materiał'}])
        return old, new

    def test_krakow_reports_merge_without_hour_despite_category_and_gps_drift(self):
        old, new = self.krakow_reports()
        self.assertEqual(rules.find_duplicate(new, [old])['id'], 488)
        patch = rules.merge_incident(old, new)
        self.assertEqual({s['url'] for s in patch['sources']},
                         {'https://example.org/first-report','https://example.org/update'})
        self.assertNotIn('event_date', patch)
        self.assertNotIn('event_time', patch)

    def test_reworded_specific_report_can_merge(self):
        old, new = self.krakow_reports()
        new['title'] = 'Strzały na Rynku Głównym w Krakowie. Nowe informacje'
        self.assertEqual(rules.find_duplicate(new, [old])['id'], 488)

    def test_conflicting_date_hour_location_or_event_kind_stays_separate(self):
        variants = [
            {'date_str':'2026-11-06'},
            {'published_at':'2026-11-07T20:00:00Z'},
            {'location':'Kraków'},
            {'location':'Kraków, Mały Rynek'},
            {'location':'Kraków, Rynek Główny, ul. Floriańska'},
            {'title':'Pożar na Rynku Głównym w Krakowie'},
            {'lat':53.0,'lng':21.0},
        ]
        for patch in variants:
            with self.subTest(patch=patch):
                old, new = self.krakow_reports()
                self.assertIsNone(rules.find_duplicate({**new, **patch}, [old]))
        old, new = self.krakow_reports()
        old.update(event_date='2026-11-07', event_time='10:00')
        new.update(event_date='2026-11-07', event_time='11:00')
        self.assertIsNone(rules.find_duplicate(new, [old]))

    def test_distinct_streets_buildings_and_roads_are_not_merged(self):
        for loc1, loc2 in [
            ('Kraków, ul. Długa', 'Kraków, ul. Floriańska'),
            ('Kraków, ul. Długa 12', 'Kraków, ul. Długa 14'),
            ('Stryków, A1', 'Stryków, A2'),
            ('Kraków, ul. Długa 12', 'Kraków, ul. Długa'),
        ]:
            with self.subTest(locations=(loc1, loc2)):
                old, new = self.krakow_reports()
                old.update(location=loc1)
                new.update(location=loc2)
                self.assertIsNone(rules.find_duplicate(new, [old]))

    def test_empty_ids_and_urls_do_not_establish_identity(self):
        old = self.incident(external_id=None, source_url=None, sources=[{'url':None}])
        self.assertIsNone(rules.find_duplicate(self.incident(external_id=None), [old]))

    def test_source_identity_ignores_tracking_but_keeps_article_ids(self):
        old, new = self.krakow_reports()
        new['source_url']='http://www.example.org/first-report/?utm_source=rss&fbclid=123#top'
        new['title']='Inny tytuł tego samego artykułu'
        self.assertEqual(rules.find_duplicate(new, [old])['id'], 488)
        self.assertNotEqual(rules.canonical_url(old['external_id']),rules.canonical_url(new['external_id']))
        patch=rules.merge_incident(old, new)
        self.assertEqual(len(patch['sources']),2)

    def test_source_identity_has_priority_over_a_similar_recent_report(self):
        old, new = self.krakow_reports()
        exact = {**old, 'id':123, 'external_id':new['external_id']}
        self.assertEqual(rules.find_duplicate(new, [old, exact])['id'],123)

    def test_archived_alias_and_manual_reports_not_fuzzy_matched(self):
        old, new = self.krakow_reports()
        self.assertIsNone(rules.find_duplicate(new, [{**old, 'merged_into':490}]))
        self.assertIsNone(rules.find_duplicate(new, [{**old, 'external_id':'manual-123'}]))

    def test_merged_alias_cannot_send_notifications(self):
        old, _ = self.krakow_reports()
        old.update(merged_into=490, published_at=NOW.isoformat())
        self.assertFalse(rules.notification_eligible(old, NOW, True))
        with patch.object(worker,'supabase') as db, patch.object(worker.urllib.request,'urlopen') as request:
            worker.notify_once(old)
            db.assert_not_called()
            db.table.assert_not_called()
            request.assert_not_called()

    def test_queue_enriches_duplicate_without_insert_or_notification(self):
        old, new = self.krakow_reports()
        class FakeQuery:
            def __init__(self, db, table): self.db=db; self.table=table; self.external=None; self.op='select'
            def select(self, *args): return self
            def eq(self, key, value):
                if key=='external_id': self.external=value
                return self
            def is_(self,*args): return self
            def lte(self,*args): return self
            def order(self,*args,**kwargs): return self
            def limit(self,*args): return self
            def update(self, value): self.op='update'; self.value=value; return self
            def insert(self, value): raise AssertionError('Duplicate must not create a pin')
            def execute(self):
                from types import SimpleNamespace
                if self.op=='update':
                    self.db.updates.append((self.table,self.value)); return SimpleNamespace(data=[])
                if self.table=='feed_queue':
                    return SimpleNamespace(data=[{'external_id':new['external_id'], 'attempts':0,
                        'payload':{'psp':False, 'quiet':False}}])
                return SimpleNamespace(data=[] if self.external else [old])
        class FakeDatabase:
            def __init__(self): self.updates=[]
            def table(self, name): return FakeQuery(self, name)
        db=FakeDatabase()
        with patch.object(worker,'supabase',db), patch.object(worker,'analyze_article',return_value={}), \
             patch.object(worker,'build_incident',return_value=new), patch.object(worker,'notify_once') as notify:
            worker.process_queue([], worker.time.monotonic()+60)
            notify.assert_not_called()
        self.assertEqual(db.updates[0][0],'incidents')
        self.assertEqual(len(db.updates[0][1]['sources']),2)
        self.assertEqual(db.updates[1][1]['incident_id'],488)

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
