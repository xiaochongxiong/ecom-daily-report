# 电商经营日报自动化

把电商店铺每天"人工导 Excel → 手工算毛利 → 群里汇报"的流程，改造成一条定时自动运行的流水线：
**采集 → 清洗 → 入库 → 毛利核算 → 异常预警 → 推送飞书**，全程无人值守。

> 目标不是"能跑 demo"，而是"像生产系统一样能上线、能排查、能重跑"。

## 它解决的业务问题

运营每天早上要花 40–60 分钟做同一件事：登录后台导数据、拼表格、算利润、找异常 SKU、发群里。

| | 改造前 | 改造后 |
|---|---|---|
| 步骤 | 人工 6 步 | 机器自动 5 步 |
| 耗时 | 40–60 分钟 | 约 0.6 秒（24 个 SKU 全链路） |
| 口径 | 因人而异，错了没人发现 | 参数化固定，可复核 |
| 异常 | 靠眼睛扫 | 7 条规则自动判定 + 主动推送 |
| 追溯 | 只有当天 | 每天落审计表，可重放任意日期 |

## 快速开始

### 方式一：Docker（推荐，一条命令）

```bash
cp .env.example .env          # 可选：填 FEISHU_WEBHOOK 才会真的推送
docker compose up -d --build  # 拉起 PostgreSQL + 模拟后台 + 定时任务
docker compose exec worker python run_once.py   # 立刻跑一次，不等定时
```

### 方式二：本地 Python

```bash
python -m venv .venv
.venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python mock_server.py         # 终端 1：模拟电商后台（默认 SQLite，零依赖）
python run_once.py --dry-run  # 终端 2：只打印不入库不推送
python run_once.py --date 2026-11-01
python run_once.py --date 2026-11-01   # 再跑一次，库里仍是 24 行（幂等）
```

`--dry-run` 不写明细、不写审计、不落盘、不推送，适合调试。

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 采集 | requests / pyquery / Playwright | 有 API 走 API，静态页走 DOM 解析，动态页走浏览器自动化 |
| 处理 | pandas | 清洗、派生指标 |
| 存储 | SQLAlchemy 2.0 + PostgreSQL / SQLite | 改一行 `.env` 切换，代码不动 |
| 调度 | APScheduler | 每天 08:30，`max_instances=1` 防并发、`coalesce` 防漏跑堆积 |
| 通知 | 飞书群机器人 Webhook | 未配置时降级为控制台 + 落文件 |
| 测试 | pytest | 19 个用例，覆盖口径、去重、推送决策与重试 |
| 部署 | Dockerfile + docker compose | 三服务 + 健康检查 + 数据卷持久化 |

## 目录结构

```
ecom-daily-report/
├── run_once.py              # 单次执行（--date / --source / --dry-run）
├── scheduler.py             # 定时调度常驻进程
├── mock_server.py           # 模拟电商后台（FastAPI，故意含脏数据）
├── Dockerfile / docker-compose.yml / .dockerignore
├── src/
│   ├── config.py            # 配置全部走环境变量
│   ├── logger.py            # 日志落文件并按大小切分
│   ├── db.py                # 引擎 / 会话 / 事务上下文
│   ├── models.py            # 表结构（联合主键保证幂等）
│   ├── collectors/          # base(接口) + mock_api + mock_web + public_site + 工厂
│   ├── transform.py         # 清洗 + 派生指标（业务核心）
│   ├── analyze.py           # 汇总 + 7 条预警规则
│   ├── notify.py            # 报表渲染 + 飞书推送（含退避重试）
│   └── pipeline.py          # run() 主流程编排
└── tests/
    ├── test_transform.py    # 口径、去重、千分位、越界
    └── test_notify.py       # 推送决策 + 重试策略
```

## 毛利与周转口径（易错点）

```
毛利   = gmv - refund_amount - commission - ad_cost - shipping_cost - cost_of_goods
毛利率 = 毛利 / gmv
退货率 = return_qty / qty_sold
周转天数 = 库存 / 日销
动销率  = 销量 / (库存 + 销量)
```

两个容易搞错的点：

1. **动销率与周转天数是同一个数学对象的两种写法**：`s = 1 / (1 + d)`。所以它们不能当两条独立指标来"互相验证"，只能选一个当主口径。
2. **整体动销率必须加权**，不能对各 SKU 的率求简单平均。本项目实测：加权 2.81% vs 简单平均 5.88%，差了整整一倍。

口径不是常量，都做成了参数：`RETURN_COST_RECOVERY`（退货成本可回收比例）、`REFUND_COMMISSION_ON_RETURN`（退款是否退佣）。真实业务里这两个数字必须和财务确认，写死在代码里就是埋雷。

## 工程能力（这部分比功能更值钱）

- **幂等入库**：`(biz_date, shop_id, sku_id)` 联合主键 + 方言级 `ON CONFLICT DO UPDATE`。同一天重跑只覆盖不新增；相比 `session.merge()` 的 SELECT+UPDATE，24 个 SKU 从 48 条 SQL 降到 1 条。
- **采集重试**：指数退避 3 次（1s / 2s / 4s），接口抖动不会导致漏数。
- **推送重试**：3 次尝试（1s / 3s 退避），只对超时、429、5xx 重试；4xx 立即失败——webhook 失效重试解决不了。这条是实测出来的：容器冷启动首次 POST 读超时 10 秒，同一份代码重跑 0.6 秒成功。
- **失败可见**：任何异常写 `etl_run` 审计表（run_id / 状态 / 行数 / 耗时 / 错误），并推送失败告警。
- **状态正交**：`status`（ETL 成败）与 `push_status`（推送终态）分列。入库成功但推送失败是真实存在的状态，塞进一个字段就再也分不清了。
- **推送四态**：`ok` / `failed` / `skipped_no_webhook`（配置缺失，要修）/ `skipped_by_policy`（策略主动不推，正常）——后两者混在一起，运维会把正常行为当故障排查。
- **可切换数据源**：采集器抽象接口 + 工厂，换平台只加一个类，主流程零改动。
- **可测试**：决策逻辑抽成纯函数（`decide_push`），不碰网络和数据库，因此"不推送"这种分支也能被单测覆盖。

## 预警规则

| 级别 | 规则 | 判据 | 业务含义 |
|---|---|---|---|
| 高 | 负毛利 | 毛利 < 0 | 卖一件亏一件，先停推广 |
| 高 | 断货风险 | 周转天数 < 7 | 立即补货 |
| 高 | 推广费失控 | 推广费 / GMV > 50% | 投放效率崩了，检查出价 |
| 中 | 毛利率偏低 | 毛利率 < 15% | 低于红线，调价或降本 |
| 中 | 滞销压货 | 周转天数 > 45 | 清库存，占资金 |
| 中 | 退货率偏高 | 退货率 > 10% | 质量问题或描述不符 |
| 中 | 销量骤降 | 环比跌幅 > 40% | 掉排名 / 断货 / 改价 |

前六条是横截面判断，最后一条依赖历史数据——所以**明细幂等，但输出依赖历史状态**：删除历史再重跑，环比规则会静默失效。这是批处理系统常见的设计陷阱。

## 部署要点（Docker）

三个服务：`db`（PostgreSQL 16）、`mock`（模拟后台）、`worker`（定时任务）。

- **容器之间用服务名通信**：`DATABASE_URL` 里的主机是 `db` 而不是 `localhost`——容器里的 localhost 指向容器自己。
- **数据卷分两类**：`pgdata`（命名卷，存数据库）、`./output` 与 `./logs`（绑定挂载，存报告和日志）。不挂卷的话，`up -d --build` 重建容器就会把历史产物清空。
- **改什么用什么命令**：改代码或依赖要 `--build`；改 `.env` 或 compose 里的运行时配置只要 `up -d`（`restart` 无效，它不重读环境变量）。
- **`.dockerignore` 必须有**：漏掉它，`.venv` 会被打进镜像，534MB 变 1.01GB，而且 `.env` 里的密钥会跟着进镜像。

## 接真实数据源要改哪里

| 场景 | 做法 |
|---|---|
| 聚水潭 / 旺店通 | 开放平台 API，补签名与分页，新增一个 `Collector` 子类 |
| 抖店 / 千牛 / 拼多多 | 无开放接口，用 Playwright 方案，登录态用 `storage_state` 持久化 |
| 平台账单 | 下载 Excel/CSV 后用 `pandas.read_excel` 接进同一条 transform |

真正的难点不在代码量，在数据质量：字段缺失、类型漂移、`""` 与 `null` 混用、退款跨天到达。所以真实接入通常要在采集和清洗之间加一层 `normalize()`，把平台字段映射成本项目的 12 列契约——mock 时代不需要，接真实系统必须有。

## 已知差距（离上生产还差什么）

- **数据源**：当前是本地 mock，接真实 API 是主要工作量（签名、分页、限流、字段映射）
- **口径对账**：需要一份"日报数与平台后台报表数不一致时怎么解释"的说明文档
- **运维配套**：每日备份、数据就绪监控（任务没跑比跑错了更隐蔽）、Alembic 管理表结构变更
- **预警落库**：预警目前只存在于报告文本里，没有 `alert` 表，回溯查询做不了

## 下一步

接进 Dify / LangGraph 做"用自然语言问经营数据"的 Agent，本项目的数据直接当燃料。
