"""纳斯达克100 历史回撤计算 —— 系统第一核心指标"""
import pandas as pd
import numpy as np


def compute_drawdown_series(df):
    """
    计算完整历史回撤序列。
    输入 df: date, open, high, low, close, volume
    输出增加列: peak(截至当日历史最高收盘价), drawdown(截至当日历史回撤%)
    """
    df = df.sort_values('date').reset_index(drop=True)
    df['peak'] = df['close'].cummax()
    df['drawdown'] = (df['close'] / df['peak'] - 1) * 100
    return df


def current_drawdown(df):
    """当前历史回撤（%）"""
    last = df.iloc[-1]
    return float(last['drawdown'])


def current_peak(df):
    """当前历史最高收盘价"""
    return float(df.iloc[-1]['peak'])


def drawdown_percentile(df):
    """
    当前回撤的历史百分位。
    定义：当前回撤超过历史上百分之多少的观察日。
    drawdown <= 当前回撤 的天数占比（回撤为负，越小越深）。
    """
    current = current_drawdown(df)
    # 历史上有多少天的回撤比当前更深（更小）
    deeper = (df['drawdown'] <= current).sum()
    percentile = (1 - deeper / len(df)) * 100
    return float(percentile)


def max_drawdown_info(df):
    """
    历史最大回撤统计：
    最大回撤幅度、最低点日期、开始日期、恢复日期、持续天数、恢复天数
    """
    peak_date = None
    max_dd = 0
    max_dd_bottom_date = None
    max_dd_peak_date = None
    max_dd_recovery_date = None

    # 扫描最大回撤
    cur_peak = df.iloc[0]['close']
    cur_peak_date = df.iloc[0]['date']
    cur_peak_idx = 0
    for idx, row in df.iterrows():
        if row['close'] > cur_peak:
            cur_peak = row['close']
            cur_peak_date = row['date']
            cur_peak_idx = idx
        dd = (row['close'] / cur_peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
            max_dd_bottom_date = row['date']
            max_dd_peak_date = cur_peak_date
            max_dd_peak_idx = cur_peak_idx

    # 寻找恢复日期（最低点之后首次回到历史峰值）
    if max_dd_peak_date and max_dd_bottom_date:
        bottom_idx = df[df['date'] == max_dd_bottom_date].index[0]
        peak_close = df.loc[max_dd_peak_idx]['close']
        for idx in range(bottom_idx + 1, len(df)):
            if df.iloc[idx]['close'] >= peak_close:
                max_dd_recovery_date = df.iloc[idx]['date']
                break

    def days_between(d1, d2):
        if not d1 or not d2:
            return None
        return (pd.Timestamp(d2) - pd.Timestamp(d1)).days

    return {
        'max_drawdown': float(max_dd),
        'peak_date': str(max_dd_peak_date)[:10],
        'bottom_date': str(max_dd_bottom_date)[:10],
        'recovery_date': str(max_dd_recovery_date)[:10] if max_dd_recovery_date else None,
        'duration_days': days_between(max_dd_peak_date, max_dd_bottom_date),
        'recovery_days': days_between(max_dd_bottom_date, max_dd_recovery_date),
    }


def classify_drawdown(current, levels):
    """
    回撤等级分类。
    levels: [{min, max, label, emoji}]，区间上限不包含本数。
    """
    for lv in levels:
        lo = lv['min']
        hi = lv['max']
        if lo <= current < hi:
            return lv
    # 兜底
    return levels[-1] if current <= levels[-1]['max'] else levels[0]


def detect_drawdown_events(df, thresholds):
    """
    自动识别历史回撤事件。
    对每个阈值（如 -10%），找到每次回撤第一次跌破该阈值的开始日期、
    最低点、恢复日期、持续时间。

    返回: [{threshold, start_date, bottom_date, max_drawdown, recovery_date,
           duration_days, recovery_days}]
    """
    events = []
    df = df.reset_index(drop=True)

    for th in thresholds:
        # 找出该阈值对应的所有区间
        in_drawdown = False
        start_idx = None
        bottom_idx = None

        for i, row in df.iterrows():
            dd = row['drawdown']
            if not in_drawdown and dd <= th:
                # 进入回撤区间
                in_drawdown = True
                start_idx = i
                bottom_idx = i
            elif in_drawdown:
                if dd <= th:
                    # 仍在区间内，更新最低点
                    if dd < df.iloc[bottom_idx]['drawdown']:
                        bottom_idx = i
                else:
                    # 回撤结束（恢复到阈值以上）
                    in_drawdown = False
                    events.append(_build_event(df, th, start_idx, bottom_idx, i))
                    start_idx = None
                    bottom_idx = None

        # 处理进行中的回撤（当前仍在阈值以下）
        if in_drawdown:
            events.append(_build_event(df, th, start_idx, bottom_idx, None))

    # 按阈值和开始日期排序
    events.sort(key=lambda e: (e['threshold'], e['start_date']))
    return events


def _build_event(df, threshold, start_idx, bottom_idx, end_idx):
    start_date = str(df.iloc[start_idx]['date'])[:10]
    bottom_date = str(df.iloc[bottom_idx]['date'])[:10]
    recovery_date = str(df.iloc[end_idx]['date'])[:10] if end_idx is not None else None
    duration = (pd.Timestamp(bottom_date) - pd.Timestamp(start_date)).days
    recovery_days = (pd.Timestamp(recovery_date) - pd.Timestamp(bottom_date)).days \
        if recovery_date else None
    return {
        'threshold': threshold,
        'start_date': start_date,
        'bottom_date': bottom_date,
        'max_drawdown': float(df.iloc[bottom_idx]['drawdown']),
        'recovery_date': recovery_date,
        'duration_days': int(duration),
        'recovery_days': int(recovery_days) if recovery_days is not None else None,
    }


def recent_threshold_dates(events):
    """
    最近一次跌破各阈值的日期。
    返回: {-10: 'YYYY-MM-DD', -15: ..., ...}
    """
    latest = {}
    for e in events:
        th = e['threshold']
        if th not in latest or e['start_date'] > latest[th]:
            latest[th] = e['start_date']
    return latest


def summary(df, levels, thresholds):
    """汇总全部回撤统计"""
    df = compute_drawdown_series(df)
    cur = current_drawdown(df)
    level = classify_drawdown(cur, levels)
    events = detect_drawdown_events(df, thresholds)
    return {
        'current_drawdown': cur,
        'current_peak': current_peak(df),
        'percentile': drawdown_percentile(df),
        'level': level,
        'max_dd': max_drawdown_info(df),
        'events': events,
        'recent_thresholds': recent_threshold_dates(events),
        'series': df,
    }
