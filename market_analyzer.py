# -*- coding: utf-8 -*-
"""
===================================
大盘复盘分析模块
===================================

职责：
1. 获取大盘指数数据（上证、深证、创业板）
2. 搜索市场新闻形成复盘情报
3. 使用大模型生成每日大盘复盘报告
"""

# =========================
# Imports
# =========================

# Standard library
import logging
import os
import random
import time
from datetime import datetime
from http.client import RemoteDisconnected
from typing import Any, Dict, List, Optional

# Third-party
import akshare as ak
import numpy as np
import pandas as pd
import requests

# Local
from config import get_config
from search_service import SearchService
from market_models import MarketIndex, MarketOverview
from market_utils import (
    parse_cn_unit_number as _parse_cn_unit_number,
    safe_float as _safe_float,
    normalize_sector_rankings,
    format_sector_list,
    install_requests_patch_for_akshare_v2,
    USER_AGENTS as _USER_AGENTS,
)

# =========================
# Logger
# =========================

logger = logging.getLogger(__name__)


# =========================
# Main Class
# =========================

class MarketAnalyzer:
    """
    大盘复盘分析器

    功能：
    1. 获取大盘指数实时行情
    2. 获取市场涨跌统计
    3. 获取板块涨跌榜
    4. 搜索市场新闻
    5. 生成大盘复盘报告
    """

    # 主要指数代码
    MAIN_INDICES = {
        'sh000001': '上证指数',
        'sz399001': '深证成指',
        'sz399006': '创业板指',
        'sh000688': '科创50',
        'sh000016': '上证50',
        'sh000300': '沪深300',
    }

    def __init__(self, search_service: Optional[SearchService] = None, analyzer=None):
        """
        初始化大盘分析器

        Args:
            search_service: 搜索服务实例
            analyzer: AI分析器实例（用于调用LLM）
        """
        install_requests_patch_for_akshare_v2()

        self.config = get_config()
        self.search_service = search_service
        self.analyzer = analyzer

    def get_market_overview(self) -> MarketOverview:
        """
        获取市场概览数据

        Returns:
            MarketOverview: 市场概览数据对象
        """
        today = datetime.now().strftime('%Y-%m-%d')
        overview = MarketOverview(date=today)

        # 1. 获取主要指数行情
        overview.indices = self._get_main_indices()

        # 2. 获取涨跌统计
        self._get_market_statistics(overview)

        # 3. 获取板块涨跌榜
        self._get_sector_rankings(overview)

        # 4. 获取北向资金（可选）
        # self._get_north_flow(overview)

        return overview

    def _call_akshare_with_retry(self, fn, name: str, attempts: int = 3):
        """调用 akshare 接口，支持重试和异常分类处理

        异常分类：
        1. 网络类异常（RemoteDisconnected等）：重试
        2. 服务器拒绝类异常（JSONDecodeError等）：不重试，直接返回 None 让 fallback 生效
        3. 其他异常：抛出
        """
        last_error = None

        # 网络类异常：可重试
        network_errors = (
            RemoteDisconnected,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ReadTimeout,
        )

        # 服务器拒绝类异常：不重试，但允许 fallback
        # JSONDecodeError 通常表示服务器返回了 HTML 错误页面而非 JSON
        server_reject_errors = (
            ValueError,  # 包含 JSONDecodeError
        )

        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except network_errors as e:
                last_error = e
                logger.warning(f"[大盘] {name} 网络错误 (attempt {attempt}/{attempts}): {e}")
                if attempt < attempts:
                    backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 1.2)
                    time.sleep(backoff)
            except server_reject_errors as e:
                # 服务器拒绝（如返回 HTML 错误页），不重试，让 fallback 机制生效
                error_msg = str(e)
                if "decode" in error_msg.lower() or "<" in error_msg:
                    logger.warning(f"[大盘] {name} 服务器拒绝访问（返回非JSON）: {error_msg[:100]}")
                else:
                    logger.warning(f"[大盘] {name} 数据解析失败: {error_msg[:100]}")
                return None  # 返回 None 让 _fetch_with_fallback 尝试下一个数据源
            except Exception as e:
                # 其他未知异常，记录并抛出
                logger.exception(f"[大盘] {name} 未知异常: {e}")
                raise

        logger.error(f"[大盘] {name} 最终失败（已重试{attempts}次）: {last_error}")
        return None

    def _fetch_with_fallback(self, sources: List[tuple], name: str):
        """统一的多源fallback获取逻辑

        Args:
            sources: [(函数名或callable, 源标签), ...] 按优先级排序
            name: 数据描述（用于日志）

        Returns:
            (DataFrame, source_label) 或 (None, None)
        """
        for fn_or_name, source_label in sources:
            # 支持字符串函数名或直接传入callable
            if isinstance(fn_or_name, str):
                if not hasattr(ak, fn_or_name):
                    logger.debug(f"[大盘] {name}: {fn_or_name} 在当前AkShare版本不可用，跳过")
                    continue
                fn = getattr(ak, fn_or_name)
            else:
                fn = fn_or_name

            try:
                df = self._call_akshare_with_retry(fn, f"{name}({source_label})", attempts=2)
                if df is not None and not df.empty:
                    logger.info(f"[大盘] {name} 获取成功 (source={source_label}), 共 {len(df)} 条")
                    return df, source_label
            except Exception as e:
                logger.warning(f"[大盘] {name}({source_label}) 失败: {e}")
                continue

        logger.error(f"[大盘] {name} 所有数据源均失败")
        return None, None

    def _get_main_indices(self) -> List[MarketIndex]:
        """获取主要指数实时行情（腾讯优先 → 新浪 → EM → 雪球 fallback）"""
        indices = []

        logger.info("[大盘] 获取主要指数实时行情...")

        # 方法1: 腾讯接口优先（最稳定，无需认证）
        indices = self._get_indices_from_tencent()
        if indices:
            logger.info(f"[大盘] 获取到 {len(indices)} 个指数行情 (source=tencent)")
            return indices

        logger.warning("[大盘] 腾讯接口失败，尝试新浪/EM批量接口...")

        # 方法2: 尝试批量接口（新浪/EM）
        df = None
        source = None

        # 新浪指数接口
        try:
            df = self._call_akshare_with_retry(ak.stock_zh_index_spot_sina, "指数行情(新浪)", attempts=2)
            if df is not None and not df.empty:
                source = "sina"
        except Exception as e:
            logger.warning(f"[大盘] 新浪指数接口失败: {e}")

        # EM 指数接口 fallback
        if df is None or df.empty:
            try:
                df = self._call_akshare_with_retry(ak.stock_zh_index_spot_em, "指数行情(EM)", attempts=2)
                if df is not None and not df.empty:
                    source = "em"
            except Exception as e:
                logger.warning(f"[大盘] EM指数接口失败: {e}")

        # 从批量接口提取指数数据
        if df is not None and not df.empty:
            for code, name in self.MAIN_INDICES.items():
                row = df[df['代码'] == code]
                if row.empty:
                    row = df[df['代码'].str.contains(code.replace('sh', '').replace('sz', ''))]

                if not row.empty:
                    row = row.iloc[0]
                    index = MarketIndex(
                        code=code,
                        name=name,
                        current=float(row.get('最新价', 0) or 0),
                        change=float(row.get('涨跌额', 0) or 0),
                        change_pct=float(row.get('涨跌幅', 0) or 0),
                        open=float(row.get('今开', 0) or 0),
                        high=float(row.get('最高', 0) or 0),
                        low=float(row.get('最低', 0) or 0),
                        prev_close=float(row.get('昨收', 0) or 0),
                        volume=float(row.get('成交量', 0) or 0),
                        amount=float(row.get('成交额', 0) or 0),
                    )
                    if index.prev_close > 0:
                        index.amplitude = (index.high - index.low) / index.prev_close * 100
                    indices.append(index)

            logger.info(f"[大盘] 获取到 {len(indices)} 个指数行情 (source={source})")
            return indices

        # 方法3: 雪球单股接口 fallback（批量接口都失败时）
        logger.warning("[大盘] 新浪/EM批量指数接口均失败，尝试雪球单股接口...")
        indices = self._get_indices_from_xueqiu()

        if indices:
            logger.info(f"[大盘] 获取到 {len(indices)} 个指数行情 (source=xueqiu)")
        else:
            logger.error("[大盘] 所有指数接口均失败")

        return indices

    def _get_xueqiu_session(self) -> Optional[requests.Session]:
        """获取已认证的雪球 Session（动态获取 token）"""
        session = requests.Session()
        headers = {
            'User-Agent': random.choice(_USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }

        try:
            # 步骤1：访问雪球主页获取初始 cookies
            r = session.get('https://xueqiu.com', headers=headers, timeout=10)
            if r.status_code != 200:
                logger.warning(f"[雪球] 访问主页失败: HTTP {r.status_code}")
                return None

            # 步骤2：检查是否有 xq_a_token（雪球会自动设置）
            if 'xq_a_token' not in session.cookies:
                # 尝试从 akshare 获取备用 token
                try:
                    from akshare.stock.cons import xq_a_token
                    session.cookies.set('xq_a_token', xq_a_token, domain='.xueqiu.com')
                except ImportError:
                    pass

            return session
        except Exception as e:
            logger.warning(f"[雪球] 初始化 session 失败: {e}")
            return None

    def _get_indices_from_xueqiu(self) -> List[MarketIndex]:
        """从雪球接口获取主要指数行情（fallback 方案）"""
        # 指数代码映射：内部代码 -> 雪球代码
        XQ_INDEX_MAP = {
            'sh000001': 'SH000001',  # 上证指数
            'sz399001': 'SZ399001',  # 深证成指
            'sz399006': 'SZ399006',  # 创业板指
            'sh000688': 'SH000688',  # 科创50
            'sh000016': 'SH000016',  # 上证50
            'sh000300': 'SH000300',  # 沪深300
        }

        indices = []

        session = self._get_xueqiu_session()
        if not session:
            return indices

        headers = {
            'User-Agent': random.choice(_USER_AGENTS),
            'Referer': 'https://xueqiu.com/',
        }

        for code, name in self.MAIN_INDICES.items():
            xq_symbol = XQ_INDEX_MAP.get(code)
            if not xq_symbol:
                continue

            try:
                url = f'https://stock.xueqiu.com/v5/stock/quote.json?symbol={xq_symbol}&extend=detail'
                r = session.get(url, headers=headers, timeout=10)

                if r.status_code != 200:
                    logger.warning(f"[雪球指数] {code} 请求失败: HTTP {r.status_code}")
                    continue

                data = r.json()
                quote = data.get('data', {}).get('quote', {})

                if not quote:
                    continue

                index = MarketIndex(
                    code=code,
                    name=name,
                    current=_safe_float(quote.get('current')),
                    change=_safe_float(quote.get('chg')),
                    change_pct=_safe_float(quote.get('percent')),
                    open=_safe_float(quote.get('open')),
                    high=_safe_float(quote.get('high')),
                    low=_safe_float(quote.get('low')),
                    prev_close=_safe_float(quote.get('last_close')),
                    volume=_safe_float(quote.get('volume')),
                    amount=_safe_float(quote.get('amount')),
                )

                if index.prev_close > 0:
                    index.amplitude = (index.high - index.low) / index.prev_close * 100

                indices.append(index)
                logger.debug(f"[雪球指数] {name}: {index.current} ({index.change_pct:+.2f}%)")

                # 请求间隔，避免被封
                time.sleep(0.3)

            except Exception as e:
                logger.warning(f"[雪球指数] {code} 获取失败: {e}")
                continue

        return indices

    def _get_indices_from_tencent(self) -> List[MarketIndex]:
        """从腾讯财经获取主要指数行情（fallback 方案）

        腾讯 API 优点：
        - 无需认证，稳定可靠
        - 数据字段完整（现价、涨跌幅、成交额等）
        """
        import re

        indices = []

        headers = {
            'User-Agent': random.choice(_USER_AGENTS),
            'Referer': 'https://gu.qq.com/',
        }

        for code, name in self.MAIN_INDICES.items():
            try:
                url = f'https://qt.gtimg.cn/q={code}'
                r = requests.get(url, headers=headers, timeout=10)

                if r.status_code != 200:
                    logger.warning(f"[腾讯指数] {code} 请求失败: HTTP {r.status_code}")
                    continue

                text = r.text
                match = re.search(r'v_[a-z]{2}\d+="([^"]+)"', text)
                if not match:
                    logger.warning(f"[腾讯指数] {code} 无法解析数据")
                    continue

                parts = match.group(1).split('~')
                if len(parts) < 50:
                    logger.warning(f"[腾讯指数] {code} 字段不足: {len(parts)}")
                    continue

                # 腾讯指数数据字段（与股票略有不同）
                # 1-名称, 3-现价, 4-昨收, 5-今开, 31-涨跌额, 32-涨跌幅
                # 33-最高, 34-最低, 36-成交量, 37-成交额
                index = MarketIndex(
                    code=code,
                    name=name,
                    current=_safe_float(parts[3]) if len(parts) > 3 else 0,
                    change=_safe_float(parts[31]) if len(parts) > 31 else 0,
                    change_pct=_safe_float(parts[32]) if len(parts) > 32 else 0,
                    open=_safe_float(parts[5]) if len(parts) > 5 else 0,
                    high=_safe_float(parts[33]) if len(parts) > 33 else 0,
                    low=_safe_float(parts[34]) if len(parts) > 34 else 0,
                    prev_close=_safe_float(parts[4]) if len(parts) > 4 else 0,
                    volume=_safe_float(parts[36]) if len(parts) > 36 else 0,
                    amount=_safe_float(parts[37]) if len(parts) > 37 else 0,
                )

                if index.prev_close > 0:
                    index.amplitude = (index.high - index.low) / index.prev_close * 100

                indices.append(index)
                logger.debug(f"[腾讯指数] {name}: {index.current} ({index.change_pct:+.2f}%)")

            except Exception as e:
                logger.warning(f"[腾讯指数] {code} 获取失败: {e}")
                continue

        return indices

    def _get_market_statistics_from_xueqiu(self) -> tuple:
        """从雪球获取 A 股市场统计数据（fallback 方案）

        通过雪球的股票列表 API 分页获取所有 A 股数据，
        用于计算涨跌统计。

        Returns:
            (DataFrame, source_label) 或 (None, None)
        """
        session = self._get_xueqiu_session()
        if not session:
            return None, None

        headers = {
            'User-Agent': random.choice(_USER_AGENTS),
            'Referer': 'https://xueqiu.com/',
        }

        all_stocks = []
        page = 1
        page_size = 90  # 雪球每页最大90条

        try:
            while True:
                # 雪球股票列表 API（按成交额排序获取所有A股）
                url = (
                    f"https://stock.xueqiu.com/v5/stock/screener/quote/list.json"
                    f"?page={page}&size={page_size}&order=desc&order_by=amount"
                    f"&market=CN&type=sh_sz"
                )

                r = session.get(url, headers=headers, timeout=15)
                if r.status_code != 200:
                    logger.warning(f"[雪球统计] 请求失败: HTTP {r.status_code}")
                    break

                data = r.json()
                stocks = data.get('data', {}).get('list', [])

                if not stocks:
                    break

                for s in stocks:
                    all_stocks.append({
                        '代码': s.get('symbol', '').replace('SH', '').replace('SZ', '').replace('BJ', ''),
                        '名称': s.get('name', ''),
                        '涨跌幅': s.get('percent', 0),
                        '成交额': s.get('amount', 0),
                    })

                # 检查是否还有更多数据
                total = data.get('data', {}).get('count', 0)
                if page * page_size >= total:
                    break

                page += 1
                time.sleep(0.3)  # 避免请求过快

                # 安全限制：最多获取100页
                if page > 100:
                    break

            if all_stocks:
                df = pd.DataFrame(all_stocks)
                logger.info(f"[雪球统计] 获取成功，共 {len(df)} 只股票")
                return df, "xueqiu"
            else:
                logger.warning("[雪球统计] 未获取到数据")
                return None, None

        except Exception as e:
            logger.error(f"[雪球统计] 获取失败: {e}")
            return None, None

    def _get_market_statistics_from_em_overview(self, overview: MarketOverview) -> bool:
        """从东方财富市场概览 API 获取涨跌统计（首选方案）

        优点：
        - 直接返回统计结果，无需获取全量股票数据
        - 响应快速，不易触发反爬
        - 数据准确（官方统计）

        Returns:
            True 表示获取成功，False 表示失败
        """
        try:
            url = 'https://push2.eastmoney.com/api/qt/ulist.np/get'
            params = {
                'fltt': '2',
                # f104=上涨家数, f105=下跌家数, f106=平盘家数
                # f107=涨停家数, f108=跌停家数, f6=成交额
                'fields': 'f6,f104,f105,f106,f107,f108,f12,f14',
                'secids': '1.000001,0.399001',  # 上证+深证
                'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
            }

            headers = {
                'User-Agent': random.choice(_USER_AGENTS),
                'Referer': 'https://quote.eastmoney.com/',
            }

            resp = requests.get(url, params=params, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"[大盘统计] EM概览API请求失败: HTTP {resp.status_code}")
                return False

            data = resp.json()
            if data.get('rc') != 0 or not data.get('data', {}).get('diff'):
                logger.warning(f"[大盘统计] EM概览API返回异常: {data}")
                return False

            # 合并上证+深证数据
            up_count = 0
            down_count = 0
            flat_count = 0
            limit_up = 0
            limit_down = 0
            total_amount = 0.0

            for item in data['data']['diff']:
                up_count += item.get('f104', 0) or 0
                down_count += item.get('f105', 0) or 0
                flat_count += item.get('f106', 0) or 0
                limit_up += item.get('f107', 0) or 0
                limit_down += item.get('f108', 0) or 0
                total_amount += item.get('f6', 0) or 0

            overview.up_count = up_count
            overview.down_count = down_count
            overview.flat_count = flat_count
            overview.limit_up_count = limit_up
            overview.limit_down_count = limit_down
            overview.total_amount = total_amount / 1e8  # 转为"亿"

            logger.info(
                f"[大盘] 涨:{overview.up_count} 跌:{overview.down_count} 平:{overview.flat_count} "
                f"涨停:{overview.limit_up_count} 跌停:{overview.limit_down_count} "
                f"成交额:{overview.total_amount:.0f}亿 (source=em_overview)"
            )
            return True

        except Exception as e:
            logger.warning(f"[大盘统计] EM概览API获取失败: {e}")
            return False

    def _get_market_statistics(self, overview: MarketOverview):
        """获取市场涨跌统计（EM概览优先 → 新浪 → EM批量 → 雪球）"""
        try:
            logger.info("[大盘] 获取市场涨跌统计...")

            # 方法1: 东方财富概览 API（首选，直接返回统计结果）
            if self._get_market_statistics_from_em_overview(overview):
                return

            logger.warning("[大盘] EM概览API失败，尝试批量接口...")

            # 方法2: 批量获取全量数据计算（fallback）
            sources = [
                ("stock_zh_a_spot", "sina"),          # 新浪
                ("stock_zh_a_spot_em", "em"),         # EM批量
            ]

            df, source = self._fetch_with_fallback(sources, "A股实时行情")

            # 如果新浪和 EM 都失败，尝试雪球
            if df is None or df.empty:
                logger.warning("[大盘] 新浪/EM 数据源均失败，尝试雪球接口...")
                df, source = self._get_market_statistics_from_xueqiu()

            if df is None or df.empty:
                logger.error("[大盘] 获取涨跌统计失败：所有行情源均不可用，保持默认 0")
                return

            # ---- 字段识别 ----
            # 涨跌幅列
            change_col = None
            for c in ("涨跌幅", "涨跌幅(%)", "涨幅", "涨幅(%)", "涨跌幅%"):
                if c in df.columns:
                    change_col = c
                    break

            # 成交额列
            amount_col = None
            for c in ("成交额", "成交额(元)", "总成交额", "成交额(亿元)", "成交额(亿)"):
                if c in df.columns:
                    amount_col = c
                    break

            if change_col is None:
                logger.warning(f"[大盘] 无法识别涨跌幅列(source={source})，cols={list(df.columns)[:30]}")
                return

            d = df.copy()
            d[change_col] = d[change_col].astype(str).str.replace("%", "", regex=False).str.replace(",", "",
                                                                                                    regex=False)
            d[change_col] = pd.to_numeric(d[change_col], errors="coerce")
            d = d.dropna(subset=[change_col])

            overview.up_count = int((d[change_col] > 0).sum())
            overview.down_count = int((d[change_col] < 0).sum())
            overview.flat_count = int((d[change_col] == 0).sum())

            # 粗略涨停/跌停（不区分 ST/科创等涨跌停板差异，后续可增强）
            overview.limit_up_count = int((d[change_col] >= 9.9).sum())
            overview.limit_down_count = int((d[change_col] <= -9.9).sum())

            if amount_col is not None:
                # 尝试解析单位：如果是纯数字，通常是"元"；如果带 万/亿 则转为元
                if d[amount_col].dtype.kind in ("i", "u", "f"):
                    total_amt = float(pd.to_numeric(d[amount_col], errors="coerce").sum())
                else:
                    total_amt = float(d[amount_col].apply(_parse_cn_unit_number).sum())
                overview.total_amount = total_amt / 1e8  # 转为"亿"
            else:
                overview.total_amount = 0.0

            logger.info(
                f"[大盘] 涨:{overview.up_count} 跌:{overview.down_count} 平:{overview.flat_count} "
                f"涨停:{overview.limit_up_count} 跌停:{overview.limit_down_count} "
                f"成交额:{overview.total_amount:.0f}亿 (source={source})"
            )

        except Exception as e:
            logger.error(f"[大盘] 获取涨跌统计失败: {e}")

    def _get_sector_rankings(self, overview: MarketOverview):
        """获取板块涨跌榜（THS优先，EM/新浪作为备选），并归一化字段供下游展示。"""
        logger.info("[大盘] 获取板块涨跌榜...")

        # 数据源优先级：THS汇总 > EM行业 > EM实时
        # 注意：stock_board_industry_name_ths 只返回 name/code，不含涨跌幅，已移除
        sources = [
            ("stock_board_industry_summary_ths", "ths"),    # THS汇总（数据最全）
            ("stock_board_industry_name_em", "em"),         # EM行业板块
            ("stock_board_industry_spot_em", "em"),         # EM实时板块（备选）
        ]

        df, source = self._fetch_with_fallback(sources, "行业板块行情")

        if df is None or df.empty:
            logger.error("[大盘] 行业板块行情 最终失败：所有数据源均不可用")
            return

        norm = normalize_sector_rankings(df, source=source, logger_instance=logger)
        if norm.empty:
            logger.error("[大盘] 行业板块行情 归一化后为空，跳过板块榜输出")
            return

        top = norm.sort_values("change_pct", ascending=False).head(5)
        bottom = norm.sort_values("change_pct", ascending=True).head(5)

        def row_to_dict(r):
            return {
                "name": r["sector_name"],
                "change_pct": float(r["change_pct"]) if r["change_pct"] == r["change_pct"] else None,
                "leader": r.get("leader"),
                "leader_change_pct": float(r["leader_change_pct"]) if r["leader_change_pct"] == r[
                    "leader_change_pct"] else None,
                "turnover_amt": float(r["turnover_amt"]) if r["turnover_amt"] == r["turnover_amt"] else None,
                "turnover_vol": float(r["turnover_vol"]) if r["turnover_vol"] == r["turnover_vol"] else None,
                "source": r.get("raw_source"),
            }

        overview.top_sectors = [row_to_dict(r) for _, r in top.iterrows()]
        overview.bottom_sectors = [row_to_dict(r) for _, r in bottom.iterrows()]

    def search_market_news(self) -> List[Dict]:
        """
        搜索市场新闻

        Returns:
            新闻列表
        """
        if not self.search_service:
            logger.warning("[大盘] 搜索服务未配置，跳过新闻搜索")
            return []

        all_news = []
        today = datetime.now()
        month_str = f"{today.year}年{today.month}月"

        # 多维度搜索
        search_queries = [
            f"A股 大盘 复盘 {month_str}",
            f"股市 行情 分析 今日 {month_str}",
            f"A股 市场 热点 板块 {month_str}",
        ]

        try:
            logger.info("[大盘] 开始搜索市场新闻...")

            for query in search_queries:
                # 使用 search_stock_news 方法，传入"大盘"作为股票名
                response = self.search_service.search_stock_news(
                    stock_code="market",
                    stock_name="大盘",
                    max_results=3,
                    focus_keywords=query.split()
                )
                if response and response.results:
                    all_news.extend(response.results)
                    logger.info(f"[大盘] 搜索 '{query}' 获取 {len(response.results)} 条结果")

            logger.info(f"[大盘] 共获取 {len(all_news)} 条市场新闻")

        except Exception as e:
            logger.error(f"[大盘] 搜索市场新闻失败: {e}")

        return all_news

    def generate_market_review(self, overview: MarketOverview, news: List) -> str:
        """
        使用大模型生成大盘复盘报告

        Args:
            overview: 市场概览数据
            news: 市场新闻列表 (SearchResult 对象列表)

        Returns:
            大盘复盘报告文本
        """
        if not self.analyzer or not self.analyzer.is_available():
            logger.warning("[大盘] AI分析器未配置或不可用，使用模板生成报告")
            return self._generate_template_review(overview, news)

        # 构建 Prompt
        prompt = self._build_review_prompt(overview, news)

        try:
            logger.info("[大盘] 调用大模型生成复盘报告...")

            generation_config = {
                'temperature': 0.7,
                'max_output_tokens': 2048,
            }

            # 根据 analyzer 使用的 API 类型调用
            if self.analyzer._use_openai:
                # 使用 OpenAI 兼容 API
                review = self.analyzer._call_openai_api(prompt, generation_config)
            else:
                # 使用 Gemini API
                response = self.analyzer._model.generate_content(
                    prompt,
                    generation_config=generation_config,
                )
                review = response.text.strip() if response and response.text else None

            if review:
                logger.info(f"[大盘] 复盘报告生成成功，长度: {len(review)} 字符")
                return review
            else:
                logger.warning("[大盘] 大模型返回为空")
                return self._generate_template_review(overview, news)

        except Exception as e:
            logger.error(f"[大盘] 大模型生成复盘报告失败: {e}")
            return self._generate_template_review(overview, news)

    def _build_review_prompt(self, overview: MarketOverview, news: List) -> str:
        """构建复盘报告 Prompt"""
        # 指数行情信息（简洁格式，不用emoji）
        indices_text = ""
        for idx in overview.indices:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            indices_text += f"- {idx.name}: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%)\n"

        # 板块信息
        top_sectors_text = ", ".join([f"{s['name']}({s['change_pct']:+.2f}%)" for s in overview.top_sectors[:3]])
        bottom_sectors_text = ", ".join([f"{s['name']}({s['change_pct']:+.2f}%)" for s in overview.bottom_sectors[:3]])

        # 新闻信息 - 支持 SearchResult 对象或字典
        news_text = ""
        for i, n in enumerate(news[:6], 1):
            # 兼容 SearchResult 对象和字典
            if hasattr(n, 'title'):
                title = n.title[:50] if n.title else ''
                snippet = n.snippet[:100] if n.snippet else ''
            else:
                title = n.get('title', '')[:50]
                snippet = n.get('snippet', '')[:100]
            news_text += f"{i}. {title}\n   {snippet}\n"

        prompt = f"""你是一位专业的A股市场分析师，请根据以下数据生成一份简洁的大盘复盘报告。

【重要】输出要求：
- 必须输出纯 Markdown 文本格式
- 禁止输出 JSON 格式
- 禁止输出代码块
- emoji 仅在标题处少量使用（每个标题最多1个）

---

# 今日市场数据

## 日期
{overview.date}

## 主要指数
{indices_text}

## 市场概况
- 上涨: {overview.up_count} 家 | 下跌: {overview.down_count} 家 | 平盘: {overview.flat_count} 家
- 涨停: {overview.limit_up_count} 家 | 跌停: {overview.limit_down_count} 家
- 两市成交额: {overview.total_amount:.0f} 亿元
- 北向资金: {overview.north_flow:+.2f} 亿元

## 板块表现
领涨: {top_sectors_text}
领跌: {bottom_sectors_text}

## 市场新闻
{news_text if news_text else "暂无相关新闻"}

---

# 输出格式模板（请严格按此格式输出）

## 📊 {overview.date} 大盘复盘

### 一、市场总结
（2-3句话概括今日市场整体表现，包括指数涨跌、成交量变化）

### 二、指数点评
（分析上证、深证、创业板等各指数走势特点）

### 三、资金动向
（解读成交额和北向资金流向的含义）

### 四、热点解读
（分析领涨领跌板块背后的逻辑和驱动因素）

### 五、后市展望
（结合当前走势和新闻，给出明日市场预判）

### 六、风险提示
（需要关注的风险点）

---

请直接输出复盘报告内容，不要输出其他说明文字。
"""
        return prompt

    def _generate_template_review(self, overview: MarketOverview, news: List) -> str:
        """使用模板生成复盘报告（无大模型时的备选方案）"""

        # 判断市场走势
        sh_index = next((idx for idx in overview.indices if idx.code == '000001'), None)
        if sh_index:
            if sh_index.change_pct > 1:
                market_mood = "强势上涨"
            elif sh_index.change_pct > 0:
                market_mood = "小幅上涨"
            elif sh_index.change_pct > -1:
                market_mood = "小幅下跌"
            else:
                market_mood = "明显下跌"
        else:
            market_mood = "震荡整理"

        # 指数行情（简洁格式）
        indices_text = ""
        for idx in overview.indices[:4]:
            direction = "↑" if idx.change_pct > 0 else "↓" if idx.change_pct < 0 else "-"
            indices_text += f"- **{idx.name}**: {idx.current:.2f} ({direction}{abs(idx.change_pct):.2f}%)\n"

        # 板块信息
        top_text = format_sector_list(getattr(overview, 'top_sectors', None) or [])
        bottom_text = format_sector_list(getattr(overview, 'bottom_sectors', None) or [])

        report = f"""## 📊 {overview.date} 大盘复盘

### 一、市场总结
今日A股市场整体呈现**{market_mood}**态势。

### 二、主要指数
{indices_text}

### 三、涨跌统计
| 指标 | 数值 |
|------|------|
| 上涨家数 | {overview.up_count} |
| 下跌家数 | {overview.down_count} |
| 涨停 | {overview.limit_up_count} |
| 跌停 | {overview.limit_down_count} |
| 两市成交额 | {overview.total_amount:.0f}亿 |
| 北向资金 | {overview.north_flow:+.2f}亿 |

### 四、板块表现
- **领涨**: {top_text}
- **领跌**: {bottom_text}

### 五、风险提示
市场有风险，投资需谨慎。以上数据仅供参考，不构成投资建议。

---
*复盘时间: {datetime.now().strftime('%H:%M')}*
"""
        return report

    def run_daily_review(self) -> str:
        """
        执行每日大盘复盘流程

        Returns:
            复盘报告文本
        """
        logger.info("========== 开始大盘复盘分析 ==========")

        # 1. 获取市场概览
        overview = self.get_market_overview()

        # 2. 搜索市场新闻
        news = self.search_market_news()

        # 3. 生成复盘报告
        report = self.generate_market_review(overview, news)

        logger.info("========== 大盘复盘分析完成 ==========")

        return report


# =========================
# Test Entry
# =========================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    )

    analyzer = MarketAnalyzer()

    # 测试获取市场概览
    overview = analyzer.get_market_overview()
    print(f"\n=== 市场概览 ===")
    print(f"日期: {overview.date}")
    print(f"指数数量: {len(overview.indices)}")
    for idx in overview.indices:
        print(f"  {idx.name}: {idx.current:.2f} ({idx.change_pct:+.2f}%)")
    print(f"上涨: {overview.up_count} | 下跌: {overview.down_count}")
    print(f"成交额: {overview.total_amount:.0f}亿")

    # 测试生成模板报告
    report = analyzer._generate_template_review(overview, [])
    print(f"\n=== 复盘报告 ===")
    print(report)
