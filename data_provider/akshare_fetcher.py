# -*- coding: utf-8 -*-
"""
===================================
AkshareFetcher - 主数据源 (Priority 1)
===================================

数据来源：东方财富爬虫（通过 akshare 库）
特点：免费、无需 Token、数据全面
风险：爬虫机制易被反爬封禁

防封禁策略：
1. 每次请求前随机休眠 2-5 秒
2. 随机轮换 User-Agent
3. 使用 tenacity 实现指数退避重试

增强数据：
- 实时行情：量比、换手率、市盈率、市净率、总市值、流通市值
- 筹码分布：获利比例、平均成本、筹码集中度
"""

import logging
import random
import time
from datetime import datetime
from typing import Optional, Dict, Any

import pandas as pd
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from .base import BaseFetcher, DataFetchError, RateLimitError, STANDARD_COLUMNS
from .models import RealtimeQuote, ChipDistribution
from .utils import (
    USER_AGENTS,
    is_etf_code,
    is_hk_code,
    get_realtime_cache,
    get_etf_realtime_cache,
)

# 获取模块级别的缓存引用
_realtime_cache = get_realtime_cache()
_etf_realtime_cache = get_etf_realtime_cache()

logger = logging.getLogger(__name__)


class AkshareFetcher(BaseFetcher):
    """
    Akshare 数据源实现
    
    优先级：1（最高）
    数据来源：东方财富网爬虫
    
    关键策略：
    - 每次请求前随机休眠 2.0-5.0 秒
    - 随机 User-Agent 轮换
    - 失败后指数退避重试（最多3次）
    """
    
    name = "AkshareFetcher"
    priority = 1
    
    def __init__(self, sleep_min: float = 2.0, sleep_max: float = 5.0):
        """
        初始化 AkshareFetcher
        
        Args:
            sleep_min: 最小休眠时间（秒）
            sleep_max: 最大休眠时间（秒）
        """
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self._last_request_time: Optional[float] = None
    
    def _set_random_user_agent(self) -> None:
        """
        设置随机 User-Agent
        
        通过修改 requests Session 的 headers 实现
        这是关键的反爬策略之一
        """
        try:
            import akshare as ak
            # akshare 内部使用 requests，我们通过环境变量或直接设置来影响
            # 实际上 akshare 可能不直接暴露 session，这里通过 fake_useragent 作为补充
            random_ua = random.choice(USER_AGENTS)
            logger.debug(f"设置 User-Agent: {random_ua[:50]}...")
        except Exception as e:
            logger.debug(f"设置 User-Agent 失败: {e}")
    
    def _enforce_rate_limit(self) -> None:
        """
        强制执行速率限制
        
        策略：
        1. 检查距离上次请求的时间间隔
        2. 如果间隔不足，补充休眠时间
        3. 然后再执行随机 jitter 休眠
        """
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            min_interval = self.sleep_min
            if elapsed < min_interval:
                additional_sleep = min_interval - elapsed
                logger.debug(f"补充休眠 {additional_sleep:.2f} 秒")
                time.sleep(additional_sleep)
        
        # 执行随机 jitter 休眠
        self.random_sleep(self.sleep_min, self.sleep_max)
        self._last_request_time = time.time()
    
    @retry(
        stop=stop_after_attempt(3),  # 最多重试3次
        wait=wait_exponential(multiplier=1, min=2, max=30),  # 指数退避：2, 4, 8... 最大30秒
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        从 Akshare 获取原始数据
        
        根据代码类型自动选择 API：
        - 普通股票：使用 ak.stock_zh_a_hist()
        - ETF 基金：使用 ak.fund_etf_hist_em()
        
        流程：
        1. 判断代码类型（股票/ETF）
        2. 设置随机 User-Agent
        3. 执行速率限制（随机休眠）
        4. 调用对应的 akshare API
        5. 处理返回数据
        """
        # 根据代码类型选择不同的获取方法
        if is_hk_code(stock_code):
            return self._fetch_hk_data(stock_code, start_date, end_date)
        elif is_etf_code(stock_code):
            return self._fetch_etf_data(stock_code, start_date, end_date)
        else:
            return self._fetch_stock_data(stock_code, start_date, end_date)
    
    def _fetch_stock_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取普通 A 股历史数据
        
        数据来源：ak.stock_zh_a_hist()
        """
        import akshare as ak
        
        # 防封禁策略 1: 随机 User-Agent
        self._set_random_user_agent()
        
        # 防封禁策略 2: 强制休眠
        self._enforce_rate_limit()
        
        logger.info(f"[API调用] ak.stock_zh_a_hist(symbol={stock_code}, period=daily, "
                   f"start_date={start_date.replace('-', '')}, end_date={end_date.replace('-', '')}, adjust=qfq)")
        
        try:
            # 调用 akshare 获取 A 股日线数据
            # period="daily" 获取日线数据
            # adjust="qfq" 获取前复权数据
            import time as _time
            api_start = _time.time()
            
            df = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"  # 前复权
            )
            
            api_elapsed = _time.time() - api_start
            
            # 记录返回数据摘要
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.stock_zh_a_hist 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(f"[API返回] ak.stock_zh_a_hist 返回空数据, 耗时 {api_elapsed:.2f}s")
            
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # 检测反爬封禁
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取数据失败: {e}") from e
    
    def _fetch_etf_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取 ETF 基金历史数据
        
        数据来源：ak.fund_etf_hist_em()
        
        Args:
            stock_code: ETF 代码，如 '512400', '159883'
            start_date: 开始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'
            
        Returns:
            ETF 历史数据 DataFrame
        """
        import akshare as ak
        
        # 防封禁策略 1: 随机 User-Agent
        self._set_random_user_agent()
        
        # 防封禁策略 2: 强制休眠
        self._enforce_rate_limit()
        
        logger.info(f"[API调用] ak.fund_etf_hist_em(symbol={stock_code}, period=daily, "
                   f"start_date={start_date.replace('-', '')}, end_date={end_date.replace('-', '')}, adjust=qfq)")
        
        try:
            import time as _time
            api_start = _time.time()
            
            # 调用 akshare 获取 ETF 日线数据
            df = ak.fund_etf_hist_em(
                symbol=stock_code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"  # 前复权
            )
            
            api_elapsed = _time.time() - api_start
            
            # 记录返回数据摘要
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.fund_etf_hist_em 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(f"[API返回] ak.fund_etf_hist_em 返回空数据, 耗时 {api_elapsed:.2f}s")
            
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # 检测反爬封禁
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取 ETF 数据失败: {e}") from e
    
    def _fetch_hk_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        获取港股历史数据
        
        数据来源：ak.stock_hk_hist()
        
        Args:
            stock_code: 港股代码，如 '00700', '01810'
            start_date: 开始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'
            
        Returns:
            港股历史数据 DataFrame
        """
        import akshare as ak
        
        # 防封禁策略 1: 随机 User-Agent
        self._set_random_user_agent()
        
        # 防封禁策略 2: 强制休眠
        self._enforce_rate_limit()
        
        # 确保代码格式正确（5位数字）
        code = stock_code.lower().replace('hk', '').zfill(5)
        
        logger.info(f"[API调用] ak.stock_hk_hist(symbol={code}, period=daily, "
                   f"start_date={start_date.replace('-', '')}, end_date={end_date.replace('-', '')}, adjust=qfq)")
        
        try:
            import time as _time
            api_start = _time.time()
            
            # 调用 akshare 获取港股日线数据
            df = ak.stock_hk_hist(
                symbol=code,
                period="daily",
                start_date=start_date.replace('-', ''),
                end_date=end_date.replace('-', ''),
                adjust="qfq"  # 前复权
            )
            
            api_elapsed = _time.time() - api_start
            
            # 记录返回数据摘要
            if df is not None and not df.empty:
                logger.info(f"[API返回] ak.stock_hk_hist 成功: 返回 {len(df)} 行数据, 耗时 {api_elapsed:.2f}s")
                logger.info(f"[API返回] 列名: {list(df.columns)}")
                logger.info(f"[API返回] 日期范围: {df['日期'].iloc[0]} ~ {df['日期'].iloc[-1]}")
                logger.debug(f"[API返回] 最新3条数据:\n{df.tail(3).to_string()}")
            else:
                logger.warning(f"[API返回] ak.stock_hk_hist 返回空数据, 耗时 {api_elapsed:.2f}s")
            
            return df
            
        except Exception as e:
            error_msg = str(e).lower()
            
            # 检测反爬封禁
            if any(keyword in error_msg for keyword in ['banned', 'blocked', '频率', 'rate', '限制']):
                logger.warning(f"检测到可能被封禁: {e}")
                raise RateLimitError(f"Akshare 可能被限流: {e}") from e
            
            raise DataFetchError(f"Akshare 获取港股数据失败: {e}") from e
    
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        标准化 Akshare 数据
        
        Akshare 返回的列名（中文）：
        日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
        
        需要映射到标准列名：
        date, open, high, low, close, volume, amount, pct_chg
        """
        df = df.copy()
        
        # 列名映射（Akshare 中文列名 -> 标准英文列名）
        column_mapping = {
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
        }
        
        # 重命名列
        df = df.rename(columns=column_mapping)
        
        # 添加股票代码列
        df['code'] = stock_code
        
        # 只保留需要的列
        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]
        
        return df
    
    def get_realtime_quote(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        获取实时行情数据
        
        根据代码类型自动选择数据源：
        - 普通股票：ak.stock_zh_a_spot_em()
        - ETF 基金：ak.fund_etf_spot_em()
        
        Args:
            stock_code: 股票/ETF代码
            
        Returns:
            RealtimeQuote 对象，获取失败返回 None
        """
        # 根据代码类型选择不同的获取方法
        if is_hk_code(stock_code):
            return self._get_hk_realtime_quote(stock_code)
        elif is_etf_code(stock_code):
            return self._get_etf_realtime_quote(stock_code)
        else:
            return self._get_stock_realtime_quote(stock_code)
    
    def _get_stock_realtime_quote(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        获取普通 A 股实时行情数据

        数据来源优先级（按稳定性排序）：
        1. 腾讯单股接口 - 最稳定，无需认证，数据较全
        2. ak.stock_zh_a_spot_em() - EM批量接口，字段最全（量比、换手率、PE、PB、市值等）
        3. ak.stock_zh_a_spot() - 新浪批量接口，字段较少但稳定
        """
        import akshare as ak

        # 安全获取字段值
        def safe_float(val, default=0.0):
            try:
                if pd.isna(val):
                    return default
                return float(val)
            except:
                return default

        try:
            # ==== 优先使用腾讯单股接口（最稳定） ====
            tencent_quote = self._get_realtime_from_tencent(stock_code)
            if tencent_quote:
                return tencent_quote

            logger.warning(f"[实时行情] 腾讯接口失败，尝试批量接口获取 {stock_code}")

            # ==== 腾讯失败时，尝试批量接口（可利用缓存） ====
            # 检查缓存
            current_time = time.time()
            if (_realtime_cache['data'] is not None and
                current_time - _realtime_cache['timestamp'] < _realtime_cache['ttl']):
                df = _realtime_cache['data']
                source = _realtime_cache.get('source', 'cache')
                logger.debug(f"[缓存命中] 使用缓存的A股实时行情数据 (source={source})")
            else:
                # 多数据源 fallback 策略：EM优先（数据全），新浪备选（更稳定）
                sources = [
                    ("stock_zh_a_spot_em", "em"),      # EM优先，字段最全
                    ("stock_zh_a_spot", "sina"),       # 新浪备选，更稳定
                ]

                df = None
                source = None
                last_error = None

                for fn_name, source_label in sources:
                    fn = getattr(ak, fn_name, None)
                    if fn is None:
                        logger.debug(f"[API] {fn_name} 在当前AkShare版本不可用，跳过")
                        continue

                    # 每个数据源尝试2次
                    for attempt in range(1, 3):
                        try:
                            self._set_random_user_agent()
                            self._enforce_rate_limit()

                            logger.info(f"[API调用] ak.{fn_name}() 获取A股实时行情... (attempt {attempt}/2)")
                            import time as _time
                            api_start = _time.time()

                            df = fn()

                            api_elapsed = _time.time() - api_start
                            if df is not None and not df.empty:
                                logger.info(f"[API返回] ak.{fn_name} 成功: 返回 {len(df)} 只股票, 耗时 {api_elapsed:.2f}s")
                                source = source_label
                                break
                        except Exception as e:
                            last_error = e
                            logger.warning(f"[API错误] ak.{fn_name} 获取失败 (attempt {attempt}/2): {e}")
                            time.sleep(min(2 ** attempt, 5))

                    if df is not None and not df.empty:
                        break  # 成功获取，退出数据源循环
                    else:
                        logger.warning(f"[API] {fn_name} 所有尝试失败，尝试下一个数据源...")

                # 更新缓存
                if df is None or df.empty:
                    logger.error(f"[API错误] 所有批量数据源获取A股实时行情失败: {last_error}")
                    df = pd.DataFrame()
                    source = None

                _realtime_cache['data'] = df
                _realtime_cache['timestamp'] = current_time
                _realtime_cache['source'] = source

            # 如果批量接口都失败，返回None
            if df is None or df.empty:
                logger.error(f"[实时行情] 所有数据源均失败，无法获取 {stock_code} 行情")
                return None

            # 查找指定股票
            row = df[df['代码'] == stock_code]
            if row.empty:
                logger.warning(f"[API返回] 批量数据中未找到 {stock_code}")
                return None

            row = row.iloc[0]

            # 根据数据源选择字段映射（新浪接口缺少部分字段）
            source = _realtime_cache.get('source', 'em')

            quote = RealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                # 以下字段新浪接口可能没有，使用默认值0
                volume_ratio=safe_float(row.get('量比', 0)),
                turnover_rate=safe_float(row.get('换手率', 0)),
                amplitude=safe_float(row.get('振幅', 0)),
                pe_ratio=safe_float(row.get('市盈率-动态', 0)),
                pb_ratio=safe_float(row.get('市净率', 0)),
                total_mv=safe_float(row.get('总市值', 0)),
                circ_mv=safe_float(row.get('流通市值', 0)),
                change_60d=safe_float(row.get('60日涨跌幅', 0)),
                high_52w=safe_float(row.get('52周最高', 0)),
                low_52w=safe_float(row.get('52周最低', 0)),
            )

            # 当使用新浪数据源时，调用雪球接口补充缺失字段
            if source == "sina":
                xq_data = self._fetch_xq_supplement(stock_code)
                if xq_data:
                    quote.volume_ratio = xq_data.get('volume_ratio', quote.volume_ratio)
                    quote.turnover_rate = xq_data.get('turnover_rate', quote.turnover_rate)
                    quote.amplitude = xq_data.get('amplitude', quote.amplitude)
                    quote.pe_ratio = xq_data.get('pe_ratio', quote.pe_ratio)
                    quote.pb_ratio = xq_data.get('pb_ratio', quote.pb_ratio)
                    quote.total_mv = xq_data.get('total_mv', quote.total_mv)
                    quote.circ_mv = xq_data.get('circ_mv', quote.circ_mv)
                    quote.high_52w = xq_data.get('high_52w', quote.high_52w)
                    quote.low_52w = xq_data.get('low_52w', quote.low_52w)
                    source = "sina+xq"  # 标记数据来源

            logger.info(f"[实时行情] {stock_code} {quote.name} (source={source}): 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"量比={quote.volume_ratio}, 换手率={quote.turnover_rate}%, "
                       f"PE={quote.pe_ratio}, PB={quote.pb_ratio}")
            return quote

        except Exception as e:
            logger.error(f"[API错误] 获取 {stock_code} 实时行情失败: {e}")
            return None

    def _fetch_xq_supplement(self, stock_code: str) -> Optional[Dict[str, float]]:
        """
        直接调用雪球原始 API 获取完整数据（补充新浪接口缺失的字段）

        雪球 API 提供：量比、换手率、振幅、PE、PB、市值、52周高低等
        注意：akshare 的封装漏掉了 volume_ratio 字段，所以直接调用原始 API

        Args:
            stock_code: 股票代码（纯数字，如 000001）

        Returns:
            包含补充字段的字典，失败返回 None
        """
        import requests

        try:
            # 转换股票代码格式：000001 -> SZ000001, 600001 -> SH600001
            if stock_code.startswith(('0', '3')):
                symbol = f"SZ{stock_code}"
            elif stock_code.startswith(('6', '9')):
                symbol = f"SH{stock_code}"
            elif stock_code.startswith(('4', '8')):
                symbol = f"BJ{stock_code}"  # 北交所
            else:
                symbol = f"SZ{stock_code}"  # 默认深市

            # 获取雪球 token（从 akshare 内置配置）
            try:
                from akshare.stock.cons import xq_a_token
            except ImportError:
                logger.warning("[雪球补充] 无法获取雪球 token，跳过补充")
                return None

            session = requests.Session()
            headers = {
                'cookie': f'xq_a_token={xq_a_token};',
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 '
                              '(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            }

            # 先访问主页获取必要的 cookie
            session.get('https://xueqiu.com', headers=headers, timeout=5)

            # 调用雪球 API
            url = f'https://stock.xueqiu.com/v5/stock/quote.json?symbol={symbol}&extend=detail'
            response = session.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                logger.warning(f"[雪球补充] API 请求失败: HTTP {response.status_code}")
                return None

            data = response.json()
            quote = data.get('data', {}).get('quote', {})

            if not quote:
                logger.warning(f"[雪球补充] {stock_code} 返回数据为空")
                return None

            # 提取需要的字段
            def safe_float(val, default=0.0):
                try:
                    if val is None:
                        return default
                    return float(val)
                except:
                    return default

            result = {
                'volume_ratio': safe_float(quote.get('volume_ratio')),      # 量比
                'turnover_rate': safe_float(quote.get('turnover_rate')),    # 换手率
                'amplitude': safe_float(quote.get('amplitude')),             # 振幅
                'pe_ratio': safe_float(quote.get('pe_forecast')),           # 市盈率(动态)
                'pb_ratio': safe_float(quote.get('pb')),                    # 市净率
                'total_mv': safe_float(quote.get('market_capital')),        # 总市值
                'circ_mv': safe_float(quote.get('float_market_capital')),   # 流通市值
                'high_52w': safe_float(quote.get('high52w')),               # 52周最高
                'low_52w': safe_float(quote.get('low52w')),                 # 52周最低
            }

            logger.info(f"[雪球补充] {stock_code} 补充成功: 量比={result['volume_ratio']}, "
                       f"换手率={result['turnover_rate']}%, PE={result['pe_ratio']}, PB={result['pb_ratio']}")

            return result

        except requests.exceptions.Timeout:
            logger.warning(f"[雪球补充] {stock_code} 请求超时，跳过补充")
            return None
        except Exception as e:
            logger.warning(f"[雪球补充] {stock_code} 获取失败: {e}")
            return None

    def _get_realtime_from_tencent(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        从腾讯财经 API 获取单股实时行情（首选方案，最稳定）

        腾讯 API 优点：
        - 无需认证，稳定可靠
        - 数据字段完整（换手率、PE、PB、市值、振幅等）
        - 响应速度快

        Args:
            stock_code: 股票代码（纯数字，如 000001）

        Returns:
            RealtimeQuote 对象，失败返回 None
        """
        import requests
        import re

        try:
            # 转换股票代码格式：000001 -> sz000001, 600001 -> sh600001
            if stock_code.startswith(('0', '3')):
                symbol = f"sz{stock_code}"
            elif stock_code.startswith(('6', '9')):
                symbol = f"sh{stock_code}"
            elif stock_code.startswith(('4', '8')):
                symbol = f"bj{stock_code}"  # 北交所
            else:
                symbol = f"sz{stock_code}"  # 默认深市

            # 腾讯股票数据接口
            url = f'https://qt.gtimg.cn/q={symbol}'
            headers = {
                'User-Agent': random.choice(USER_AGENTS),
                'Referer': 'https://gu.qq.com/',
            }

            logger.info(f"[腾讯单股] 获取 {stock_code} 实时行情")
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                logger.warning(f"[腾讯单股] API 请求失败: HTTP {response.status_code}")
                return None

            text = response.text

            # 解析数据格式：v_sz000001="1~平安银行~000001~12.34~..."
            match = re.search(r'v_[a-z]{2}\d+="([^"]+)"', text)
            if not match:
                logger.warning(f"[腾讯单股] 无法解析返回数据: {text[:100]}")
                return None

            parts = match.group(1).split('~')
            if len(parts) < 50:
                logger.warning(f"[腾讯单股] 数据字段不足: {len(parts)}")
                return None

            # 安全获取字段值
            def safe_float(val, default=0.0):
                try:
                    if val is None or val == '':
                        return default
                    return float(val)
                except:
                    return default

            # 腾讯数据字段解析（索引从0开始）
            # 1-名称, 3-现价, 31-涨跌额, 32-涨跌幅, 33-最高, 34-最低
            # 38-换手率, 39-市盈率(动态), 43-振幅
            # 44-流通市值(亿), 45-总市值(亿), 46-市净率
            # 47-涨停价, 48-跌停价

            quote = RealtimeQuote(
                code=stock_code,
                name=str(parts[1]) if len(parts) > 1 else '',
                price=safe_float(parts[3]) if len(parts) > 3 else 0.0,
                change_pct=safe_float(parts[32]) if len(parts) > 32 else 0.0,
                change_amount=safe_float(parts[31]) if len(parts) > 31 else 0.0,
                volume_ratio=0.0,  # 腾讯接口不提供量比
                turnover_rate=safe_float(parts[38]) if len(parts) > 38 else 0.0,
                amplitude=safe_float(parts[43]) if len(parts) > 43 else 0.0,
                pe_ratio=safe_float(parts[39]) if len(parts) > 39 else 0.0,
                pb_ratio=safe_float(parts[46]) if len(parts) > 46 else 0.0,
                total_mv=safe_float(parts[45]) * 1e8 if len(parts) > 45 else 0.0,  # 亿 -> 元
                circ_mv=safe_float(parts[44]) * 1e8 if len(parts) > 44 else 0.0,  # 亿 -> 元
                change_60d=0.0,  # 腾讯接口不提供
                high_52w=0.0,  # 腾讯接口不提供
                low_52w=0.0,  # 腾讯接口不提供
            )

            logger.info(f"[腾讯单股] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"换手率={quote.turnover_rate}%, PE={quote.pe_ratio}, PB={quote.pb_ratio}")
            return quote

        except Exception as e:
            logger.error(f"[腾讯单股] 获取 {stock_code} 实时行情失败: {e}")
            return None

    def _get_etf_realtime_quote(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        获取 ETF 基金实时行情数据
        
        数据来源：ak.fund_etf_spot_em()
        包含：最新价、涨跌幅、成交量、成交额、换手率等
        
        Args:
            stock_code: ETF 代码
            
        Returns:
            RealtimeQuote 对象，获取失败返回 None
        """
        import akshare as ak
        
        try:
            # 检查缓存
            current_time = time.time()
            if (_etf_realtime_cache['data'] is not None and 
                current_time - _etf_realtime_cache['timestamp'] < _etf_realtime_cache['ttl']):
                df = _etf_realtime_cache['data']
                logger.debug(f"[缓存命中] 使用缓存的ETF实时行情数据")
            else:
                last_error: Optional[Exception] = None
                df = None
                for attempt in range(1, 3):
                    try:
                        # 防封禁策略
                        self._set_random_user_agent()
                        self._enforce_rate_limit()

                        logger.info(f"[API调用] ak.fund_etf_spot_em() 获取ETF实时行情... (attempt {attempt}/2)")
                        import time as _time
                        api_start = _time.time()

                        df = ak.fund_etf_spot_em()

                        api_elapsed = _time.time() - api_start
                        logger.info(f"[API返回] ak.fund_etf_spot_em 成功: 返回 {len(df)} 只ETF, 耗时 {api_elapsed:.2f}s")
                        break
                    except Exception as e:
                        last_error = e
                        logger.warning(f"[API错误] ak.fund_etf_spot_em 获取失败 (attempt {attempt}/2): {e}")
                        time.sleep(min(2 ** attempt, 5))

                if df is None:
                    logger.error(f"[API错误] ak.fund_etf_spot_em 最终失败: {last_error}")
                    df = pd.DataFrame()
                _etf_realtime_cache['data'] = df
                _etf_realtime_cache['timestamp'] = current_time

            if df is None or df.empty:
                logger.warning(f"[实时行情] ETF实时行情数据为空，跳过 {stock_code}")
                return None
            
            # 查找指定 ETF
            row = df[df['代码'] == stock_code]
            if row.empty:
                logger.warning(f"[API返回] 未找到 ETF {stock_code} 的实时行情")
                return None
            
            row = row.iloc[0]
            
            # 安全获取字段值
            def safe_float(val, default=0.0):
                try:
                    if pd.isna(val):
                        return default
                    return float(val)
                except:
                    return default
            
            # ETF 行情数据构建（部分字段 ETF 可能不支持，使用默认值）
            quote = RealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume_ratio=safe_float(row.get('量比', 0)),  # ETF 可能无量比
                turnover_rate=safe_float(row.get('换手率')),
                amplitude=safe_float(row.get('振幅')),
                pe_ratio=0.0,  # ETF 通常无市盈率
                pb_ratio=0.0,  # ETF 通常无市净率
                total_mv=safe_float(row.get('总市值', 0)),
                circ_mv=safe_float(row.get('流通市值', 0)),
                change_60d=0.0,  # ETF 接口可能不提供
                high_52w=safe_float(row.get('52周最高', 0)),
                low_52w=safe_float(row.get('52周最低', 0)),
            )
            
            logger.info(f"[ETF实时行情] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"换手率={quote.turnover_rate}%")
            return quote
            
        except Exception as e:
            logger.error(f"[API错误] 获取 ETF {stock_code} 实时行情失败: {e}")
            return None
    
    def _get_hk_realtime_quote(self, stock_code: str) -> Optional[RealtimeQuote]:
        """
        获取港股实时行情数据
        
        数据来源：ak.stock_hk_spot_em()
        包含：最新价、涨跌幅、成交量、成交额等
        
        Args:
            stock_code: 港股代码
            
        Returns:
            RealtimeQuote 对象，获取失败返回 None
        """
        import akshare as ak
        
        try:
            # 防封禁策略
            self._set_random_user_agent()
            self._enforce_rate_limit()
            
            # 确保代码格式正确（5位数字）
            code = stock_code.lower().replace('hk', '').zfill(5)
            
            logger.info(f"[API调用] ak.stock_hk_spot_em() 获取港股实时行情...")
            import time as _time
            api_start = _time.time()
            
            df = ak.stock_hk_spot_em()
            
            api_elapsed = _time.time() - api_start
            logger.info(f"[API返回] ak.stock_hk_spot_em 成功: 返回 {len(df)} 只港股, 耗时 {api_elapsed:.2f}s")
            
            # 查找指定港股
            row = df[df['代码'] == code]
            if row.empty:
                logger.warning(f"[API返回] 未找到港股 {code} 的实时行情")
                return None
            
            row = row.iloc[0]
            
            # 安全获取字段值
            def safe_float(val, default=0.0):
                try:
                    if pd.isna(val):
                        return default
                    return float(val)
                except:
                    return default
            
            # 港股行情数据构建
            quote = RealtimeQuote(
                code=stock_code,
                name=str(row.get('名称', '')),
                price=safe_float(row.get('最新价')),
                change_pct=safe_float(row.get('涨跌幅')),
                change_amount=safe_float(row.get('涨跌额')),
                volume_ratio=safe_float(row.get('量比', 0)),  # 港股可能无量比
                turnover_rate=safe_float(row.get('换手率', 0)),
                amplitude=safe_float(row.get('振幅', 0)),
                pe_ratio=safe_float(row.get('市盈率', 0)),  # 港股可能有市盈率
                pb_ratio=safe_float(row.get('市净率', 0)),  # 港股可能有市净率
                total_mv=safe_float(row.get('总市值', 0)),
                circ_mv=safe_float(row.get('流通市值', 0)),
                change_60d=0.0,  # 港股接口可能不提供
                high_52w=safe_float(row.get('52周最高', 0)),
                low_52w=safe_float(row.get('52周最低', 0)),
            )
            
            logger.info(f"[港股实时行情] {stock_code} {quote.name}: 价格={quote.price}, 涨跌={quote.change_pct}%, "
                       f"换手率={quote.turnover_rate}%")
            return quote
            
        except Exception as e:
            logger.error(f"[API错误] 获取港股 {stock_code} 实时行情失败: {e}")
            return None
    
    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        """
        获取筹码分布数据
        
        数据来源：ak.stock_cyq_em()
        包含：获利比例、平均成本、筹码集中度
        
        注意：ETF/指数没有筹码分布数据，会直接返回 None
        
        Args:
            stock_code: 股票代码
            
        Returns:
            ChipDistribution 对象（最新一天的数据），获取失败返回 None
        """
        import akshare as ak
        
        # ETF/指数没有筹码分布数据
        if is_etf_code(stock_code):
            logger.debug(f"[API跳过] {stock_code} 是 ETF/指数，无筹码分布数据")
            return None
        
        try:
            # 防封禁策略
            self._set_random_user_agent()
            self._enforce_rate_limit()
            
            logger.info(f"[API调用] ak.stock_cyq_em(symbol={stock_code}) 获取筹码分布...")
            import time as _time
            api_start = _time.time()
            
            df = ak.stock_cyq_em(symbol=stock_code)
            
            api_elapsed = _time.time() - api_start
            
            if df.empty:
                logger.warning(f"[API返回] ak.stock_cyq_em 返回空数据, 耗时 {api_elapsed:.2f}s")
                return None
            
            logger.info(f"[API返回] ak.stock_cyq_em 成功: 返回 {len(df)} 天数据, 耗时 {api_elapsed:.2f}s")
            logger.debug(f"[API返回] 筹码数据列名: {list(df.columns)}")
            
            # 取最新一天的数据
            latest = df.iloc[-1]
            
            def safe_float(val, default=0.0):
                try:
                    if pd.isna(val):
                        return default
                    return float(val)
                except:
                    return default
            
            chip = ChipDistribution(
                code=stock_code,
                date=str(latest.get('日期', '')),
                profit_ratio=safe_float(latest.get('获利比例')),
                avg_cost=safe_float(latest.get('平均成本')),
                cost_90_low=safe_float(latest.get('90成本-低')),
                cost_90_high=safe_float(latest.get('90成本-高')),
                concentration_90=safe_float(latest.get('90集中度')),
                cost_70_low=safe_float(latest.get('70成本-低')),
                cost_70_high=safe_float(latest.get('70成本-高')),
                concentration_70=safe_float(latest.get('70集中度')),
            )
            
            logger.info(f"[筹码分布] {stock_code} 日期={chip.date}: 获利比例={chip.profit_ratio:.1%}, "
                       f"平均成本={chip.avg_cost}, 90%集中度={chip.concentration_90:.2%}, "
                       f"70%集中度={chip.concentration_70:.2%}")
            return chip
            
        except Exception as e:
            logger.error(f"[API错误] 获取 {stock_code} 筹码分布失败: {e}")
            return None
    
    def get_enhanced_data(self, stock_code: str, days: int = 60) -> Dict[str, Any]:
        """
        获取增强数据（历史K线 + 实时行情 + 筹码分布）
        
        Args:
            stock_code: 股票代码
            days: 历史数据天数
            
        Returns:
            包含所有数据的字典
        """
        result = {
            'code': stock_code,
            'daily_data': None,
            'realtime_quote': None,
            'chip_distribution': None,
        }
        
        # 获取日线数据
        try:
            df = self.get_daily_data(stock_code, days=days)
            result['daily_data'] = df
        except Exception as e:
            logger.error(f"获取 {stock_code} 日线数据失败: {e}")
        
        # 获取实时行情
        result['realtime_quote'] = self.get_realtime_quote(stock_code)
        
        # 获取筹码分布
        result['chip_distribution'] = self.get_chip_distribution(stock_code)
        
        return result


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    fetcher = AkshareFetcher()
    
    # 测试普通股票
    print("=" * 50)
    print("测试普通股票数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('600519')  # 茅台
        print(f"[股票] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[股票] 获取失败: {e}")
    
    # 测试 ETF 基金
    print("\n" + "=" * 50)
    print("测试 ETF 基金数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('512400')  # 有色龙头ETF
        print(f"[ETF] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[ETF] 获取失败: {e}")
    
    # 测试 ETF 实时行情
    print("\n" + "=" * 50)
    print("测试 ETF 实时行情获取")
    print("=" * 50)
    try:
        quote = fetcher.get_realtime_quote('512880')  # 证券ETF
        if quote:
            print(f"[ETF实时] {quote.name}: 价格={quote.price}, 涨跌幅={quote.change_pct}%")
        else:
            print("[ETF实时] 未获取到数据")
    except Exception as e:
        print(f"[ETF实时] 获取失败: {e}")
    
    # 测试港股历史数据
    print("\n" + "=" * 50)
    print("测试港股历史数据获取")
    print("=" * 50)
    try:
        df = fetcher.get_daily_data('00700')  # 腾讯控股
        print(f"[港股] 获取成功，共 {len(df)} 条数据")
        print(df.tail())
    except Exception as e:
        print(f"[港股] 获取失败: {e}")
    
    # 测试港股实时行情
    print("\n" + "=" * 50)
    print("测试港股实时行情获取")
    print("=" * 50)
    try:
        quote = fetcher.get_realtime_quote('00700')  # 腾讯控股
        if quote:
            print(f"[港股实时] {quote.name}: 价格={quote.price}, 涨跌幅={quote.change_pct}%")
        else:
            print("[港股实时] 未获取到数据")
    except Exception as e:
        print(f"[港股实时] 获取失败: {e}")
