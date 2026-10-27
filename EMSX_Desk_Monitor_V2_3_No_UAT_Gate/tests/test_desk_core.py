import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desk_core import DeskBook,safe_pct
from bloomberg_feed import team_topic,read_field
from demo_feed import SAMPLE


class TestProjector(unittest.TestCase):
    def setUp(self):
        self.clock=[1000.]
        self.b=DeskBook(now=lambda:self.clock[0])
        self.b.update_reference("TEST JP Equity", {"iso_country": "JP"})
        self.b.subscriptions={'O':'STARTED','R':'STARTED'}
    def order(self,status=4,seq=17,**kwargs):
        return self.b.ingest('O',status,{'EMSX_SEQUENCE':seq, 'EMSX_TICKER':'TEST JP Equity', **kwargs})
    def route(self,status=4,seq=17,rid=1,**kwargs):
        return self.b.ingest('R',status,{'EMSX_SEQUENCE':seq,'EMSX_ROUTE_ID':rid,**kwargs})
    def ready(self):
        self.b.ingest('O',11,{})
        self.b.ingest('R',11,{})
    def test_partial_dynamic_update_preserves_static_fields(self):
        self.order(EMSX_AMOUNT=100,EMSX_ACCOUNT='FUND',EMSX_TICKER='NVDA',EMSX_FILLED=0)
        self.order(7,EMSX_FILLED=50)
        self.assertEqual(self.b.full_view()[0]['account'],'FUND')
        self.assertEqual(self.b.full_view()[0]['remain'],50)
    def test_routes_cumulative_not_double_counted(self):
        self.order(EMSX_AMOUNT=100,EMSX_FILLED=90)
        self.route(EMSX_AMOUNT=100,EMSX_FILLED=10,EMSX_BROKER='JEFF')
        self.route(7,EMSX_FILLED=90)
        self.assertEqual(self.b.broker_summary()[0]['filled'],90)
        self.assertEqual(self.b.broker_summary()[0]['routes'],1)
    def test_route_split_brokers(self):
        self.order(EMSX_AMOUNT=100)
        self.route(rid=1,EMSX_AMOUNT=40,EMSX_BROKER='A',EMSX_FILLED=10)
        self.route(rid=2,EMSX_AMOUNT=60,EMSX_BROKER='B',EMSX_FILLED=30)
        self.assertEqual(len(self.b.broker_summary()),2)
    def test_unrouted_after_observation_only(self):
        self.order(EMSX_AMOUNT=100,EMSX_STATUS='NEW')
        self.ready()
        self.assertEqual(self.b.full_view()[0]['warning'],'')
        self.clock[0]+=301
        self.assertIn('Unrouted Warning',self.b.full_view()[0]['warning'])
    def test_snapshot_not_older_than_actual_age(self):
        self.order(EMSX_AMOUNT=100,EMSX_STATUS='NEW')
        self.assertEqual(self.b.full_view()[0]['warning'],'')
        self.clock[0]+=601
        self.assertEqual(self.b.full_view()[0]['warning'],'')
    def test_e2e_alert(self):
        self.order(EMSX_AMOUNT=100,EMSX_STATUS='ORD-PEND')
        self.ready()
        self.assertIn('Ack Pending',self.b.full_view()[0]['warning'])
    def test_terminal_order_not_unrouted(self):
        self.order(EMSX_AMOUNT=100,EMSX_STATUS='FILLED',EMSX_FILLED=100)
        self.ready();self.clock[0]+=999
        self.assertEqual(self.b.full_view()[0]['warning'],'')
    def test_unknown_route_parent_is_preserved(self):
        self.route(EMSX_AMOUNT=99,EMSX_BROKER='GS')
        self.assertEqual(self.b.route_view()[0]['parent_missing'],True)
    def test_delete_removes_record(self):
        self.order(EMSX_AMOUNT=20)
        self.order(8)
        self.assertEqual(len(self.b.orders),0)
    def test_api_gap_degraded(self):
        self.order(API_SEQ_NUM=1,EMSX_AMOUNT=20)
        self.order(7,API_SEQ_NUM=4)
        self.ready()
        self.assertEqual(len(self.b.event_gaps),1)
        self.assertFalse(self.b.counters()['stream_ok'])
    def test_empty_account_visible(self):
        self.order(EMSX_AMOUNT=100,EMSX_ACCOUNT='',EMSX_STATUS='NEW')
        self.assertEqual(len(self.b.full_view()),1)
    def test_pct_and_zero(self):
        self.assertEqual(safe_pct(20,50),40.0)
        self.assertEqual(safe_pct(0,0),0.0)
        self.assertEqual(safe_pct(100,50),100.0)
    def test_no_fill_change_observation_alert(self):
        self.order(EMSX_AMOUNT=100,EMSX_FILLED=0,EMSX_STATUS='WORKING')
        self.route(EMSX_AMOUNT=100,EMSX_FILLED=0,EMSX_BROKER='J',EMSX_STATUS='WORKING')
        self.ready();self.clock[0]+=901
        self.assertIn('No-Fill Alert',self.b.full_view()[0]['warning'])
    def test_no_false_double_counting_fill_events(self):
        self.route(EMSX_AMOUNT=100,EMSX_FILLED=10)
        self.route(7,EMSX_FILLED=10)
        self.assertEqual(self.b.broker_summary()[0]['filled'],10)
    def test_team_topic_shape(self):
        self.assertEqual(team_topic('//blp/emapisvc','order',['EMSX_AMOUNT'],'T1'),
                         '//blp/emapisvc/order;team=T1?fields=EMSX_AMOUNT')
        with self.assertRaises(ValueError):
            team_topic('//blp/emapisvc','order',['EMSX_AMOUNT'],'T?fields=BAD')
    def test_demo_records(self):
        self.assertEqual(len(SAMPLE),5)

if __name__=='__main__':unittest.main()
