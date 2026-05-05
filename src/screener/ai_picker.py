"""
AI 精选引擎 — 两轮 DeepSeek 筛选，从 Top 100 量化候选池中精选 Top 20。
Stage 1: 快速扫描 100→40（标记 keep/drop）
Stage 2: 深度精选 40→20（打分+推荐理由）
"""

import os
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Prompt templates
# ------------------------------------------------------------------

STAGE1_SYSTEM = """你是A股短线交易专家。我会给你一批股票的量化指标，请快速判断每只是否值得关注。

返回纯JSON: [{"code":"000001","verdict":"keep","reason":"放量突破+趋势向好,5字以内原因"}, ...]

verdict: "keep" 表示值得进入下一轮深度分析, "drop" 表示淘汰。
标准: 趋势强劲+量能配合+无明显风险信号→keep; 其余→drop。保留约40%即可。"""

STAGE2_SYSTEM = """你是A股短线交易专家。请对候选股票做深度研判，给出最终评分和买卖建议。

返回纯JSON: [{"code":"000001","signal":"买入","score":88,"reason":"多头排列+量价齐升+政策催化"}, ...]

signal: "买入"(强烈看好)/"观察"(值得跟踪)
score: 0-100综合评分，体现短期上涨潜力
reason: 15字以内核心理由，必须结合具体的技术形态或催化剂

评分标准:
- 80-100: 多重共振(趋势+量能+资金+催化) → "买入"
- 60-79: 1-2个亮点但有关注点 → "观察"
- 0-59: 不建议，不会出现在最终列表中"""


# ------------------------------------------------------------------
# LLM caller
# ------------------------------------------------------------------
def _call_deepseek(
    user_prompt: str,
    system_prompt: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2048,
    timeout: int = 60,
) -> str:
    """调用 DeepSeek API，返回文本内容。"""
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        api_key = os.getenv("DEEPSEEK_API_KEYS", "").split(",")[0].strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法进行 AI 精选")

    # 优先从 LITELLM_MODEL 推断模型名，否则用默认
    model = os.getenv("LITELLM_MODEL", "").strip()
    if not model or "/" not in model:
        model = "deepseek/deepseek-chat"

    import litellm
    try:
        response = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            timeout=timeout,
        )
    except Exception:
        # 如果 litellm 调用失败（如模型路由问题），尝试直接用 openai 兼容接口
        from openai import OpenAI
        base_url = os.getenv("DEEPSEEK_BASE_URL",
                             "https://api.deepseek.com")
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    return response.choices[0].message.content or ""


def _parse_json_list(text: str) -> List[Dict]:
    """从 AI 返回文本中提取 JSON 列表。"""
    # 清理 markdown 代码块
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 [...] 部分
    import re
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    logger.warning(f"无法解析 AI 返回: {text[:200]}")
    return []


# ------------------------------------------------------------------
# AIPicker
# ------------------------------------------------------------------
class AIPicker:
    """两轮 AI 精选器。"""

    def __init__(self, stage1_batch: int = 20, stage2_batch: int = 8, max_workers: int = 5):
        self.stage1_batch = stage1_batch
        self.stage2_batch = stage2_batch
        self.max_workers = max_workers

    # ── Stage 1: 快速扫描 ──────────────────────────────────────
    def _build_stage1_prompt(self, stocks: pd.DataFrame) -> str:
        """构建一批股票的快速扫描 prompt。"""
        rows = []
        for _, s in stocks.iterrows():
            rows.append(
                f"{s['code']} {s['name']}: "
                f"量化={int(s['total_score'])} "
                f"趋势={int(s['trend_s'])} 动量={int(s['momentum_s'])} "
                f"量能={int(s['volume_s'])} 突破={int(s['breakout_s'])} "
                f"资金={int(s['flow_s'])} PE={s.get('pe',0):.0f} "
                f"涨跌={s['change_pct']:.1f}% 量比={s['volume_ratio']:.1f}"
            )
        return "\n".join(rows)

    def _stage1_batch(self, batch: pd.DataFrame) -> List[Dict]:
        """处理一批股票（20 只），返回 keep 列表。"""
        prompt = self._build_stage1_prompt(batch)
        try:
            text = _call_deepseek(prompt, STAGE1_SYSTEM, max_tokens=1024)
            results = _parse_json_list(text)
            kept = [r for r in results if r.get("verdict") == "keep"]
            logger.info(f"[AIPicker S1] {len(batch)}→{len(kept)} keep")
            return kept
        except Exception as e:
            logger.warning(f"[AIPicker S1] 批次失败: {e}，保留前 50% 进入下轮")
            # 降级：保留前一半
            mid = len(batch) // 2
            return [
                {"code": row["code"], "verdict": "keep", "reason": "量化降级"}
                for _, row in batch.head(mid).iterrows()
            ]

    def stage1_quick_scan(self, top100: pd.DataFrame) -> List[Dict]:
        """快速扫描 Top 100，并发 5 路，返回约 40 只。"""
        logger.info(f"[AIPicker] Stage 1 开始: {len(top100)} 只→?")
        t0 = time.time()

        batches = [
            top100.iloc[i : i + self.stage1_batch]
            for i in range(0, len(top100), self.stage1_batch)
        ]
        all_kept: List[Dict] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {ex.submit(self._stage1_batch, b): i for i, b in enumerate(batches)}
            for fut in as_completed(futures):
                try:
                    all_kept.extend(fut.result())
                except Exception as e:
                    logger.warning(f"[AIPicker S1] 并发批次异常: {e}")

        elapsed = time.time() - t0
        logger.info(f"[AIPicker] Stage 1 完成: {len(all_kept)} 只, 耗时 {elapsed:.1f}s")
        return all_kept

    # ── Stage 2: 深度精选 ──────────────────────────────────────
    def _build_stage2_prompt(
        self, stocks: List[Dict], top100: pd.DataFrame
    ) -> str:
        """构建深度精选 prompt（含更多上下文）。"""
        # 建 code→row 索引
        idx = top100.set_index("code", drop=False)

        rows = []
        for s in stocks:
            code = s.get("code", "")
            if code not in idx.index:
                continue
            r = idx.loc[code]
            rows.append(
                f"{code} {r['name']}: "
                f"涨跌={r['change_pct']:.1f}% 量比={r['volume_ratio']:.1f} "
                f"换手={r['turnover']:.1f}% PE={r.get('pe',0):.0f} "
                f"量化分项→趋势={int(r['trend_s'])} 动量={int(r['momentum_s'])} "
                f"量能={int(r['volume_s'])} 突破={int(r['breakout_s'])} "
                f"资金={int(r['flow_s'])} "
                f"Stage1理由={s.get('reason','')}"
            )
        return "\n".join(rows)

    def _stage2_batch(
        self, batch: List[Dict], top100: pd.DataFrame
    ) -> List[Dict]:
        """处理一批（8 只），返回带评分+理由的结果。"""
        prompt = self._build_stage2_prompt(batch, top100)
        try:
            text = _call_deepseek(prompt, STAGE2_SYSTEM, max_tokens=1536)
            results = _parse_json_list(text)
            valid = [r for r in results if "code" in r and "score" in r]
            if not valid:
                logger.warning("[AIPicker S2] AI 未返回有效结果")
                return []
            logger.info(f"[AIPicker S2] {len(batch)}只→{len(valid)}只有效")
            return valid
        except Exception as e:
            logger.warning(f"[AIPicker S2] 批次失败: {e}")
            return []

    def stage2_deep_pick(
        self, candidates: List[Dict], top100: pd.DataFrame
    ) -> List[Dict]:
        """深度精选候选池，返回 Top 20（含 signal/score/reason）。"""
        logger.info(f"[AIPicker] Stage 2 开始: {len(candidates)} 只")
        t0 = time.time()

        batches = [
            candidates[i : i + self.stage2_batch]
            for i in range(0, len(candidates), self.stage2_batch)
        ]
        all_picks: List[Dict] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {
                ex.submit(self._stage2_batch, b, top100): i for i, b in enumerate(batches)
            }
            for fut in as_completed(futures):
                try:
                    all_picks.extend(fut.result())
                except Exception as e:
                    logger.warning(f"[AIPicker S2] 并发批次异常: {e}")

        # 按 AI 评分排序
        all_picks.sort(key=lambda x: x.get("score", 0), reverse=True)

        elapsed = time.time() - t0
        logger.info(f"[AIPicker] Stage 2 完成: {len(all_picks)} 只, 耗时 {elapsed:.1f}s")
        return all_picks

    # ── 公共入口 ──────────────────────────────────────────────
    def pick_top20(
        self, top100: pd.DataFrame
    ) -> Tuple[List[Dict], Optional[List[Dict]]]:
        """两轮筛选入口。

        Returns:
            (top20, all_s2) — top20 是最终精选列表（最多20只），
            all_s2 是 Stage2 全部评分结果（用于报告展示）。
        """
        # Stage 1: 100→≈40
        kept = self.stage1_quick_scan(top100)
        if not kept:
            logger.warning("[AIPicker] Stage 1 返回空，降级为纯量化 Top 20")
            return self._fallback_quant(top100), None

        # Stage 2: ≈40→20
        picks = self.stage2_deep_pick(kept, top100)
        if not picks:
            logger.warning("[AIPicker] Stage 2 返回空，降级为纯量化 Top 20")
            return self._fallback_quant(top100), None

        # Top 20
        top20 = picks[:20]
        return top20, picks

    @staticmethod
    def _fallback_quant(top100: pd.DataFrame) -> List[Dict]:
        """AI 不可用时的纯量化降级方案。"""
        top20 = top100.head(20)
        return [
            {
                "code": row["code"],
                "signal": "观察",
                "score": int(row["total_score"]),
                "reason": "量化筛选",
            }
            for _, row in top20.iterrows()
        ]
