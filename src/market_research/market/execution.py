"""Long-only, fractional-unit diagnostic execution at the next session's open."""
import numpy as np
import pandas as pd

FEE_SCENARIOS = ((0., 0.), (10., 5.), (30., 10.))
ENTRY_PROBABILITY = .6
EXIT_PROBABILITY = .4


def _flag(value, label):
    if pd.isna(value):
        return None
    if isinstance(value, (bool, np.bool_)) or value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    raise ValueError(f'{label}必须为布尔值、0或1')


def _validate_rates(fee_bps, slippage_bps):
    for value in (fee_bps, slippage_bps):
        if isinstance(value, bool) or not np.isfinite(value) or not 0 <= value < 10000:
            raise ValueError('费率与滑点需为0至10000之间的有限基点数')


def _execution_frame(bars, targets, calendar):
    if bars.empty or not isinstance(bars.index, pd.DatetimeIndex) or not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError('行情需为非空、唯一且升序的日期索引')
    prices = bars[['open', 'high', 'low', 'close']]
    if not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any().any():
        raise ValueError('OHLC必须为有限正数')
    if ((bars.high < bars[['open', 'close', 'low']].max(axis=1)) | (bars.low > bars[['open', 'close', 'high']].min(axis=1))).any():
        raise ValueError('OHLC高低价关系异常')
    if not targets.index.is_unique:
        raise ValueError('信号日期不能重复')
    targets = targets.reindex(bars.index).astype(float)
    if not targets.dropna().isin([0., 1.]).all():
        raise ValueError('目标只能为0、1或空值')
    frame = bars.copy()
    for name in ('buy_allowed', 'sell_allowed', 'is_session'):
        if name in frame:
            frame[name] = frame[name].map(lambda v: _flag(v, name))
    for name in ('volume', 'limit_up', 'limit_down'):
        if name in frame:
            valid = frame[name].dropna()
            if not np.isfinite(valid.to_numpy()).all() or (valid < 0).any() or (name != 'volume' and (valid == 0).any()):
                raise ValueError(f'{name}含非法数值')
    if {'limit_up','limit_down'}.issubset(frame) and (frame.limit_down > frame.limit_up).any():
        raise ValueError('涨跌停边界顺序错误')
    missing_dates = []
    if calendar is not None:
        sessions = pd.DatetimeIndex(calendar)
        if not sessions.is_unique or not sessions.is_monotonic_increasing:
            raise ValueError('交易日历必须唯一且升序')
        sessions = sessions[(sessions >= bars.index[0]) & (sessions <= bars.index[-1])]
        if not bars.index.isin(sessions).all():
            raise ValueError('行情含日历之外的日期')
        missing_dates = sessions.difference(bars.index).strftime('%Y-%m-%d').tolist()
        frame = frame.reindex(sessions)
    frame['_missing'] = frame.close.isna()
    frame['close'] = frame.close.ffill()  # valuation only, never fill executable open
    valid_signals = targets.reindex(frame.index)
    if 'is_session' in frame:
        valid_signals = valid_signals.mask(frame.is_session.eq(False))
    orders = valid_signals.shift(1)
    return frame, orders, missing_dates


def _block_reason(row, frame, side):
    if row['_missing']:
        return 'missing_session'
    if 'is_session' in frame and _flag(row.is_session, 'is_session') is False:
        return 'closed_session'
    if 'volume' in frame and pd.notna(row.volume) and row.volume <= 0:
        return 'zero_volume'
    if side+'_allowed' in frame and _flag(row[side+'_allowed'], side+'_allowed') is False:
        return 'explicit_restriction'
    if side == 'buy' and pd.notna(row.get('limit_up')) and row.open >= row.limit_up - 1e-10:
        return 'upper_limit'
    if side == 'sell' and pd.notna(row.get('limit_down')) and row.open <= row.limit_down + 1e-10:
        return 'lower_limit'
    return None


def simulate_execution(bars, targets, fee_bps=10., slippage_bps=5., calendar=None):
    """Day orders from the previous close; final positions are marked, not sold."""
    _validate_rates(fee_bps, slippage_bps)
    frame, orders, missing_dates = _execution_frame(bars, targets, calendar)
    fee, slip = fee_bps/10000, slippage_bps/10000
    cash, units, entry_cost = 1., 0., None
    equity, exposure, fills, blocked, round_trips = [], [], [], [], []
    turnover, fees, slippage = 0., 0., 0.
    for i, (date, row) in enumerate(frame.iterrows()):
        target = orders.iloc[i]
        side = 'buy' if target == 1 and units == 0 else 'sell' if target == 0 and units > 0 else None
        if side:
            reason = _block_reason(row, frame, side)
            fill_price = row.open*(1+slip if side == 'buy' else 1-slip)
            signal_date = frame.index[i-1].date().isoformat()
            if reason:
                blocked.append({'signal_date': signal_date, 'date': date.date().isoformat(), 'side': side, 'reason': reason})
            else:
                nav_at_open = cash+units*row.open
                if side == 'buy':
                    entry_cost = cash
                    units = cash/(fill_price*(1+fee))
                    notional = units*fill_price
                    paid = notional*fee
                    slip_cost = units*(fill_price-row.open)
                    cash = 0.
                else:
                    notional = units*fill_price
                    paid = notional*fee
                    slip_cost = units*(row.open-fill_price)
                    cash = notional-paid
                    round_trips.append(cash/entry_cost-1)
                    units, entry_cost = 0., None
                turnover += notional/nav_at_open
                fees += paid; slippage += slip_cost
                fills.append({'signal_date': signal_date, 'date': date.date().isoformat(), 'side': side,
                              'open_price': float(row.open), 'effective_price': float(fill_price),
                              'notional': float(notional), 'fee': float(paid)})
        nav = cash+units*row.close
        equity.append(float(nav)); exposure.append(float(units*row.close/nav))
    curve = np.array([1.]+equity)
    returns = curve[1:]/curve[:-1]-1
    wins = [r for r in round_trips if r > 1e-12]
    losses = [r for r in round_trips if r < -1e-12]
    coverage = {name: int(frame[name].notna().sum()) if name in frame else 0
                for name in ('volume','limit_up','limit_down','buy_allowed','sell_allowed')}
    return {'n_sessions': len(frame), 'net_return': equity[-1]-1,
            'max_drawdown': float(np.min(curve/np.maximum.accumulate(curve)-1)),
            'annual_volatility': float(np.std(returns, ddof=1)*np.sqrt(252)) if len(returns)>1 else None,
            'fills': len(fills), 'closed_trades': len(round_trips),
            'win_rate': len(wins)/len(round_trips) if round_trips else None,
            'payoff_ratio': float(np.mean(wins)/abs(np.mean(losses))) if wins and losses else None,
            'profit_factor': float(sum(wins)/abs(sum(losses))) if losses else None,
            'turnover': float(turnover), 'annual_turnover': float(turnover*252/len(frame)),
            'average_exposure': float(np.mean(exposure)),
            'fees_initial_capital': float(fees), 'slippage_initial_capital': float(slippage),
            'open_position': bool(units), 'fee_bps': fee_bps, 'slippage_bps': slippage_bps,
            'blocked_count': len(blocked), 'blocked_orders': blocked, 'trades': fills,
            'dates': frame.index.strftime('%Y-%m-%d').tolist(), 'equity': equity, 'exposure': exposure,
            'calendar_verified': calendar is not None, 'missing_sessions': missing_dates,
            'constraint_coverage': coverage,
            'assumptions': ['收盘信号只在下一会话开盘尝试成交，受限订单当日失效；无卖空、杠杆或日内往返。',
                            '单位可分割，不含整手、最低佣金、税费明细及资金容量。费用为固定情景假设。',
                            '期末持仓按收盘估值，未强制平仓；胜率和盈亏比仅统计已闭合交易。',
                            '滑点为开盘价的固定冲击情景，不利用当日高低价决定开盘能否成交，也不保证冲击价为真实报价。',
                            '未提供日历时仅按有效日线推进；未知涨跌停或成交权限按可成交假设，不能认定真实可执行。',
                            '复权价和指数点位仅作诊断，涨跌停价格必须与OHLC同口径；指数还需ETF验证。']}


def rule_targets(indicators):
    out = pd.Series(np.nan, index=indicators.index)
    out.loc[indicators.entry] = 1.
    out.loc[indicators.exit] = 0.
    return out


def matched_exposure_control(strategy, passive):
    """Ex-post constant initial cash/hold sleeve with the same mean exposure.

    Allocation is calibrated after the full interval solely as a diagnostic
    comparator. It must never be consumed by a signal or described as tradable
    out-of-sample allocation advice. The invested sleeve drifts naturally.
    """
    if strategy['dates'] != passive['dates']:
        raise ValueError('仓位对照必须使用相同日期')
    target = strategy['average_exposure']
    equity = np.asarray(passive['equity'], dtype=float)
    exposure = np.asarray(passive['exposure'], dtype=float)
    report = {'matched': False, 'target_average_exposure': target,
              'ex_post': True, 'note': '事后匹配平均仓位的固定初始现金/持有组合，持仓随后自然漂移；不参与信号或模型选择，不代表可执行配置或显著择时贡献。'}
    def average(q):
        return float(np.mean(q*equity*exposure/(1-q+q*equity)))
    if target < -1e-12 or target > average(1.)+1e-10:
        report['reason'] = '该被动组合无法达到策略的平均仓位'
        return report
    lo, hi = 0., 1.
    for _ in range(60):
        mid = (lo+hi)/2
        if average(mid) < target:
            lo = mid
        else:
            hi = mid
    q = 0. if target <= 1e-12 else (lo+hi)/2
    nav = 1-q+q*equity
    curve = np.r_[1., nav]
    net = float(nav[-1]-1)
    report.update(matched=True, initial_allocation=float(q), average_exposure=average(q),
                  net_return=net, max_drawdown=float(np.min(curve/np.maximum.accumulate(curve)-1)),
                  excess_net_return=float(strategy['net_return']-net),
                  fees_initial_capital=q*passive['fees_initial_capital'],
                  slippage_initial_capital=q*passive['slippage_initial_capital'])
    return report


def execution_summary(result):
    return {k:v for k,v in result.items() if k not in ('dates','equity','exposure','trades','blocked_orders')}


def execution_diagnostics(bars, targets, calendar=None, detail=False):
    """Fixed cost scenarios; same input bars and execution rules for buy-and-hold."""
    strategies = {'rules': targets, 'buy_hold': pd.Series(1., index=bars.index)}
    report = {'strategies': {}, 'note': '仅作成交假设诊断；同区间收盘信号→次会话开盘，固定单边费用与滑点情景。指数点位和复权价格不能直接作为成交报价。策略与持有对照的平均仓位不同，收益差不代表独立择时贡献。'}
    report['strategies'] = {key:[] for key in strategies}
    for fee, slip in FEE_SCENARIOS:
        results = {key:simulate_execution(bars, signal, fee, slip, calendar) for key,signal in strategies.items()}
        results['rules']['exposure_control'] = matched_exposure_control(results['rules'],results['buy_hold'])
        for key,result in results.items():
            report['strategies'][key].append(result if detail else execution_summary(result))
    return report


def probability_targets(probability):
    """Fixed hysteresis, checked every close; no probability-to-size mapping."""
    values = probability.astype(float)
    if not values.index.is_unique:
        raise ValueError('概率日期不能重复')
    valid = values.dropna()
    if not np.isfinite(valid.to_numpy()).all() or not valid.between(0, 1).all():
        raise ValueError('概率必须在0到1之间或为空值')
    out = pd.Series(np.nan, index=values.index)
    out.loc[values >= ENTRY_PROBABILITY] = 1.
    out.loc[values <= EXIT_PROBABILITY] = 0.
    return out


def compare_probability_execution(bars, base, candidate, calendar=None):
    """Paired trading diagnostics without filtering future realized outcomes.

    Only current probability availability determines the common start. Missing
    interior predictions suppress both models' new orders, while existing
    positions remain marked to market through the same requested final close.
    """
    # Validate original inputs even if invalid values would be masked by pairing.
    probability_targets(base); probability_targets(candidate)
    pair = pd.DataFrame({'base': base.reindex(bars.index),
                         'candidate': candidate.reindex(bars.index)}, index=bars.index)
    common = pair.notna().all(axis=1)
    report = {'status': 'insufficient', 'n_common_predictions': int(common.sum()),
              'sample_sufficient': int(common.sum()) >= 20, 'start': None, 'end': None,
              'entry_probability': ENTRY_PROBABILITY, 'exit_probability': EXIT_PROBABILITY,
              'strategies': {},
              'note': '固定概率阈值：≥60%关注买入，≤40%退出，中间无新订单；逐日检查，不是固定持有预测周期。双方使用相同可预测起点、缺失屏蔽、会话与费用。收益差仍包含仓位差，非显著性或独立择时贡献证明。'}
    if not common.any():
        return report
    start = pair.index[common][0]
    window = bars.loc[start:]
    report.update(start=start.date().isoformat(), end=window.index[-1].date().isoformat(),
                  n_sessions=len(window), missing_prediction_sessions=int((~common.loc[start:]).sum()))
    if len(window) < 2:
        return report
    signals = {key: probability_targets(pair[key].where(common).loc[start:]) for key in ('base','candidate')}
    signals['buy_hold'] = pd.Series(1., index=window.index)
    report['strategies'] = {key:[] for key in signals}
    for fee, slip in FEE_SCENARIOS:
        results = {key:simulate_execution(window, target, fee, slip, calendar) for key,target in signals.items()}
        for key in ('base','candidate'):
            results[key]['exposure_control'] = matched_exposure_control(results[key],results['buy_hold'])
        for key,result in results.items():
            report['strategies'][key].append(execution_summary(result))
    report['status'] = 'evaluated'
    return report
