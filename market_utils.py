# -*- coding: utf-8 -*-
"""
===================================
大盘复盘工具函数
===================================

包含网络请求补丁、数据解析和格式化等工具。
"""

import logging
import random
import re
import time
from http.client import RemoteDisconnected
from typing import Optional
from urllib.parse import urlparse, urlunparse

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# =========================
# Constants
# =========================

_AK_PATCHED_V2 = False
_GLOBAL_SESSION = None

EASTMONEY_HOST_POOL = [
    "push2.eastmoney.com",
    "80.push2.eastmoney.com",
    "81.push2.eastmoney.com",
    "82.push2.eastmoney.com",
    "72.push2.eastmoney.com",
]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]


# =========================
# Utility Functions
# =========================

def parse_cn_unit_number(x):
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


def safe_float(val, default=0.0):
    """安全转换为浮点数"""
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def normalize_sector_rankings(df, source: str, logger_instance=None):
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

    # 成交额/成交量单位处理
    if amt_col:
        raw_amt = d[amt_col].apply(parse_cn_unit_number)
        if source == "ths" and raw_amt.max() < 1e6:
            out["turnover_amt"] = raw_amt * 1e8
        else:
            out["turnover_amt"] = raw_amt
    else:
        out["turnover_amt"] = np.nan

    if vol_col:
        raw_vol = d[vol_col].apply(parse_cn_unit_number)
        if source == "ths" and raw_vol.max() < 1e6:
            out["turnover_vol"] = raw_vol * 1e4
        else:
            out["turnover_vol"] = raw_vol
    else:
        out["turnover_vol"] = np.nan

    out["raw_source"] = source

    out = out.dropna(subset=["sector_name"]).reset_index(drop=True)

    if logger_instance:
        missing = []
        if name_col is None:
            missing.append("name")
        if change_col is None:
            missing.append("change")
        if missing:
            logger_instance.warning(f"[大盘] 行业板块字段不完整(source={source}) missing={missing}, cols={list(d.columns)[:30]}")
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

def _replace_host(url: str, new_host: str) -> str:
    """替换 URL 中的 host"""
    u = urlparse(url)
    return urlunparse((u.scheme, new_host, u.path, u.params, u.query, u.fragment))


def install_requests_patch_for_akshare_v2():
    """为 AkShare/requests 安装更稳健的网络补丁（连接复用 + retry + headers + 轻量限速 + host 轮换）。"""
    global _AK_PATCHED_V2, _GLOBAL_SESSION
    if _AK_PATCHED_V2:
        return

    # 1) 创建全局 Session
    _GLOBAL_SESSION = requests.Session()
    _GLOBAL_SESSION.trust_env = False

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

    # 2) Patch requests.api.request
    import requests.api as _api
    _orig_api_request = _api.request

    def _api_request_patched(method, url, **kwargs):
        return _GLOBAL_SESSION.request(method=method, url=url, **kwargs)

    _api.request = _api_request_patched
    requests.request = _api_request_patched

    # 3) Patch Session.request
    _orig_request = requests.Session.request

    def _patched_request(self, method, url, *args, **kwargs):
        if isinstance(url, str) and ("eastmoney.com" in url):
            time.sleep(random.uniform(0.25, 0.65))
            kwargs.setdefault("proxies", {})

            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("User-Agent", random.choice(USER_AGENTS))
            headers.setdefault("Referer", "https://quote.eastmoney.com/")
            headers.setdefault("Accept", "*/*")
            headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
            headers.setdefault("Connection", "keep-alive")
            kwargs["headers"] = headers

            kwargs.setdefault("timeout", (3.05, 20))

            if "push2.eastmoney.com" in url:
                last_exc = None
                for host in EASTMONEY_HOST_POOL:
                    try_url = _replace_host(url, host)
                    try:
                        return _orig_request(self, method, try_url, *args, **kwargs)
                    except (RemoteDisconnected,
                            requests.exceptions.ConnectionError,
                            requests.exceptions.ChunkedEncodingError,
                            requests.exceptions.ReadTimeout) as e:
                        last_exc = e
                        time.sleep(random.uniform(1.2, 2.8))
                        continue
                raise last_exc

        return _orig_request(self, method, url, *args, **kwargs)

    requests.Session.request = _patched_request
    _AK_PATCHED_V2 = True
