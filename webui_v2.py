# -*- coding: utf-8 -*-
"""
===================================
A股智能分析系统 - Web UI V2
===================================

职责：
1. 提供 RESTful API 接口
2. 选股池展示和操作
3. 大盘复盘数据查询
4. 决策仪表盘
5. 历史记录查询

技术栈：
- Flask (后端 API)
- Vue3 + ECharts (前端)
"""

import os
import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS

from config import get_config
from storage import (
    get_db,
    MarketState,
    MarketDaily,
    SectorDaily,
    StockPool,
    StockDaily,
)
from stock_picker import StockPicker, run_stock_picker

logger = logging.getLogger(__name__)

# Flask 应用
app = Flask(__name__, static_folder='webui_v2/dist', static_url_path='')
CORS(app)  # 开发时允许跨域


# ========== 辅助函数 ==========

def json_response(data: Any, code: int = 200) -> Response:
    """统一的 JSON 响应格式"""
    return Response(
        json.dumps({
            'code': code,
            'data': data,
            'timestamp': datetime.now().isoformat()
        }, ensure_ascii=False, default=str),
        mimetype='application/json',
        status=code
    )


def parse_date(date_str: Optional[str]) -> date:
    """解析日期字符串"""
    if not date_str:
        return date.today()
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return date.today()


def get_trading_days(days: int = 30) -> List[date]:
    """获取最近的交易日列表（简化版，实际应从数据库获取）"""
    db = get_db()
    history = db.get_market_history(days=days)
    return [h.date for h in history if h.date]


# ========== API 路由 ==========

# ---------- 选股池 API ----------

@app.route('/api/stock-pool', methods=['GET'])
def get_stock_pool():
    """
    获取选股池列表

    Query params:
        - date: 日期 (YYYY-MM-DD)，默认今天
        - limit: 返回数量，默认10
    """
    target_date = parse_date(request.args.get('date'))
    limit = int(request.args.get('limit', 10))

    db = get_db()
    pool = db.get_stock_pool(target_date, top_n=limit)

    result = {
        'date': target_date.isoformat(),
        'count': len(pool),
        'items': [p.to_dict() for p in pool]
    }

    return json_response(result)


@app.route('/api/stock-pool/<stock_code>/status', methods=['PUT'])
def update_stock_status(stock_code: str):
    """
    更新选股状态

    Body:
        - status: 'pending' | 'selected' | 'dismissed'
        - date: 日期 (可选)
    """
    data = request.get_json() or {}
    target_date = parse_date(data.get('date'))
    new_status = data.get('status', 'pending')

    if new_status not in ['pending', 'selected', 'dismissed']:
        return json_response({'error': 'Invalid status'}, 400)

    db = get_db()
    with db.get_session() as session:
        from sqlalchemy import select, and_
        pool = session.execute(
            select(StockPool).where(
                and_(
                    StockPool.date == target_date,
                    StockPool.stock_code == stock_code
                )
            )
        ).scalar_one_or_none()

        if not pool:
            return json_response({'error': 'Stock not found'}, 404)

        pool.status = new_status
        session.commit()

    return json_response({'success': True, 'stock_code': stock_code, 'status': new_status})


@app.route('/api/stock-pool/selected', methods=['GET'])
def get_selected_stocks():
    """获取已选中的股票列表"""
    target_date = parse_date(request.args.get('date'))

    db = get_db()
    with db.get_session() as session:
        from sqlalchemy import select, and_
        results = session.execute(
            select(StockPool).where(
                and_(
                    StockPool.date == target_date,
                    StockPool.status == 'selected'
                )
            ).order_by(StockPool.priority)
        ).scalars().all()

        return json_response({
            'date': target_date.isoformat(),
            'count': len(results),
            'items': [p.to_dict() for p in results]
        })


@app.route('/api/stock-pool/run', methods=['POST'])
def run_stock_selection():
    """执行选股"""
    data = request.get_json() or {}
    target_date = parse_date(data.get('date'))

    try:
        picker = StockPicker()
        candidates = picker.run(target_date)

        return json_response({
            'success': True,
            'date': target_date.isoformat(),
            'count': len(candidates),
            'items': [c.to_dict() for c in candidates]
        })
    except Exception as e:
        logger.error(f"选股执行失败: {e}")
        return json_response({'error': str(e)}, 500)


# ---------- 大盘数据 API ----------

@app.route('/api/market/today', methods=['GET'])
def get_market_today():
    """获取今日大盘数据"""
    target_date = parse_date(request.args.get('date'))

    db = get_db()
    market = db.get_market_daily(target_date)

    if not market:
        return json_response({'error': 'No data for this date'}, 404)

    return json_response(market.to_dict())


@app.route('/api/market/history', methods=['GET'])
def get_market_history():
    """
    获取大盘历史数据

    Query params:
        - days: 天数，默认30
    """
    days = int(request.args.get('days', 30))

    db = get_db()
    history = db.get_market_history(days=days)

    # 按日期正序排列（用于图表）
    history_sorted = sorted(history, key=lambda x: x.date)

    result = {
        'count': len(history_sorted),
        'items': [h.to_dict() for h in history_sorted],
        # 提取用于图表的数据
        'chart': {
            'dates': [h.date.isoformat() for h in history_sorted],
            'sh_index': [h.sh_index for h in history_sorted],
            'sz_index': [h.sz_index for h in history_sorted],
            'cyb_index': [h.cyb_index for h in history_sorted],
            'total_amount': [h.total_amount for h in history_sorted],
            'north_flow': [h.north_flow for h in history_sorted],
        }
    }

    return json_response(result)


@app.route('/api/market/state', methods=['GET'])
def get_market_state():
    """获取市场状态"""
    db = get_db()
    state = db.get_market_state()
    threshold = db.get_mv_threshold()

    return json_response({
        'state': state.value,
        'state_label': {
            'bull': '牛市',
            'bear': '熊市',
            'neutral': '震荡'
        }.get(state.value, '未知'),
        'mv_threshold': threshold
    })


# ---------- 板块数据 API ----------

@app.route('/api/sectors/today', methods=['GET'])
def get_sectors_today():
    """
    获取今日板块数据

    Query params:
        - date: 日期
        - type: 'top' | 'bottom' | 'all'，默认 'all'
    """
    target_date = parse_date(request.args.get('date'))
    rank_type = request.args.get('type', 'all')

    db = get_db()

    if rank_type == 'all':
        top_sectors = db.get_sectors_by_date(target_date, rank_type='top')
        bottom_sectors = db.get_sectors_by_date(target_date, rank_type='bottom')
        result = {
            'date': target_date.isoformat(),
            'top': [s.to_dict() for s in top_sectors],
            'bottom': [s.to_dict() for s in bottom_sectors],
        }
    else:
        sectors = db.get_sectors_by_date(target_date, rank_type=rank_type if rank_type != 'all' else None)
        result = {
            'date': target_date.isoformat(),
            'items': [s.to_dict() for s in sectors]
        }

    return json_response(result)


@app.route('/api/sectors/heatmap', methods=['GET'])
def get_sectors_heatmap():
    """
    获取板块热力图数据

    Query params:
        - date: 日期
    """
    target_date = parse_date(request.args.get('date'))

    db = get_db()
    sectors = db.get_sectors_by_date(target_date)

    # 转换为热力图数据格式
    heatmap_data = []
    for s in sectors:
        heatmap_data.append({
            'name': s.sector_name,
            'value': s.change_pct or 0,
            'leader': s.leader,
            'turnover': s.turnover_amt,
        })

    # 按涨跌幅排序
    heatmap_data.sort(key=lambda x: x['value'], reverse=True)

    return json_response({
        'date': target_date.isoformat(),
        'items': heatmap_data
    })


@app.route('/api/sectors/<sector_name>/history', methods=['GET'])
def get_sector_history(sector_name: str):
    """获取板块历史数据"""
    days = int(request.args.get('days', 5))

    db = get_db()
    history = db.get_sector_history(sector_name, days=days)

    return json_response({
        'sector_name': sector_name,
        'count': len(history),
        'items': [h.to_dict() for h in history]
    })


# ---------- 个股数据 API ----------

@app.route('/api/stock/<code>', methods=['GET'])
def get_stock_info(code: str):
    """获取个股信息"""
    db = get_db()
    data = db.get_latest_data(code, days=30)

    if not data:
        return json_response({'error': 'Stock not found'}, 404)

    # 按日期正序
    data_sorted = sorted(data, key=lambda x: x.date)

    result = {
        'code': code,
        'latest': data_sorted[-1].to_dict() if data_sorted else None,
        'history': [d.to_dict() for d in data_sorted],
        'chart': {
            'dates': [d.date.isoformat() for d in data_sorted],
            'close': [d.close for d in data_sorted],
            'volume': [d.volume for d in data_sorted],
            'ma5': [d.ma5 for d in data_sorted],
            'ma10': [d.ma10 for d in data_sorted],
            'ma20': [d.ma20 for d in data_sorted],
        }
    }

    return json_response(result)


@app.route('/api/stock/<code>/analysis', methods=['GET'])
def get_stock_analysis(code: str):
    """获取个股分析上下文"""
    db = get_db()
    context = db.get_analysis_context(code)

    if not context:
        return json_response({'error': 'No analysis data'}, 404)

    return json_response(context)


# ---------- 历史记录 API ----------

@app.route('/api/history/stock-pool', methods=['GET'])
def get_stock_pool_history():
    """
    获取选股历史记录

    Query params:
        - days: 查询天数，默认7
    """
    days = int(request.args.get('days', 7))

    db = get_db()
    trading_days = get_trading_days(days)

    result = []
    for d in trading_days[:days]:
        pool = db.get_stock_pool(d, top_n=20)
        if pool:
            result.append({
                'date': d.isoformat(),
                'count': len(pool),
                'selected_count': sum(1 for p in pool if p.status == 'selected'),
                'items': [p.to_dict() for p in pool[:5]]  # 只返回前5个
            })

    return json_response({
        'count': len(result),
        'items': result
    })


@app.route('/api/history/dates', methods=['GET'])
def get_available_dates():
    """获取有数据的日期列表"""
    days = int(request.args.get('days', 30))

    db = get_db()
    history = db.get_market_history(days=days)

    dates = sorted([h.date.isoformat() for h in history if h.date], reverse=True)

    return json_response({
        'count': len(dates),
        'dates': dates
    })


# ---------- 复盘报告 API ----------

@app.route('/api/report/market-review', methods=['GET'])
def get_market_review_report():
    """
    获取大盘复盘报告

    Query params:
        - date: 日期
    """
    target_date = parse_date(request.args.get('date'))

    # 读取 Markdown 报告文件
    report_path = f"reports/market_review_{target_date.strftime('%Y%m%d')}.md"

    if os.path.exists(report_path):
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return json_response({
            'date': target_date.isoformat(),
            'content': content,
            'format': 'markdown'
        })
    else:
        return json_response({'error': 'Report not found'}, 404)


# ---------- 概览 API ----------

@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    """获取仪表盘概览数据"""
    target_date = parse_date(request.args.get('date'))

    db = get_db()

    # 市场数据
    market = db.get_market_daily(target_date)

    # 选股池
    pool = db.get_stock_pool(target_date, top_n=10)
    selected = [p for p in pool if p.status == 'selected']

    # 板块数据
    top_sectors = db.get_sectors_by_date(target_date, rank_type='top')[:5]
    bottom_sectors = db.get_sectors_by_date(target_date, rank_type='bottom')[:5]

    # 市场状态
    state = db.get_market_state()

    result = {
        'date': target_date.isoformat(),
        'market': market.to_dict() if market else None,
        'market_state': {
            'state': state.value,
            'label': {'bull': '牛市', 'bear': '熊市', 'neutral': '震荡'}.get(state.value),
        },
        'stock_pool': {
            'total': len(pool),
            'selected': len(selected),
            'top3': [p.to_dict() for p in pool[:3]]
        },
        'sectors': {
            'top': [s.to_dict() for s in top_sectors],
            'bottom': [s.to_dict() for s in bottom_sectors],
        }
    }

    return json_response(result)


# ---------- 静态文件服务 ----------

@app.route('/')
def serve_index():
    """服务前端页面"""
    if os.path.exists(os.path.join(app.static_folder or '.', 'index.html')):
        return send_from_directory(app.static_folder or '.', 'index.html')
    else:
        # 开发模式下返回简单的 HTML
        return '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>A股智能分析系统</title>
    <style>
        body { font-family: system-ui; padding: 2rem; text-align: center; }
        .info { background: #f0f9ff; padding: 2rem; border-radius: 8px; margin: 2rem auto; max-width: 600px; }
        code { background: #e0f2fe; padding: 2px 8px; border-radius: 4px; }
    </style>
</head>
<body>
    <h1>A股智能分析系统 API</h1>
    <div class="info">
        <p>API 服务已启动</p>
        <p>前端开发模式请运行:</p>
        <code>cd webui_v2 && npm run dev</code>
    </div>
    <h3>可用 API 端点</h3>
    <ul style="text-align: left; max-width: 600px; margin: 0 auto;">
        <li><code>GET /api/dashboard</code> - 仪表盘概览</li>
        <li><code>GET /api/stock-pool</code> - 选股池列表</li>
        <li><code>GET /api/market/today</code> - 今日大盘</li>
        <li><code>GET /api/market/history</code> - 大盘历史</li>
        <li><code>GET /api/sectors/today</code> - 今日板块</li>
        <li><code>GET /api/stock/{code}</code> - 个股数据</li>
    </ul>
</body>
</html>
'''


@app.route('/<path:path>')
def serve_static(path):
    """服务静态文件"""
    if app.static_folder and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    elif app.static_folder and os.path.exists(os.path.join(app.static_folder, 'index.html')):
        return send_from_directory(app.static_folder, 'index.html')
    else:
        return json_response({'error': 'Not found'}, 404)


# ========== 启动入口 ==========

def run_webui_v2(host: str = '127.0.0.1', port: int = 5000, debug: bool = False):
    """
    启动 Web UI V2

    Args:
        host: 监听地址
        port: 监听端口
        debug: 调试模式
    """
    logger.info(f"WebUI V2 启动: http://{host}:{port}")
    print(f"\n{'='*50}")
    print(f"A股智能分析系统 - Web UI V2")
    print(f"{'='*50}")
    print(f"访问地址: http://{host}:{port}")
    print(f"API 文档: http://{host}:{port}/api/dashboard")
    print(f"{'='*50}\n")

    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run_webui_v2(debug=True)
