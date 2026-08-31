# 🌍 Global Market Drawdown Radar

全球市场回撤雷达 — 个人长期投资监控系统。

每天北京时间 07:21 自动运行，获取 11 个全球主要 ETF 的最新数据，计算多时间维度回撤、历史回撤分位数、波动率等指标，生成 Bloomberg 风格的专业 HTML 晨报邮件。

---

## 📊 监控资产

| Ticker | 中文名称 | 市场 |
|--------|----------|------|
| QQQM | 纳斯达克100 | 美国科技股 |
| SPY | 标普500 | 美国大盘 |
| EWJ | 日本 | 日本 |
| EWY | 韩国 | 韩国 |
| INDA | 印度 | 印度 |
| EWT | 台湾 | 台湾 |
| EWC | 加拿大 | 加拿大 |
| EWW | 墨西哥 | 墨西哥 |
| EWA | 澳大利亚 | 澳大利亚 |
| EWZ | 巴西 | 巴西 |
| GLD | 黄金 | 黄金 |

---

## 📈 核心指标

| 指标 | 说明 |
|------|------|
| 最新价 | 最近有效交易日收盘价（未复权） |
| 昨日涨跌 | 最近交易日 / 前一交易日 - 1 |
| 52周回撤 | 当前价格 / 52周最高价 - 1 |
| 52周最大回撤 | 52周内最大回撤（通过完整回撤序列计算） |
| 5年回撤 | 当前价格 / 5年最高价 - 1 |
| 历史回撤 | 当前价格 / 历史最高价 - 1 |
| 历史最大回撤 | ETF上市以来最大回撤 |
| 回撤分位 | 当前回撤在历史回撤分布中的百分位 |
| 本轮最大回撤 | 从最近历史高点开始的最大回撤 |
| 20日波动率 | 20日年化波动率 |
| 距历史高点 | 距离历史最高点的天数 |

---

## 🔬 回撤计算方法

### 回撤分位数（无未来数据泄露）

回撤分位数是系统最核心的指标。计算方式：

```
对于每一个历史交易日 t：
  running_max[t] = max(price[0], price[1], ..., price[t])
  drawdown[t] = price[t] / running_max[t] - 1

然后：
  percentile = (历史交易日中 drawdown <= 当前drawdown 的天数) / 总交易天数 × 100
```

**关键**：使用 expanding window（扩展窗口）的 cummax，每天的回撤只使用当天及之前已知的最高价计算，不存在未来数据泄露。

### ETF 分红与复权

- **长期回撤指标**（历史回撤、52周回撤、最大回撤等）：使用 **Adjusted Close（复权价格）**，已调整分红和拆股
- **最新市场价格**：使用 **Close（未复权收盘价）**，反映实际交易价格

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.9+
- Git

### 2. 安装

```bash
git clone https://github.com/zhai66666-max/global-drawdown-radar.git
cd global-drawdown-radar
pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
# 编辑 .env 填入你的 Gmail 和 SMTP 信息
```

### 4. 本地预览

```bash
python -m src.main --preview
# 生成 preview.html，在浏览器中查看邮件效果
```

### 5. 发送测试邮件

```bash
python -m src.main --send-test
# 发送到 TEST_RECIPIENT 指定的地址
```

---

## ✉️ Gmail App Password 配置

1. 登录 Gmail → 管理您的 Google 账号 → 安全性
2. 开启「两步验证」
3. 搜索「应用专用密码」→ 生成一个 16 位密码
4. 填入 `.env` 的 `SMTP_PASSWORD`

---

## ⚙️ GitHub Actions

### 设置 Secrets

在 GitHub 仓库 Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 |
|--------|------|
| `SMTP_USER` | Gmail 地址 |
| `SMTP_PASSWORD` | Gmail App Password |
| `EMAIL_TO` | 收件邮箱 |
| `TEST_RECIPIENT` | 测试收件邮箱 |
| `DEEPSEEK_API_KEY` | (可选) DeepSeek API Key，用于 AI 市场评论 |
| `GH_PAT` | GitHub Personal Access Token，用于自动提交 state.json |

### 定时运行

- Cron: `21 23 * * *` (UTC 23:21 = 北京时间 07:21)
- 也支持手动触发 (`workflow_dispatch`)

---

## 📁 项目结构

```
global-drawdown-radar/
├── src/
│   ├── config.py          # 配置常量、ETF定义、阈值
│   ├── data_fetcher.py    # yfinance 数据获取 + 重试
│   ├── drawdown.py        # 回撤/波动率计算引擎
│   ├── signal.py          # 阈值突破检测
│   ├── state.py           # state.json 读写 + git提交
│   ├── report.py          # Jinja2 上下文构建 + HTML渲染
│   ├── email_sender.py    # Gmail SMTP 发送
│   └── main.py            # CLI 入口 + 流程编排
├── templates/
│   └── report.html        # Bloomberg 风格邮件模板
├── data/
│   └── state.json         # 告警持久化状态
├── .github/workflows/
│   └── daily_radar.yml    # GitHub Actions 定时任务
├── requirements.txt
└── README.md
```

---

## 🔧 自定义

### 添加新的 ETF

编辑 `src/config.py` 的 `ETFS` 列表：

```python
ETFS = [
    ...
    {"ticker": "VWO", "name_cn": "新兴市场", "market": "全球"},
]
```

### 修改回撤阈值

编辑 `src/config.py`：

```python
THRESHOLDS = [0.20, 0.30, 0.40]  # -20%, -30%, -40%
ALERT_COOLDOWN_DAYS = 7           # 同一信号冷却天数
```

### 修改运行时间

编辑 `.github/workflows/daily_radar.yml` 的 cron 表达式。

---

## ⚠️ 数据局限性

- 数据来源：Yahoo Finance，可能存在延迟或数据缺失
- ETF 价格受汇率、分红、拆股等多因素影响
- 回撤信号仅用于市场监控，**不构成投资建议**
- 不同 ETF 上市时间不同，历史数据长度不同
- 历史回撤分位数基于该 ETF 自身的全部历史，跨 ETF 不可直接比较

---

## 📝 常见问题

**Q: yfinance 下载失败？**
A: 可能被限流。程序已内置 3 次重试。单只 ETF 失败不影响其他 ETF。

**Q: 邮件没有收到？**
A: 检查 Gmail App Password 是否正确，SMTP_PORT 是否为 587。

**Q: state.json 冲突？**
A: GitHub Actions 会自动 git pull --rebase 处理冲突。

---

## 📄 License

MIT
