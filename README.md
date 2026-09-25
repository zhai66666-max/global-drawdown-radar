# 纳斯达克100 每日简报

把原来三个各自跑、各发一封邮件的仓库，合并成**一次运行、一封邮件**。

| 原仓库（已并入） | 原邮件的标题 | 现在变成 |
|---|---|---|
| `nasdaq-etf-monitor` | 纳指回撤 × 国内ETF溢价 | 01 加仓决策 · 02 纳指历史回撤统计 · 03 国内纳指ETF溢价排名 |
| `nasdaq100-daily-report` | 纳斯达克100 QDII 每日深度分析 | 04 纳指市场概况 · 05 宏观指标仪表盘 · 06 技术指标 · 07 综合研判 · 08 成分股涨跌榜 · 12 AI 深度分析 |
| `global-drawdown-radar` | 全球市场回撤雷达 | 09 全球市场横向对比 · 10 全球重点观察 · 11 全球回撤新信号 |

**三个仓库的内容一条都没丢**，只是从三封信变成一封信的 12 个区块。

---

## 为什么合并

1. **三封信讲的是同一件事。** 纳指回撤、全球回撤、纳指行情，2026 年 9 月同一周的判断经常互相矛盾（比如 A 信说"回撤已进入加仓区间"，B 信说"风险偏高观望"），因为各自只看自己那一块数据。
2. **重复取数。** 三个仓库都在抓纳指历史价，都在调 DeepSeek，都在拼 HTML 邮件头。
3. **合并后只占 1 个 Actions 任务**，不再是 3 个 Cron 抢同一时段。

---

## 邮件长什么样

浅色主题、720px 定宽、纯 table 布局（兼容 QQ 邮箱 / Gmail / Apple Mail），顶部是深色渐变头图 + 当日核心四项，中间 12 个区块，底部是数据来源与口径声明。

- **涨跌配色默认用中国习惯（涨红跌绿）**。三个旧项目原来都用欧美习惯（涨绿跌红），改回国标只需改 `config/display.yaml` 里的 `color_convention: intl`。
- 邮件正文用内联样式，**不依赖外部 CSS 和图片**，任何客户端都能正常渲染。
- 某个数据源挂了，对应区块会显示"数据源未就绪"或从目录里消失，**其余内容照常发送**，不会整封邮件失败。

---

## 快速开始

```bash
# 1. 依赖
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置（照 .env.example 填，两套旧命名任选一套）
cp .env.example .env && $EDITOR .env

# 3. 先看效果，不发信
python -m src.main --dry-run        # 抓真实数据 → 渲染 preview.html → 打开浏览器
python -m src.main --preview        # 同上但不打开浏览器

# 4. 验证邮箱能不能登录
python -m src.main --check-smtp

# 5. 正式跑一次
python -m src.main

# 6. 用已经生成好的 HTML 补发一封
python -m src.main --replay data/archive/brief_2026-09-25.html
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--preview` | 只渲染不发信，写出 `preview.html` |
| `--dry-run` | 同 `--preview`，并自动打开浏览器 |
| `--no-email` | 跑完整流程（含归档）但不发信 |
| `--no-ai` | 跳过 DeepSeek 调用，省 token 调试用 |
| `--no-archive` | 不写 `data/archive/` |
| `--check-smtp` | 只验证 SMTP 账号密码 |
| `--replay <html>` | 拿已有 HTML 补发 |
| `-v` | DEBUG 日志 |

---

## 环境变量

两套旧命名都兼容，**不用重新配 Secrets**，任意一套齐全即可（同时存在时以方案 A 为准）。

| 用途 | 方案 A（原 etf-monitor） | 方案 B（原 radar / nasdaq100） |
|---|---|---|
| 服务器 | `EMAIL_HOST` | `SMTP_SERVER` |
| 端口 | `EMAIL_PORT` | `SMTP_PORT` |
| 发件账号 | `EMAIL_USERNAME` | `SENDER_EMAIL` |
| 授权码/密码 | `EMAIL_PASSWORD` | `SENDER_PASSWORD` |
| 收件人 | `EMAIL_TO` | `RECIPIENT_EMAIL` |
| 测试收件人 | `TEST_RECIPIENT`（两套通用） | |
| 收件黑名单 | `EMAIL_BLOCKLIST`（两套通用，默认 `1741534484@qq.com`） | |
| AI 解读 | `DEEPSEEK_API_KEY`（不配也能跑，只是没有第 12 节） | |

端口按约定自动判别：**465 → 隐式 SSL，587/25 → STARTTLS**，不需要额外开关。

---

## 配置项

| 文件 | 管什么 |
|---|---|
| `config/display.yaml` | 涨跌配色约定、邮件主题配色、**每个区块的开关**、加仓档位阈值与文案 |
| `config/strategy.yaml` | 回撤分档、溢价分档、回撤×溢价矩阵、事件阈值 |
| `config/etfs.yaml` | 跟踪的国内纳指 ETF 清单与流动性阈值 |

不想要哪个区块，把 `display.yaml` 里对应的 `sections.*` 改成 `false` 即可——**内容不会丢，只是不渲染**。

---

## 数据来源

按"能算准就行、拿不到就如实标注"的原则，每个区块都有独立的降级链：

| 区块 | 主源 | 兜底 1 | 兜底 2 |
|---|---|---|---|
| 纳指历史回撤 | NASDAQ 官方 API（全历史 OHLCV） | Yahoo Finance | 仓库内 CSV 缓存 |
| 国内 ETF 行情 | 腾讯财经 `qt.gtimg.cn` | 东方财富 `push2` | — |
| 国内 ETF 净值 | 东方财富 `api.fund`（T-1 净值） | — | — |
| 指数行情（^NDX） | Yahoo Finance | NASDAQ 官方 historical | NASDAQ 官方 chart |
| 成分股（40 只） | Yahoo Finance | NASDAQ 官方 chart | 内置中文公司名表 |
| 全球回撤（11 类资产） | Yahoo Finance 复权价 | NASDAQ 官方 chart（一次请求含上市以来全部日线） | — |
| 宏观：QQQ / SOXX | Yahoo Finance | NASDAQ 官方 chart | 腾讯财经 |
| 宏观：VIX | Yahoo Finance | 腾讯财经 `usVIX` | — |
| 宏观：美元指数 / 美元兑离岸人民币 | Yahoo Finance | 东方财富 `100.UDI` / `133.USDCNH` | — |
| 宏观：10 年期美债收益率 | Yahoo Finance | **无**（公开免费接口均不提供，该指标会缺席并如实标注覆盖项数） | — |

几个刻意的设计决定：

- **不把静态净值伪装成实时 IOPV。** 免费源拿不到可靠 IOPV，就用 T-1 净值算溢价并在邮件里写明基准日期。
- **兜底源给不出历史就不编。** 例如 VIX 走腾讯实时行情时没有历史序列，回撤字段留空显示"—"，而不是填 0 假装"零回撤"。
- **页脚的数据来源是运行时真实结果**，不是写死的文案。走兜底了就会写"NASDAQ 官方接口（Yahoo 限流兜底）"。
- **yfinance 熔断器**：Yahoo 一旦限流，连续失败 4 次后本轮直接判为不可用，后续标的立即转兜底，不逐个硬等。实测把单次运行从 288 秒压到 216 秒。

---

## 定时任务

`.github/workflows/daily_brief.yml`，cron `45 22 * * 1-5`（UTC）。

**GitHub 的 schedule 不是准点触发的**——原 `nasdaq-etf-monitor` 声明"北京 07:21"，实测连续 12 天都落在北京 09:32 左右，稳定晚约 2 小时。所以这里的 cron 是**倒推着填**的：

```
UTC 22:45（北京 06:45，美股收盘后）
  + 实测约 2 小时排队漂移
  ≈ 北京 08:45 送达
```

跑**周二到周六**：美股在北京时间凌晨收盘，周一、周日的早上没有新数据，加了只会每天多两封重复邮件。

要改时间就改那一行 cron，**并同步更新文件顶部的注释**，别让注释和时间对不上。

---

## 目录结构

```
src/
├── main.py              # 唯一入口：采集 → AI → 渲染 → 发信 → 归档
├── pipeline.py          # 三来源并发采集，单源失败隔离
├── derive.py            # 原始数据 → 模板要的结论/文案/配色
├── ai.py                # DeepSeek 两次调用（深度分析 + 全球评论），提示词沿用原项目
├── render.py            # 拼上下文 + 渲染 Jinja2
├── email_sender.py      # 统一 SMTP（465/587 自动判别、3 次退避重试、纯文本兜底）
├── settings.py          # 双套 Secrets 命名兼容 + 发信配置校验
├── paths.py             # 全部路径的唯一来源
└── providers/
    ├── common/          # 跨来源共用
    │   ├── us_history.py      # NASDAQ 官方 chart 兜底源（一次请求取全历史）
    │   ├── macro_fallback.py  # 腾讯 / 东财宏观行情兜底
    │   └── yf_breaker.py      # yfinance 熔断器
    ├── etf_monitor/     # ← 原 nasdaq-etf-monitor，仅重写 import 与路径常量
    ├── drawdown_radar/  # ← 原 global-drawdown-radar，同上
    └── nasdaq100/       # ← 原 nasdaq100-daily-report 的计算核
config/                  # 三个仓库的配置合并后按职责拆成三份
templates/brief.html     # 唯一的邮件模板（12 区块 + 页脚）
data/                    # DuckDB 行情库、雷达状态、CSV 缓存、归档
```

被搬进来的三个 provider **计算逻辑一字未改**——`drawdown.py`、`signal.py`、`ranking.py`、`etf.py`、核心的 RSI/均线/回撤/文案判断全部原样保留，只重写了包导入路径和几个被搬家打断的路径常量。

---

## 部署

```bash
git init && git add -A && git commit -m "init: 合并三个简报项目"
gh repo create nasdaq-daily-brief --public --source=. --push
```

然后在仓库 Settings → Secrets and variables → Actions 里配上表里的那套变量。

配完可以先手动触发一次 `workflow_dispatch`，把 `no_email` 勾上，确认 Actions 里能正常出 `preview.html` 再开定时。

> **首次部署后建议跑一次 `workflow_dispatch` 且不勾 `no_email`**，确认邮件能真正送达。SMTP 授权码错、QQ 邮箱未开 SMTP 服务这类问题，只有真发一次才能发现。

---

## 已知限制

- **VIX 走腾讯兜底时行情可能滞后**（实测数据时间戳晚约两周），仅作参考，不会阻塞发信。
- **10 年期美债收益率没有免费兜底源**，Yahoo 不可用时该卡片缺席，邮件会在第 05 节标注"仅 N/M 项数据可用"。
- **溢价率是 T-1 净值口径**，不是实时 IOPV，盘中会与实际折溢价有偏差。
- `data/market.duckdb` 会随每次运行增长，长期跑需要偶尔清理历史。
