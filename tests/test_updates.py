"""Cross-day follow-ups: source evidence, conflicts and notification behavior."""
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import reliability as rules
import worker

NOW=datetime(2026,11,8,10,tzinfo=timezone.utc)
OLD_TEXT='W Krakowie policjant został ranny w czasie strzelaniny. Interweniowały służby.'
NEW_TEXT='Podczas sobotniej strzelaniny w Krakowie ranny został policjant. Trwa leczenie.'

class UpdateTests(unittest.TestCase):
    def reports(self):
        old={'id':11,'external_id':'old-report','title':'Strzały na Rynku Głównym w Krakowie',
             'description':'Strzelanina w Krakowie','location':'Kraków, Rynek Główny',
             'category':'hazard','lat':50.062,'lng':19.937,'event_date':'2026-11-07',
             'event_time':None,'date_str':'2026-11-07','created_at':'2026-11-07T14:00:00Z',
             'sources':[{'url':'https://example.org/original','title':'Pierwszy materiał'}],
             'source_url':'https://example.org/original','_source_text':OLD_TEXT}
        new={'id':None,'external_id':'new-report','title':'Ranny policjant po strzelaninie',
             'description':'Aktualizacja stanu policjanta','location':'Kraków','category':'accident',
             'lat':50.061,'lng':19.936,'event_date':'2026-11-07','event_time':None,
             'date_str':'2026-11-07','published_at':'2026-11-08T09:00:00Z',
             'updated_at':NOW.isoformat(),'sources':[{'url':'https://example.org/follow-up'}]}
        data={'is_incident':True,'report_type':'update','duplicate':{'incident_id':11,'confidence':'high',
              'same_event':True,'new_evidence':NEW_TEXT,'old_evidence':OLD_TEXT}}
        return old,new,data

    def test_saturday_reference_uses_publication_day_not_download_or_ai_date(self):
        result=rules.event_fields({'date_evidence':'sobotniej strzelaniny','event_date':'2026-11-08'},NEW_TEXT,NOW,NOW)
        self.assertEqual(result,{'event_date':'2026-11-07','event_time':None})
        self.assertFalse(rules.notification_eligible({'lat':50.06,'lng':19.93,'event_date':result['event_date'],
            'published_at':NOW.isoformat()},NOW,True))

    def test_written_date_and_year_boundary(self):
        data={'date_evidence':'7 listopada','event_date':'2026-11-07'}
        self.assertEqual(rules.event_fields(data,'Zdarzenie miało miejsce 7 listopada.',NOW,NOW)['event_date'],'2026-11-07')
        jan=datetime(2027,1,2,10,tzinfo=timezone.utc)
        data={'date_evidence':'31 grudnia','event_date':'2026-12-31'}
        self.assertEqual(rules.event_fields(data,'Wypadek z 31 grudnia.',jan,jan)['event_date'],'2026-12-31')
        data['event_date']='2027-12-31'
        self.assertIsNone(rules.event_fields(data,'Wypadek z 31 grudnia.',jan,jan)['event_date'])

    def test_unquoted_or_future_weekday_is_not_accepted(self):
        self.assertIsNone(rules.event_fields({'date_evidence':'w sobotę'},'Brak daty',NOW,NOW)['event_date'])
        self.assertIsNone(rules.event_fields({'date_evidence':'w przyszłą sobotę'},'Ćwiczenia w przyszłą sobotę',NOW,NOW)['event_date'])

    def test_cross_day_reworded_follow_up_uses_verified_source_fragments(self):
        old,new,data=self.reports()
        self.assertIsNone(rules.find_duplicate(new,[old]))
        self.assertEqual(rules.confirmed_duplicate(data,new,[old],NEW_TEXT)['id'],11)
        patch_data=rules.merge_incident({**old,'event_date':None},new)
        self.assertEqual(patch_data['date_str'],'2026-11-07')
        self.assertEqual(len(patch_data['sources']),2)
        self.assertNotIn('lat',patch_data)

    def test_city_alone_fake_quotes_unknown_id_and_low_confidence_do_not_merge(self):
        old,new,data=self.reports()
        for change in ({'confidence':'medium'},{'same_event':False},{'incident_id':999},
                       {'new_evidence':'Nieistniejący dosłowny cytat o zdarzeniu'},
                       {'old_evidence':'Nieistniejący dosłowny cytat o zdarzeniu'}):
            with self.subTest(change=change):
                proposal={**data,'duplicate':{**data['duplicate'],**change}}
                self.assertIsNone(rules.confirmed_duplicate(proposal,new,[old],NEW_TEXT))
        generic='Doszło do pożaru w Krakowie. Na miejscu trwa akcja straży.'
        old.update(title='Pożar w Krakowie',location='Kraków',_source_text=generic)
        new.update(title='Pożar w Krakowie')
        data['duplicate'].update(new_evidence=generic,old_evidence=generic)
        self.assertIsNone(rules.confirmed_duplicate(data,new,[old],generic))

    def test_conflicting_dates_times_streets_regions_and_kinds_remain_separate(self):
        for change in ({'event_date':'2026-11-08'}, {'lat':54.0,'lng':20.0},
                       {'title':'Pożar w Krakowie'}):
            old,new,data=self.reports()
            with self.subTest(change=change):
                self.assertIsNone(rules.confirmed_duplicate(data,{**new,**change},[old],NEW_TEXT))
        old,new,data=self.reports();old['event_time']='10:00';new['event_time']='11:00'
        self.assertIsNone(rules.confirmed_duplicate(data,new,[old],NEW_TEXT))
        old,new,data=self.reports();old['event_date']=None;new['event_date']='2026-11-08'
        self.assertIsNone(rules.confirmed_duplicate(data,new,[old],NEW_TEXT),'A new Sunday event cannot be described by a Saturday report')
        old,new,data=self.reports();old['location']='Kraków, ul. Długa';new['location']='Kraków, ul. Floriańska'
        self.assertIsNone(rules.confirmed_duplicate(data,new,[old],NEW_TEXT))

    def test_shortlist_uses_named_town_and_excludes_old_manual_and_archived_records(self):
        old,new,_=self.reports()
        payload={'title':'Policjant z Krakowa','summary':NEW_TEXT,'published_at':NOW.isoformat()}
        rows=[old,{**old,'id':12,'location':'Warszawa'}, {**old,'id':13,'merged_into':11},
              {**old,'id':14,'external_id':'manual-1'}, {**old,'id':15,'created_at':'2026-10-01T12:00:00Z'}]
        self.assertEqual([r['id'] for r in rules.matching_candidates(payload,rows)],[11])

    def test_match_comparison_uses_the_existing_single_ai_call(self):
        old,_,data=self.reports()
        client=MagicMock();client.models.generate_content.return_value=SimpleNamespace(text=__import__('json').dumps(data))
        calls=[0]
        with patch.object(worker,'ai_client',client),patch.object(worker,'now_utc',return_value=NOW):
            result=worker.analyze_article({'title':'Strzelanina w Krakowie','summary':NEW_TEXT,'published_at':NOW.isoformat()},['test-model'],calls,[old])
        self.assertEqual(calls,[1]);self.assertEqual(result['report_type'],'update')
        self.assertIn('previous',client.models.generate_content.call_args.kwargs['contents'])

    def test_source_context_excludes_material_not_owned_by_the_candidate(self):
        old,_,_=self.reports()
        client=MagicMock()
        query=client.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value
        query.execute.return_value=SimpleNamespace(data=[
            {'payload':{'link':old['source_url'],'title':'Pierwszy materiał','summary':OLD_TEXT}},
            {'payload':{'link':'https://example.org/unrelated','title':'Inne zdarzenie','summary':'Niepowiązany materiał'}}])
        with patch.object(worker,'supabase',client):
            result=worker.candidate_contexts({'title':'Policjant z Krakowa','summary':NEW_TEXT,'published_at':NOW.isoformat()},[old])
        self.assertIn(OLD_TEXT,result[0]['_source_text'])
        self.assertNotIn('Niepowiązany',result[0]['_source_text'])

    def test_unknown_event_date_uses_publication_only_for_calendar_bucket(self):
        with patch.object(worker,'now_utc',return_value=NOW),patch.object(worker,'geocode_place',return_value=None):
            row=worker.build_incident({'external_id':'first-report','payload':{'title':'Pożar','summary':'Brak czasu zdarzenia',
                'link':'https://example.org','published_at':'2026-11-07T22:00:00Z'}},{'is_incident':True})
        self.assertIsNone(row['event_date']);self.assertEqual(row['date_str'],'2026-11-07')

    def run_queue(self,match):
        old,new,data=self.reports()
        if not match:data['duplicate']=None
        updates=[]
        class Query:
            def __init__(self,table):self.table=table;self.external=None;self.write=None
            def select(self,*a):return self
            def is_(self,*a):return self
            def order(self,*a,**k):return self
            def limit(self,*a):return self
            def lte(self,*a):return self
            def eq(self,key,value):
                if key=='external_id':self.external=value
                return self
            def update(self,value):self.write=value;return self
            def insert(self,value):raise AssertionError('A follow-up must not insert a new incident')
            def execute(self):
                if self.write is not None:updates.append((self.table,self.write));return SimpleNamespace(data=[])
                if self.table=='feed_queue':return SimpleNamespace(data=[{'external_id':'new-report','attempts':0,
                    'payload':{'title':'Aktualizacja','summary':NEW_TEXT,'published_at':NOW.isoformat(),'psp':False,'quiet':False}}])
                return SimpleNamespace(data=[] if self.external else [old])
        db=SimpleNamespace(table=lambda table:Query(table))
        with patch.object(worker,'supabase',db),patch.object(worker,'now_utc',return_value=NOW),\
             patch.object(worker,'candidate_contexts',return_value=[old]),patch.object(worker,'analyze_article',return_value=data),\
             patch.object(worker,'build_incident',return_value=new),patch.object(worker,'enrich_existing',return_value=old) as enrich,\
             patch.object(worker,'notify_once') as notify:
            worker.process_queue([],worker.time.monotonic()+30)
            notify.assert_not_called()
            if match:enrich.assert_called_once_with(11,new)
            else:enrich.assert_not_called()
        return updates

    def test_matched_follow_up_enriches_without_new_pin_or_ntfy(self):
        updates=self.run_queue(True)
        self.assertEqual(updates[-1][1]['incident_id'],11)
        self.assertEqual(updates[-1][1]['state'],'done')

    def test_unmatched_follow_up_stays_in_queue_without_fresh_emergency(self):
        updates=self.run_queue(False)
        self.assertEqual(updates[-1][1]['state'],'rejected')
        self.assertIn('brak pewnego',updates[-1][1]['last_error'])

if __name__=='__main__':unittest.main()
