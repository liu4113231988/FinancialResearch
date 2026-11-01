"""Frozen logit calibration on a separate chronological window."""
import numpy as np
import pandas as pd
from scipy.special import logit
from sklearn.linear_model import LogisticRegression


def calibrate_probabilities(bars, probability, horizon, start, holdout, training_end):
    if not pd.Timestamp(training_end) < pd.Timestamp(start) < pd.Timestamp(holdout):
        raise ValueError('模型训练截止需早于校准起点，校准起点需早于留出起点')
    if isinstance(horizon,bool) or not isinstance(horizon,int) or horizon<1:
        raise ValueError('预测周期需为正整数')
    if not bars.index.equals(probability.index) or not bars.index.is_unique or not bars.index.is_monotonic_increasing or bars.index.hasnans:
        raise ValueError('概率与行情需同日期、唯一且升序')
    valid=probability.dropna()
    if not np.isfinite(valid).all() or not valid.between(0,1).all():
        raise ValueError('概率需为0到1的有限值或缺失')
    close=bars.loc[start:pd.Timestamp(holdout)-pd.Timedelta(days=1),'close']
    if not np.isfinite(close).all() or (close<=0).any():
        raise ValueError('校准价格需有限且为正')
    future=close.shift(-horizon)
    frame=pd.DataFrame({'p':probability.reindex(close.index),
                        'y':(future>close).astype(float).where(future.notna())}).dropna()
    keep, last=[], -horizon
    for i,position in enumerate(close.index.get_indexer(frame.index)):
        if position>=last+horizon:
            keep.append(i); last=position
    frame=frame.iloc[keep]
    n=len(frame); positive=int(frame.y.sum())
    out=pd.Series(np.nan,index=bars.index)
    meta={'status':'insufficient','n':n,'positive':positive,'negative':n-positive,
          'minimum_n':20,'minimum_each_class':5,'model_training_cutoff':training_end,
          'start':frame.index[0].date().isoformat() if n else None,
          'end':frame.index[-1].date().isoformat() if n else None,
          'last_label_end':close.index[close.index.get_loc(frame.index[-1])+horizon].date().isoformat() if n else None,
          'method':'logistic_on_clipped_logit','C':1.,'clip_probability':1e-6,
          'slope':None,'intercept':None,
          'note':'固定C=1的logit逻辑校准，只拟合校准窗口已成熟且按周期隔开的标签；样本/类别门槛不是校准有效性保证。非正斜率不用于反转预测排名。'}
    if n<20 or min(positive,n-positive)<5:
        return out,meta
    x=logit(frame.p.clip(1e-6,1-1e-6).to_numpy()).reshape(-1,1)
    if np.ptp(x)<=1e-12:
        meta['status']='degenerate'
        return out,meta
    model=LogisticRegression(C=1.,max_iter=1000,random_state=0).fit(x,frame.y)
    meta.update(slope=float(model.coef_[0,0]),intercept=float(model.intercept_[0]))
    if model.n_iter_[0]>=model.max_iter:
        meta['status']='non_convergent'
        return out,meta
    if meta['slope']<=0:
        meta['status']='non_monotone'
        return out,meta
    test=probability.loc[holdout:].dropna()
    if len(test):
        out.loc[test.index]=model.predict_proba(logit(test.clip(1e-6,1-1e-6).to_numpy()).reshape(-1,1))[:,1]
    meta['status']='fitted'
    return out,meta
