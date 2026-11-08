"""仅使用当日及此前行情的环境与候选特征，不产生交易指令。"""
import numpy as np
import pandas as pd


def percentile(series, window=252, minimum=60):
    values=series.replace([np.inf,-np.inf],np.nan)
    return values.rolling(window, min_periods=minimum).apply(
        lambda x: np.mean(x[~np.isnan(x)] <= x[-1]) if not np.isnan(x[-1]) else np.nan, raw=True)


def context_features(bars, ind, reference=None):
    close = bars.close
    out = pd.DataFrame(index=bars.index)
    out['atr_percentile'] = percentile(ind.atr / close)
    out['bandwidth_percentile'] = percentile((ind.boll_upper - ind.boll_lower) / ind.ma20)
    up, down = bars.high.diff(), -bars.low.diff()
    from market_research.market.technical import wilder
    plus = wilder(up.where((up > down) & (up > 0), 0.))
    minus = wilder(down.where((down > up) & (down > 0), 0.))
    out['adx'] = wilder(100 * (plus - minus).abs() / (plus + minus).replace(0, np.nan))
    if 'volume' in bars:
        out['volume_ratio'] = ind.volume_ratio
    for field in ('amount', 'turnover'):
        if field in bars:
            prior = bars[field].shift().rolling(20).mean().replace(0, np.nan)
            out[field + '_ratio'] = bars[field] / prior
    if reference is not None:
        # Exact date alignment: missing benchmark sessions stay missing.
        ref = reference.reindex(bars.index).where(lambda s: s > 0)
        for n in (20, 60):
            out[f'relative{n}'] = (close / ref).pct_change(n, fill_method=None)
        ma = ref.rolling(60).mean()
        out['benchmark_gap60'] = ref / ma - 1
        out['benchmark_return20'] = ref.pct_change(20, fill_method=None)
    return out.replace([np.inf, -np.inf], np.nan)


def market_regimes(features):
    gap = features.get('benchmark_gap60', pd.Series(np.nan, index=features.index))
    momentum = features.get('benchmark_return20', pd.Series(np.nan, index=features.index))
    out = pd.Series('unknown', index=features.index)
    valid = gap.notna() & momentum.notna()
    out.loc[valid] = 'mixed'
    out.loc[valid & (gap > 0) & (momentum > 0)] = 'strong'
    out.loc[valid & (gap < 0) & (momentum < 0)] = 'weak'
    return out


def context_payload(features, benchmark):
    last = features.iloc[-1]
    gap = last.get('benchmark_gap60', np.nan)
    momentum = last.get('benchmark_return20', np.nan)
    environment = ('数据不足' if pd.isna(gap) or pd.isna(momentum) else
                   '偏强' if gap > 0 and momentum > 0 else
                   '偏弱' if gap < 0 and momentum < 0 else '分歧 / 震荡')
    return {'benchmark': benchmark, 'environment': environment,
            'latest': last.to_dict(),
            'note': '环境为基准相对 MA60 与20日动量的规则描述；ADX衡量趋势强度、不代表方向。分位只使用最多252条日线内的有限观测，至少60个，当日缺失不输出；分位含当日自身参与排名（等值按小于等于计入），因此最低为 1/N、恒定窗口恒为 100%，不是排除当日的历史分位。相对强弱为价格比率变化，未含分红。'}
