# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 存储层
===================================

职责：
1. 管理 SQLite 数据库连接（单例模式）
2. 定义 ORM 数据模型
3. 提供数据存取接口
4. 实现智能更新逻辑（断点续传）
"""

import logging
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any
from pathlib import Path

import pandas as pd
from sqlalchemy import (
    create_engine,
    Column,
    String,
    Float,
    Date,
    DateTime,
    Integer,
    Text,
    Index,
    UniqueConstraint,
    select,
    and_,
    desc,
)
from sqlalchemy.orm import (
    declarative_base,
    sessionmaker,
    Session,
)
from sqlalchemy.exc import IntegrityError

from config import get_config

logger = logging.getLogger(__name__)

# SQLAlchemy ORM 基类
Base = declarative_base()


# === 枚举定义 ===

class MarketState(Enum):
    """市场状态枚举"""
    BULL = "bull"           # 牛市
    BEAR = "bear"           # 熊市/筑底
    NEUTRAL = "neutral"     # 震荡市


# === 数据模型定义 ===

class StockDaily(Base):
    """
    股票日线数据模型
    
    存储每日行情数据和计算的技术指标
    支持多股票、多日期的唯一约束
    """
    __tablename__ = 'stock_daily'
    
    # 主键
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 股票代码（如 600519, 000001）
    code = Column(String(10), nullable=False, index=True)
    
    # 交易日期
    date = Column(Date, nullable=False, index=True)
    
    # OHLC 数据
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    
    # 成交数据
    volume = Column(Float)  # 成交量（股）
    amount = Column(Float)  # 成交额（元）
    pct_chg = Column(Float)  # 涨跌幅（%）
    
    # 技术指标
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    volume_ratio = Column(Float)  # 量比
    
    # 数据来源
    data_source = Column(String(50))  # 记录数据来源（如 AkshareFetcher）
    
    # 更新时间
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 唯一约束：同一股票同一日期只能有一条数据
    __table_args__ = (
        UniqueConstraint('code', 'date', name='uix_code_date'),
        Index('ix_code_date', 'code', 'date'),
    )
    
    def __repr__(self):
        return f"<StockDaily(code={self.code}, date={self.date}, close={self.close})>"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'code': self.code,
            'date': self.date,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'amount': self.amount,
            'pct_chg': self.pct_chg,
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'volume_ratio': self.volume_ratio,
            'data_source': self.data_source,
        }


class MarketDaily(Base):
    """
    市场每日数据模型

    存储每日大盘概况数据，用于历史对比分析
    """
    __tablename__ = 'market_daily'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, unique=True, index=True)

    # 涨跌家数
    up_count = Column(Integer)          # 上涨家数
    down_count = Column(Integer)        # 下跌家数
    flat_count = Column(Integer)        # 平盘家数
    limit_up_count = Column(Integer)    # 涨停家数
    limit_down_count = Column(Integer)  # 跌停家数

    # 成交数据
    total_amount = Column(Float)        # 两市成交额(亿元)
    total_volume = Column(Float)        # 两市成交量(亿股)

    # 北向资金
    north_flow = Column(Float)          # 北向资金净流入(亿元)

    # 主要指数收盘
    sh_index = Column(Float)            # 上证指数
    sh_change_pct = Column(Float)       # 上证涨跌幅
    sz_index = Column(Float)            # 深证成指
    sz_change_pct = Column(Float)       # 深证涨跌幅
    cyb_index = Column(Float)           # 创业板指
    cyb_change_pct = Column(Float)      # 创业板涨跌幅

    # 市场状态判断字段
    sh_ma60 = Column(Float)             # 上证60日均线
    sh_close_20d_ago = Column(Float)    # 20日前上证收盘价
    market_state = Column(String(20))   # 市场状态: bull/bear/neutral

    # 元数据
    created_at = Column(DateTime, default=datetime.now)

    def __repr__(self):
        return f"<MarketDaily(date={self.date}, amount={self.total_amount})>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date.isoformat() if self.date else None,
            'up_count': self.up_count,
            'down_count': self.down_count,
            'flat_count': self.flat_count,
            'limit_up_count': self.limit_up_count,
            'limit_down_count': self.limit_down_count,
            'total_amount': self.total_amount,
            'total_volume': self.total_volume,
            'north_flow': self.north_flow,
            'sh_index': self.sh_index,
            'sh_change_pct': self.sh_change_pct,
            'sz_index': self.sz_index,
            'sz_change_pct': self.sz_change_pct,
            'cyb_index': self.cyb_index,
            'cyb_change_pct': self.cyb_change_pct,
            'sh_ma60': self.sh_ma60,
            'market_state': self.market_state,
        }


class SectorDaily(Base):
    """
    板块每日数据模型

    存储每日板块涨跌榜数据，用于分析板块轮动和连续性
    """
    __tablename__ = 'sector_daily'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    sector_name = Column(String(50), nullable=False, index=True)

    # 涨跌数据
    change_pct = Column(Float)          # 涨跌幅(%)
    rank = Column(Integer)              # 当日排名(1=最高)
    rank_type = Column(String(10))      # 'top'=涨幅榜, 'bottom'=跌幅榜

    # 领涨股信息
    leader = Column(String(20))         # 领涨股名称
    leader_code = Column(String(10))    # 领涨股代码
    leader_change_pct = Column(Float)   # 领涨股涨跌幅

    # 成交数据
    turnover_amt = Column(Float)        # 成交额(元)
    turnover_vol = Column(Float)        # 成交量(股)

    # 数据来源
    source = Column(String(20))         # ths/em/sina

    # 元数据
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('ix_sector_date_name', 'date', 'sector_name'),
    )

    def __repr__(self):
        return f"<SectorDaily(date={self.date}, name={self.sector_name}, pct={self.change_pct})>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date.isoformat() if self.date else None,
            'sector_name': self.sector_name,
            'change_pct': self.change_pct,
            'rank': self.rank,
            'rank_type': self.rank_type,
            'leader': self.leader,
            'leader_code': self.leader_code,
            'leader_change_pct': self.leader_change_pct,
            'turnover_amt': self.turnover_amt,
            'turnover_vol': self.turnover_vol,
            'source': self.source,
        }


class SectorStock(Base):
    """
    板块成分股映射表

    存储板块与成分股的对应关系，用于从板块选股
    """
    __tablename__ = 'sector_stocks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_name = Column(String(50), nullable=False, index=True)  # 板块名称
    stock_code = Column(String(10), nullable=False, index=True)   # 股票代码
    stock_name = Column(String(20))                               # 股票名称
    market = Column(String(10))           # 市场: SH/SZ

    # 元数据
    updated_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('ix_sector_stock', 'sector_name', 'stock_code'),
        UniqueConstraint('sector_name', 'stock_code', name='uix_sector_stock'),
    )

    def __repr__(self):
        return f"<SectorStock(sector={self.sector_name}, code={self.stock_code})>"


class StockPool(Base):
    """
    选股池表

    存储每日选股结果，包含选股策略、评分等信息
    """
    __tablename__ = 'stock_pool'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)        # 选股日期
    stock_code = Column(String(10), nullable=False)        # 股票代码
    stock_name = Column(String(20))                        # 股票名称
    market = Column(String(10))                            # 市场: SH/SZ

    # 选股来源
    strategy = Column(String(50))         # 策略名称
    sector_name = Column(String(50))      # 来源板块

    # 选股时指标快照
    sector_rank = Column(Integer)         # 板块当日排名
    sector_change_pct = Column(Float)     # 板块涨跌幅
    stock_change_pct = Column(Float)      # 个股涨跌幅
    ma_status = Column(String(20))        # 均线状态
    volume_ratio = Column(Float)          # 量比
    circ_mv = Column(Float)               # 流通市值(亿)

    # 评分
    macro_score = Column(Float)           # 宏观评分
    tech_score = Column(Float)            # 技术评分
    total_score = Column(Float)           # 综合评分
    priority = Column(Integer)            # 优先级 (1=最高)

    # 状态
    status = Column(String(20), default='pending')  # pending/selected/dismissed

    # 元数据
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('ix_pool_date_code', 'date', 'stock_code'),
    )

    def __repr__(self):
        return f"<StockPool(date={self.date}, code={self.stock_code}, score={self.total_score})>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date.isoformat() if self.date else None,
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
            'priority': self.priority,
            'status': self.status,
        }


class PolicyAnalysis(Base):
    """
    政策分析结果表

    存储LLM对政策新闻的分析结果，用于政策利好选股
    """
    __tablename__ = 'policy_analysis'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)    # 分析日期
    session = Column(String(10))          # 'pre'=盘前, 'post'=盘后

    # 政策信息
    policy_title = Column(String(200))    # 政策/新闻标题
    policy_summary = Column(Text)         # 政策摘要
    source = Column(String(100))          # 来源

    # 影响分析
    sector_name = Column(String(50), index=True)  # 受影响板块
    impact_type = Column(String(10))      # positive/negative/neutral
    impact_score = Column(Integer)        # 影响程度 1-5

    # LLM分析
    analysis_reason = Column(Text)        # 分析理由

    # 元数据
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index('ix_policy_date_sector', 'date', 'sector_name'),
    )

    def __repr__(self):
        return f"<PolicyAnalysis(date={self.date}, sector={self.sector_name}, score={self.impact_score})>"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date.isoformat() if self.date else None,
            'session': self.session,
            'policy_title': self.policy_title,
            'sector_name': self.sector_name,
            'impact_type': self.impact_type,
            'impact_score': self.impact_score,
            'analysis_reason': self.analysis_reason,
        }


class DatabaseManager:
    """
    数据库管理器 - 单例模式
    
    职责：
    1. 管理数据库连接池
    2. 提供 Session 上下文管理
    3. 封装数据存取操作
    """
    
    _instance: Optional['DatabaseManager'] = None
    
    def __new__(cls, *args, **kwargs):
        """单例模式实现"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, db_url: Optional[str] = None):
        """
        初始化数据库管理器
        
        Args:
            db_url: 数据库连接 URL（可选，默认从配置读取）
        """
        if self._initialized:
            return
        
        if db_url is None:
            config = get_config()
            db_url = config.get_db_url()
        
        # 创建数据库引擎
        self._engine = create_engine(
            db_url,
            echo=False,  # 设为 True 可查看 SQL 语句
            pool_pre_ping=True,  # 连接健康检查
        )
        
        # 创建 Session 工厂
        self._SessionLocal = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
        )
        
        # 创建所有表
        Base.metadata.create_all(self._engine)
        
        self._initialized = True
        logger.info(f"数据库初始化完成: {db_url}")
    
    @classmethod
    def get_instance(cls) -> 'DatabaseManager':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（用于测试）"""
        if cls._instance is not None:
            cls._instance._engine.dispose()
            cls._instance = None
    
    def get_session(self) -> Session:
        """
        获取数据库 Session
        
        使用示例:
            with db.get_session() as session:
                # 执行查询
                session.commit()  # 如果需要
        """
        session = self._SessionLocal()
        try:
            return session
        except Exception:
            session.close()
            raise
    
    def has_today_data(self, code: str, target_date: Optional[date] = None) -> bool:
        """
        检查是否已有指定日期的数据
        
        用于断点续传逻辑：如果已有数据则跳过网络请求
        
        Args:
            code: 股票代码
            target_date: 目标日期（默认今天）
            
        Returns:
            是否存在数据
        """
        if target_date is None:
            target_date = date.today()
        
        with self.get_session() as session:
            result = session.execute(
                select(StockDaily).where(
                    and_(
                        StockDaily.code == code,
                        StockDaily.date == target_date
                    )
                )
            ).scalar_one_or_none()
            
            return result is not None
    
    def get_latest_data(
        self, 
        code: str, 
        days: int = 2
    ) -> List[StockDaily]:
        """
        获取最近 N 天的数据
        
        用于计算"相比昨日"的变化
        
        Args:
            code: 股票代码
            days: 获取天数
            
        Returns:
            StockDaily 对象列表（按日期降序）
        """
        with self.get_session() as session:
            results = session.execute(
                select(StockDaily)
                .where(StockDaily.code == code)
                .order_by(desc(StockDaily.date))
                .limit(days)
            ).scalars().all()
            
            return list(results)
    
    def get_data_range(
        self, 
        code: str, 
        start_date: date, 
        end_date: date
    ) -> List[StockDaily]:
        """
        获取指定日期范围的数据
        
        Args:
            code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            StockDaily 对象列表
        """
        with self.get_session() as session:
            results = session.execute(
                select(StockDaily)
                .where(
                    and_(
                        StockDaily.code == code,
                        StockDaily.date >= start_date,
                        StockDaily.date <= end_date
                    )
                )
                .order_by(StockDaily.date)
            ).scalars().all()
            
            return list(results)
    
    def save_daily_data(
        self, 
        df: pd.DataFrame, 
        code: str,
        data_source: str = "Unknown"
    ) -> int:
        """
        保存日线数据到数据库
        
        策略：
        - 使用 UPSERT 逻辑（存在则更新，不存在则插入）
        - 跳过已存在的数据，避免重复
        
        Args:
            df: 包含日线数据的 DataFrame
            code: 股票代码
            data_source: 数据来源名称
            
        Returns:
            新增/更新的记录数
        """
        if df is None or df.empty:
            logger.warning(f"保存数据为空，跳过 {code}")
            return 0
        
        saved_count = 0
        
        with self.get_session() as session:
            try:
                for _, row in df.iterrows():
                    # 解析日期
                    row_date = row.get('date')
                    if isinstance(row_date, str):
                        row_date = datetime.strptime(row_date, '%Y-%m-%d').date()
                    elif isinstance(row_date, datetime):
                        row_date = row_date.date()
                    elif isinstance(row_date, pd.Timestamp):
                        row_date = row_date.date()
                    
                    # 检查是否已存在
                    existing = session.execute(
                        select(StockDaily).where(
                            and_(
                                StockDaily.code == code,
                                StockDaily.date == row_date
                            )
                        )
                    ).scalar_one_or_none()
                    
                    if existing:
                        # 更新现有记录
                        existing.open = row.get('open')
                        existing.high = row.get('high')
                        existing.low = row.get('low')
                        existing.close = row.get('close')
                        existing.volume = row.get('volume')
                        existing.amount = row.get('amount')
                        existing.pct_chg = row.get('pct_chg')
                        existing.ma5 = row.get('ma5')
                        existing.ma10 = row.get('ma10')
                        existing.ma20 = row.get('ma20')
                        existing.volume_ratio = row.get('volume_ratio')
                        existing.data_source = data_source
                        existing.updated_at = datetime.now()
                    else:
                        # 创建新记录
                        record = StockDaily(
                            code=code,
                            date=row_date,
                            open=row.get('open'),
                            high=row.get('high'),
                            low=row.get('low'),
                            close=row.get('close'),
                            volume=row.get('volume'),
                            amount=row.get('amount'),
                            pct_chg=row.get('pct_chg'),
                            ma5=row.get('ma5'),
                            ma10=row.get('ma10'),
                            ma20=row.get('ma20'),
                            volume_ratio=row.get('volume_ratio'),
                            data_source=data_source,
                        )
                        session.add(record)
                        saved_count += 1
                
                session.commit()
                logger.info(f"保存 {code} 数据成功，新增 {saved_count} 条")
                
            except Exception as e:
                session.rollback()
                logger.error(f"保存 {code} 数据失败: {e}")
                raise
        
        return saved_count
    
    def get_analysis_context(
        self, 
        code: str,
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取分析所需的上下文数据
        
        返回今日数据 + 昨日数据的对比信息
        
        Args:
            code: 股票代码
            target_date: 目标日期（默认今天）
            
        Returns:
            包含今日数据、昨日对比等信息的字典
        """
        if target_date is None:
            target_date = date.today()
        
        # 获取最近2天数据
        recent_data = self.get_latest_data(code, days=2)
        
        if not recent_data:
            logger.warning(f"未找到 {code} 的数据")
            return None
        
        today_data = recent_data[0]
        yesterday_data = recent_data[1] if len(recent_data) > 1 else None
        
        context = {
            'code': code,
            'date': today_data.date.isoformat(),
            'today': today_data.to_dict(),
        }
        
        if yesterday_data:
            context['yesterday'] = yesterday_data.to_dict()
            
            # 计算相比昨日的变化
            if yesterday_data.volume and yesterday_data.volume > 0:
                context['volume_change_ratio'] = round(
                    today_data.volume / yesterday_data.volume, 2
                )
            
            if yesterday_data.close and yesterday_data.close > 0:
                context['price_change_ratio'] = round(
                    (today_data.close - yesterday_data.close) / yesterday_data.close * 100, 2
                )
            
            # 均线形态判断
            context['ma_status'] = self._analyze_ma_status(today_data)
        
        return context
    
    def _analyze_ma_status(self, data: StockDaily) -> str:
        """
        分析均线形态

        判断条件：
        - 多头排列：close > ma5 > ma10 > ma20
        - 空头排列：close < ma5 < ma10 < ma20
        - 震荡整理：其他情况
        """
        close = data.close or 0
        ma5 = data.ma5 or 0
        ma10 = data.ma10 or 0
        ma20 = data.ma20 or 0

        if close > ma5 > ma10 > ma20 > 0:
            return "多头排列 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "空头排列 📉"
        elif close > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif close < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"

    # ========== 市场数据方法 ==========

    def save_market_daily(self, data: Dict[str, Any]) -> bool:
        """
        保存市场每日数据

        Args:
            data: 包含市场数据的字典，需包含 'date' 字段

        Returns:
            是否保存成功
        """
        if not data or 'date' not in data:
            logger.warning("市场数据为空或缺少日期")
            return False

        target_date = data['date']
        if isinstance(target_date, str):
            target_date = datetime.strptime(target_date, '%Y-%m-%d').date()

        with self.get_session() as session:
            try:
                existing = session.execute(
                    select(MarketDaily).where(MarketDaily.date == target_date)
                ).scalar_one_or_none()

                if existing:
                    # 更新现有记录
                    for key, value in data.items():
                        if key != 'date' and hasattr(existing, key):
                            setattr(existing, key, value)
                    logger.info(f"更新市场数据: {target_date}")
                else:
                    # 创建新记录
                    record = MarketDaily(
                        date=target_date,
                        up_count=data.get('up_count'),
                        down_count=data.get('down_count'),
                        flat_count=data.get('flat_count'),
                        limit_up_count=data.get('limit_up_count'),
                        limit_down_count=data.get('limit_down_count'),
                        total_amount=data.get('total_amount'),
                        total_volume=data.get('total_volume'),
                        north_flow=data.get('north_flow'),
                        sh_index=data.get('sh_index'),
                        sh_change_pct=data.get('sh_change_pct'),
                        sz_index=data.get('sz_index'),
                        sz_change_pct=data.get('sz_change_pct'),
                        cyb_index=data.get('cyb_index'),
                        cyb_change_pct=data.get('cyb_change_pct'),
                        sh_ma60=data.get('sh_ma60'),
                        sh_close_20d_ago=data.get('sh_close_20d_ago'),
                        market_state=data.get('market_state'),
                    )
                    session.add(record)
                    logger.info(f"保存市场数据: {target_date}")

                session.commit()
                return True

            except Exception as e:
                session.rollback()
                logger.error(f"保存市场数据失败: {e}")
                return False

    def get_market_daily(self, target_date: date) -> Optional[MarketDaily]:
        """获取指定日期的市场数据"""
        with self.get_session() as session:
            return session.execute(
                select(MarketDaily).where(MarketDaily.date == target_date)
            ).scalar_one_or_none()

    def get_market_history(self, days: int = 5) -> List[MarketDaily]:
        """
        获取最近N天的市场数据

        Args:
            days: 获取天数

        Returns:
            MarketDaily 列表（按日期降序）
        """
        with self.get_session() as session:
            results = session.execute(
                select(MarketDaily)
                .order_by(desc(MarketDaily.date))
                .limit(days)
            ).scalars().all()
            return list(results)

    # ========== 板块数据方法 ==========

    def save_sector_daily(
        self,
        sectors: List[Dict[str, Any]],
        target_date: date,
        rank_type: str = 'top',
        source: str = 'unknown'
    ) -> int:
        """
        保存板块每日数据

        Args:
            sectors: 板块数据列表
            target_date: 交易日期
            rank_type: 'top' 或 'bottom'
            source: 数据来源

        Returns:
            保存的记录数
        """
        if not sectors:
            return 0

        saved_count = 0
        with self.get_session() as session:
            try:
                for i, sector in enumerate(sectors, 1):
                    sector_name = sector.get('name') or sector.get('sector_name')
                    if not sector_name:
                        continue

                    # 检查是否已存在
                    existing = session.execute(
                        select(SectorDaily).where(
                            and_(
                                SectorDaily.date == target_date,
                                SectorDaily.sector_name == sector_name,
                                SectorDaily.rank_type == rank_type
                            )
                        )
                    ).scalar_one_or_none()

                    if existing:
                        # 更新
                        existing.change_pct = sector.get('change_pct')
                        existing.rank = i
                        existing.leader = sector.get('leader')
                        existing.leader_code = sector.get('leader_code')
                        existing.leader_change_pct = sector.get('leader_change_pct')
                        existing.turnover_amt = sector.get('turnover_amt')
                        existing.turnover_vol = sector.get('turnover_vol')
                        existing.source = source
                    else:
                        # 新增
                        record = SectorDaily(
                            date=target_date,
                            sector_name=sector_name,
                            change_pct=sector.get('change_pct'),
                            rank=i,
                            rank_type=rank_type,
                            leader=sector.get('leader'),
                            leader_code=sector.get('leader_code'),
                            leader_change_pct=sector.get('leader_change_pct'),
                            turnover_amt=sector.get('turnover_amt'),
                            turnover_vol=sector.get('turnover_vol'),
                            source=source,
                        )
                        session.add(record)
                        saved_count += 1

                session.commit()
                logger.info(f"保存板块数据({rank_type}): {saved_count} 条")
                return saved_count

            except Exception as e:
                session.rollback()
                logger.error(f"保存板块数据失败: {e}")
                return 0

    def get_sector_history(
        self,
        sector_name: str,
        days: int = 5
    ) -> List[SectorDaily]:
        """
        获取指定板块的历史数据

        Args:
            sector_name: 板块名称
            days: 获取天数

        Returns:
            SectorDaily 列表（按日期降序）
        """
        with self.get_session() as session:
            results = session.execute(
                select(SectorDaily)
                .where(SectorDaily.sector_name == sector_name)
                .order_by(desc(SectorDaily.date))
                .limit(days)
            ).scalars().all()
            return list(results)

    def get_sectors_by_date(
        self,
        target_date: date,
        rank_type: Optional[str] = None
    ) -> List[SectorDaily]:
        """
        获取指定日期的板块数据

        Args:
            target_date: 日期
            rank_type: 可选，'top' 或 'bottom'

        Returns:
            SectorDaily 列表
        """
        with self.get_session() as session:
            query = select(SectorDaily).where(SectorDaily.date == target_date)
            if rank_type:
                query = query.where(SectorDaily.rank_type == rank_type)
            query = query.order_by(SectorDaily.rank)

            results = session.execute(query).scalars().all()
            return list(results)

    # ========== 数据清理方法 ==========

    def cleanup_old_data(self, retention_days: int = 30) -> Dict[str, int]:
        """
        清理超过保留期限的历史数据

        Args:
            retention_days: 保留天数，默认30天

        Returns:
            各表删除的记录数
        """
        cutoff_date = date.today() - timedelta(days=retention_days)
        deleted = {
            'stock_daily': 0, 'market_daily': 0, 'sector_daily': 0,
            'stock_pool': 0, 'policy_analysis': 0
        }

        with self.get_session() as session:
            try:
                # 清理 stock_daily
                result = session.query(StockDaily).filter(
                    StockDaily.date < cutoff_date
                ).delete(synchronize_session=False)
                deleted['stock_daily'] = result

                # 清理 market_daily
                result = session.query(MarketDaily).filter(
                    MarketDaily.date < cutoff_date
                ).delete(synchronize_session=False)
                deleted['market_daily'] = result

                # 清理 sector_daily
                result = session.query(SectorDaily).filter(
                    SectorDaily.date < cutoff_date
                ).delete(synchronize_session=False)
                deleted['sector_daily'] = result

                # 清理 stock_pool
                result = session.query(StockPool).filter(
                    StockPool.date < cutoff_date
                ).delete(synchronize_session=False)
                deleted['stock_pool'] = result

                # 清理 policy_analysis
                result = session.query(PolicyAnalysis).filter(
                    PolicyAnalysis.date < cutoff_date
                ).delete(synchronize_session=False)
                deleted['policy_analysis'] = result

                session.commit()
                logger.info(f"数据清理完成（保留{retention_days}天）: {deleted}")

            except Exception as e:
                session.rollback()
                logger.error(f"数据清理失败: {e}")

        return deleted

    # ========== 板块成分股方法 ==========

    def save_sector_stocks(
        self,
        sector_name: str,
        stocks: List[Dict[str, Any]]
    ) -> int:
        """
        保存板块成分股

        Args:
            sector_name: 板块名称
            stocks: 成分股列表 [{'code': '600519', 'name': '贵州茅台', 'market': 'SH'}]

        Returns:
            保存的记录数
        """
        if not stocks:
            return 0

        saved_count = 0
        with self.get_session() as session:
            try:
                for stock in stocks:
                    code = stock.get('code') or stock.get('stock_code')
                    if not code:
                        continue

                    existing = session.execute(
                        select(SectorStock).where(
                            and_(
                                SectorStock.sector_name == sector_name,
                                SectorStock.stock_code == code
                            )
                        )
                    ).scalar_one_or_none()

                    if existing:
                        existing.stock_name = stock.get('name') or stock.get('stock_name')
                        existing.market = stock.get('market')
                        existing.updated_at = datetime.now()
                    else:
                        record = SectorStock(
                            sector_name=sector_name,
                            stock_code=code,
                            stock_name=stock.get('name') or stock.get('stock_name'),
                            market=stock.get('market'),
                        )
                        session.add(record)
                        saved_count += 1

                session.commit()
                logger.debug(f"保存板块成分股 {sector_name}: {saved_count} 条新增")
                return saved_count

            except Exception as e:
                session.rollback()
                logger.error(f"保存板块成分股失败: {e}")
                return 0

    def get_sector_stocks(self, sector_name: str) -> List[SectorStock]:
        """获取板块成分股"""
        with self.get_session() as session:
            results = session.execute(
                select(SectorStock).where(SectorStock.sector_name == sector_name)
            ).scalars().all()
            return list(results)

    def get_stock_sectors(self, stock_code: str) -> List[str]:
        """获取股票所属板块列表"""
        with self.get_session() as session:
            results = session.execute(
                select(SectorStock.sector_name).where(
                    SectorStock.stock_code == stock_code
                )
            ).scalars().all()
            return list(results)

    # ========== 选股池方法 ==========

    def save_stock_pool(
        self,
        candidates: List[Dict[str, Any]],
        target_date: date
    ) -> int:
        """
        保存选股结果

        Args:
            candidates: 候选股列表
            target_date: 选股日期

        Returns:
            保存的记录数
        """
        if not candidates:
            return 0

        saved_count = 0
        with self.get_session() as session:
            try:
                for i, c in enumerate(candidates, 1):
                    code = c.get('stock_code') or c.get('code')
                    if not code:
                        continue

                    # 检查是否已存在
                    existing = session.execute(
                        select(StockPool).where(
                            and_(
                                StockPool.date == target_date,
                                StockPool.stock_code == code
                            )
                        )
                    ).scalar_one_or_none()

                    if existing:
                        # 更新评分
                        existing.macro_score = c.get('macro_score')
                        existing.tech_score = c.get('tech_score')
                        existing.total_score = c.get('total_score')
                        existing.priority = i
                    else:
                        record = StockPool(
                            date=target_date,
                            stock_code=code,
                            stock_name=c.get('stock_name') or c.get('name'),
                            market=c.get('market'),
                            strategy=c.get('strategy'),
                            sector_name=c.get('sector_name'),
                            sector_rank=c.get('sector_rank'),
                            sector_change_pct=c.get('sector_change_pct'),
                            stock_change_pct=c.get('stock_change_pct'),
                            ma_status=c.get('ma_status'),
                            volume_ratio=c.get('volume_ratio'),
                            circ_mv=c.get('circ_mv'),
                            macro_score=c.get('macro_score'),
                            tech_score=c.get('tech_score'),
                            total_score=c.get('total_score'),
                            priority=i,
                            status='pending',
                        )
                        session.add(record)
                        saved_count += 1

                session.commit()
                logger.info(f"保存选股结果: {saved_count} 条")
                return saved_count

            except Exception as e:
                session.rollback()
                logger.error(f"保存选股结果失败: {e}")
                return 0

    def get_stock_pool(
        self,
        target_date: date,
        top_n: int = 10
    ) -> List[StockPool]:
        """
        获取选股池结果

        Args:
            target_date: 日期
            top_n: 返回前N条

        Returns:
            StockPool 列表（按评分降序）
        """
        with self.get_session() as session:
            results = session.execute(
                select(StockPool)
                .where(StockPool.date == target_date)
                .order_by(desc(StockPool.total_score))
                .limit(top_n)
            ).scalars().all()
            return list(results)

    # ========== 政策分析方法 ==========

    def save_policy_analysis(
        self,
        analyses: List[Dict[str, Any]],
        target_date: date,
        session_type: str = 'pre'
    ) -> int:
        """
        保存政策分析结果

        Args:
            analyses: 分析结果列表
            target_date: 分析日期
            session_type: 'pre'=盘前, 'post'=盘后

        Returns:
            保存的记录数
        """
        if not analyses:
            return 0

        saved_count = 0
        with self.get_session() as session:
            try:
                for a in analyses:
                    sector = a.get('sector') or a.get('sector_name')
                    if not sector:
                        continue

                    record = PolicyAnalysis(
                        date=target_date,
                        session=session_type,
                        policy_title=a.get('policy_title'),
                        policy_summary=a.get('policy_summary'),
                        source=a.get('source'),
                        sector_name=sector,
                        impact_type=a.get('impact_type', 'positive'),
                        impact_score=a.get('impact_score') or a.get('score'),
                        analysis_reason=a.get('reason') or a.get('analysis_reason'),
                    )
                    session.add(record)
                    saved_count += 1

                session.commit()
                logger.info(f"保存政策分析: {saved_count} 条")
                return saved_count

            except Exception as e:
                session.rollback()
                logger.error(f"保存政策分析失败: {e}")
                return 0

    def get_policy_positive_sectors(
        self,
        target_date: date,
        min_score: int = 4
    ) -> List[PolicyAnalysis]:
        """
        获取政策利好板块

        Args:
            target_date: 日期
            min_score: 最低影响分数

        Returns:
            PolicyAnalysis 列表
        """
        with self.get_session() as session:
            results = session.execute(
                select(PolicyAnalysis)
                .where(
                    and_(
                        PolicyAnalysis.date == target_date,
                        PolicyAnalysis.impact_type == 'positive',
                        PolicyAnalysis.impact_score >= min_score
                    )
                )
                .order_by(desc(PolicyAnalysis.impact_score))
            ).scalars().all()
            return list(results)

    # ========== 市场状态方法 ==========

    def get_market_state(self) -> MarketState:
        """
        判断当前市场状态

        基于：
        1. 上证指数与MA60关系
        2. 近20日涨跌幅

        Returns:
            MarketState 枚举
        """
        history = self.get_market_history(days=25)

        if len(history) < 20:
            return MarketState.NEUTRAL

        today = history[0]
        day_20_ago = history[19] if len(history) > 19 else history[-1]

        # 判断条件
        sh_close = today.sh_index or 0
        sh_ma60 = today.sh_ma60 or 0
        sh_20d_ago = day_20_ago.sh_index or 0

        if sh_ma60 <= 0 or sh_20d_ago <= 0:
            return MarketState.NEUTRAL

        above_ma60 = sh_close > sh_ma60
        change_20d = (sh_close - sh_20d_ago) / sh_20d_ago * 100

        if above_ma60 and change_20d > 5:
            return MarketState.BULL
        elif not above_ma60 and change_20d < -5:
            return MarketState.BEAR
        else:
            return MarketState.NEUTRAL

    def get_mv_threshold(self) -> float:
        """
        获取当前市场状态下的市值阈值

        Returns:
            流通市值阈值（亿）
        """
        thresholds = {
            MarketState.BULL: 80.0,
            MarketState.NEUTRAL: 50.0,
            MarketState.BEAR: 40.0,
        }
        state = self.get_market_state()
        threshold = thresholds.get(state, 50.0)
        logger.info(f"当前市场状态: {state.value}, 市值阈值: {threshold}亿")
        return threshold


# 便捷函数
def get_db() -> DatabaseManager:
    """获取数据库管理器实例的快捷方式"""
    return DatabaseManager.get_instance()


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    db = get_db()
    
    print("=== 数据库测试 ===")
    print(f"数据库初始化成功")
    
    # 测试检查今日数据
    has_data = db.has_today_data('600519')
    print(f"茅台今日是否有数据: {has_data}")
    
    # 测试保存数据
    test_df = pd.DataFrame({
        'date': [date.today()],
        'open': [1800.0],
        'high': [1850.0],
        'low': [1780.0],
        'close': [1820.0],
        'volume': [10000000],
        'amount': [18200000000],
        'pct_chg': [1.5],
        'ma5': [1810.0],
        'ma10': [1800.0],
        'ma20': [1790.0],
        'volume_ratio': [1.2],
    })
    
    saved = db.save_daily_data(test_df, '600519', 'TestSource')
    print(f"保存测试数据: {saved} 条")
    
    # 测试获取上下文
    context = db.get_analysis_context('600519')
    print(f"分析上下文: {context}")
