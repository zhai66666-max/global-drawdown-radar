"""综合判断：历史回撤 × ETF溢价 矩阵 —— 加仓条件判断"""
from src.providers.etf_monitor import config_loader


def match_matrix(current_dd, min_premium, matrix):
    """
    根据当前历史回撤和最低可买ETF溢价，匹配矩阵。
    matrix: [{drawdown_range: [lo, hi], premium_max, signal, emoji, level}]
    回撤区间：lo <= dd < hi（负值）。
    """
    for m in matrix:
        lo, hi = m['drawdown_range']
        if lo <= current_dd < hi:
            if min_premium is not None and min_premium <= m['premium_max']:
                return {**m, 'premium_ok': True}
            return {**m, 'premium_ok': False}
    return None


def build_signal(drawdown_summary, ranked_etfs, premium_accept_max):
    """构建综合信号"""
    current_dd = drawdown_summary['current_drawdown']
    level = drawdown_summary['level']

    # 最低可买溢价（考虑流动性过滤：成交额过低的ETF不参与"可买"判断）
    buyable = [e for e in ranked_etfs if e['premium'] <= premium_accept_max and not e['low_liquidity']]
    if not buyable:
        # 放宽：不考虑流动性，只看溢价
        buyable = [e for e in ranked_etfs if e['premium'] <= premium_accept_max]

    min_premium_etf = buyable[0] if buyable else None

    strategy = config_loader.load_strategy()
    matrix_hit = match_matrix(current_dd,
                              min_premium_etf['premium'] if min_premium_etf else None,
                              strategy['matrix'])

    # 回撤条件：是否进入历史加仓区间（阈值从 strategy.yaml 读取）
    dd_in_zone = current_dd <= strategy['add_zone_drawdown']

    signals = {
        'dd_in_zone': dd_in_zone,
        'premium_ok': bool(min_premium_etf),
        'best_etf': min_premium_etf,
        'matrix': matrix_hit,
        'satisfied': dd_in_zone and bool(min_premium_etf),
    }
    return signals


def build_recommendations(ranked_etfs, liquidity_config):
    """生成推荐：最佳/第二/第三选择（排除流动性过低）"""
    recs = []
    for e in ranked_etfs:
        if len(recs) >= 3:
            break
        if e['low_liquidity'] and len(recs) >= 1:
            continue  # 已有推荐时跳过低流动性
        recs.append(e)

    # 如果全部低流动性，直接取前三
    if not recs:
        recs = ranked_etfs[:3]

    labels = ['最佳场内ETF', '第二选择', '第三选择']
    out = []
    for i, e in enumerate(recs):
        out.append({
            'label': labels[i],
            'code': e['code'],
            'name': e['name'],
            'short_name': e['short_name'],
            'premium': e['premium'],
            'emoji': e['premium_level']['emoji'],
        })
    return out
