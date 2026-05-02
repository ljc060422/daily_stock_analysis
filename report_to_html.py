#!/usr/bin/env python3
"""Convert Markdown stock report to mobile-friendly HTML with full details + charts."""

import re, sys, json, base64, io, os, shutil, warnings
from datetime import datetime, timedelta
from functools import lru_cache

warnings.filterwarnings("ignore")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import mplfinance as mpf
import pandas as pd

# ─── Data fetching ───────────────────────────────────────────────────────────

@lru_cache(maxsize=50)
def fetch_kline(code):
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if df is None or len(df) < 5:
            return None
        df = df.rename(columns={"日期":"Date","开盘":"Open","收盘":"Close","最高":"High","最低":"Low","成交量":"Volume"})
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        df = df[["Open","High","Low","Close","Volume"]]
        for col in ["Open","High","Low","Close","Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.tail(90)
    except Exception:
        return None

def fetch_intraday(code):
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist_min_em(symbol=code, period="1", adjust="qfq")
        if df is None or len(df) < 10:
            return None
        df = df.rename(columns={"时间":"Time","开盘":"Open","收盘":"Close","最高":"High","最低":"Low","成交量":"Volume"})
        if "Time" in df.columns:
            df["Time"] = pd.to_datetime(df["Time"])
            df = df.set_index("Time")
        for col in ["Open","High","Low","Close","Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.tail(240)
    except Exception:
        return None

# ─── Chart generation ────────────────────────────────────────────────────────

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
BG, FG, GRID = "#0f172a", "#e2e8f0", "#1e293b"

def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor=BG, edgecolor="none")
    buf.seek(0)
    plt.close(fig)
    return base64.b64encode(buf.read()).decode()

def make_kline_chart(code, name):
    df = fetch_kline(code)
    if df is None or len(df) < 5:
        return None
    df = df.tail(60)
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["MA120"] = df["Close"].rolling(120).mean()
    plot_df = df.tail(30).copy()
    if len(plot_df) < 5:
        return None

    mc = mpf.make_marketcolors(up="#10b981", down="#ef4444", edge="inherit", wick="inherit", volume="inherit")
    s = mpf.make_mpf_style(marketcolors=mc, facecolor=BG, edgecolor="#334155", gridcolor=GRID, figcolor=BG)
    fig, axes = mpf.plot(plot_df, type="candle", style=s, volume=False, figsize=(10,5),
                         returnfig=True, mav=(5,10,20,60), tight_layout=True)
    ax = axes[0]
    ax.set_facecolor(BG)
    ma_colors = ["#6366f1","#f59e0b","#10b981","#ef4444"]
    for i, line in enumerate(ax.lines):
        if i < len(ma_colors):
            line.set_color(ma_colors[i])
            line.set_linewidth(1.0)
    ax.set_title(f"{name} ({code}) 30日K线", color=FG, fontsize=13, fontweight="bold", pad=10)
    ax.tick_params(colors="#64748b", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0],[0],color="#6366f1",lw=1.5,label="MA5"),
        Line2D([0],[0],color="#f59e0b",lw=1.5,label="MA10"),
        Line2D([0],[0],color="#10b981",lw=1.5,label="MA20"),
        Line2D([0],[0],color="#ef4444",lw=1.5,label="MA60"),
    ]
    ax.legend(handles=legend_elements, loc="upper left", fontsize=7, facecolor=BG, edgecolor=GRID, labelcolor=FG, ncol=2)
    return fig_to_b64(fig)

def make_intraday_chart(code, name):
    df = fetch_intraday(code)
    if df is None or len(df) < 10:
        return None
    fig, ax = plt.subplots(figsize=(10, 3.5))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    closes = df["Close"].values
    color = "#10b981" if closes[-1] >= closes[0] else "#ef4444"
    x = range(len(df))
    ax.plot(x, closes, color=color, linewidth=1.0)
    ax.fill_between(x, closes, closes[0], alpha=0.15, color=color)
    avg = closes.mean()
    ax.axhline(y=avg, color="#f59e0b", linewidth=0.8, linestyle="--", alpha=0.6)
    ax.text(len(x)-1, avg, f" 均{avg:.2f}", color="#f59e0b", fontsize=7, va="center")
    ax.set_title(f"{name} 分时走势", color=FG, fontsize=12, fontweight="bold", pad=8)
    ax.tick_params(colors="#64748b", labelsize=7)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(color=GRID, linewidth=0.5, alpha=0.5)
    step = max(1, len(df) // 6)
    ax.set_xticks(list(range(0, len(df), step)))
    if hasattr(df.index[0], "strftime"):
        ax.set_xticklabels([d.strftime("%H:%M") for d in df.index[::step]], fontsize=7)
    ax.set_xlim(0, len(df)-1)
    return fig_to_b64(fig)

# ─── Markdown parsing ────────────────────────────────────────────────────────

def parse_report(md_text):
    result = {"date": datetime.now().strftime("%Y-%m-%d"), "summary": {}, "stocks_summary": [], "stocks": []}
    m = re.search(r"共分析\s*\*{0,2}(\d+)\*{0,2}\s*只.*?买入[:：](\d+).*?观望[:：](\d+).*?卖出[:：](\d+)", md_text)
    if m:
        result["summary"] = {"total":int(m.group(1)),"buy":int(m.group(2)),"hold":int(m.group(3)),"sell":int(m.group(4))}
    summary_section = re.search(r"## 📊 分析结果摘要\s*\n(.*?)(?=\n---)", md_text, re.DOTALL)
    if summary_section:
        for line in summary_section.group(1).strip().split("\n"):
            # Format: 🟢 **钒钛股份(000629)**: 买入 | 评分 79 | 强烈看多
            m2 = re.match(r"(\S+)\s+\*{0,2}(.+?)\*{0,2}\s*[：:]\s*(买入|卖出|持有|持有观望|观望|减仓)\s*.*?评分\s*(\d+)", line)
            if m2:
                result["stocks_summary"].append({"signal":m2.group(1),"name":m2.group(2).strip(),"action":m2.group(3),"score":int(m2.group(4))})
    sections = re.split(r"\n## (?:🟢|🟡|🔴|⚪|🟠)\s", md_text)
    for i, sec in enumerate(sections):
        if i == 0:
            continue
        header_match = re.match(r"(.+?)\s+\((\d{6})\)", sec)
        if not header_match:
            continue
        name, code = header_match.group(1).strip(), header_match.group(2)
        d = {"name":name, "code":code}
        def ext(pattern, text=sec, default="", group=1):
            m = re.search(pattern, text); return m.group(group).strip() if m else default
        d["decision"] = ext(r"\*\*[🟢🟡🔴⚪🟠]*\s*(买入|卖出|持有|减仓|观望|持有观望)\*\*")
        d["sentiment"] = ext(r"\*\*(强烈看多|看多|震荡|看空|强烈看空|震荡偏空)\*\*")
        d["one_liner"] = ext(r"一句话决策[:：]\*\*(.+?)\n")
        d["trend_strength"] = ext(r"趋势强度[:：]\s*(\d+/\d+)")
        d["bullish"] = ext(r"多头排列[:：]\s*(.+?)\s*\|")
        pm = re.search(r"\|\s*收盘\s*\|.*?\n\|[-\s|]+\n\|(.*?)\|", sec)
        if pm:
            cells = [c.strip() for c in pm.group(1).split("|")]
            if len(cells) >= 9:
                d["close"]=cells[0]; d["change_pct"]=cells[5]
        d["current_price"] = ext(r"\|\s*当前价\s*\|\s*\n\|\s*\|\s*([\d.]+)\s*\|")
        d["volume_ratio"] = ext(r"\|\s*量比\s*\|\s*([\d.]+)\s*\|")
        d["turnover_rate"] = ext(r"\|\s*换手率\s*\|\s*([\d.%]+)\s*\|")
        d["ma5"] = ext(r"\|\s*MA5\s*\|\s*([\d.]+)\s*\|")
        d["ma10"] = ext(r"\|\s*MA10\s*\|\s*([\d.]+)\s*\|")
        d["ma20"] = ext(r"\|\s*MA20\s*\|\s*([\d.]+)\s*\|")
        d["bias"] = ext(r"乖离率\(MA5\)[:：]\s*(.+?)\s*\|")
        d["support"] = ext(r"\|\s*支撑位\s*\|\s*([\d.]+)\s*\|")
        d["resistance"] = ext(r"\|\s*压力位\s*\|\s*([\d.]+)\s*\|")
        d["sentiment_text"] = ext(r"\*\*💭 舆情情绪\*\*[:：]\s*(.+?)\n")
        d["latest_news"] = ext(r"\*\*📢 最新动态\*\*[:：]\s*(.+?)\n")
        risks = re.findall(r"风险点\d+[:：](.+?)(?:\n|$)", sec)
        d["risks"] = risks
        catalysts = re.findall(r"利好\d+[:：](.+?)(?:\n|$)", sec)
        d["catalysts"] = catalysts
        ops = {}
        for op_match in re.finditer(r"\|\s*(?:🆕|💼)\s*\*{0,2}(.+?)\*{0,2}\s*\|\s*(.+?)\s*\|", sec):
            key = op_match.group(1).replace("**","").strip()
            val = op_match.group(2).replace("**","").strip()
            if len(key)<20 and len(val)>2:
                ops[key]=val
        d["operations"] = ops
        checks = re.findall(r"([✅⚠️❌])\s*(检查项\d+[:：].+?)(?:\n|$)", sec)
        d["checks"] = [(c[0], c[1].strip()) for c in checks]

        # 作战计划点位
        for row in re.finditer(r"\|\s*(?:🎯|🔵|🛑|🎊)\s*(.+?)\s*\|\s*([\d.]+元[^\|]*|[暂不无].+?)\s*\|", sec):
            label = row.group(1).strip()
            val = row.group(2).strip()
            if label not in d:
                d[label] = val

        result["stocks"].append(d)

    # Map codes from summary to detail
    for s in result["stocks_summary"]:
        code_m = re.search(r"\((\d{6})\)", s.get("name","")+"()")
        scode = code_m.group(1) if code_m else ""
        for d in result["stocks"]:
            if d.get("code") == scode or s["name"].startswith(d.get("name","")[:2]):
                s["code"] = d.get("code","")
                break

    return result

# ─── HTML generation ─────────────────────────────────────────────────────────

CSS = '''
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding-bottom:100px}
.header{background:linear-gradient(135deg,#1e293b,#0f172a);padding:24px 16px 20px;text-align:center;border-bottom:1px solid #1e293b}
.header h1{font-size:20px;font-weight:700;margin-bottom:4px}
.header .date{font-size:13px;color:#64748b}
.stats{display:flex;justify-content:center;gap:12px;padding:16px;flex-wrap:wrap}
.stat{background:#1e293b;border-radius:12px;padding:12px 16px;text-align:center;min-width:60px}
.stat .num{font-size:24px;font-weight:800}
.stat .label{font-size:11px;color:#94a3b8;margin-top:2px}
.stat.buy .num{color:#10b981}.stat.hold .num{color:#eab308}.stat.sell .num{color:#ef4444}
.cards{padding:0 12px}
.stock-card{display:flex;align-items:center;background:#1e293b;border-radius:12px;margin-bottom:8px;padding:14px 12px;cursor:pointer;transition:background .15s}
.stock-card:active{background:#334155}
.card-left{flex:1;min-width:0}
.card-name{font-size:15px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-code{font-size:12px;color:#64748b}
.card-center{margin:0 10px}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;color:#fff;white-space:nowrap}
.card-right{text-align:center;min-width:44px}
.score{font-size:22px;font-weight:800}
.score-label{font-size:10px;color:#64748b}
.detail-section{margin:12px 12px 0;background:#1e293b;border-radius:12px;overflow:hidden}
.detail-header{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;color:#fff;cursor:pointer}
.detail-title{font-size:17px;font-weight:700}
.detail-code{font-size:12px;opacity:0.8}
.detail-header-right{display:flex;align-items:center;gap:8px}
.detail-decision{font-size:14px;font-weight:700}
.detail-sentiment{font-size:12px;opacity:0.85}
.toggle-icon{transition:transform .2s}
.toggle-icon.open{transform:rotate(180deg)}
.detail-body{padding:16px}
.one-liner{font-size:14px;color:#94a3b8;margin-bottom:14px;line-height:1.6}
.chart-container{margin-bottom:12px;text-align:center;min-height:60px}
.chart-container img{max-width:100%;border-radius:8px}
.chart-loading{color:#64748b;font-size:12px;padding:20px}
.chart-caption{font-size:13px;font-weight:600;color:#94a3b8;margin-bottom:6px;text-align:center}
.close-price-box{background:#0f172a;border-radius:8px;padding:12px;margin-bottom:14px;display:flex;justify-content:space-around;text-align:center}
.close-price-item .cp-val{font-size:20px;font-weight:800}
.close-price-item .cp-label{font-size:11px;color:#64748b;margin-top:2px}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:14px}
.info-row{display:flex;justify-content:space-between;background:#0f172a;border-radius:6px;padding:8px 10px;font-size:12px}
.info-row span:first-child{color:#64748b}
.info-row span:last-child{font-weight:600;text-align:right}
.news-block{background:#0f172a;border-radius:8px;padding:10px 12px;margin-bottom:8px}
.news-title{font-size:13px;font-weight:600;margin-bottom:6px}
.news-item{font-size:12px;color:#94a3b8;line-height:1.5;margin-bottom:4px}
.news-block.risk{border-left:3px solid #ef4444}
.news-block.good{border-left:3px solid #10b981}
.section-title{font-size:14px;font-weight:700;padding:12px 0 8px}
.op-row{display:flex;justify-content:space-between;padding:10px 12px;background:#0f172a;border-radius:8px;margin-bottom:6px;font-size:13px}
.op-label{color:#64748b;min-width:60px}
.op-text{text-align:right;flex:1;margin-left:12px;color:#e2e8f0}
.checks-list{font-size:12px;color:#94a3b8;line-height:2}
.check-item{padding:2px 0}
.footer{text-align:center;padding:24px;font-size:12px;color:#475569}
.footer a{color:#64748b}
'''

JS = '''
window._chartCache = {};
window._loadChart = async function(code, type) {
    var key = code + '_' + type;
    if (window._chartCache[key]) return window._chartCache[key];
    var resp = await fetch('charts/' + code + '_' + type + '.png');
    if (resp.ok) {
        var blob = await resp.blob();
        var url = URL.createObjectURL(blob);
        window._chartCache[key] = url;
        return url;
    }
    return null;
};
window._renderCharts = function(code) {
    var kd = document.getElementById('kline-' + code);
    var id = document.getElementById('itraday-' + code);
    if (kd && !kd.querySelector('img')) {
        window._loadChart(code, 'kline').then(function(u) {
            kd.innerHTML = u ? '<img src="'+u+'" alt="K线">' : '<div class="chart-loading">暂无K线数据</div>';
        });
    }
    if (id && !id.querySelector('img')) {
        window._loadChart(code, 'intraday').then(function(u) {
            id.innerHTML = u ? '<img src="'+u+'" alt="分时">' : '<div class="chart-loading">暂无分时数据</div>';
        });
    }
};
function toggleDetail(id) {
    var el = document.getElementById(id);
    var icon = document.getElementById('icon-' + id);
    var code = id.replace('stock-', '');
    if (el.style.display === 'none') {
        el.style.display = 'block';
        if (icon) icon.classList.add('open');
        window._renderCharts(code);
        el.scrollIntoView({behavior:'smooth',block:'center'});
    } else {
        el.style.display = 'none';
        if (icon) icon.classList.remove('open');
    }
}
'''

def signal_color(action):
    if "买入" in action: return "#10b981"
    if "卖出" in action: return "#ef4444"
    if "减仓" in action: return "#f97316"
    if "持有" in action: return "#eab308"
    return "#9ca3af"

def html_escape(text):
    return str(text).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def generate_html(data):
    summary = data.get("summary", {})
    stocks_summary = data.get("stocks_summary", [])
    stocks = data.get("stocks", [])

    # Summary cards
    summary_cards = []
    for s in stocks_summary:
        color = signal_color(s.get("action", ""))
        code = s.get("code", "")
        name = html_escape(s["name"])
        summary_cards.append('''<div class="stock-card" style="border-left:4px solid '''+color+'''" onclick="toggleDetail('stock-'''+code+'''')">
          <div class="card-left"><div class="card-name">'''+name+'''</div><div class="card-code">'''+code+'''</div></div>
          <div class="card-center"><span class="badge" style="background:'''+color+'''">'''+s["action"]+'''</span></div>
          <div class="card-right"><div class="score" style="color:'''+color+'''">'''+str(s["score"])+'''</div><div class="score-label">评分</div></div>
        </div>''')

    # Detail sections
    detail_sections = []
    for d in stocks:
        code = d.get("code", "")
        name = html_escape(d.get("name", ""))
        color = signal_color(d.get("decision", ""))
        decision = d.get("decision", "")
        sentiment = d.get("sentiment", "")

        # Info rows
        fields = [
            ("当前价", d.get("current_price","-")),
            ("涨跌幅", d.get("change_pct","-")),
            ("量比", d.get("volume_ratio","-")),
            ("换手率", d.get("turnover_rate","-")),
            ("MA5", d.get("ma5","-")),
            ("MA10", d.get("ma10","-")),
            ("MA20", d.get("ma20","-")),
            ("乖离率", d.get("bias","-")),
            ("多头排列", (d.get("bullish","-") or "-")[:12]),
            ("趋势强度", d.get("trend_strength","-")),
            ("支撑位", d.get("support","-")),
            ("压力位", d.get("resistance","-")),
        ]
        info_rows = []
        for label, val in fields:
            if val and val != "-":
                info_rows.append('<div class="info-row"><span>'+label+'</span><span>'+html_escape(str(val))+'</span></div>')

        # News
        news_parts = []
        risks = d.get("risks", [])
        if risks:
            items = "".join('<div class="news-item">'+html_escape(r[:150])+'</div>' for r in risks[:3])
            news_parts.append('<div class="news-block risk"><div class="news-title">🚨 风险警报</div>'+items+'</div>')
        catalysts = d.get("catalysts", [])
        if catalysts:
            items = "".join('<div class="news-item">'+html_escape(c[:150])+'</div>' for c in catalysts[:3])
            news_parts.append('<div class="news-block good"><div class="news-title">✨ 利好催化</div>'+items+'</div>')
        if d.get("sentiment_text"):
            news_parts.append('<div class="news-block"><div class="news-title">💭 舆情</div><div class="news-item">'+html_escape(d["sentiment_text"][:200])+'</div></div>')
        if d.get("latest_news"):
            news_parts.append('<div class="news-block"><div class="news-title">📢 最新动态</div><div class="news-item">'+html_escape(d["latest_news"][:200])+'</div></div>')

        # Operations
        ops_parts = []
        for k, v in d.get("operations", {}).items():
            ops_parts.append('<div class="op-row"><span class="op-label">'+html_escape(k)+'</span><span class="op-text">'+html_escape(v[:120])+'</span></div>')

        # Checks
        checks_parts = []
        for icon, text in d.get("checks", []):
            checks_parts.append('<div class="check-item">'+icon+' '+html_escape(text)+'</div>')

        # Extra 作战计划点位
        for key_label in ["理想买入点","次优买入点","止损位","目标位"]:
            if key_label in d and not d[key_label].startswith("暂"):
                val = d[key_label]
                info_rows.append('<div class="info-row"><span>'+key_label+'</span><span>'+html_escape(val)+'</span></div>')

        detail_sections.append('''<div class="detail-section" id="stock-'''+code+'''" style="display:none">
          <div class="detail-header" style="background:'''+color+'''" onclick="toggleDetail('stock-'''+code+'''')">
            <div>
              <div class="detail-title">'''+name+'''</div>
              <div class="detail-code">'''+code+'''</div>
            </div>
            <div class="detail-header-right">
              <span class="detail-decision">'''+decision+'''</span>
              <span class="detail-sentiment">'''+sentiment+'''</span>
              <svg class="toggle-icon" id="icon-stock-'''+code+'''" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3"><polyline points="18 15 12 9 6 15"/></svg>
            </div>
          </div>
          <div class="detail-body">
            '''+('<p class="one-liner">💡 '+html_escape(d.get("one_liner",""))+'</p>' if d.get("one_liner") else '')+'''
            <div class="close-price-box">
              <div class="close-price-item"><div class="cp-val" style="color:'''+(("#10b981" if d.get("change_pct","-").startswith("+") else "#ef4444") if d.get("change_pct","-") not in ("-","") else "#94a3b8")+'''">'''+(d.get("close") or d.get("current_price") or "-")+'''</div><div class="cp-label">上次收盘价</div></div>
              <div class="close-price-item"><div class="cp-val" style="color:'''+(("#10b981" if d.get("change_pct","-").startswith("+") else "#ef4444") if d.get("change_pct","-") not in ("-","") else "#94a3b8")+'''">'''+(d.get("change_pct") or "-")+'''</div><div class="cp-label">涨跌幅</div></div>
            </div>
            <div class="chart-container"><div class="chart-caption">📊 30日K线图（MA5/10/20/60）</div><div id="kline-'''+code+'''"><div class="chart-loading">加载中...</div></div></div>
            <div class="chart-container"><div class="chart-caption">📈 上次交易日分时图</div><div id="itraday-'''+code+'''"><div class="chart-loading">加载中...</div></div></div>
            <div class="section-title">📊 关键指标</div>
            <div class="info-grid">'''+"\n".join(info_rows)+'''</div>
            '''+"\n".join(news_parts)+'''
            '''+('<div class="section-title">📋 操作建议</div>'+"\n".join(ops_parts) if ops_parts else '')+'''
            '''+('<div class="section-title">✅ 检查清单</div><div class="checks-list">'+"\n".join(checks_parts)+'</div>' if checks_parts else '')+'''
          </div>
        </div>''')

    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>股票日报 '''+data["date"]+'''</title>
<style>'''+CSS+'''</style>
</head>
<body>
<div class="header"><h1>📊 股票日报</h1><div class="date">'''+data["date"]+'''</div></div>
<div class="stats">
  <div class="stat buy"><div class="num">'''+str(summary.get("buy",0))+'''</div><div class="label">🟢 买入</div></div>
  <div class="stat hold"><div class="num">'''+str(summary.get("hold",0))+'''</div><div class="label">🟡 观望</div></div>
  <div class="stat sell"><div class="num">'''+str(summary.get("sell",0))+'''</div><div class="label">🔴 卖出</div></div>
</div>
<div class="cards">'''+"\n".join(summary_cards)+'''</div>
<div class="section-title" style="padding:20px 16px 8px;font-size:16px;font-weight:700">📋 个股详情（点击展开）</div>
'''+"\n".join(detail_sections)+'''
<div class="footer">
  <p>数据来源：东方财富 · 腾讯财经 | AI：DeepSeek</p>
  <p>仅供参考，不构成投资建议</p>
</div>
<script>'''+JS+'''</script>
</body>
</html>'''

# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        md_path = sys.argv[1]
    else:
        md_path = "reports/report_" + datetime.now().strftime("%Y%m%d") + ".md"

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    data = parse_report(md_text)

    base_dir = os.path.dirname(os.path.abspath(md_path)) if os.path.dirname(md_path) else "reports"
    charts_dir = os.path.join(base_dir, "charts")
    os.makedirs(charts_dir, exist_ok=True)

    print("Generating charts...")
    for stock in data.get("stocks", []):
        code = stock.get("code", "")
        name = stock.get("name", "")
        if not code:
            continue

        kline_b64 = make_kline_chart(code, name)
        if kline_b64:
            with open(os.path.join(charts_dir, f"{code}_kline.png"), "wb") as f:
                f.write(base64.b64decode(kline_b64))
            print(f"  [{code}] K-line OK")
        else:
            print(f"  [{code}] No K-line data")

        itra_b64 = make_intraday_chart(code, name)
        if itra_b64:
            with open(os.path.join(charts_dir, f"{code}_intraday.png"), "wb") as f:
                f.write(base64.b64decode(itra_b64))
            print(f"  [{code}] Intraday OK")
        else:
            print(f"  [{code}] No intraday data")

    html = generate_html(data)

    out_path = os.path.join(base_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nDone! HTML: {out_path}")
    print(f"Charts: {charts_dir}")
