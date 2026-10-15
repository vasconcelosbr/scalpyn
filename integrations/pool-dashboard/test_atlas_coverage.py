import copy,json,unittest
from unittest.mock import patch
from test_atlas import AtlasTests,FakeProvider,NOW,opinion
from atlas import store,pipeline,sources,specialized

class CoverageTests(AtlasTests):
    def test_revision_preserves_original_and_shares_budget(self):
        first=self.run_cycle()
        with store.db() as c:
            before=[tuple(r) for r in c.execute('SELECT * FROM agents WHERE run_id=?',(first,))]
            report=tuple(c.execute('SELECT * FROM reports WHERE run_id=?',(first,)).fetchone())
        p=FakeProvider();allocations=[];normal=p.run
        def run(code,context,cents,save):
            allocations.append(cents);return normal(code,context,cents,save)
        p.run=run
        second=pipeline.cycle(NOW,p,lambda t:([],[]),lambda t:copy.deepcopy(self.snap),revision=1)
        self.assertNotEqual(first,second);self.assertEqual(allocations,[70]*7)
        pipeline.cycle(NOW,p,revision=1);self.assertEqual(len(p.calls),7)
        with store.db() as c:
            self.assertEqual(before,[tuple(r) for r in c.execute('SELECT * FROM agents WHERE run_id=?',(first,))])
            self.assertEqual(report,tuple(c.execute('SELECT * FROM reports WHERE run_id=?',(first,)).fetchone()))
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
    def test_unknown_prior_usage_blocks_revision(self):
        first=self.run_cycle()
        with store.db(True) as c:c.execute("UPDATE agents SET usage=NULL WHERE run_id=? AND code='MACRO'",(first,))
        p=FakeProvider();pipeline.cycle(NOW,p,lambda t:([],[]),lambda t:copy.deepcopy(self.snap),revision=1)
        self.assertFalse(p.calls)
    def test_pool_membership_market_and_freshness(self):
        snap=copy.deepcopy(self.snap);snap['data']['market_mode']='spot'
        snap['pool_context']={'row_count':3,'coins':[
          {'id':'pool-one','symbol':'TEST_USDT','market_type':'spot','is_active':True,'indicator_readings':[{'timestamp':'2026-10-15T20:00:00Z','timeframe':'15m','values':{'rsi':42}}]},
          {'id':'pool-two','symbol':'TEST_USDT','market_type':'futures','is_active':True,'indicator_readings':[]},
          {'id':'inactive','symbol':'OLD_USDT','market_type':'spot','is_active':False,'indicator_readings':[]}]}
        q=sources.quant(snap,NOW)
        self.assertEqual(q['pool_total'],2);self.assertEqual(q['matched'],['pool-one'])
        self.assertEqual(q['pool_only'],['pool-two']);self.assertEqual(q['assets'][0]['alpha_score'],17.2)
        self.assertIsNone(q['assets'][1]['alpha_score'])
        self.assertEqual(q['assets'][1]['coverage_status'],'PARTIAL')
        self.assertEqual(q['assets'][0]['freshness_by_reading'][0]['age_seconds'],7200)
        self.assertEqual(q['assets'][0]['freshness_by_reading'][0]['status'],'STALE')
        self.assertEqual(q['assets'][0]['indicator_readings'][0]['values'],{'rsi':42})
    def test_history_does_not_duplicate_specialized_or_superseded_feeds(self):
        with store.db(True) as c:
            for idx,source in enumerate(('THE_BLOCK','THE_BLOCK','GATE_METADATA')):
                e={'evidence_id':str(idx),'source_id':source,'source_tier':'SINGLE_SOURCE_REPORT','url':'https://example.test','published_at':None}
                c.execute('INSERT INTO evidence VALUES (?,?,?,?,?)',(str(idx),e['url'],str(idx),store.encode(e),str(idx)))
        with patch.object(sources,'FEEDS',(('THE_BLOCK','https://example.test','SINGLE_SOURCE_REPORT','GENERAL'),)),patch.object(sources.httpx,'Client',side_effect=ValueError('offline')):
            e,h=sources.collect_feeds(NOW)
        self.assertEqual([x['evidence_id'] for x in e],['1'])
    def test_mapper_cannot_omit_evidence(self):
        inputs={'snapshot':None,'cutoff':NOW.isoformat(),'quant':{'assets':[]},'evidence':[{'evidence_id':'a'}],'risk_policy':'UNKNOWN'}
        raw=opinion('MAPPER',inputs)
        with self.assertRaisesRegex(ValueError,'EVIDENCE_COVERAGE'):pipeline.validate_opinion('MAPPER',raw,inputs)
        raw['evidence_dispositions']={'a':'INSUFFICIENT'}
        self.assertEqual(pipeline.validate_opinion('MAPPER',raw,inputs)['evidence_dispositions'],raw['evidence_dispositions'])
    def test_date_only_calendar_and_unavailable_sources(self):
        self.assertEqual(specialized.fomc_date(2026,'October','27-28*'),'2026-10-28')
        with patch.object(specialized,'request',side_effect=ValueError('offline')):
            e,h,i=specialized.collect([],NOW)
        self.assertFalse(e);self.assertTrue(all(x['status']=='UNAVAILABLE' for x in h))
    def test_authorized_recovery_keeps_uncertain_cost_reserved_and_parent_immutable(self):
        first=self.run_cycle()
        interrupted=pipeline.cycle(NOW,FakeProvider(),lambda t:([],[]),lambda t:copy.deepcopy(self.snap),revision=1)
        with store.db(True) as c:
            c.execute("DELETE FROM reports WHERE run_id=?",(interrupted,))
            c.execute("UPDATE cycles SET status='INTERRUPTED' WHERE id=?",(interrupted,))
            c.execute("UPDATE agents SET status='INTERRUPTED',usage=NULL,output=NULL,started_at=NULL,session_id=NULL WHERE run_id=? AND code!='QUANT'",(interrupted,))
            c.execute("UPDATE agents SET started_at='2026-10-15T22:00:00Z',session_id='messages:fixture',reserved_cents=70 WHERE run_id=? AND code='MACRO'",(interrupted,))
            before=[tuple(r) for r in c.execute('SELECT * FROM agents WHERE run_id=?',(interrupted,))]
        p=FakeProvider();allocations=[];normal=p.run
        def run(code,context,cents,save):
            allocations.append(cents);return normal(code,context,cents,save)
        p.run=run
        recovered=pipeline.cycle(NOW,p,revision=2)
        self.assertEqual(allocations,[60]*7)
        with store.db() as c:
            self.assertEqual(before,[tuple(r) for r in c.execute('SELECT * FROM agents WHERE run_id=?',(interrupted,))])
            hashes=[r[0] for r in c.execute('SELECT input_hash FROM cycles WHERE id IN (?,?)',(interrupted,recovered))]
            self.assertEqual(hashes[0],hashes[1])
        pipeline.cycle(NOW,p,revision=2);self.assertEqual(len(p.calls),7)
