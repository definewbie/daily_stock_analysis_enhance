# -*- coding: utf-8 -*-

# =========================
# Eastmoney/AkShare 网络稳健性补丁（v2）
# 目标：
# 1) 强制可观测：urllib3/requests DEBUG 可见（不依赖项目 logger）
# 2) 强制连接复用：将 requests.api.request 从“每次新建 Session”改为“全局单例 Session”
# 3) 降低被断连概率：对 eastmoney/push2 请求做轻量限速 + 子域名轮换 + 失败退避
# 通过环境变量控制：
#   AK_DEBUG=1        开启 urllib3/requests debug
#   AK_PATCH_TRACE=1  打印 patch 命中探针
# =========================
import os as _os
import sys as _sys
import time as _time
import random as _random
import logging as _logging
import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter
from urllib3.util.retry import Retry as _Retry
from http.client import RemoteDisconnected as _RemoteDisconnected

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


def enable_urllib3_debug_force():
    """强制让 urllib3/requests 的 DEBUG 输出可见，绕过 loguru/自定义 logger 吞日志问题。"""
    if _os.getenv("AK_DEBUG", "0") != "1":
        return
    import http.client as http_client
    http_client.HTTPConnection.debuglevel = 1

    h = _logging.StreamHandler(_sys.stderr)
    h.setLevel(_logging.DEBUG)
    fmt = _logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    h.setFormatter(fmt)

    for name in ("urllib3", "requests"):
        lg = _logging.getLogger(name)
        lg.handlers = [h]
        lg.setLevel(_logging.DEBUG)
        lg.propagate = False


def _replace_host(url: str, new_host: str) -> str:
    from urllib.parse import urlparse, urlunparse
    u = urlparse(url)
    return urlunparse((u.scheme, new_host, u.path, u.params, u.query, u.fragment))


def install_requests_patch_for_akshare_v2():
    """为 AkShare/requests 安装更稳健的网络补丁（连接复用 + retry + headers + 轻量限速 + host 轮换）。"""
    global _AK_PATCHED_V2, _GLOBAL_SESSION
    if _AK_PATCHED_V2:
        return

    # 0) 可观测性：按需打开
    enable_urllib3_debug_force()

    # 1) 创建全局 Session（关键：避免 requests.api.request 每次 new Session 造成 TLS 握手风控 + 无法复用连接池）
    _GLOBAL_SESSION = _requests.Session()
    _GLOBAL_SESSION.trust_env = False  # 不读取环境变量代理(HTTP(S)_PROXY)，避免 CONNECT 代理导致断连

    retry = _Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = _HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    _GLOBAL_SESSION.mount("http://", adapter)
    _GLOBAL_SESSION.mount("https://", adapter)

    # 2) Patch requests.api.request：把“with Session() as s”替换为“使用全局 session”
    import requests.api as _api
    _orig_api_request = _api.request

    def _api_request_patched(method, url, **kwargs):
        # 这里不能用 with，否则又关闭 session/连接池
        return _GLOBAL_SESSION.request(method=method, url=url, **kwargs)

    _api.request = _api_request_patched
    _requests.request = _api_request_patched  # 兼容直接 requests.request()

    # 3) Patch Session.request：统一 headers/timeout；对 eastmoney/push2 做轻量限速 + host 轮换
    _orig_request = _requests.Session.request

    def _patched_request(self, method, url, *args, **kwargs):
        trace = (_os.getenv("AK_PATCH_TRACE", "0") == "1")
        if isinstance(url, str) and ("eastmoney.com" in url):
            # 轻量限速：避免连续大批量请求触发风控（经验值 200~600ms）
            _time.sleep(_random.uniform(0.25, 0.65))

            # 禁用代理（requests 默认会读取 HTTP(S)_PROXY / NO_PROXY 环境变量）
            kwargs.setdefault("proxies", {})

            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("User-Agent", _random.choice(_USER_AGENTS))
            headers.setdefault("Referer", "https://quote.eastmoney.com/")
            headers.setdefault("Accept", "*/*")
            headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
            headers.setdefault("Connection", "keep-alive")
            kwargs["headers"] = headers

            kwargs.setdefault("timeout", (3.05, 20))
            # 关键：禁用环境变量代理，避免走 CONNECT 代理导致 RemoteDisconnected
            kwargs.setdefault("proxies", {})

            # 子域名轮换：针对 push2 的不稳定/封禁（特别是高频脚本）
            if "push2.eastmoney.com" in url:
                last_exc = None
                for host in _EASTMONEY_HOST_POOL:
                    try_url = _replace_host(url, host)
                    if trace:
                        print(f"[AK PATCH HIT] {method} {try_url}", file=_sys.stderr)
                    try:
                        return _orig_request(self, method, try_url, *args, **kwargs)
                    except (_RemoteDisconnected,
                            _requests.exceptions.ConnectionError,
                            _requests.exceptions.ChunkedEncodingError,
                            _requests.exceptions.ReadTimeout) as e:
                        last_exc = e
                        # 更长退避：遇到 reply '' 这种“直接断”时，短退避几乎无效
                        _time.sleep(_random.uniform(1.2, 2.8))
                        continue
                # host 池都失败，抛出最后一个异常给上层重试逻辑
                raise last_exc

            if trace:
                print(f"[AK PATCH HIT] {method} {url}", file=_sys.stderr)

        return _orig_request(self, method, url, *args, **kwargs)

    _requests.Session.request = _patched_request
    _AK_PATCHED_V2 = True


def _parse_cn_unit_number(x):
    """解析 '123.4亿'/'56万'/'7.8' 等字符串为 float（以元为单位的通用数值）。
    - 如果没有单位，直接按数值返回
    - '万' => *1e4, '亿' => *1e8
    """
    import numpy as np
    import pandas as pd
    import re
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


def normalize_sector_rankings(df, source: str, logger=None):
    """将不同数据源(EM/THS)的行业/板块排行 DataFrame 归一化为统一字段。"""
    import pandas as pd
    import numpy as np

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
        name_col = pick(["行业", "行业名称", "板块", "板块名称"])
        change_col = pick(["涨跌幅", "涨跌幅(%)", "涨幅", "涨幅(%)"])
        leader_col = pick(["领涨股", "领涨股名称", "领涨股/代码", "领涨股(代码)", "领涨股(股票)"])
        leader_chg_col = pick(["领涨股涨跌幅", "领涨股-涨跌幅", "领涨股涨幅", "领涨股(%)"])
        amt_col = pick(["成交额", "总成交额", "成交额(亿)", "成交额(亿元)"])
        vol_col = pick(["成交量", "总成交量", "成交量(万手)", "成交量(手)"])

    out = pd.DataFrame()
    out["sector_name"] = d[name_col].astype(str) if name_col else None
    out["change_pct"] = to_float(d[change_col]) if change_col else pd.Series([np.nan] * len(d))
    out["leader"] = d[leader_col].astype(str) if leader_col else None
    out["leader_change_pct"] = to_float(d[leader_chg_col]) if leader_chg_col else pd.Series([np.nan] * len(d))

    out["turnover_amt"] = d[amt_col].apply(_parse_cn_unit_number) if amt_col else np.nan
    out["turnover_vol"] = d[vol_col].apply(_parse_cn_unit_number) if vol_col else np.nan
    out["raw_source"] = source

    out = out.dropna(subset=["sector_name"]).reset_index(drop=True)

    if logger:
        missing = []
        if name_col is None: missing.append("name")
        if change_col is None: missing.append("change")
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


"""
===================================
大盘复盘分析模块
===================================

职责：
1. 获取大盘指数数据（上证、深证、创业板）
2. 搜索市场新闻形成复盘情报
3. 使用大模型生成每日大盘复盘报告
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

import akshare as ak
import pandas as pd

from config import get_config
from search_service import SearchService

logger = logging.getLogger(__name__)


def enable_urllib3_debug_force() -> None:
    """强制开启 requests/urllib3 的 DEBUG 输出（绕过 loguru 等日志封装）。

    使用方法：设置环境变量 AK_DEBUG=1 后运行。
    """
    import os
    if os.getenv("AK_DEBUG", "").strip() not in {"1", "true", "True", "YES", "yes"}:
        return

    import sys
    import logging
    import http.client as http_client

    http_client.HTTPConnection.debuglevel = 1

    h = logging.StreamHandler(sys.stderr)
    h.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    h.setFormatter(fmt)

    for name in ("urllib3", "requests"):
        lg = logging.getLogger(name)
        lg.handlers = [h]
        lg.setLevel(logging.DEBUG)
        lg.propagate = False


# ======= Eastmoney/AkShare requests 兼容补丁（建议开启）=======
import random
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from http.client import RemoteDisconnected

_EASTMONEY_PATCHED = False
USER_AGENTS = [
    # 你也可以扩充；关键是不要用 python-requests 默认 UA
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]


def install_requests_patch_for_akshare_v2():
    global _EASTMONEY_PATCHED
    if _EASTMONEY_PATCHED:
        return

    # 1) 给所有 requests 请求挂上 retry/连接池（AkShare 内部用 requests.get 也会走到这里）
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)

    _orig_session_init = requests.Session.__init__

    def _session_init(self, *args, **kwargs):
        _orig_session_init(self, *args, **kwargs)
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    requests.Session.__init__ = _session_init

    # 2) patch request：补 headers、timeout，并可选修正某些 host
    _orig_request = requests.Session.request

    def _patched_request(self, method, url, *args, **kwargs):
        # 强制调试（仅在 AK_DEBUG=1 时启用）
        enable_urllib3_debug_force()

        # headers / timeout 统一注入
        if isinstance(url, str):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("User-Agent", random.choice(USER_AGENTS))
            # Eastmoney 对 Referer/Accept 更敏感，尽量模拟浏览器
            headers.setdefault("Referer", "https://quote.eastmoney.com/")
            headers.setdefault("Accept", "*/*")
            headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
            headers.setdefault("Connection", "keep-alive")
            kwargs["headers"] = headers

        # timeout：避免默认无限等待
        kwargs.setdefault("timeout", (3.05, 20))

        # 可观测性探针：确认 patch 是否命中（AK_PATCH_TRACE=1）
        import os, sys
        if isinstance(url, str) and os.getenv("AK_PATCH_TRACE", "").strip() in {"1", "true", "True"}:
            if "eastmoney.com" in url or "push2" in url:
                print(f"[AK PATCH HIT] {method} {url}", file=sys.stderr)

        # Eastmoney 子域名可能波动或被风控：做 host 池轮换（命中 Eastmoney 才做）
        if isinstance(url, str) and ("push2.eastmoney.com" in url or ".push2.eastmoney.com" in url):
            from urllib.parse import urlparse, urlunparse

            def _replace_host(_url: str, new_host: str) -> str:
                u = urlparse(_url)
                return urlunparse((u.scheme, new_host, u.path, u.params, u.query, u.fragment))

            host_pool = [
                "push2.eastmoney.com",
                "80.push2.eastmoney.com",
                "81.push2.eastmoney.com",
                "82.push2.eastmoney.com",
                "72.push2.eastmoney.com",
            ]

            last_exc = None
            for host in host_pool:
                try_url = _replace_host(url, host)
                try:
                    return _orig_request(self, method, try_url, *args, **kwargs)
                except (RemoteDisconnected, requests.exceptions.ConnectionError,
                        requests.exceptions.ChunkedEncodingError,
                        requests.exceptions.ReadTimeout) as e:
                    last_exc = e
                    continue
            # host 池全部失败，抛出最后异常
            raise last_exc

        return _orig_request(self, method, url, *args, **kwargs)

    requests.Session.request = _patched_request
    _EASTMONEY_PATCHED = True


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
        enable_urllib3_debug_force()

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

    # def _call_akshare_with_retry(self, fn, name: str, attempts: int = 2):
    #     last_error: Optional[Exception] = None
    #     for attempt in range(1, attempts + 1):
    #         try:
    #             return fn()
    #         except Exception as e:
    #             last_error = e
    #             logger.warning(f"[大盘] {name} 获取失败 (attempt {attempt}/{attempts}): {e}")
    #             if attempt < attempts:
    #                 time.sleep(min(2 ** attempt, 5))
    #     logger.error(f"[大盘] {name} 最终失败: {last_error}")
    #     return None
    def _call_akshare_with_retry(self, fn, name: str, attempts: int = 3):
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return fn()
            except (RemoteDisconnected, requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ReadTimeout) as e:
                last_error = e
                logger.warning(f"[大盘] {name} 获取失败 (attempt {attempt}/{attempts}): {e}")
                if attempt < attempts:
                    backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 1.2)
                    time.sleep(backoff)
            except Exception as e:
                # 非网络类异常直接抛出更利于定位
                logger.exception(f"[大盘] {name} 非网络异常: {e}")
                raise
        logger.error(f"[大盘] {name} 最终失败: {last_error}")
        return None

    def _get_main_indices(self) -> List[MarketIndex]:
        """获取主要指数实时行情"""
        indices = []

        try:
            logger.info("[大盘] 获取主要指数实时行情...")

            # 使用 akshare 获取指数行情（新浪财经接口，包含深市指数）
            df = self._call_akshare_with_retry(ak.stock_zh_index_spot_sina, "指数行情", attempts=2)

            if df is not None and not df.empty:
                for code, name in self.MAIN_INDICES.items():
                    # 查找对应指数
                    row = df[df['代码'] == code]
                    if row.empty:
                        # 尝试带前缀查找
                        row = df[df['代码'].str.contains(code)]

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
                        # 计算振幅
                        if index.prev_close > 0:
                            index.amplitude = (index.high - index.low) / index.prev_close * 100
                        indices.append(index)

                logger.info(f"[大盘] 获取到 {len(indices)} 个指数行情")

        except Exception as e:
            logger.error(f"[大盘] 获取指数行情失败: {e}")

        return indices

    def _get_market_statistics(self, overview: MarketOverview):
        """获取市场涨跌统计（EM 优先，失败回退其他源）"""
        try:
            logger.info("[大盘] 获取市场涨跌统计...")

            df = None
            source = None

            # 1) EM 优先
            try:
                df = self._call_akshare_with_retry(ak.stock_zh_a_spot_em, "A股实时行情(EM)", attempts=2)
                if df is not None and not df.empty:
                    source = "em"
            except Exception as e:
                logger.warning(f"[大盘] A股实时行情(EM) 异常，将回退: {e}")
                df = None

            # 2) 回退：新浪等（不同版本函数名可能不同，这里尽量兼容）
            if df is None or getattr(df, "empty", True):
                alt_fn = None
                for cand in ("stock_zh_a_spot", "stock_zh_a_spot_sina"):
                    if hasattr(ak, cand):
                        alt_fn = getattr(ak, cand)
                        break
                if alt_fn is not None:
                    try:
                        df = self._call_akshare_with_retry(alt_fn, "A股实时行情(ALT)", attempts=2)
                        if df is not None and not df.empty:
                            source = cand
                    except Exception as e:
                        logger.warning(f"[大盘] A股实时行情(ALT) 也失败: {e}")
                        df = None

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
                # 尝试解析单位：如果是纯数字，通常是“元”；如果带 万/亿 则转为元
                if d[amount_col].dtype.kind in ("i", "u", "f"):
                    total_amt = float(pd.to_numeric(d[amount_col], errors="coerce").sum())
                else:
                    total_amt = float(d[amount_col].apply(_parse_cn_unit_number).sum())
                overview.total_amount = total_amt / 1e8  # 转为“亿”
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
        """获取板块涨跌榜（EM 优先，失败自动回退 THS），并归一化字段供下游展示。"""
        logger.info("[大盘] 获取板块涨跌榜...")

        df = None
        source = None

        # 1) EM 优先
        try:
            df = self._call_akshare_with_retry(ak.stock_board_industry_name_em, "行业板块行情(EM)", attempts=2)
            if df is not None and not df.empty:
                source = "em"
        except Exception as e:
            logger.warning(f"[大盘] 行业板块行情(EM) 异常，将回退 THS: {e}")
            df = None

        # 2) THS 回退（更稳）
        if df is None or getattr(df, "empty", True):
            ths_fn = None
            for cand in ("stock_board_industry_summary_ths", "stock_board_industry_name_ths"):
                if hasattr(ak, cand):
                    ths_fn = getattr(ak, cand)
                    break
            if ths_fn is None:
                logger.error("[大盘] THS 行业板块接口在当前 AkShare 版本中不可用")
                return
            df = self._call_akshare_with_retry(ths_fn, "行业板块行情(THS)", attempts=2)
            if df is not None and not df.empty:
                source = "ths"

        if df is None or df.empty:
            logger.error("[大盘] 行业板块行情 最终失败：EM/THS 均返回空数据")
            return

        logger.info(f"[大盘] 行业板块行情 获取成功 (source={source}), 共 {len(df)} 条")

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


# 测试入口
if __name__ == "__main__":
    import sys

    sys.path.insert(0, '.')

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
