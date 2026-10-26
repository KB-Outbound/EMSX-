import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ack_engine import (default_config,eligibility,write_authorized,AckLedger,AckEngine,load_config)

ORDER={'EMSX_SEQUENCE':111,'EMSX_STATUS':'ORD-PEND','EMSX_ACCOUNT':'INSTITUTION_ACCT_ABC',
       'EMSX_TICKER':'NVDA US Equity','EMSX_SIDE':'BUY', 'EMSX_AMOUNT':5000,
       'EMSX_TIF':'DAY','EMSX_ORDER_TYPE':'MKT'}

class TestIntegratedACK(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)
        self.cfg=default_config()
        self.log=[]
        self.sent=[]
        self.ledger=AckLedger(self.path/'ledger.db')
        self.ack=AckEngine('BETA','HDGB',self.cfg,self.ledger,
                           lambda typ,**kw:self.log.append((typ,kw)),
                           lambda seq,cid:self.sent.append((seq,cid)))
        self.ack.set_ready(True)
    def tearDown(self):
        self.ack.close();self.ledger.close();self.tmp.cleanup()
    def arm(self):
        self.cfg['beta_write_enabled']=True
        self.ack.set_arm(True)
        self.assertTrue(self.ack.armed)
    def test_default_admin_flags_off(self):
        self.assertFalse(write_authorized('PROD','HDGB',self.cfg)[0])
        self.assertFalse(write_authorized('BETA','HDGB',self.cfg)[0])
    def test_team_is_not_broker_filter(self):
        self.assertFalse(write_authorized('BETA','OTHER',self.cfg)[0])
        self.assertEqual(self.cfg['team_name'],'HDGB')
    def test_account_not_allowlisted_at_any_scale(self):
        self.assertTrue(eligibility(ORDER,self.cfg)[0])
        self.assertTrue(eligibility({**ORDER,'EMSX_ACCOUNT':'FUND99999'},self.cfg)[0])
    def test_only_e2e_ord_pend(self):
        for status in ('NEW','WORKING','MOD-PEND','FILLED','SENT'):
            self.assertFalse(eligibility({**ORDER,'EMSX_STATUS':status},self.cfg)[0])
    def test_blank_account_blocked(self):
        self.assertFalse(eligibility({**ORDER,'EMSX_ACCOUNT':''},self.cfg)[0])
    def test_huge_order_blocked(self):
        self.assertFalse(eligibility({**ORDER,'EMSX_AMOUNT':300000},self.cfg)[0])
    def test_missing_order_type_blocked(self):
        self.assertFalse(eligibility({k:v for k,v in ORDER.items() if k!='EMSX_ORDER_TYPE'},self.cfg)[0])
    def test_snapshot_never_auto_acks(self):
        self.arm()
        self.ack.on_order(4,ORDER,True,True)
        self.assertEqual(self.sent,[])
    def test_prearm_new_never_acked_on_later_arm(self):
        self.cfg['beta_write_enabled']=True
        self.ack.on_order(6,ORDER,True,True)
        self.ack.set_arm(True)
        self.ack.on_order(7,{'EMSX_SEQUENCE':111,'EMSX_AMOUNT':5000},True,True)
        self.assertEqual(self.sent,[])
    def test_new_before_snapshot_never_auto_ack(self):
        self.arm()
        self.ack.on_order(6,ORDER,False,False)
        self.ack.on_order(7,{'EMSX_SEQUENCE':111},True,True)
        self.assertEqual(self.sent,[])
    def test_fully_qualified_new_ack_once(self):
        self.arm()
        self.ack.on_order(6,ORDER,True,True)
        self.assertEqual(len(self.sent),1)
        self.ack.on_order(7,{'EMSX_SEQUENCE':111,'EMSX_STATUS':'ORD-PEND'},True,True)
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.ledger.get('BETA','HDGB',111),'SENT')
    def test_bad_new_cannot_auto_ack_after_partial_update(self):
        self.arm()
        part={k:v for k,v in ORDER.items() if k!='EMSX_TIF'}
        self.ack.on_order(6,part,True,True)
        self.ack.on_order(7,{'EMSX_SEQUENCE':111,'EMSX_TIF':'DAY'},True,True)
        self.assertEqual(self.sent,[])
    def test_existing_prearm_order_replayed_as_new_does_not_ack(self):
        self.cfg['beta_write_enabled']=True
        self.ack.on_order(6,ORDER,True,True)
        self.ack.set_arm(True)
        self.ack.on_order(6,ORDER,True,True)
        self.assertEqual(self.sent,[])
    def test_reconnect_duplicate_blocked(self):
        self.arm()
        self.ack.on_order(6,ORDER,True,True)
        self.ack.on_order(6,ORDER,True,True)
        self.assertEqual(len(self.sent),1)
    def test_arm_requires_healthy(self):
        self.ack.set_ready(False)
        self.cfg['beta_write_enabled']=True
        self.ack.set_arm(True)
        self.assertFalse(self.ack.armed)
    def test_disarm_stops_new_writes(self):
        self.arm()
        self.ack.set_arm(False)
        self.ack.on_order(6,ORDER,True,True)
        self.assertFalse(self.sent)
    def test_no_cross_environment_journal_collision(self):
        self.ledger.reserve('BETA','HDGB',111)
        self.assertTrue(self.ledger.reserve('PROD','HDGB',111))
    def test_ack_api_reply_not_claiming_final_state(self):
        self.arm()
        self.ack.on_order(6,ORDER,True,True)
        self.ack.on_response(self.sent[0][1],'SellSideAck',0,'OK')
        self.assertEqual(self.ledger.get('BETA','HDGB',111),'API_RESPONSE_OK_UNVERIFIED')
    def test_prod_three_flags_no_uat_reference_required(self):
        self.cfg['production_write_enabled']=True
        self.cfg['production_team_hdgb_approved']=True
        self.assertEqual(write_authorized('PROD','HDGB',self.cfg), (False, 'PRODUCTION_WRITES_LOCKED'))
        self.cfg['production_all_accounts_approved']=True
        self.assertTrue(write_authorized('PROD','HDGB',self.cfg)[0])
        self.assertNotIn('uat_reference', self.cfg)

    def test_old_config_with_blank_uat_reference_accepted(self):
        # Existing Windows installations may retain an old uat_reference entry.
        old = {**self.cfg, 'uat_reference': ''}
        path = self.path/'legacy_config.json'
        import json
        path.write_text(json.dumps(old), encoding='utf-8')
        loaded = load_config(path)
        self.assertNotIn('uat_reference', loaded)
        loaded.update(production_write_enabled=True,
                      production_team_hdgb_approved=True,
                      production_all_accounts_approved=True)
        self.assertTrue(write_authorized('PROD','HDGB',loaded)[0])

    def test_each_prod_flag_required(self):
        flags = ('production_write_enabled','production_team_hdgb_approved','production_all_accounts_approved')
        for omitted in flags:
            cfg = default_config()
            for key in flags:
                cfg[key] = key != omitted
            self.assertEqual(write_authorized('PROD','HDGB',cfg)[1], 'PRODUCTION_WRITES_LOCKED')
    def test_load_initializes_off_config(self):
        config=load_config(self.path/'admin_config.json')
        self.assertFalse(config['production_write_enabled'])
        self.assertTrue((self.path/'admin_config.json').exists())
    def test_ack_unknown_on_send_exception(self):
        self.arm()
        self.ack.send_func=lambda seq,cid:1/0
        self.ack.on_order(6,ORDER,True,True)
        self.assertEqual(self.ledger.get('BETA','HDGB',111),'UNKNOWN')
        self.assertFalse(self.ack.armed)

if __name__=='__main__':unittest.main()
