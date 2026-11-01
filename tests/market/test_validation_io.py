from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import market_research.research.market_validation as validation
from market_research.research import market_snapshot
from tests.market.test_execution import bars
from tests.market.test_validation_coverage import comparisons


class ValidationIOTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path.cwd()/'output')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.frame=bars([100]*100)
        self.plan=dict(start=str(self.frame.index[0].date()),development_start=str(self.frame.index[20].date()),
                       holdout_start=str(self.frame.index[70].date()),end=str(self.frame.index[-1].date()),
                       assets=[dict(kind='stock',code='sh600519',csv='good.csv')])
        self.frame.rename_axis('date').to_csv(self.root/'good.csv')

    def read(self,name='good.csv'):
        return validation.read_bars(dict(kind='stock',code='sh600519',csv=name),self.plan,self.root)

    def test_report_created_during_evaluation_is_never_overwritten(self):
        source=self.root/'plan.json'; target=self.root/'report.json'
        source.write_text('{}',encoding='utf-8')
        original=b'{"owner":"other run"}'
        def evaluate(*args,**kwargs):
            target.write_bytes(original)
            return {'complete':True}
        with patch.object(validation,'run_plan',side_effect=evaluate),contextlib.redirect_stderr(io.StringIO()),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                validation.main(['--manifest',str(source),'--output',str(target)])
        self.assertEqual(caught.exception.code,2)
        self.assertEqual(target.read_bytes(),original)

    def test_bad_dates_are_rejected_at_read_boundary(self):
        raw=self.frame.rename_axis('date').reset_index()
        variants=[raw.assign(date=['invalid']+raw.date.iloc[1:].astype(str).tolist()),
                  raw.assign(date=[None]+raw.date.iloc[1:].tolist()),
                  pd.concat([raw,raw.iloc[[-1]]]),raw.iloc[::-1],
                  raw.assign(date=raw.date+pd.Timedelta(hours=1))]
        for data in variants:
            data.to_csv(self.root/'bad.csv',index=False)
            with self.subTest(first=str(data.date.iloc[0])),self.assertRaises(ValueError):
                self.read('bad.csv')

    def test_price_and_optional_number_errors_are_explicit(self):
        for field,value in (('close','bad'),('high',1),('volume',-1),('amount',-1),('turnover','bad')):
            data=self.frame.copy()
            data[field]=data[field].astype(object) if field in data else pd.Series(np.nan,index=data.index,dtype=object)
            data.loc[data.index[0],field]=value
            data.rename_axis('date').to_csv(self.root/'bad.csv')
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.read('bad.csv')

    def test_one_bad_asset_does_not_abort_the_rest_of_the_plan(self):
        (self.root/'bad.csv').write_text('date,open,close,high,low\ninvalid,100,100,110,90\n',encoding='utf-8')
        plan=self.plan|{'assets':[dict(kind='stock',code='sh600519',csv='bad.csv'),
                                 dict(kind='stock',code='sz000001',csv='good.csv')]}
        report=validation.run_plan(plan,self.root,execution_only=True)
        self.assertEqual([a['status'] for a in report['assets']],['unavailable','evaluated'])
        self.assertIn('日期',report['assets'][0]['error'])
        self.assertFalse(report['complete'])
        self.assertEqual(report['coverage']['planned'],24)

    def test_invalid_benchmark_uses_same_read_guard(self):
        (self.root/'bad.csv').write_text('date,open,close,high,low\ninvalid,100,100,110,90\n',encoding='utf-8')
        plan=self.plan|{'assets':[self.plan['assets'][0]|{'benchmark_csv':'bad.csv'},
                                 dict(kind='stock',code='sz000001',csv='good.csv',benchmark_csv='good.csv')]}
        with patch.object(validation,'evaluate_asset',return_value=comparisons()) as evaluate:
            report=validation.run_plan(plan,self.root)
        self.assertEqual([a['status'] for a in report['assets']],['unavailable','evaluated'])
        self.assertEqual(evaluate.call_count,1)

    def test_cache_validation_keeps_extras_units_missing_values_and_original_frame(self):
        frame=self.frame.copy(); frame['buy_allowed']=True; frame['limit_up']=110.; frame.iloc[0,frame.columns.get_loc('volume')]=np.nan
        before=frame.copy()
        with patch('market_research.tasks.sync.cached',return_value=frame):
            loaded=validation.read_bars(dict(kind='stock',code='sh600519'),self.plan,self.root)
        pd.testing.assert_frame_equal(frame,before)
        pd.testing.assert_frame_equal(loaded,before)
        with patch('market_research.tasks.sync.cached',return_value=frame.iloc[::-1]),self.assertRaises(ValueError):
            validation.read_bars(dict(kind='stock',code='sh600519'),self.plan,self.root)

    def test_competing_writers_publish_one_complete_json_and_leave_no_temp_files(self):
        folder=self.root/'writers'; folder.mkdir(); target=folder/'report.json'; barrier=threading.Barrier(2)
        def write(owner):
            barrier.wait()
            try:
                validation.save_report({'owner':owner,'values':list(range(2000))},target)
                return owner
            except FileExistsError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(write,(1,2)))
        winners=[r for r in results if r is not None]
        self.assertEqual(len(winners),1)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'owner':winners[0],'values':list(range(2000))})
        self.assertEqual(list(folder.iterdir()),[target])

    def test_serialization_and_publication_failures_leave_no_partial_report(self):
        folder=self.root/'failure'; folder.mkdir(); target=folder/'report.json'
        with self.assertRaises(ValueError):
            validation.save_report({'value':np.nan},target)
        self.assertEqual(list(folder.iterdir()),[])
        operation='rename' if validation.os.name=='nt' else 'link'
        with patch.object(validation.os,operation,side_effect=OSError('publish failed')),self.assertRaises(OSError):
            validation.save_report({'complete':True},target)
        self.assertEqual(list(folder.iterdir()),[])

    def test_bad_manifests_are_cli_errors_without_output_or_traceback(self):
        source=self.root/'plan.json'; target=self.root/'report.json'
        for text in ('{','[]',json.dumps(self.plan|{'assets':{}}),json.dumps(self.plan|{'assets':[1]}),
                     json.dumps(self.plan|{'assets':[self.plan['assets'][0]|{'csv':123}]})):
            source.write_text(text,encoding='utf-8')
            with self.subTest(text=text),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as caught:
                validation.main(['--manifest',str(source),'--output',str(target)])
            self.assertEqual(caught.exception.code,2)
            self.assertFalse(target.exists())

    def test_bom_manifest_is_accepted_and_existing_report_skips_evaluation(self):
        source=self.root/'plan.json'; target=self.root/'nested'/'report.json'
        source.write_text('{}',encoding='utf-8-sig')
        with patch.object(market_snapshot,'prepare_snapshot',return_value={'complete':True,'errors':[]}) as prepare,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(market_snapshot.main(['--manifest',str(source),'--output-dir',str(self.root/'snapshot')]),0)
            prepare.assert_called_once()
        with patch.object(validation,'run_plan',return_value={'complete':True}) as evaluate,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(validation.main(['--manifest',str(source),'--output',str(target)]),0)
            evaluate.assert_called_once()
        before=target.read_bytes()
        with patch.object(validation,'run_plan') as evaluate,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            validation.main(['--manifest',str(source),'--output',str(target)])
        evaluate.assert_not_called()
        self.assertEqual(target.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
