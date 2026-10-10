"""Synthetic offline checks of actual deploy source. No model/network calls."""
import ast
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = Path(os.getenv('RT_DEPLOY_ROOT', str(ROOT / 'deploy-compatible')))
SOURCE = Path(os.getenv('RT_BRAIN_SOURCE', str(DEPLOY / 'analysis/claude_brain.py')))
spec=importlib.util.spec_from_file_location('audit_strategy_mode',ROOT/'strategy_mode.py')
strategy=importlib.util.module_from_spec(spec);spec.loader.exec_module(strategy)
tree=ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
# Import-free evaluation of definitions avoids application/provider constructors.
selected=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef,ast.Assign))]
module=types.ModuleType('offline_deploy_brain');sys.modules[module.__name__]=module
import re
from dataclasses import dataclass,field
from typing import Optional,List
module.__dict__.update(json=json,math=math,os=os,re=re,dataclass=dataclass,field=field,Optional=Optional,List=List,strategy_mode=strategy)
exec(compile(ast.Module(body=selected,type_ignores=[]),str(SOURCE),'exec'),module.__dict__)

class CompatibleBrainTests(unittest.TestCase):
    def setUp(self):
        self.old=dict(os.environ)
        os.environ.update(BRAIN_STYLE='smc_flow',ALLOWED_STRATEGIES='SCALP,SWING,POSITION',MIN_RR='2.0',SMC_MIN_SL_PCT='0.9',MAX_SL_PCT='1.5',SMC_MIN_CONFIDENCE='55')
        self.brain=module.ClaudeBrain.__new__(module.ClaudeBrain)
    def tearDown(self):
        os.environ.clear();os.environ.update(self.old)
    def parse(self,**overrides):
        p=dict(action='BUY',confidence=70,entry=100,sl=99,tp=103,strategy='SCALP',reason='synthetic fixture')
        p.update(overrides)
        return self.brain._parse_response(json.dumps(p),100,'SYNTHETIC/USDT')
    def test_eligible_buy_accepted_unchanged(self):
        s=self.parse();self.assertEqual(s.decision,'BUY');self.assertEqual((s.entry,s.stop_loss,s.take_profit,s.confidence),(100,99,103,70))
    def test_eligible_sell_accepted_unchanged(self):
        s=self.parse(action='SELL',sl=101,tp=97);self.assertEqual(s.decision,'SELL');self.assertEqual(s.take_profit,97)
    def test_insufficient_rr_not_rewritten(self):
        s=self.parse(tp=101);self.assertEqual(s.decision,'HOLD');self.assertIn('RR:',s.reasoning)
    def test_inverted_bracket_not_repaired(self):
        self.assertEqual(self.parse(sl=101,tp=99).decision,'HOLD')
    def test_nonfinite_prices_rejected(self):
        for v in (float('nan'),float('inf'),-1):
            for k in ('entry','sl','tp'):
                with self.subTest(k=k,v=v): self.assertEqual(self.parse(**{k:v}).decision,'HOLD')
    def test_invalid_confidence_rejected(self):
        for v in (-5,101): self.assertEqual(self.parse(confidence=v).decision,'HOLD')
    def test_unallowed_strategy_rejected(self):
        self.assertEqual(self.parse(strategy='MARTINGALE').decision,'HOLD')
    def test_missing_sl_rejected(self): self.assertEqual(self.parse(sl=None).decision,'HOLD')
    def test_missing_tp_rejected(self): self.assertEqual(self.parse(tp=None).decision,'HOLD')
    def test_hold_journal_preserved(self):
        s=self.parse(action='HOLD',confidence=0,trade_journal={'hold_code':'RR','wait_condition':'synthetic pullback to supplied level','data_caveats':'news unknown'})
        self.assertEqual(s.trade_journal['hold_code'],'RR');self.assertIn('pullback',s.trade_journal['wait_condition'])
    def test_decisive_prompt_keeps_risk_constraints(self):
        p=module.build_smc_system_prompt()
        for text in ('choose BUY/SELL','wait_condition','Missing timestamps','0.9%','1.5%','>= 2:1','55'):
            self.assertIn(text,p)
    def test_new_calendar_status_api_wired(self):
        text=SOURCE.read_text(encoding='utf-8-sig')
        self.assertIn('macro_calendar.blackout_status',text)
        self.assertNotIn('blackout = f"ACTIVE ({ev[0]})" if ev else "none"',text)

if __name__=='__main__': unittest.main(verbosity=2)
