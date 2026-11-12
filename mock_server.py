"""本地模拟电商后台（开发阶段用，不需要真实账号）。

作用有两个：
1. 给采集器一个"像真的一样"的数据源，让整条链路今天就能跑通；
2. 故意混入脏数据（重复行、带千分位的金额、带空格的名称），
   用来验证清洗逻辑真的有效：重复行、千分位金额、负数退货量都会走到这条路径。

启动：python mock_server.py
接口：
  GET /api/daily-report?biz_date=2026-11-01   返回 JSON
  GET /report?biz_date=2026-11-01             返回 HTML 表格（练 Playwright 用）
"""
from __future__ import annotations

import random
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from typing import Annotated

app = FastAPI(title="模拟电商后台", version="0.1")

SHOP_ID = "DY001"

# (SKU编号, 名称, 类目, 售价, 成本价, 日均销量, 类目佣金率)
SKUS = [
    ("SKU-001", "无痕聚拢文胸 裸感薄杯", "文胸", 89.0, 32.0, 38, 0.05),
    ("SKU-002", "冰丝无钢圈内衣 大码", "文胸", 79.0, 29.5, 26, 0.05),
    ("SKU-003", "小胸显大调整型文胸", "文胸", 99.0, 36.0, 18, 0.05),
    ("SKU-004", "纯棉家居服套装 长袖", "家居服", 129.0, 52.0, 22, 0.05),
    ("SKU-005", "珊瑚绒睡袍 秋冬加厚", "家居服", 159.0, 68.0, 14, 0.05),
    ("SKU-006", "真丝感吊带睡裙", "家居服", 119.0, 45.0, 31, 0.05),
    ("SKU-007", "男士纯棉平角内裤 3条装", "内裤", 69.0, 24.0, 45, 0.05),
    ("SKU-008", "女士中腰无痕内裤 5条装", "内裤", 59.0, 19.0, 52, 0.05),
    ("SKU-009", "冰丝透气男士内裤 4条装", "内裤", 65.0, 22.0, 28, 0.05),
    ("SKU-010", "高腰收腹内裤 产后塑形", "内裤", 75.0, 26.0, 12, 0.05),
    ("SKU-011", "德绒保暖打底衫 半高领", "打底衫", 99.0, 41.0, 33, 0.05),
    ("SKU-012", "莫代尔修身打底衫", "打底衫", 89.0, 35.0, 20, 0.05),
    ("SKU-013", "加绒加厚打底衫 冬季", "打底衫", 109.0, 46.0, 16, 0.05),
    ("SKU-014", "纯棉低帮船袜 10双装", "袜子", 39.0, 12.0, 68, 0.05),
    ("SKU-015", "羊毛混纺中筒袜 5双装", "袜子", 49.0, 17.0, 24, 0.05),
    ("SKU-016", "运动速干短袜 6双装", "袜子", 35.0, 10.5, 40, 0.05),
    ("SKU-017", "美背无痕文胸 露背款", "文胸", 95.0, 34.0, 15, 0.05),
    ("SKU-018", "孕妇哺乳内衣 前开扣", "文胸", 109.0, 40.0, 9, 0.05),
    ("SKU-019", "法兰绒家居裤 松紧腰", "家居服", 79.0, 30.0, 21, 0.05),
    ("SKU-020", "云朵棉睡衣两件套", "家居服", 139.0, 58.0, 13, 0.05),
    ("SKU-021", "无缝美体塑身衣", "打底衫", 149.0, 62.0, 8, 0.05),
    ("SKU-022", "儿童纯棉内裤 4条装", "内裤", 49.0, 16.0, 30, 0.05),
    ("SKU-023", "月子袜 产后加厚", "袜子", 45.0, 15.0, 11, 0.05),
    ("SKU-024", "冰丝安全裤 防走光", "内裤", 55.0, 18.0, 36, 0.05),
]

# 人为制造的异常，用来验证预警规则能不能抓到
FORCE_NEGATIVE_PROFIT = "SKU-006"   # 推广费烧太狠，毛利为负
FORCE_LOW_STOCK = "SKU-010"         # 只剩几件，断货风险
FORCE_HIGH_RETURN = "SKU-018"       # 退货率高


def build_rows(biz_date: date) -> list[dict]:
    """按日期做随机种子：同一天反复请求结果完全一致（方便验证幂等）。"""
    rnd = random.Random(biz_date.toordinal() * 31 + 7)
    weekday_factor = 1.25 if biz_date.weekday() >= 5 else 1.0
    rows: list[dict] = []

    for sku_id, name, category, price, cost, base_qty, rate in SKUS:
        # 变异系数=0.35 中等波动 ，大部分日常商
        # 使用高斯分布模拟销量，避免销量为负
        qty = max(0, int(rnd.gauss(base_qty * weekday_factor, base_qty * 0.35)))
        # 随机退货率 2%~8% 之间
        return_qty = min(qty, int(qty * rnd.uniform(0.02, 0.08)))
        # 计算 Gmv、退款、佣金、运费、广告成本
        # 注意：这里使用四舍五入，避免金额计算误差
        # 成交总额 = 销量 * 售价
        gmv = round(price * qty, 2)
        # 退款金额 = 退货量 * 售价
        refund = round(price * return_qty, 2)
        # 佣金 = 成交总额 * 类目佣金率
        commission = round(gmv * rate, 2)
        # 运费 = 销量 * 4.5
        shipping = round(qty * 4.5, 2)
        # 广告成本 = 成交总额 * 8%~18%
        ad_cost = round(gmv * rnd.uniform(0.08, 0.18), 2)
        # 期末库存按"可售天数"生成：正常款 18~50 天，避免全部触发断货预警
        stock = int(qty * rnd.uniform(18, 50))

        if sku_id == FORCE_NEGATIVE_PROFIT:
            ad_cost = round(gmv * 0.62, 2)      # 推广费吃掉全部利润
        if sku_id == FORCE_LOW_STOCK:
            stock = 5
        if sku_id == FORCE_HIGH_RETURN:
            return_qty = max(1, int(qty * 0.22))
            refund = round(price * return_qty, 2)

        rows.append({
            "biz_date": biz_date.isoformat(),
            "shop_id": SHOP_ID,
            "sku_id": sku_id,
            "sku_name": f"  {name} ",          # 故意带前后空格
            "category": category,
            "qty_sold": qty,
            "return_qty": return_qty,
            "stock_qty": stock,
            "gmv": gmv,
            "refund_amount": refund,
            "commission": commission,
            "ad_cost": ad_cost,
            "shipping_cost": shipping,
            "unit_cost": cost,
        })

    # 故意塞一条重复 SKU：真实系统导数据经常出现
    dup = dict(rows[2])
    dup["qty_sold"] = dup["qty_sold"] + 3
    rows.append(dup)

    # 故意把一个金额写成带千分位的字符串
    if rows[7]["gmv"] >= 1000:
        rows[7]["gmv"] = f"{rows[7]['gmv']:,.2f}"

    return rows


def _parse(d: str) -> date:
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="biz_date 格式应为 YYYY-MM-DD") from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "shop_id": SHOP_ID}


@app.get("/api/daily-report")
def daily_report(biz_date: str = Query(..., description="YYYY-MM-DD")) -> dict:
    d = _parse(biz_date)
    rows = build_rows(d)
    return {"code": 0, "shop_id": SHOP_ID, "biz_date": biz_date, "count": len(rows), "rows": rows}


@app.get("/report", response_class=HTMLResponse)
def report_page(biz_date: Annotated[str, Query(..., description="YYYY-MM-DD")]):
    d = _parse(biz_date)
    rows = build_rows(d)
    body = []
    for r in rows:
        body.append(
            f'<tr data-sku="{r["sku_id"]}">'
            f'<td>{r["sku_name"].strip()}</td><td>{r["category"]}</td>'
            f'<td>{r["qty_sold"]}</td><td>{r["return_qty"]}</td><td>{r["stock_qty"]}</td>'
            f'<td>{r["gmv"]}</td><td>{r["refund_amount"]}</td><td>{r["commission"]}</td>'
            f'<td>{r["ad_cost"]}</td><td>{r["shipping_cost"]}</td><td>{r["unit_cost"]}</td>'
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>经营日报 {biz_date}</title>
<style>
body{{font-family:system-ui,sans-serif;padding:24px;color:#1a1a2e}}
table{{border-collapse:collapse;font-size:13px}}
th,td{{border:1px solid #d8d8e0;padding:6px 10px;text-align:right}}
th{{background:#f2f3f7;font-weight:500}}
td:first-child,td:nth-child(2),th:first-child,th:nth-child(2){{text-align:left}}
</style></head>
<body>
<h1 style="font-size:16px">经营日报 {biz_date} · 店铺 {SHOP_ID}</h1>
<table id="report">
<thead><tr><th>商品</th><th>类目</th><th>销量</th><th>退货</th><th>库存</th>
<th>GMV</th><th>退款</th><th>佣金</th><th>推广费</th><th>快递费</th><th>成本价</th></tr></thead>
<tbody>

{chr(10).join(body)}
</tbody></table>
</body></html>"""


if __name__ == "__main__":
    import uvicorn

    print("Mock server is running on http://localhost:8000 - 1")

    # 必须监听 0.0.0.0（所有网卡）：容器里 127.0.0.1 只接受容器内部连接，
    # worker 容器从外部连 mock:8000 会 Connection refused。本机跑 0.0.0.0 同样正常。
    uvicorn.run(app, host="0.0.0.0", port=8000)
