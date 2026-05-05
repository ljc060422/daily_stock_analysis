"""
全市场量化打分引擎 — 基于 akshare stock_zh_a_spot_em 全量快照。
一次 API 调用覆盖 5000+ A 股，7 维度打分，返回 Top 100。
"""

import time
import logging
from typing import Optional, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 模块级缓存，避免重复拉取全量数据
_cache: Dict = {"data": None, "timestamp": 0, "ttl": 600}


class QuantScorer:
    """全市场 A 股量化打分器。"""

    def __init__(self):
        self._df: Optional[pd.DataFrame] = None  # 原始全量数据
        self._scored: Optional[pd.DataFrame] = None  # 打分结果

    # ------------------------------------------------------------------
    # 数据获取
    # ------------------------------------------------------------------
    def _fetch_market_snapshot(self) -> pd.DataFrame:
        """获取全市场实时快照（优先命中缓存）。"""
        now = time.time()
        if _cache["data"] is not None and (now - _cache["timestamp"]) < _cache["ttl"]:
            logger.info(f"[QuantScorer] 缓存命中, age={int(now - _cache['timestamp'])}s")
            return _cache["data"].copy()

        import akshare as ak
        logger.info("[QuantScorer] 拉取全市场实时行情 …")
        df = ak.stock_zh_a_spot_em()
        logger.info(f"[QuantScorer] 获取 {len(df)} 只股票")

        _cache["data"] = df
        _cache["timestamp"] = now
        return df.copy()

    # ------------------------------------------------------------------
    # 过滤
    # ------------------------------------------------------------------
    @staticmethod
    def _filter(df: pd.DataFrame) -> pd.DataFrame:
        """剔除不适合推荐的股票。"""
        before = len(df)

        # 去掉 ST / *ST / N 开头新股
        name_col = "名称"
        mask_st = ~df[name_col].str.contains(r"ST|\*ST|^N", na=True, regex=True)
        df = df[mask_st]

        # 去掉涨跌停 (±9.9% 以上视为涨跌停板)
        change_col = "涨跌幅"
        change = pd.to_numeric(df[change_col], errors="coerce")
        df = df[(change > -9.8) & (change < 9.8)]

        # 换手率太低（无人问津）
        turnover_col = "换手率"
        turnover = pd.to_numeric(df.get(turnover_col, 0), errors="coerce")
        df = df[turnover >= 0.3]

        # PE 无效或极端
        pe_col = "市盈率-动态"
        pe = pd.to_numeric(df.get(pe_col, np.nan), errors="coerce")
        df = df[(pe > 0) & (pe < 500)]

        after = len(df)
        logger.info(f"[QuantScorer] 过滤: {before} → {after} ({before - after} 只剔除)")
        return df

    # ------------------------------------------------------------------
    # 打分
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_float(series: pd.Series, default: float = 0.0) -> pd.Series:
        return pd.to_numeric(series, errors="coerce").fillna(default)

    def _score(self, df: pd.DataFrame) -> pd.DataFrame:
        """7 维度打分，返回带分项得分的 DataFrame。"""
        s = pd.DataFrame(index=df.index)
        s["code"] = df["代码"].astype(str).str.zfill(6)
        s["name"] = df["名称"].astype(str)

        price = self._safe_float(df["最新价"])
        change_pct = self._safe_float(df["涨跌幅"])
        volume_ratio = self._safe_float(df["量比"])
        turnover = self._safe_float(df["换手率"])
        pe = self._safe_float(df["市盈率-动态"])
        pb = self._safe_float(df["市净率"])
        amplitude = self._safe_float(df["振幅"])
        amount = self._safe_float(df["成交额"])
        change_60d = self._safe_float(df["60日涨跌幅"])
        ytd = self._safe_float(df["年初至今涨跌幅"])

        # ── 1. 趋势强度 (25分) ──
        cond_25 = change_60d > 30
        cond_20 = (change_60d > 20) & ~cond_25
        cond_15 = (change_60d > 10) & ~cond_25 & ~cond_20
        cond_10 = (change_60d > 3) & ~cond_25 & ~cond_20 & ~cond_15
        cond_5 = (change_60d > -5) & ~cond_25 & ~cond_20 & ~cond_15 & ~cond_10
        s["trend_s"] = np.select(
            [cond_25, cond_20, cond_15, cond_10, cond_5],
            [25, 20, 15, 10, 5],
            default=2,
        )

        # ── 2. 短期动量 (20分) ──
        cond_20m = change_pct > 5
        cond_17 = (change_pct > 3) & ~cond_20m
        cond_14 = (change_pct > 1) & ~cond_20m & ~cond_17
        cond_10m = (change_pct > 0) & ~cond_20m & ~cond_17 & ~cond_14
        cond_5m = (change_pct > -2) & ~cond_20m & ~cond_17 & ~cond_14 & ~cond_10m
        s["momentum_s"] = np.select(
            [cond_20m, cond_17, cond_14, cond_10m, cond_5m],
            [20, 17, 14, 10, 5],
            default=2,
        )

        # ── 3. 量能健康 (15分) ──
        # 量比贡献 10 分
        cond_v10 = volume_ratio > 3
        cond_v7 = (volume_ratio > 2) & ~cond_v10
        cond_v5 = (volume_ratio > 1.2) & ~cond_v10 & ~cond_v7
        cond_v3 = (volume_ratio > 0.8) & ~cond_v10 & ~cond_v7 & ~cond_v5
        vol_score = np.select([cond_v10, cond_v7, cond_v5, cond_v3], [10, 7, 5, 3], default=1)

        # 换手率贡献 5 分
        cond_t5 = (turnover >= 2) & (turnover <= 10)
        cond_t3 = (turnover >= 1) & (turnover < 2)
        cond_t2 = (turnover >= 0.5) & (turnover < 1)
        turnover_score = np.select([cond_t5, cond_t3, cond_t2], [5, 3, 2], default=1)

        s["volume_s"] = vol_score + turnover_score

        # ── 4. 突破形态 (15分) ──
        # 放量上涨且振幅适中，视为攻击形态
        cond_b15 = (change_pct >= 3) & (volume_ratio >= 1.5) & (amplitude < 8)
        cond_b12 = (change_pct >= 1) & (volume_ratio >= 1.2) & (amplitude < 6)
        cond_b8 = (change_pct >= 0) & (volume_ratio >= 1)
        cond_b5 = (change_pct >= -1) & (volume_ratio >= 0.8)
        s["breakout_s"] = np.select(
            [cond_b15, cond_b12, cond_b8, cond_b5],
            [15, 12, 8, 5],
            default=3,
        )

        # ── 5. 资金强度 (10分) ──
        cond_f10 = (change_pct >= 3) & (volume_ratio >= 2)
        cond_f7 = (change_pct >= 1) & (volume_ratio >= 1.2)
        cond_f4 = change_pct >= 0
        cond_f2 = change_pct >= -2
        s["flow_s"] = np.select(
            [cond_f10, cond_f7, cond_f4, cond_f2],
            [10, 7, 4, 2],
            default=0,
        )

        # ── 6. 板块强度 (10分) ──
        # 全量快照不含板块信息，默认给 5 分（中性），AI 阶段补充
        s["sector_s"] = 5

        # ── 7. 质量过滤 (5分) ──
        cond_q3 = (pe >= 10) & (pe <= 50)
        cond_q2 = (pe > 0) & (pe < 10)
        cond_q1 = (pe > 50) & (pe <= 100)
        pe_score = np.select([cond_q3, cond_q2, cond_q1], [3, 2, 1], default=0)

        cond_pb2 = (pb >= 1) & (pb <= 5)
        cond_pb1 = (pb > 0) & (pb < 1)
        pb_score = np.select([cond_pb2, cond_pb1], [2, 1], default=0)

        s["quality_s"] = pe_score + pb_score

        # ── 总分 ──
        s["total_score"] = (
            s["trend_s"]
            + s["momentum_s"]
            + s["volume_s"]
            + s["breakout_s"]
            + s["flow_s"]
            + s["sector_s"]
            + s["quality_s"]
        )

        s["price"] = price
        s["change_pct"] = change_pct
        s["volume_ratio"] = volume_ratio
        s["turnover"] = turnover
        s["pe"] = pe

        return s

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def scan_and_score(self, top_n: int = 100) -> pd.DataFrame:
        """全市场扫描+打分，返回 Top N 只股票 DataFrame。

        列：code, name, total_score, trend_s, momentum_s, volume_s,
             breakout_s, flow_s, sector_s, quality_s, price, change_pct,
             volume_ratio, turnover, pe
        """
        t0 = time.time()

        raw = self._fetch_market_snapshot()
        filtered = self._filter(raw)
        self._scored = self._score(filtered)

        top = self._scored.nlargest(top_n, "total_score")
        top = top.sort_values("total_score", ascending=False)

        elapsed = time.time() - t0
        logger.info(
            f"[QuantScorer] 打分完成: {len(top)} 只入池, "
            f"最高={top['total_score'].iloc[0]:.0f}, 耗时 {elapsed:.1f}s"
        )
        return top.reset_index(drop=True)
