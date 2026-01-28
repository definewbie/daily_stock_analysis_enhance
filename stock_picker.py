# -*- coding: utf-8 -*-
"""
===================================
选股模块
===================================

职责：
1. 基于板块数据进行宏观选股
2. 基于技术指标进行个股筛选
3. 政策分析驱动的选股策略
4. 综合评分和排序
"""

import logging
import json
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

import akshare as ak

from config import get_config
from storage import (
    get_db,
    MarketState,
    SectorDaily,
    SectorStock,
    StockPool,
    PolicyAnalysis,
    StockDaily,
)

logger = logging.getLogger(__name__)


# ========== 配置常量 ==========

# 市值阈值（亿）
MV_THRESHOLDS = {
    MarketState.BULL: 80.0,
    MarketState.NEUTRAL: 50.0,
    MarketState.BEAR: 40.0,
}

# 策略评分权重
MACRO_WEIGHTS = {
    'policy': 40,      # 政策利好
    'hot_sector': 30,  # 热门板块
    'north_flow': 20,  # 北向资金
    'reversal': 10,    # 板块反转
}

TECH_WEIGHTS = {
    'ma_bull': 30,     # 多头排列
    'volume_break': 25, # 放量突破
    'new_high': 20,    # 创新高
    'ma_support': 15,  # 均线支撑
    'trend_up': 10,    # 趋势向上
}


@dataclass
class StockCandidate:
    """选股候选股"""
    stock_code: str
    stock_name: str = ''
    market: str = ''

    # 来源信息
    strategy: str = ''
    sector_name: str = ''
    sector_rank: int = 0
    sector_change_pct: float = 0.0

    # 个股指标
    stock_change_pct: float = 0.0
    ma_status: str = ''
    volume_ratio: float = 0.0
    circ_mv: float = 0.0

    # 评分
    macro_score: float = 0.0
    tech_score: float = 0.0
    total_score: float = 0.0

    # 评分明细
    score_details: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'market': self.market,
            'strategy': self.strategy,
            'sector_name': self.sector_name,
            'sector_rank': self.sector_rank,
            'sector_change_pct': self.sector_change_pct,
            'stock_change_pct': self.stock_change_pct,
            'ma_status': self.ma_status,
            'volume_ratio': self.volume_ratio,
            'circ_mv': self.circ_mv,
            'macro_score': self.macro_score,
            'tech_score': self.tech_score,
            'total_score': self.total_score,
        }


class StockPicker:
    """
    选股器

    功能：
    1. 板块成分股采集
    2. 宏观层选股（政策+板块）
    3. 技术层筛选
    4. 综合评分排序
    """

    def __init__(self, analyzer=None, search_service=None):
        """
        初始化选股器

        Args:
            analyzer: AI分析器（用于政策分析）
            search_service: 搜索服务（用于政策新闻搜索）
        """
        self.config = get_config()
        self.db = get_db()
        self.analyzer = analyzer
        self.search_service = search_service

        # 配置
        self.markets = ['SH', 'SZ']  # 仅A股
        self.top_n = 10  # 每日最多选股数

    def run(self, target_date: Optional[date] = None) -> List[StockCandidate]:
        """
        执行选股流程

        Args:
            target_date: 目标日期，默认今天

        Returns:
            选股结果列表
        """
        if target_date is None:
            target_date = date.today()

        logger.info(f"[选股] 开始选股流程，日期: {target_date}")

        # 1. 获取动态市值阈值
        mv_threshold = self.db.get_mv_threshold()
        logger.info(f"[选股] 市值阈值: {mv_threshold}亿")

        # 2. 宏观层选股：获取候选股池
        candidates = self._macro_selection(target_date)
        logger.info(f"[选股] 宏观层筛选出 {len(candidates)} 只候选股")

        if not candidates:
            logger.warning("[选股] 宏观层无候选股，跳过技术层筛选")
            return []

        # 3. 技术层筛选：评分和过滤
        candidates = self._tech_selection(candidates, mv_threshold)
        logger.info(f"[选股] 技术层筛选后剩余 {len(candidates)} 只")

        # 4. 综合评分排序
        candidates = self._rank_candidates(candidates)

        # 5. 取前N只
        top_candidates = candidates[:self.top_n]

        # 6. 保存结果
        self.db.save_stock_pool(
            [c.to_dict() for c in top_candidates],
            target_date
        )

        logger.info(f"[选股] 完成，共选出 {len(top_candidates)} 只股票")
        return top_candidates

    def _macro_selection(self, target_date: date) -> List[StockCandidate]:
        """
        宏观层选股

        策略：
        A. 政策利好板块
        B. 热门板块（连续领涨）
        C. 北向资金流入板块
        D. 板块反转

        Returns:
            候选股列表
        """
        candidates = []
        sectors_processed = set()

        # 策略A: 政策利好板块
        policy_candidates = self._strategy_policy(target_date)
        for c in policy_candidates:
            if c.sector_name not in sectors_processed:
                sectors_processed.add(c.sector_name)
            candidates.append(c)
        logger.info(f"[选股] 策略A(政策利好): {len(policy_candidates)} 只")

        # 策略B: 热门板块
        hot_candidates = self._strategy_hot_sector(target_date)
        for c in hot_candidates:
            if c.stock_code not in [x.stock_code for x in candidates]:
                candidates.append(c)
        logger.info(f"[选股] 策略B(热门板块): {len(hot_candidates)} 只")

        # 策略C: 北向资金流入
        north_candidates = self._strategy_north_flow(target_date)
        for c in north_candidates:
            if c.stock_code not in [x.stock_code for x in candidates]:
                candidates.append(c)
        logger.info(f"[选股] 策略C(北向资金): {len(north_candidates)} 只")

        # 策略D: 板块反转
        reversal_candidates = self._strategy_reversal(target_date)
        for c in reversal_candidates:
            if c.stock_code not in [x.stock_code for x in candidates]:
                candidates.append(c)
        logger.info(f"[选股] 策略D(板块反转): {len(reversal_candidates)} 只")

        return candidates

    def _strategy_policy(self, target_date: date) -> List[StockCandidate]:
        """策略A: 政策利好板块选股"""
        candidates = []

        # 获取政策利好板块
        policy_sectors = self.db.get_policy_positive_sectors(target_date, min_score=4)

        for policy in policy_sectors:
            sector_name = policy.sector_name
            score = policy.impact_score or 4

            # 获取板块成分股
            stocks = self._get_sector_stocks_with_cache(sector_name)

            for stock in stocks[:5]:  # 每个板块取前5只
                c = StockCandidate(
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name or '',
                    market=stock.market or '',
                    strategy='policy',
                    sector_name=sector_name,
                    macro_score=MACRO_WEIGHTS['policy'] * (score / 5),
                )
                c.score_details['policy'] = c.macro_score
                candidates.append(c)

        return candidates

    def _strategy_hot_sector(self, target_date: date) -> List[StockCandidate]:
        """策略B: 热门板块选股（连续2天涨幅榜前5）"""
        candidates = []

        # 获取今日和昨日涨幅榜前5
        today_top = self.db.get_sectors_by_date(target_date, rank_type='top')[:5]
        yesterday = target_date - timedelta(days=1)
        yesterday_top = self.db.get_sectors_by_date(yesterday, rank_type='top')[:5]

        today_names = {s.sector_name for s in today_top}
        yesterday_names = {s.sector_name for s in yesterday_top}

        # 连续2天都在前5的板块
        hot_sectors = today_names & yesterday_names

        for sector in today_top:
            if sector.sector_name not in hot_sectors:
                continue

            stocks = self._get_sector_stocks_with_cache(sector.sector_name)

            for stock in stocks[:3]:  # 每个板块取前3只
                c = StockCandidate(
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name or '',
                    market=stock.market or '',
                    strategy='hot_sector',
                    sector_name=sector.sector_name,
                    sector_rank=sector.rank,
                    sector_change_pct=sector.change_pct or 0,
                    macro_score=MACRO_WEIGHTS['hot_sector'],
                )
                c.score_details['hot_sector'] = c.macro_score
                candidates.append(c)

        return candidates

    def _strategy_north_flow(self, target_date: date) -> List[StockCandidate]:
        """策略C: 北向资金流入选股"""
        candidates = []

        # 获取近3日北向资金
        history = self.db.get_market_history(days=3)
        if len(history) < 3:
            return candidates

        total_north = sum(h.north_flow or 0 for h in history)

        if total_north <= 0:
            return candidates

        # 北向资金净流入，选择涨幅榜前10板块
        today_top = self.db.get_sectors_by_date(target_date, rank_type='top')[:10]

        for sector in today_top:
            stocks = self._get_sector_stocks_with_cache(sector.sector_name)

            for stock in stocks[:2]:  # 每个板块取前2只
                c = StockCandidate(
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name or '',
                    market=stock.market or '',
                    strategy='north_flow',
                    sector_name=sector.sector_name,
                    sector_rank=sector.rank,
                    sector_change_pct=sector.change_pct or 0,
                    macro_score=MACRO_WEIGHTS['north_flow'],
                )
                c.score_details['north_flow'] = c.macro_score
                candidates.append(c)

        return candidates

    def _strategy_reversal(self, target_date: date) -> List[StockCandidate]:
        """策略D: 板块反转选股"""
        candidates = []

        # 获取昨日跌幅榜前3
        yesterday = target_date - timedelta(days=1)
        yesterday_bottom = self.db.get_sectors_by_date(yesterday, rank_type='bottom')[:3]

        if not yesterday_bottom:
            return candidates

        # 获取今日板块数据
        today_sectors = self.db.get_sectors_by_date(target_date)
        today_map = {s.sector_name: s for s in today_sectors}

        for sector in yesterday_bottom:
            today_sector = today_map.get(sector.sector_name)
            if not today_sector:
                continue

            # 今日涨幅转正
            if (today_sector.change_pct or 0) > 0:
                stocks = self._get_sector_stocks_with_cache(sector.sector_name)

                for stock in stocks[:2]:
                    c = StockCandidate(
                        stock_code=stock.stock_code,
                        stock_name=stock.stock_name or '',
                        market=stock.market or '',
                        strategy='reversal',
                        sector_name=sector.sector_name,
                        sector_change_pct=today_sector.change_pct or 0,
                        macro_score=MACRO_WEIGHTS['reversal'],
                    )
                    c.score_details['reversal'] = c.macro_score
                    candidates.append(c)

        return candidates

    def _get_sector_stocks_with_cache(self, sector_name: str) -> List[SectorStock]:
        """获取板块成分股（带缓存）"""
        stocks = self.db.get_sector_stocks(sector_name)

        if not stocks:
            # 尝试从接口获取并缓存
            stocks = self._fetch_sector_stocks(sector_name)

        return stocks

    def _fetch_sector_stocks(self, sector_name: str) -> List[SectorStock]:
        """从接口获取板块成分股"""
        try:
            df = ak.stock_board_industry_cons_em(symbol=sector_name)

            if df is None or df.empty:
                return []

            stocks = []
            for _, row in df.iterrows():
                code = str(row.get('代码', ''))
                name = str(row.get('名称', ''))

                # 判断市场
                if code.startswith('6'):
                    market = 'SH'
                elif code.startswith(('0', '3')):
                    market = 'SZ'
                else:
                    continue  # 跳过非A股

                stocks.append({
                    'code': code,
                    'name': name,
                    'market': market,
                })

            # 保存到数据库
            self.db.save_sector_stocks(sector_name, stocks)

            return self.db.get_sector_stocks(sector_name)

        except Exception as e:
            logger.warning(f"[选股] 获取板块成分股失败 {sector_name}: {e}")
            return []

    def _tech_selection(
        self,
        candidates: List[StockCandidate],
        mv_threshold: float
    ) -> List[StockCandidate]:
        """
        技术层筛选

        1. 过滤不符合条件的股票
        2. 计算技术评分
        """
        filtered = []
        mv_filtered_count = 0

        for c in candidates:
            # 基础过滤
            if not self._pass_basic_filter(c, mv_threshold):
                continue

            # 获取个股技术指标
            tech_data = self._get_stock_tech_data(c.stock_code)
            if not tech_data:
                continue

            # 流通市值过滤
            circ_mv = tech_data.get('circ_mv', 0)
            if circ_mv > 0 and circ_mv < mv_threshold:
                mv_filtered_count += 1
                logger.debug(f"[选股] {c.stock_code} 流通市值 {circ_mv:.1f}亿 < 阈值 {mv_threshold}亿，跳过")
                continue

            # 计算技术评分
            c.tech_score = self._calc_tech_score(c, tech_data)
            c.stock_change_pct = tech_data.get('pct_chg', 0)
            c.ma_status = tech_data.get('ma_status', '')
            c.volume_ratio = tech_data.get('volume_ratio', 0)
            c.circ_mv = circ_mv

            filtered.append(c)

        if mv_filtered_count > 0:
            logger.info(f"[选股] 市值过滤: {mv_filtered_count} 只股票因流通市值<{mv_threshold}亿被排除")

        return filtered

    def _pass_basic_filter(self, c: StockCandidate, mv_threshold: float) -> bool:
        """基础过滤"""
        code = c.stock_code

        # 排除非A股代码
        if not code or len(code) != 6:
            return False

        # 排除ST股（通常名称包含ST）
        if c.stock_name and 'ST' in c.stock_name.upper():
            return False

        # 市场过滤
        if code.startswith('6'):
            c.market = 'SH'
        elif code.startswith(('0', '3')):
            c.market = 'SZ'
        else:
            return False

        return True

    def _get_stock_tech_data(self, code: str) -> Optional[Dict[str, Any]]:
        """获取个股技术指标数据"""
        data = self.db.get_latest_data(code, days=2)

        if not data:
            return None

        today = data[0]
        yesterday = data[1] if len(data) > 1 else None

        # 获取流通市值
        circ_mv = self._get_stock_circ_mv(code)

        result = {
            'close': today.close,
            'pct_chg': today.pct_chg or 0,
            'volume_ratio': today.volume_ratio or 0,
            'ma5': today.ma5,
            'ma10': today.ma10,
            'ma20': today.ma20,
            'circ_mv': circ_mv,
        }

        # 判断均线状态
        close = today.close or 0
        ma5 = today.ma5 or 0
        ma10 = today.ma10 or 0
        ma20 = today.ma20 or 0

        if close > ma5 > ma10 > ma20 > 0:
            result['ma_status'] = 'bull'  # 多头排列
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            result['ma_status'] = 'bear'  # 空头排列
        else:
            result['ma_status'] = 'neutral'

        return result

    def _get_stock_circ_mv(self, code: str) -> float:
        """
        获取股票流通市值（亿元）

        Args:
            code: 股票代码

        Returns:
            流通市值（亿元），获取失败返回0
        """
        try:
            # 构造完整代码
            if code.startswith('6'):
                full_code = f"sh{code}"
            elif code.startswith(('0', '3')):
                full_code = f"sz{code}"
            else:
                return 0

            # 使用东财实时行情接口
            df = ak.stock_individual_info_em(symbol=code)

            if df is None or df.empty:
                return 0

            # 查找流通市值
            for _, row in df.iterrows():
                item = str(row.get('item', ''))
                if '流通市值' in item:
                    value = row.get('value', 0)
                    # 转换为亿元
                    if isinstance(value, (int, float)):
                        return float(value) / 1e8
                    elif isinstance(value, str):
                        # 处理带单位的字符串
                        value = value.replace(',', '')
                        if '亿' in value:
                            return float(value.replace('亿', ''))
                        elif '万' in value:
                            return float(value.replace('万', '')) / 1e4
                        else:
                            return float(value) / 1e8

            return 0

        except Exception as e:
            logger.debug(f"[选股] 获取流通市值失败 {code}: {e}")
            return 0

    def _calc_tech_score(self, c: StockCandidate, data: Dict) -> float:
        """计算技术评分"""
        score = 0.0

        # 多头排列 +30
        if data.get('ma_status') == 'bull':
            score += TECH_WEIGHTS['ma_bull']
            c.score_details['ma_bull'] = TECH_WEIGHTS['ma_bull']

        # 放量突破 +25
        volume_ratio = data.get('volume_ratio', 0)
        pct_chg = data.get('pct_chg', 0)
        if volume_ratio > 1.5 and pct_chg > 2:
            score += TECH_WEIGHTS['volume_break']
            c.score_details['volume_break'] = TECH_WEIGHTS['volume_break']

        # 趋势向上 +10
        if pct_chg > 0:
            score += TECH_WEIGHTS['trend_up']
            c.score_details['trend_up'] = TECH_WEIGHTS['trend_up']

        return score

    def _rank_candidates(self, candidates: List[StockCandidate]) -> List[StockCandidate]:
        """综合评分排序"""
        for c in candidates:
            c.total_score = c.macro_score + c.tech_score

        # 按总分降序排序
        candidates.sort(key=lambda x: x.total_score, reverse=True)

        return candidates

    # ========== 政策分析 ==========

    def analyze_policy(
        self,
        target_date: Optional[date] = None,
        session_type: str = 'pre'
    ) -> List[Dict[str, Any]]:
        """
        执行政策分析

        Args:
            target_date: 目标日期
            session_type: 'pre'=盘前, 'post'=盘后

        Returns:
            政策分析结果列表
        """
        if target_date is None:
            target_date = date.today()

        if not self.search_service or not self.analyzer:
            logger.warning("[选股] 政策分析需要配置搜索服务和AI分析器")
            return []

        logger.info(f"[选股] 开始政策分析，session={session_type}")

        # 1. 搜索政策新闻
        news = self._search_policy_news()
        if not news:
            logger.warning("[选股] 未搜索到政策新闻")
            return []

        # 2. 调用LLM分析政策影响
        analyses = self._analyze_policy_with_llm(news)

        # 3. 保存分析结果
        if analyses:
            self.db.save_policy_analysis(analyses, target_date, session_type)

        return analyses

    def _search_policy_news(self) -> List[Dict[str, Any]]:
        """搜索政策新闻"""
        if not self.search_service:
            return []

        today = datetime.now()
        month_str = f"{today.year}年{today.month}月"

        queries = [
            f"A股 政策 利好 {month_str}",
            f"产业政策 扶持 {month_str}",
            f"国务院 发改委 政策 {month_str}",
        ]

        all_news = []
        for query in queries:
            try:
                results = self.search_service.search(query, max_results=3)
                all_news.extend(results)
            except Exception as e:
                logger.warning(f"[选股] 搜索政策新闻失败: {e}")

        return all_news[:10]

    def _analyze_policy_with_llm(self, news: List) -> List[Dict[str, Any]]:
        """使用LLM分析政策影响"""
        if not self.analyzer or not self.analyzer.is_available():
            return []

        # 构建新闻文本
        news_text = ""
        for i, n in enumerate(news[:6], 1):
            if hasattr(n, 'title'):
                title = n.title[:80] if n.title else ''
                snippet = n.snippet[:150] if n.snippet else ''
            else:
                title = n.get('title', '')[:80]
                snippet = n.get('snippet', '')[:150]
            news_text += f"{i}. {title}\n   {snippet}\n\n"

        prompt = f"""你是一位专业的A股政策分析师。请分析以下政策/新闻对A股各板块的影响。

【近期政策新闻】
{news_text}

【输出要求】
请以JSON格式输出，包含利好板块及影响程度(1-5分，5分最强)。

【输出格式】
{{
  "positive": [
    {{"sector": "板块名", "score": 5, "reason": "简要原因"}}
  ],
  "negative": [
    {{"sector": "板块名", "score": 3, "reason": "简要原因"}}
  ]
}}

注意：
- 只输出确定性较高的判断（至少3分以上）
- 板块名称使用标准行业名称（如：半导体、光伏设备、白酒、银行等）
- 最多输出5个利好板块和3个利空板块
"""

        try:
            if self.analyzer._use_openai:
                response = self.analyzer._call_openai_api(prompt, {'temperature': 0.3})
            else:
                result = self.analyzer._model.generate_content(
                    prompt,
                    generation_config={'temperature': 0.3}
                )
                response = result.text.strip() if result and result.text else None

            if not response:
                return []

            # 解析JSON
            # 尝试提取JSON部分
            if '```json' in response:
                response = response.split('```json')[1].split('```')[0]
            elif '```' in response:
                response = response.split('```')[1].split('```')[0]

            data = json.loads(response)

            analyses = []

            # 处理利好板块
            for item in data.get('positive', []):
                analyses.append({
                    'sector': item.get('sector'),
                    'impact_type': 'positive',
                    'score': item.get('score', 3),
                    'reason': item.get('reason', ''),
                })

            # 处理利空板块
            for item in data.get('negative', []):
                analyses.append({
                    'sector': item.get('sector'),
                    'impact_type': 'negative',
                    'score': item.get('score', 3),
                    'reason': item.get('reason', ''),
                })

            logger.info(f"[选股] 政策分析完成，识别 {len(analyses)} 个受影响板块")
            return analyses

        except json.JSONDecodeError as e:
            logger.error(f"[选股] 政策分析JSON解析失败: {e}")
            return []
        except Exception as e:
            logger.error(f"[选股] 政策分析失败: {e}")
            return []

    # ========== 板块成分股更新 ==========

    def update_sector_stocks(self, sector_names: Optional[List[str]] = None):
        """
        更新板块成分股

        Args:
            sector_names: 指定板块列表，为空则更新热门板块
        """
        if sector_names is None:
            # 获取最近的热门板块
            today = date.today()
            sectors = self.db.get_sectors_by_date(today, rank_type='top')[:10]
            sector_names = [s.sector_name for s in sectors]

        for name in sector_names:
            try:
                stocks = self._fetch_sector_stocks(name)
                logger.info(f"[选股] 更新板块成分股 {name}: {len(stocks)} 只")
            except Exception as e:
                logger.warning(f"[选股] 更新板块成分股失败 {name}: {e}")


# ========== 便捷函数 ==========

def run_stock_picker(
    analyzer=None,
    search_service=None,
    target_date: Optional[date] = None
) -> List[StockCandidate]:
    """
    运行选股

    Args:
        analyzer: AI分析器
        search_service: 搜索服务
        target_date: 目标日期

    Returns:
        选股结果列表
    """
    picker = StockPicker(analyzer=analyzer, search_service=search_service)
    return picker.run(target_date)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    picker = StockPicker()

    # 测试市场状态
    state = picker.db.get_market_state()
    print(f"当前市场状态: {state}")

    threshold = picker.db.get_mv_threshold()
    print(f"市值阈值: {threshold}亿")
