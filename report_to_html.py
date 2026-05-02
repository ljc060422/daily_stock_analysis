#!/usr/bin/env python3
"""Convert Markdown stock report to mobile-friendly HTML."""

import re
import sys
import json
from datetime import datetime

def parse_report(md_text):
    """Parse markdown report into structured data."""
    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "summary": {"total": 0, "buy": 0, "hold": 0, "sell": 0},
        "stocks": []
    }

    # Extract summary line: 共分析 **13** 只股票 | 🟢买入:3 🟡观望:8 🔴卖出:2
    m = re.search(r"共分析\s*\*{0,2}(\d+)\*{0,2}\s*只股票.*?买入[:：](\d+).*?观望[:：](\d+).*?卖出[:：](\d+)", md_text)
    if m:
        result["summary"] = {"total": int(m.group(1)), "buy": int(m.group(2)), "hold": int(m.group(3)), "sell": int(m.group(4))}

    # Extract summary list
    summary_section = re.search(r"## 📊 分析结果摘要\s*\n(.*?)(?=\n---)", md_text, re.DOTALL)
    if summary_section:
        for line in summary_section.group(1).strip().split("\n"):
            m = re.match(r"(\S+)\s+\*{0,2}(.+?)\*{0,2}\s*[:：]\s*(买入|卖出|持有|观望|持有观望|减仓).*?评分\s*(\d+)", line)
            if m:
                result["stocks"].append({
                    "signal": m.group(1),
                    "name": m.group(2).strip(),
                    "action": m.group(3),
                    "score": int(m.group(4)),
                })

    # Parse individual stock sections
    stock_blocks = re.split(r"\n## (?:🟢|🟡|🔴|⚪|🟠)\s", md_text)
    stock_headers = re.findall(r"\n## ((?:🟢|🟡|🔴|⚪|🟠)\s.+)", md_text)

    for i, header in enumerate(stock_headers):
        if i + 1 < len(stock_blocks):
            block = stock_blocks[i + 1]
            code_match = re.search(r"\((\d{6})\)", header)
            name_match = re.match(r"[🟢🟡🔴⚪🟠]\s+(.+?)\s+\(", header)
            signal_match = re.match(r"(\S+)", header)

            stock_detail = {
                "code": code_match.group(1) if code_match else "",
                "name": name_match.group(1) if name_match else header.split("(")[0][2:].strip(),
                "signal_emoji": signal_match.group(1) if signal_match else "",
            }

            # Extract key fields
            def extract(pattern, text, default=""):
                m = re.search(pattern, text)
                return m.group(1).strip() if m else default

            stock_detail["decision"] = extract(r"\*\*(买入|卖出|持有|观望|持有观望|减仓)\*\*.*?\|\s*(.+?)\n", block)
            stock_detail["one_liner"] = extract(r"> \*\*一句话决策[:：]\*\*\s*(.+?)\n", block)
            stock_detail["trend"] = extract(r"趋势强度[:：]\s*(\d+/\d+)", block)
            stock_detail["bullish"] = extract(r"多头排列[:：]\s*(.+?)\s*\|", block)
            stock_detail["bias"] = extract(r"乖离率\(MA5\)[:：]\s*(.+?)\s*\|", block)
            stock_detail["price"] = extract(r"\|\s*当前价\s*\|\s*\n\|\s*\|\s*([\d.]+)\s*\|", block)
            stock_detail["ma5"] = extract(r"\|\s*MA5\s*\|\s*([\d.]+)\s*\|", block)

            # Extract price data
            price_match = re.search(r"\|\s*收盘\s*\|\s*昨收\s*\|\s*开盘\s*\|\s*最高\s*\|\s*最低\s*\|.*?\n\|[-\s|]+\n\|(.*?)\|", block)
            if price_match:
                cells = [c.strip() for c in price_match.group(1).split("|")]
                if len(cells) >= 6:
                    stock_detail["close"] = cells[0]
                    stock_detail["change_pct"] = cells[5]

            # Extract operation table
            ops = {}
            for op_match in re.finditer(r"\|\s*(🆕|💼)\s*\*{0,2}(.+?)\*{0,2}\s*\|\s*(.+?)\s*\|", block):
                ops[op_match.group(2).replace("**", "")] = op_match.group(3).replace("**", "")
            stock_detail["operations"] = ops

            # Extract check list
            checks = re.findall(r"[✅⚠️❌]\s*(检查项\d+[:：].+?)(?:\n|$)", block)
            stock_detail["checks"] = [c.strip() for c in checks]

            result["stocks"].append(stock_detail)

    return result


def signal_color(action):
    if "买入" in action: return "#10b981"
    if "卖出" in action: return "#ef4444"
    if "减仓" in action: return "#f97316"
    if "持有" in action: return "#eab308"
    return "#9ca3af"


def generate_html(data):
    summary = data["summary"]
    stocks = data["stocks"][:13]  # first 13 = summary entries

    stock_cards = ""
    for i, s in enumerate(stocks):
        action = s.get("action", "")
        color = signal_color(action)
        code = s.get("code", "")
        score = s.get("score", "-")

        # Map to detail stock if available
        detail = next((d for d in data["stocks"][13:] if d.get("code") == code), None)
        if not detail:
            detail = next((d for d in data["stocks"][13:] if d.get("name", "").startswith(s["name"][:2])), None)

        stock_cards += f'''
        <a href="#stock-{code}" class="stock-card" style="border-left: 4px solid {color}">
          <div class="card-left">
            <div class="card-name">{s["name"]}</div>
            <div class="card-code">{code}</div>
          </div>
          <div class="card-center">
            <span class="badge" style="background:{color}">{action}</span>
          </div>
          <div class="card-right">
            <div class="score" style="color:{color}">{score}</div>
            <div class="score-label">评分</div>
          </div>
        </a>'''

    # Detail sections
    detail_sections = ""
    for detail in data["stocks"][13:]:
        code = detail.get("code", "")
        name = detail.get("name", "")
        color = signal_color(detail.get("decision", ""))
        one_liner = detail.get("one_liner", "")
        price = detail.get("price", detail.get("close", "-"))
        change = detail.get("change_pct", "")
        ma5 = detail.get("ma5", "-")
        bias = detail.get("bias", "-")
        bullish = detail.get("bullish", "-")
        trend = detail.get("trend", "-")

        ops_html = ""
        for k, v in detail.get("operations", {}).items():
            ops_html += f'<div class="op-row"><span class="op-label">{k}</span><span class="op-text">{v}</span></div>'

        checks_html = ""
        for c in detail.get("checks", []):
            checks_html += f'<li>{c}</li>'

        detail_sections += f'''
        <div class="detail-section" id="stock-{code}">
          <div class="detail-header" style="background:{color}">
            <div>
              <div class="detail-title">{name}</div>
              <div class="detail-code">{code}</div>
            </div>
            <div>
              <div class="detail-price">¥{price}</div>
              {f'<div class="detail-change" style="color:{"#10b981" if "+" in change else "#ef4444"}">{change}</div>' if change else ''}
            </div>
          </div>
          <div class="detail-body">
            <p class="one-liner">💡 {one_liner}</p>
            <div class="kpi-grid">
              <div class="kpi"><span class="kpi-label">多头排列</span><span class="kpi-value">{bullish[:20]}</span></div>
              <div class="kpi"><span class="kpi-label">乖离率</span><span class="kpi-value">{bias[:15]}</span></div>
              <div class="kpi"><span class="kpi-label">趋势强度</span><span class="kpi-value">{trend}</span></div>
              <div class="kpi"><span class="kpi-label">MA5</span><span class="kpi-value">¥{ma5}</span></div>
            </div>
            {ops_html}
            {f'<ul class="checks-list">{checks_html}</ul>' if checks_html else ''}
          </div>
        </div>'''

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>股票日报 {data["date"]}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; padding-bottom:40px; }}
.header {{ background: linear-gradient(135deg,#1e293b,#0f172a); padding:24px 16px 20px; text-align:center; border-bottom:1px solid #1e293b; }}
.header h1 {{ font-size:20px; font-weight:700; margin-bottom:4px; }}
.header .date {{ font-size:13px; color:#64748b; }}
.stats {{ display:flex; justify-content:center; gap:12px; padding:16px; flex-wrap:wrap; }}
.stat {{ background:#1e293b; border-radius:12px; padding:12px 16px; text-align:center; min-width:60px; }}
.stat .num {{ font-size:24px; font-weight:800; }}
.stat .label {{ font-size:11px; color:#94a3b8; margin-top:2px; }}
.stat.buy .num {{ color:#10b981; }}
.stat.hold .num {{ color:#eab308; }}
.stat.sell .num {{ color:#ef4444; }}
.cards {{ padding:0 12px; }}
.stock-card {{ display:flex; align-items:center; background:#1e293b; border-radius:12px; margin-bottom:8px; padding:14px 12px; text-decoration:none; color:inherit; }}
.stock-card:active {{ background:#334155; }}
.card-left {{ flex:1; min-width:0; }}
.card-name {{ font-size:15px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.card-code {{ font-size:12px; color:#64748b; }}
.card-center {{ margin:0 10px; }}
.badge {{ display:inline-block; padding:3px 10px; border-radius:20px; font-size:12px; font-weight:600; color:#fff; white-space:nowrap; }}
.card-right {{ text-align:center; min-width:44px; }}
.score {{ font-size:22px; font-weight:800; }}
.score-label {{ font-size:10px; color:#64748b; }}

.detail-section {{ margin:12px 12px 0; background:#1e293b; border-radius:12px; overflow:hidden; }}
.detail-header {{ display:flex; justify-content:space-between; align-items:center; padding:16px; color:#fff; }}
.detail-title {{ font-size:17px; font-weight:700; }}
.detail-code {{ font-size:12px; opacity:0.8; }}
.detail-price {{ font-size:22px; font-weight:800; text-align:right; }}
.detail-change {{ font-size:13px; text-align:right; }}
.detail-body {{ padding:16px; }}
.one-liner {{ font-size:14px; color:#94a3b8; margin-bottom:14px; line-height:1.5; }}
.kpi-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:14px; }}
.kpi {{ background:#0f172a; border-radius:8px; padding:10px 12px; }}
.kpi-label {{ display:block; font-size:11px; color:#64748b; }}
.kpi-value {{ display:block; font-size:14px; font-weight:600; margin-top:2px; }}
.op-row {{ display:flex; justify-content:space-between; padding:10px 12px; background:#0f172a; border-radius:8px; margin-bottom:6px; font-size:13px; }}
.op-label {{ color:#64748b; }}
.op-text {{ text-align:right; flex:1; margin-left:12px; }}
.checks-list {{ font-size:12px; color:#94a3b8; padding-left:18px; line-height:1.8; }}
.footer {{ text-align:center; padding:20px; font-size:12px; color:#475569; }}
.footer a {{ color:#64748b; }}
</style>
</head>
<body>

<div class="header">
  <h1>📊 股票日报</h1>
  <div class="date">{data["date"]}</div>
</div>

<div class="stats">
  <div class="stat buy"><div class="num">{summary["buy"]}</div><div class="label">🟢 买入</div></div>
  <div class="stat hold"><div class="num">{summary["hold"]}</div><div class="label">🟡 观望</div></div>
  <div class="stat sell"><div class="num">{summary["sell"]}</div><div class="label">🔴 卖出</div></div>
</div>

<div class="cards">
{stock_cards}
</div>

<div class="section-title" style="padding:20px 16px 8px; font-size:16px; font-weight:700;">📋 个股详情</div>
{detail_sections}

<div class="footer">
  <p>数据来源：东方财富 · 腾讯财经</p>
  <p>AI 分析：DeepSeek · 仅供参考不构成投资建议</p>
  <p>Generated by <a href="https://github.com/ljc060422/daily_stock_analysis">daily_stock_analysis</a></p>
</div>

</body>
</html>'''


if __name__ == "__main__":
    if len(sys.argv) > 1:
        md_path = sys.argv[1]
    else:
        md_path = "reports/report_" + datetime.now().strftime("%Y%m%d") + ".md"

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    data = parse_report(md_text)
    html = generate_html(data)

    out_path = md_path.replace(".md", ".html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"HTML report saved to: {out_path}")
