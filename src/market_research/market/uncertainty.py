"""Deterministic paired moving-block bootstrap diagnostics, not strategy selection."""
import numpy as np

SEED = 20261101
REPLICATES = 1000


def block_intervals(values, *, statistic='mean', blocks=(1, 3, 5), minimum=20,
                    replicates=REPLICATES, seed=SEED):
    data=np.asarray(values,dtype=float)
    if statistic not in ('mean','return_gap'):
        raise ValueError('未知统计量')
    if data.ndim != (1 if statistic=='mean' else 2) or (statistic=='return_gap' and data.shape[1]!=2):
        raise ValueError('均值需一维输入，收益差需两列配对日收益')
    if not np.isfinite(data).all():
        raise ValueError('输入含非有限值，不能静默删除时间观测')
    if statistic=='return_gap' and (data<=-1).any():
        raise ValueError('账户日收益必须大于-100%')
    if isinstance(replicates,bool) or not isinstance(replicates,int) or not 100<=replicates<=20000:
        raise ValueError('重复次数需为100到20000的整数')
    if not blocks or any(isinstance(b,bool) or not isinstance(b,int) or b<1 for b in blocks):
        raise ValueError('块长度需为非空正整数列表')
    if isinstance(minimum,bool) or not isinstance(minimum,int) or minimum<2:
        raise ValueError('样本门槛需为至少2的整数')
    n=len(data)
    def measure(samples):
        if statistic=='mean': return samples.mean(axis=1)
        with np.errstate(over='ignore',invalid='ignore'):
            wealth=np.exp(np.log1p(samples).sum(axis=1))
            return wealth[:,1]-wealth[:,0]
    observed=float(measure(data[None,...])[0]) if n else None
    result={'n':n,'point_estimate':observed if observed is not None and np.isfinite(observed) else None,'replicates':replicates,'seed':seed,
            'confidence':.95,'method':'paired_non_circular_moving_block_percentile',
            'intervals':[], 'note':'逐块长度的点态95%诊断区间，未做区间多重比较校正，不用于选块、调参、选模型或收益显著性断言。'}
    for block in blocks:
        row={'block':block,'required_n':max(minimum,4*block),'status':'insufficient', 'lower':None,'upper':None}
        if n>=row['required_n']:
            rng=np.random.default_rng(seed)
            samples=[]
            for first in range(0,replicates,64):
                size=min(64,replicates-first)
                starts=rng.integers(0,n-block+1,size=(size,int(np.ceil(n/block))))
                indices=(starts[:,:,None]+np.arange(block)).reshape(size,-1)[:,:n]
                samples.append(measure(data[indices]))
            estimates=np.concatenate(samples)
            if not np.isfinite(estimates).all() or not np.isfinite(observed):
                row['status']='unstable'
            elif np.ptp(estimates)<=1e-14:
                row['status']='degenerate'
            else:
                low,high=np.quantile(estimates,[.025,.975])
                row.update(status='estimated',lower=float(low),upper=float(high))
        result['intervals'].append(row)
    return result


def execution_intervals(base, candidate):
    if base['dates']!=candidate['dates']:
        raise ValueError('收益不确定性必须使用相同会话日期')
    a=np.array(base.get('equity',base.get('equity_cash')),dtype=float,copy=True)
    b=np.array(candidate.get('equity',candidate.get('equity_cash')),dtype=float,copy=True)
    for path, account in ((a,base),(b,candidate)):
        if 'config' in account:
            initial=account['config']['initial_cash']
            if not np.isfinite(initial) or initial<=0:
                raise ValueError('初始资金必须有限且为正')
            path/=initial
    if a.ndim!=1 or b.ndim!=1 or not a.size or a.size!=b.size or a.size!=len(base['dates']) or not np.isfinite(a).all() or not np.isfinite(b).all() or (a<=0).any() or (b<=0).any():
        raise ValueError('配对权益必须等长、有限且为正')
    result={'status':'sparse_trades','n_sessions':len(a),
            'base_closed_trades':base['closed_trades'],'candidate_closed_trades':candidate['closed_trades'],
            'minimum_closed_trades_each':5,'point_estimate':float(b[-1]-a[-1]),'bootstrap':None,
            'note':'仅对固定账户净日收益路径做配对条件再抽样；不重拟合、不重放订单/资金/整手约束，不是完整策略生成分布。至少各5笔闭合交易仅为展示门槛，不是功效或有效性保证。'}
    if min(base['closed_trades'],candidate['closed_trades'])<5:
        return result
    # shortcut: resample fixed returns; replay orders and fitting for full strategy uncertainty.
    returns=np.column_stack([np.diff(np.r_[1.,a])/np.r_[1.,a][:-1],
                             np.diff(np.r_[1.,b])/np.r_[1.,b][:-1]])
    result['bootstrap']=block_intervals(returns,statistic='return_gap',blocks=(5,10,20),minimum=60)
    statuses=[x['status'] for x in result['bootstrap']['intervals']]
    result['status']=('unstable' if 'unstable' in statuses else 'estimated' if 'estimated' in statuses
                      else 'degenerate' if 'degenerate' in statuses else 'insufficient')
    return result
