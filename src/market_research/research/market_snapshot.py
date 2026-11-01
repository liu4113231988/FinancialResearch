"""Prepare immutable local snapshots for a fixed validation manifest."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd

from market_research.market.analysis import load_bars, clean_bars
from market_research.research.market_validation import validate_plan, read_bars, fingerprint, check_calendar_coverage
from market_research.data.providers.calendar import bundled_calendar


def prepare_snapshot(plan, root, output, fetch_missing=False, with_calendar=False, progress=None):
    validate_plan(plan)
    # Fail before changing anything when a receipt/snapshot already exists.
    output.mkdir(parents=True, exist_ok=False)
    prepared = deepcopy(plan)
    receipt = {'created_at':datetime.now(timezone.utc).isoformat(), 'manifest':deepcopy(plan),
               'files':[], 'calendar':None, 'explicit_calendars':[], 'errors':[]}
    calendar = None
    if with_calendar:
        try:
            calendar, receipt['calendar'] = bundled_calendar(plan['start'],plan['end'])
            pd.DataFrame({'date':calendar}).to_csv(output/'calendar.csv',index=False)
        except (ValueError, OSError) as error:
            receipt['errors'].append({'calendar':str(error)})
    saved = {}

    def save(asset):
        key = (asset['kind'],asset['code'],asset.get('adjust','qfq') if asset['kind']=='stock' else '')
        effective_calendar=calendar
        if asset.get('calendar_csv'):
            effective_calendar=pd.DatetimeIndex(pd.read_csv(root/asset['calendar_csv'],parse_dates=['date'],date_format='ISO8601')['date'])
        if key in saved:
            if effective_calendar is not None:
                dates=pd.DatetimeIndex(pd.read_csv(output/saved[key][0],parse_dates=['date'],date_format='ISO8601')['date'])
                check_calendar_coverage(pd.DataFrame(index=dates),effective_calendar,plan)
            return saved[key]
        if progress:
            progress(asset['code'])
        source = 'existing_csv' if asset.get('csv') else 'local_sync_cache'
        try:
            frame = read_bars(asset,plan,root)
        except ValueError:
            if not fetch_missing or asset.get('csv'):
                raise
            source = 'fetched_for_validation'
            frame = load_bars(asset['kind'],asset['code'],plan['start'],plan['end'],key[2],refresh=True,_fetch_only=True)
        info = dict(frame.attrs)
        frame = frame.loc[plan['start']:plan['end']].copy()
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError('快照日期重复或未升序')
        # Reuse price validation; do not allow nonpositive adjustment tails here.
        clean_bars(frame.rename_axis('date').reset_index(),plan['start'],plan['end'])
        if frame.empty or frame.index[-1] < pd.Timestamp(plan['holdout_start']):
            raise ValueError('行情未覆盖留出区间')
        check_calendar_coverage(frame,effective_calendar,plan)
        calendar_ok=calendar is not None
        filename = f'{key[0]}-{key[1]}-{key[2] or "raw"}.csv'
        frame.rename_axis('date').to_csv(output/filename)
        receipt['files'].append({'kind':key[0],'code':key[1],'basis':key[2],'file':filename,'source':source,
                                 'provider_info':info,'data_sha256':fingerprint(frame),
                                 'csv_sha256':hashlib.sha256((output/filename).read_bytes()).hexdigest(),
                                 'start':str(frame.index[0].date()),'end':str(frame.index[-1].date()),
                                 'rows':len(frame),'calendar_attached':effective_calendar is not None})
        saved[key] = filename, calendar_ok
        return saved[key]

    for asset in prepared['assets']:
        try:
            explicit_calendar=asset.get('calendar_csv')
            filename, calendar_ok = save(asset)
            asset['csv'] = filename
            if explicit_calendar:
                source_calendar = root/explicit_calendar
                sessions = pd.DatetimeIndex(pd.read_csv(source_calendar,parse_dates=['date'],date_format='ISO8601')['date'])
                dates = pd.DatetimeIndex(pd.read_csv(output/filename,parse_dates=['date'],date_format='ISO8601')['date'])
                check_calendar_coverage(pd.DataFrame(index=dates),sessions,plan)
                cal_name=f'calendar-{asset["kind"]}-{asset["code"]}.csv'
                pd.DataFrame({'date':sessions}).to_csv(output/cal_name,index=False)
                receipt['explicit_calendars'].append({'file':cal_name,'source':str(source_calendar),
                    'sha256':hashlib.sha256((output/cal_name).read_bytes()).hexdigest()})
                asset['calendar_csv']=cal_name
            elif calendar_ok:
                asset['calendar_csv'] = 'calendar.csv'
            elif with_calendar:
                asset.pop('calendar_csv',None)
            benchmark = {'kind':'index','code':asset.get('benchmark','sh000300')}
            if explicit_calendar:
                benchmark['calendar_csv']=explicit_calendar
            if asset.get('benchmark_csv'):
                benchmark['csv'] = asset['benchmark_csv']
            asset['benchmark_csv'] = save(benchmark)[0]
        except (ValueError, KeyError, OSError) as error:
            receipt['errors'].append({'kind':asset['kind'],'code':asset['code'],'error':str(error)})
            # Retain the fixed asset, with an explicitly absent snapshot path.
            # Later offline validation must not silently fall back to a changed DB.
            asset['csv'] = f'unavailable-{asset["kind"]}-{asset["code"]}.csv'
    receipt['complete'] = not receipt['errors']
    (output/'manifest.json').write_text(json.dumps(prepared,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return receipt


def main(argv=None, *, prog=None):
    parser=argparse.ArgumentParser(description=__doc__,prog=prog)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--fetch-missing',action='store_true')
    parser.add_argument('--calendar',action='store_true',help='附加包内供应商日历，校验覆盖范围与行情日期')
    args=parser.parse_args(argv)
    source=Path(args.manifest)
    try:
        receipt=prepare_snapshot(json.loads(source.read_text(encoding='utf-8-sig')),source.parent,Path(args.output_dir),
                                 args.fetch_missing,args.calendar,progress=lambda code:print(f'快照 {code}…',flush=True))
    except (ValueError,KeyError,OSError) as error:
        parser.error(str(error))
    print(f"快照完成；完整覆盖：{receipt['complete']}；错误：{len(receipt['errors'])}")
    return 0 if receipt['complete'] else 2


if __name__=='__main__':
    raise SystemExit(main())
