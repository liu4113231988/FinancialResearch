"""Raw-quote capital scenarios: board lots, commission floors and lagged capacity.

All parameters are explicit research assumptions, not exchange/broker rules.
Daily capacity uses only the previous completed session's volume.
"""
from dataclasses import asdict, dataclass
from collections import Counter
import math

import numpy as np
import pandas as pd

from market_research.market.execution import (_execution_frame, _block_reason,
                                              _validate_rates, FEE_SCENARIOS,
                                              FLAT_PNL_EPSILON, rule_targets)


@dataclass(frozen=True)
class CapitalConfig:
    initial_cash: float = 100000.
    lot_size: int = 100
    minimum_commission: float = 5.
    max_participation: float | None = None
    volume_unit: str = 'unknown'

    def __post_init__(self):
        for key in ('initial_cash','minimum_commission'):
            value=getattr(self,key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                raise ValueError(f'{key}需为有限非负数值')
        if self.initial_cash<=0:
            raise ValueError('initial_cash需大于0')
        if isinstance(self.lot_size,bool) or not isinstance(self.lot_size,int) or self.lot_size<1:
            raise ValueError('lot_size需为正整数')
        if self.max_participation is not None:
            value=self.max_participation
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<value<=1:
                raise ValueError('max_participation需在0到1之间且大于0')
            if self.volume_unit!='shares':
                raise ValueError('容量约束需明确volume_unit=shares')


def parse_capital_config(value):
    if not isinstance(value,dict):
        raise ValueError('capital需为对象')
    try:
        return CapitalConfig(**value)
    except TypeError as error:
        raise ValueError('capital包含未知参数或参数类型无效') from error


def simulate_capital(bars, targets, config, fee_bps=10., slippage_bps=5., calendar=None):
    _validate_rates(fee_bps,slippage_bps)
    frame,orders,missing=_execution_frame(bars,targets,calendar)
    volume=frame['volume'] if 'volume' in frame else pd.Series(np.nan,index=frame.index)
    if 'is_session' in frame:
        volume=volume.mask(frame.is_session.eq(False))
    previous_volume=volume.shift()
    cash=config.initial_cash; units=0; cycle_cost=0.; cycle_proceeds=0.
    fills=[]; blocked=[]; equity=[]; exposures=[]; quantities=[]; cash_path=[]; cycles=[]
    fees=0.; impact=0.; turnover=0.; partial_fills=0
    last_buy_date, t1_locked = None, 0
    lot=config.lot_size; rate=fee_bps/10000; slip=slippage_bps/10000
    # 首日是 shift(1) 产生的 NaN，不代表信号缺失；其余会话是否连续决定
    # 输入是「持续满仓目标」还是「规则/概率脉冲事件」，两者的补仓语义不同。
    active_orders=orders.iloc[1:] if len(orders)>1 else orders
    signal_semantics=('persistent_target' if len(active_orders) and bool(active_orders.notna().all())
                      else 'pulse_events')
    for i,(date,row) in enumerate(frame.iterrows()):
        target=orders.iloc[i]
        side='buy' if target==1 else 'sell' if target==0 and units>0 else None
        if side:
            reason=_block_reason(row,frame,side)
            if reason is None and side=='sell' and last_buy_date==date:
                reason='t1_lock'
            price=row.open*(1+slip if side=='buy' else 1-slip)
            capacity=None
            if reason is None and config.max_participation is not None:
                volume=previous_volume.iloc[i]
                if pd.isna(volume):
                    reason='unknown_previous_volume'
                else:
                    capacity=math.floor(volume*config.max_participation/lot)*lot
            desired=0
            if reason is None:
                if side=='buy':
                    by_rate=math.floor(cash/(price*(1+rate))/lot)*lot
                    by_floor=math.floor(max(0,cash-config.minimum_commission)/price/lot)*lot
                    desired=max(0,min(by_rate,by_floor))
                    # A residual balance below one lot is not a new order while already invested.
                    if desired==0 and units>0:
                        side=None
                    elif desired==0:
                        reason='insufficient_cash_for_lot'
                else:
                    desired=units
            quantity=min(desired,capacity) if capacity is not None else desired
            if side and reason is None and quantity<lot:
                reason='previous_volume_capacity'
            if side and reason is None:
                notional=quantity*price
                commission=max(notional*rate,config.minimum_commission)
                if side=='sell' and cash+notional-commission<0:
                    reason='insufficient_cash_for_sell_fee'
                else:
                    nav_at_open=cash+units*row.open
                    if side=='buy':
                        spent=notional+commission
                        cash-=spent; units+=quantity; cycle_cost+=spent
                        last_buy_date=date
                    else:
                        net=notional-commission
                        cash+=net; units-=quantity; cycle_proceeds+=net
                        if units==0:
                            cycles.append({'pnl':cycle_proceeds-cycle_cost,'return':cycle_proceeds/cycle_cost-1})
                            cycle_cost=0.; cycle_proceeds=0.
                    partial_fills+=quantity<desired
                    turnover+=notional/nav_at_open
                    fees+=commission; impact+=quantity*abs(price-row.open)
                    fills.append({'signal_date':str(frame.index[i-1].date()),'date':str(date.date()),'side':side,
                                  'quantity':int(quantity),'requested_quantity':int(desired),'open_price':float(row.open),
                                  'effective_price':float(price),'commission':float(commission),'cash_after':float(cash),
                                  'units_after':int(units),'previous_volume':None if pd.isna(previous_volume.iloc[i]) else float(previous_volume.iloc[i])})
            if side and reason:
                blocked.append({'signal_date':str(frame.index[i-1].date()),'date':str(date.date()),'side':side,'reason':reason})
                if reason=='t1_lock':
                    t1_locked+=1
        nav=cash+units*row.close
        equity.append(float(nav)); exposures.append(float(units*row.close/nav)); quantities.append(units); cash_path.append(float(cash))
    normalized=np.r_[1.,np.asarray(equity)/config.initial_cash]
    profits=[x['pnl'] for x in cycles if x['pnl']>FLAT_PNL_EPSILON]
    losses=[x['pnl'] for x in cycles if x['pnl']<-FLAT_PNL_EPSILON]
    decided_cycles=len(profits)+len(losses); flat_cycles=len(cycles)-decided_cycles
    coverage={name:int(frame[name].notna().sum()) if name in frame else 0
              for name in ('volume','limit_up','limit_down','buy_allowed','sell_allowed')}
    constraint_data_available=any(coverage[name] for name in
                                  ('limit_up','limit_down','buy_allowed','sell_allowed'))
    if signal_semantics=='persistent_target':
        _retry_note=('受限与未成交余额当日失效。目标为满仓时，每个交易日都会用剩余现金重新尝试买入'
                     '（含部分成交后的次日），直到不足一手、现金耗尽或触及容量上限；'
                     '这不是"只在新目标出现时才补仓"。')
    else:
        _retry_note=('脉冲目标（规则条件或概率阈值）只在目标出现后的首个会话产生订单；'
                     '受限与未成交余额当日失效，不会跨日自动重试补仓，部分卖出后的残余仓位'
                     '可能保留到下一次反向信号。该口径与可分割账户一致，但资金曲线不可直接互比。')
    return {'config':asdict(config),'n_sessions':len(frame),'net_return':float(normalized[-1]-1),
            'ending_cash':float(cash),'ending_units':units,'ending_equity':equity[-1],
            'max_drawdown':float(np.min(normalized/np.maximum.accumulate(normalized)-1)),
            'fills':len(fills),'partial_fills':int(partial_fills),'closed_trades':len(cycles),
            'win_rate':len(profits)/decided_cycles if decided_cycles else None,
            'flat_cycles':flat_cycles,'signal_semantics':signal_semantics,
            'payoff_ratio':float(np.mean(profits)/abs(np.mean(losses))) if profits and losses else None,
            'profit_factor':float(sum(profits)/abs(sum(losses))) if losses else None,
            'turnover':float(turnover),'annual_turnover':float(turnover*252/len(frame)),
            'average_exposure':float(np.mean(exposures)), 'fees_cash':float(fees),'slippage_cash':float(impact),
            'fee_bps':fee_bps,'slippage_bps':slippage_bps,'open_position':units>0,
            'blocked_count':len(blocked),'blocked_reasons':dict(Counter(x['reason'] for x in blocked)),
            'blocked_orders':blocked,'trades':fills,'closed_cycles':cycles,
            'dates':frame.index.strftime('%Y-%m-%d').tolist(),'equity_cash':equity,'cash_path':cash_path,'units_path':quantities,
            'calendar_verified':calendar is not None,'missing_sessions':missing,
            'constraint_coverage':coverage,
            'constraint_data_available':constraint_data_available,
            't1_locked_count':t1_locked,
            'capacity_mode':'previous_session_volume' if config.max_participation is not None else 'disabled',
            'exposure_control':{'matched':False,'reason':'整手和最低佣金为非线性约束，不使用可分割账户的缩放对照'},
            'assumptions':['仅用于显式不复权股票报价；分红、拆分和实际费用明细尚未入账，不是总回报或券商账户复现。',
                           '买入当日不可卖出（T+1）已显式拦截；constraint_data_available=false 时'
                           '涨跌停/停牌/成交权限按可成交假设，结果不能作为真实可执行性证据。',
                           '资金、整手、最低佣金及容量都是情景参数，不是交易所或券商规则。费率为0时仍可能收取配置的最低佣金。',
                           '容量使用上一完整会话成交量，缺失则拒绝成交；它不是开盘集合竞价可成交数量保证。',
                           _retry_note,
                           '期末按收盘估值不强制平仓；部分卖出直到持仓全清才统计一个闭合周期；'
                           '胜率分母只含有明确盈亏的闭合周期，平局单列 flat_cycles。']}


def capital_diagnostics(bars, indicators, config, calendar=None):
    targets={'rules':rule_targets(indicators),'buy_hold':pd.Series(1.,index=bars.index)}
    out={'account_mode':'capital','config':asdict(config),'strategies':{}}
    for name,signal in targets.items():
        rows=[]
        for fee,slip in FEE_SCENARIOS:
            result=simulate_capital(bars,signal,config,fee,slip,calendar)
            for key in ('dates','equity_cash','cash_path','units_path','trades','blocked_orders','closed_cycles'):
                result.pop(key)
            rows.append(result)
        out['strategies'][name]=rows
    return out
