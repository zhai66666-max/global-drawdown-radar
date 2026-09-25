"""AI 解读层。

两个 AI 调用的提示词与解析规则都从原项目原样搬过来，保证输出内容不变：

  1. `nasdaq100_analysis`  ← 原 nasdaq100-daily-report 的 deepseek_analysis
                             （QDII 投资者视角的 5 段式深度分析）
  2. `radar_commentary`    ← 原 global-drawdown-radar/src/main.py 的
                             全球市场概览评论（3-5 句）

两次调用并发执行；任何一次失败都返回 None，邮件里对应区块降级显示，不影响发信。
"""
from __future__ import annotations

import html
import logging
from concurrent.futures import ThreadPoolExecutor

import requests

from src.settings import DEEPSEEK_API, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

# 与原 generate_html 中的解析顺序、图标、配色一致
SECTION_ORDER = ["市场总览", "宏观环境评估", "技术面分析",
                 "加仓策略评估", "QDII 投资建议", "风险提示"]
SECTION_ICONS = {
    "市场总览": "📈", "宏观环境评估": "🌐", "技术面分析": "📊",
    "加仓策略评估": "💰", "QDII 投资建议": "💡", "风险提示": "⚠️",
}
SECTION_COLORS = {
    "市场总览": "#2563eb", "宏观环境评估": "#d97706", "技术面分析": "#16a34a",
    "加仓策略评估": "#ea580c", "QDII 投资建议": "#7c3aed", "风险提示": "#dc2626",
}


def _post(prompt: str, system: str, api_key: str,
          temperature: float = 0.4, max_tokens: int = 1500, timeout: int = 120) -> str | None:
    try:
        resp = requests.post(
            DEEPSEEK_API,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("DeepSeek 调用失败: %s", exc)
        return None


# ─── 1. 纳指 QDII 深度分析（原 nasdaq100-daily-report）────────────────────────

def nasdaq100_analysis(ix: dict, macro: list[dict], components: list[dict],
                       api_key: str, thresholds: dict | None = None) -> str | None:
    """复用被合并进来的 core.deepseek_analysis，提示词零改动。"""
    from src.providers.nasdaq100 import core

    thresholds = thresholds or {}
    th_2x = float(thresholds.get("qqq_add_2x", 10))
    th_5x = float(thresholds.get("qqq_add_5x", 20))

    # core.deepseek_analysis 内部把 10/20 写死在文案里，这里按配置覆盖提示词中的阈值表述
    text = core.deepseek_analysis(ix, macro, components, api_key)
    if text is None:
        return None

    # 若用户在 display.yaml 改了档位，回调一下文案里的数字（默认 10/20 时无变化）
    if (th_2x, th_5x) != (10.0, 20.0):
        text = text.replace("10% 双倍", f"{th_2x:.0f}% 双倍") \
                   .replace("20% 五倍", f"{th_5x:.0f}% 五倍") \
                   .replace(">10% 双倍", f">{th_2x:.0f}% 双倍") \
                   .replace(">20% 五倍", f">{th_5x:.0f}% 五倍")
    return text


def parse_analysis(text: str | None) -> list[dict]:
    """把 【小节】 文本切成模板区块。解析规则与原 generate_html 一致。"""
    if not text:
        return []
    sections: dict[str, str] = {}
    current: str | None = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for kw in SECTION_ORDER:
            if f"【{kw}】" in line:
                current = kw
                sections[current] = line.split(f"【{kw}】", 1)[-1].strip()
                break
        else:
            if current:
                sections[current] = sections.get(current, "") + "\n" + line

    out = []
    for key in SECTION_ORDER:
        body = (sections.get(key) or "").strip()
        if body:
            out.append({
                "key": key,
                "icon": SECTION_ICONS.get(key, "📄"),
                "color": SECTION_COLORS.get(key, "#64748b"),
                "text": body,
                "html": html.escape(body),
            })
    return out


# ─── 2. 全球市场概览评论（原 global-drawdown-radar）──────────────────────────

def radar_commentary(metrics: list[dict], api_key: str) -> str | None:
    """提示词与原 drawdown-radar 的 main.py 完全一致。"""
    lines = []
    for m in metrics:
        dd = m.get("dd_historical")
        change = m.get("daily_change_pct")
        if dd is None:
            continue
        if change is not None:
            lines.append(f"{m['ticker']}|{m['name_cn']}: 历史回撤 {dd*100:.1f}%, 昨日 {change*100:.1f}%")
        else:
            lines.append(f"{m['ticker']}|{m['name_cn']}: 历史回撤 {dd*100:.1f}%")
    data_summary = "\n".join(lines)

    prompt = f"""你是全球宏观市场分析师。基于以下今日全球ETF回撤数据，用3-5句话做一个简洁的全球市场概览评论。

{data_summary}

要求：
1. 重点指出今日最值得关注的1-2个市场
2. 如果存在深度回撤（超过-30%）或历史级回撤（超过-40%）的市场，着重说明
3. 纯文本，不用markdown，中文输出，直接给出评论，不要多余的开场白"""

    return _post(
        prompt,
        "你是全球宏观市场分析师，擅长用简洁专业的语言解读全球市场数据。",
        api_key, temperature=0.4, max_tokens=400, timeout=60,
    )


# ─── 并发调用 ────────────────────────────────────────────────────────────────

def run_all(nasdaq100_raw: dict | None, radar_raw: dict | None,
            api_key: str, thresholds: dict | None = None) -> dict:
    """两次 AI 调用并行执行。缺数据或没配 Key 就跳过对应调用。"""
    result = {"analysis_sections": [], "commentary": None}

    jobs = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        if api_key and nasdaq100_raw:
            jobs[ex.submit(nasdaq100_analysis, nasdaq100_raw["ix"], nasdaq100_raw["macro"],
                           nasdaq100_raw["components"], api_key, thresholds)] = "analysis"
        if api_key and radar_raw:
            jobs[ex.submit(radar_commentary, radar_raw["metrics"], api_key)] = "commentary"

        for fut in jobs:
            kind = jobs[fut]
            try:
                val = fut.result()
            except Exception as exc:
                logger.warning("AI 任务 %s 异常: %s", kind, exc)
                val = None
            if kind == "analysis":
                result["analysis_sections"] = parse_analysis(val)
            else:
                result["commentary"] = val

    if api_key:
        logger.info("AI: 分析小节 %d 段, 全球评论 %s",
                    len(result["analysis_sections"]),
                    "有" if result["commentary"] else "无")
    return result
