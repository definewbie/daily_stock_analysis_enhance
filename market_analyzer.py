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
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from http.client import RemoteDisconnected
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

# Third-party
import akshare as ak
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Local
from config import get_config
from search_service import SearchService

# =========================
# Logger
# =========================

logger = logging.getLogger(__name__)

# =========================
# Constants
# =========================

_AK_PATCHED_V2 = False
_GLOBAL_SESSION = None

_EASTMONEY_HOST_POOL = [
    "push2.eastmoney.com",
    "80.push2.eastmoney.com",
    "81.push2.eastmoney.com",
    "82.push2.eastmoney.com",
    "72.push2.eastmoney.com",
]

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]


# =========================
# Utility Functions
# =========================

def _parse_cn_unit_number(x):
    """解析 '123.4亿'/'56万'/'7.8' 等字符串为 float（以元为单位的通用数值）。
    - 如果没有单位，直接按数值返回
    - '万' => *1e4, '亿' => *1e8
    """
    if x is None:
        return np.nan
    if isinstance(x, (int, float)) and not (isinstance(x, float) and pd.isna(x)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if s == "" or s.lower() == "nan":
        return np.nan
    m = re.match(r"^(-?\d+(?:\.\d+)?)([万亿])?$", s)
    if not m:
        return np.nan
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "万":
        return num * 1e4
    if unit == "亿":
        return num * 1e8
    return num


def _safe_float(val, default=0.0):
    """安全转换为浮点数"""
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def normalize_sector_rankings(df, source: str, logger=None):
    """将不同数据源(EM/THS)的行业/板块排行 DataFrame 归一化为统一字段。"""
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame(columns=[
            "sector_name", "change_pct", "leader", "leader_change_pct", "turnover_amt", "turnover_vol", "raw_source"
        ])

    d = df.copy()

    def pick(cols):
        return next((c for c in cols if c in d.columns), None)

    def to_float(s):
        if s is None:
            return pd.Series([np.nan] * len(d))
        s = s.astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False)
        return pd.to_numeric(s, errors="coerce")

    if source == "em":
        name_col = pick(["板块名称", "行业名称", "名称", "板块"])
        change_col = pick(["涨跌幅", "涨跌幅(%)", "涨幅", "涨跌幅%"])
        leader_col = pick(["领涨股", "领涨股票", "领涨股名称"])
        leader_chg_col = pick(["领涨股-涨跌幅", "领涨股涨跌幅", "领涨股涨幅"])
        amt_col = pick(["总成交额", "成交额", "成交额(元)", "成交额(亿元)", "成交额(亿)"])
        vol_col = pick(["总成交量", "成交量", "成交量(手)", "成交量(股)"])
    else:  # ths
        # stock_board_industry_summary_ths 返回: 序号,板块,涨跌幅,总成交量,总成交额,...,领涨股,领涨股-涨跌幅
        name_col = pick(["板块", "行业", "行业名称", "板块名称"])
        change_col = pick(["涨跌幅", "涨跌幅(%)", "涨幅", "涨幅(%)"])
        leader_col = pick(["领涨股", "领涨股名称", "领涨股/代码", "领涨股(代码)", "领涨股(股票)"])
        leader_chg_col = pick(["领涨股-涨跌幅", "领涨股涨跌幅", "领涨股涨幅", "领涨股(%)"])
        amt_col = pick(["总成交额", "成交额", "成交额(亿)", "成交额(亿元)"])
        vol_col = pick(["总成交量", "成交量", "成交量(万手)", "成交量(手)"])

    out = pd.DataFrame()
    out["sector_name"] = d[name_col].astype(str) if name_col else None
    out["change_pct"] = to_float(d[change_col]) if change_col else pd.Series([np.nan] * len(d))
    out["leader"] = d[leader_col].astype(str) if leader_col else None
    out["leader_change_pct"] = to_float(d[leader_chg_col]) if leader_chg_col else pd.Series([np.nan] * len(d))

    # 成交额/成交量单位处理：THS 返回的已是"亿/万手"，需转换为标准单位"元/手"
    if amt_col:
        raw_amt = d[amt_col].apply(_parse_cn_unit_number)
        # THS 的 stock_board_industry_summary_ths 返回的成交额已是"亿"为单位
        if source == "ths" and raw_amt.max() < 1e6:  # 如果最大值 < 100万，说明单位是亿
            out["turnover_amt"] = raw_amt * 1e8
        else:
            out["turnover_amt"] = raw_amt
    else:
        out["turnover_amt"] = np.nan

    if vol_col:
        raw_vol = d[vol_col].apply(_parse_cn_unit_number)
        # THS 的成交量是"万手"为单位
        if source == "ths" and raw_vol.max() < 1e6:  # 如果最大值 < 100万，说明单位是万手
            out["turnover_vol"] = raw_vol * 1e4
        else:
            out["turnover_vol"] = raw_vol
    else:
        out["turnover_vol"] = np.nan

    out["raw_source"] = source

    out = out.dropna(subset=["sector_name"]).reset_index(drop=True)

    if logger:
        missing = []
        if name_col is None:
            missing.append("name")
        if change_col is None:
            missing.append("change")
        if missing:
            logger.warning(f"[大盘] 行业板块字段不完整(source={source}) missing={missing}, cols={list(d.columns)[:30]}")
    return out


def format_sector_list(sectors):
    """将板块列表格式化为展示字符串（含涨跌幅、领涨股、成交额）。"""
    if not sectors:
        return ""
    parts = []
    for s in sectors:
        name = s.get("name")
        chg = s.get("change_pct")
        leader = s.get("leader")
        leader_chg = s.get("leader_change_pct")
        amt = s.get("turnover_amt")
        amt_str = None
        if isinstance(amt, (int, float)) and amt is not None:
            try:
                amt_str = f"{amt / 1e8:.1f}亿"
            except Exception:
                amt_str = None

        seg = str(name)
        if chg is not None:
            seg += f"({chg:+.2f}%)"
        if leader and leader != "None":
            if leader_chg is not None:
                seg += f"·{leader}({leader_chg:+.2f}%)"
            else:
                seg += f"·{leader}"
        if amt_str:
            seg += f"·{amt_str}"
        parts.append(seg)
    return "、".join(parts)


# =========================
# Network Patch for AkShare/Eastmoney
# =========================
# 目标：
# 1) 强制连接复用：将 requests.api.request 从"每次新建 Session"改为"全局单例 Session"
# 2) 降低被断连概率：对 eastmoney/push2 请求做轻量限速 + 子域名轮换 + 失败退避

def _replace_host(url: str, new_host: str) -> str:
    """替换 URL 中的 host"""
    u = urlparse(url)
    return urlunparse((u.scheme, new_host, u.path, u.params, u.query, u.fragment))


def install_requests_patch_for_akshare_v2():
    """为 AkShare/requests 安装更稳健的网络补丁（连接复用 + retry + headers + 轻量限速 + host 轮换）。"""
    global _AK_PATCHED_V2, _GLOBAL_SESSION
    if _AK_PATCHED_V2:
        return

    # 1) 创建全局 Session（避免 requests.api.request 每次 new Session 造成 TLS 握手风控 + 无法复用连接池）
    _GLOBAL_SESSION = requests.Session()
    _GLOBAL_SESSION.trust_env = False  # 不读取环境变量代理(HTTP(S)_PROXY)，避免 CONNECT 代理导致断连

    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    _GLOBAL_SESSION.mount("http://", adapter)
    _GLOBAL_SESSION.mount("https://", adapter)

    # 2) Patch requests.api.request：把"with Session() as s"替换为"使用全局 session"
    import requests.api as _api
    _orig_api_request = _api.request

    def _api_request_patched(method, url, **kwargs):
        return _GLOBAL_SESSION.request(method=method, url=url, **kwargs)

    _api.request = _api_request_patched
    requests.request = _api_request_patched  # 兼容直接 requests.request()

    # 3) Patch Session.request：统一 headers/timeout；对 eastmoney/push2 做轻量限速 + host 轮换
    _orig_request = requests.Session.request

    def _patched_request(self, method, url, *args, **kwargs):
        if isinstance(url, str) and ("eastmoney.com" in url):
            # 轻量限速：避免连续大批量请求触发风控（经验值 200~600ms）
            time.sleep(random.uniform(0.25, 0.65))

            # 禁用代理
            kwargs.setdefault("proxies", {})

            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("User-Agent", random.choice(_USER_AGENTS))
            headers.setdefault("Referer", "https://quote.eastmoney.com/")
            headers.setdefault("Accept", "*/*")
            headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
            headers.setdefault("Connection", "keep-alive")
            kwargs["headers"] = headers

            kwargs.setdefault("timeout", (3.05, 20))

            # 子域名轮换：针对 push2 的不稳定/封禁
            if "push2.eastmoney.com" in url:
                last_exc = None
                for host in _EASTMONEY_HOST_POOL:
                    try_url = _replace_host(url, host)
                    try:
                        return _orig_request(self, method, try_url, *args, **kwargs)
                    except (RemoteDisconnected,
                            requests.exceptions.ConnectionError,
                            requests.exceptions.ChunkedEncodingError,
                            requests.exceptions.ReadTimeout) as e:
                        last_exc = e
                        # 更长退避：遇到 reply '' 这种"直接断"时，短退避几乎无效
                        time.sleep(random.uniform(1.2, 2.8))
                        continue
                # host 池都失败，抛出最后一个异常给上层重试逻辑
                raise last_exc

        return _orig_request(self, method, url, *args, **kwargs)

    requests.Session.request = _patched_request
    _AK_PATCHED_V2 = True


# =========================
# Data Classes
# =========================

@dataclass
class MarketIndex:
    """大盘指数数据"""
    code: str  # 指数代码
    name: str  # 指数名称
    current: float = 0.0  # 当前点位
    change: float = 0.0  # 涨跌点数
    change_pct: float = 0.0  # 涨跌幅(%)
    open: float = 0.0  # 开盘点位
    high: float = 0.0  # 最高点位
    low: float = 0.0  # 最低点位
    prev_close: float = 0.0  # 昨收点位
    volume: float = 0.0  # 成交量（手）
    amount: float = 0.0  # 成交额（元）
    amplitude: float = 0.0  # 振幅(%)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'name': self.name,
            'current': self.current,
            'change': self.change,
            'change_pct': self.change_pct,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'volume': self.volume,
            'amount': self.amount,
            'amplitude': self.amplitude,
        }


@dataclass
class MarketOverview:
    """市场概览数据"""
    date: str  # 日期
    indices: List[MarketIndex] = field(default_factory=list)  # 主要指数
    up_count: int = 0  # 上涨家数
    down_count: int = 0  # 下跌家数
    flat_count: int = 0  # 平盘家数
    limit_up_count: int = 0  # 涨停家数
    limit_down_count: int = 0  # 跌停家数
    total_amount: float = 0.0  # 两市成交额（亿元）
    north_flow: float = 0.0  # 北向资金净流入（亿元）

    # 板块涨幅榜
    top_sectors: List[Dict] = field(default_factory=list)  # 涨幅前5板块
    bottom_sectors: List[Dict] = field(default_factory=list)  # 跌幅前5板块


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

    def _get_market_statistics(self, overview: MarketOverview):
        """获取市场涨跌统计（新浪优先，EM备选，雪球兜底）"""
        try:
            logger.info("[大盘] 获取市场涨跌统计...")

            # 数据源优先级：新浪 > EM > 雪球（新浪更稳定，EM容易被反爬block，雪球作为兜底）
            sources = [
                ("stock_zh_a_spot", "sina"),          # 新浪优先（更稳定）
                ("stock_zh_a_spot_em", "em"),         # EM作为备选
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

        norm = normalize_sector_rankings(df, source=source, logger=logger)
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
