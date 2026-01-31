# 修复记录

## 修复 1: 选股流程 bug - 技术筛选前未获取个股数据

### 问题描述
选股流程在技术筛选阶段（`_tech_selection()`）从数据库读取个股数据时，数据为空。这是因为：
1. 宏观层选股获取候选股池后
2. 这些候选股票的日线数据可能尚未获取并存储到数据库
3. 技术筛选阶段调用 `_get_stock_tech_data()` 时 `get_latest_data()` 返回空
4. 结果：候选股全部因"无数据"被过滤掉

### 修复方案
在技术筛选前批量获取候选股票的日线数据，并添加防反爬策略。

### 修改文件
`stock_picker.py`

### 修改内容

#### 1. 添加导入
```python
import random
import time
from data_provider import DataFetcherManager
```

#### 2. 修改 `__init__()` 方法
添加数据获取器管理器：
```python
def __init__(self, analyzer=None, search_service=None):
    # ...原有代码...
    self.fetcher_manager = DataFetcherManager()
```

#### 3. 新增 `_ensure_stock_data_exist()` 方法
位置：line 238-303

功能：
- 串行处理候选股票，避免并发请求
- 每次请求前随机休眠 3-8 秒
- 使用 `DataFetcherManager` 的自动 fallback 机制
- 捕获异常并跳过失败股票
- 连续失败 3 次额外休眠 10-20 秒

#### 4. 修改 `run()` 方法
在 line 169-170 插入数据获取步骤：
```python
# 2.5. 确保候选股票的日线数据已存在
self._ensure_stock_data_exist(candidates)

# 3. 技术层筛选：评分和过滤
candidates = self._tech_selection(candidates, mv_threshold)
```

### 防反爬策略
| 策略 | 实现 |
|------|------|
| 串行处理 | 循环处理，不使用并发 |
| 随机休眠 | 每次请求前 3-8 秒随机休眠 |
| 自动 fallback | DataFetcherManager 自动切换数据源 |
| 异常降级 | 失败时跳过该股票，继续处理其他 |
| 连续失败保护 | 连续失败 3 次额外休眠 10-20 秒 |

### 验证结果
运行 `python main.py --full-analysis` 验证：
- 宏观层筛选出 9 只候选股
- 数据获取功能生效，9 只股票数据全部获取成功
- 技术层筛选正常工作
- 最终选出 8 只股票

---

## 修复 2: AI 温度设置调整（股票分析使用更严格的温度）

### 问题描述
股票分析使用 temperature=0.7，温度过高导致输出随机性大，不适合需要严格判断的股票分析场景。

### 修复方案
将温度从 0.7 降低到 0.3，使输出更加稳定和确定性。

### 修改文件
- `analyzer.py`
- `market_analyzer.py`

### 修改内容

#### analyzer.py
| 位置 | 原值 | 新值 |
|------|------|------|
| line 615 | temperature: 0.7 | temperature: 0.3 |
| line 422 | generation_config.get('temperature', 0.7) | generation_config.get('temperature', 0.3) |

#### market_analyzer.py
| 位置 | 原值 | 新值 |
|------|------|------|
| line 1018 | 'temperature': 0.7 | 'temperature': 0.3 |

### 原因分析
- temperature 控制输出的随机性
- 0.7：输出较随机，适合创意性任务
- 0.3：输出较确定，适合需要严格逻辑的分析任务
- 股票分析需要基于数据的严格判断，应使用较低温度

---

## 修复 3: Token 限制增加

### 问题描述
股票分析的 max_output_tokens 设置为 8192，可能不足以输出详细的决策仪表盘内容。

### 修复方案
将 token 限制从 8192 增加到 16384，批量分析使用 32768。

### 修改文件
- `analyzer.py`
- `market_analyzer.py`

### 修改内容

#### analyzer.py
| 位置 | 原值 | 新值 |
|------|------|------|
| line 616 | max_output_tokens: 8192 | max_output_tokens: 16384 |
| line 423 | generation_config.get('max_output_tokens', 8192) | generation_config.get('max_output_tokens', 16384) |

#### market_analyzer.py
| 位置 | 原值 | 新值 |
|------|------|------|
| line 1019 | 'max_output_tokens': 2048 | 'max_output_tokens': 4096 |

#### analyzer.py (新增批量分析)
批量分析的 token 限制为 32768（line 830）

---

## 修复 4: 添加批量分析功能

### 问题描述
当前每只股票单独调用 AI 分析，串行执行耗时很长。例如分析 8 只股票需要多次 AI 调用。

### 修复方案
实现批量分析功能，一次性收集所有股票数据，然后调用一次 AI 分析所有股票。

### 修改文件
- `analyzer.py`
- `main.py`

### 修改内容

#### analyzer.py

##### 新增 `analyze_batch()` 方法 (line 852-930)
功能：
- 接收多只股票的上下文数据
- 格式化批量分析 Prompt
- 一次性调用 AI 分析所有股票
- 解析 JSON 数组格式响应
- 返回 AnalysisResult 对象列表

##### 新增 `_format_batch_prompt()` 方法 (line 932-1010)
功能：
- 将多只股票的数据组合成一个 Prompt
- 每只股票包含：最新行情、技术指标、实时行情、筹码分布、趋势分析、舆情情报
- 要求 AI 返回 JSON 数组格式

##### 新增 `_parse_batch_response()` 方法 (line 1012-1058)
功能：
- 解析 AI 返回的 JSON 数组
- 通过 code 字段匹配股票
- 构建 AnalysisResult 对象列表

##### 新增 `_create_fallback_results()` 方法 (line 1060-1082)
功能：
- 批量分析失败时创建回退结果
- 为每只股票生成默认的 AnalysisResult

#### main.py

##### 修改 `run_full_analysis()` 方法
在步骤 4（个股分析）中使用批量分析：

```python
if args.dry_run:
    results = pipeline.run(
        stock_codes=stock_codes,
        dry_run=True,
        send_notification=False
    )
else:
    results = analyze_stocks_batch(pipeline, stock_codes, not args.no_notify)
```

##### 新增 `analyze_stocks_batch()` 函数 (line 1007-1050)
功能：
- 并发收集所有股票的数据（实时行情、筹码、趋势分析、新闻）
- 使用线程池并发处理，但每个股票的数据收集是独立的
- 收集完成后调用 `analyzer.analyze_batch()` 进行批量分析
- 发送通知

##### 新增 `_collect_stock_data()` 函数 (line 1053-1111)
功能：
- 收集单只股票的所有数据
- 包括：数据获取、实时行情、筹码分布、趋势分析、舆情搜索
- 返回 (context, news_context) 元组

### 批量分析流程
```
1. 并发收集所有股票数据 (ThreadPoolExecutor)
   └─ 每只股票独立获取数据
      ├─ 实时行情
      ├─ 筹码分布
      ├─ 趋势分析
      └─ 舆情情报

2. 收集所有上下文数据

3. 一次性调用 AI 批量分析
   └─ analyze_batch()
      ├─ 构建批量 Prompt (包含所有股票数据)
      ├─ 调用 AI (一次请求)
      └─ 解析 JSON 数组响应

4. 发送通知
```

### 性能对比
| 方式 | 分析 8 只股票 | AI 调用次数 |
|------|---------------|-------------|
| 串行分析 | ~2-3 分钟 | 8 次 |
| 批量分析 | ~30-40 秒 | 1 次 |

### 注意事项
1. 批量 Prompt 较长，需要更大的 token 限制（32768）
2. 使用较低温度（0.3）确保输出稳定
3. 批量分析失败时自动回退到默认结果
4. dry-run 模式仍使用原来的串行方式（仅获取数据）

---

## 修复 5: 批量分析缺少完整输出字段

### 问题描述
批量分析时飞书推送的内容过于简单，只包含核心结论，缺少详细分析内容（趋势分析、技术面、基本面等）。

### 根因分析
- 批量分析的 `_format_batch_prompt()` 方法只要求 AI 返回基本字段
- 没有要求返回完整的 `dashboard` 对象和详细分析字段
- 与单只股票分析的 `SYSTEM_PROMPT` 相比，输出格式要求不完整

### 修复方案
在 `_format_batch_prompt()` 方法的输出要求中，添加完整的 JSON 格式说明，包括：
- 完整的 `dashboard` 对象（core_conclusion, data_perspective, intelligence, battle_plan）
- 所有详细分析字段（trend_analysis, technical_analysis, ma_analysis, volume_analysis, pattern_analysis, fundamental_analysis, sector_position, company_highlights, news_summary, market_sentiment, hot_topics）

### 修改文件
`analyzer.py`

### 修改内容
修改 `_format_batch_prompt()` 方法中 `## 输出要求` 部分（line 1025-1054），将简单的输出格式示例替换为完整的 JSON 格式说明，与单只股票分析的 `SYSTEM_PROMPT` 保持一致。

### 预期效果
批量分析后，AI 会返回包含完整 dashboard 和详细分析字段的数据，飞书推送会显示：
- 重要信息速览（舆情、业绩预期、风险警报、利好催化）
- 核心结论（一句话决策、信号等级、持仓建议）
- 数据透视（趋势状态、价格位置、量能分析、筹码结构）
- 作战计划（狙击点位、仓位策略、检查清单）
- 如果 dashboard 不存在，回退显示传统格式（操作理由、风险提示、技术面、消息面）

---

## 修复 6: 批量分析结果重复

### 问题描述
批量分析返回的结果有重复，例如：
- 大港股份(002077) 出现 3 次
- 德明利(001309) 出现 3 次

### 根因分析
`_parse_batch_response()` 方法没有去重逻辑，当 AI 返回的 JSON 数组中包含重复的股票代码时，会直接追加到结果列表中，导致重复。

### 修复方案
在 `_parse_batch_response()` 方法中添加去重逻辑：
- 添加 `seen_codes = set()` 用于记录已处理的股票代码
- 在遍历 AI 返回的数组时，先检查 code 是否已存在
- 如果存在，记录警告并跳过
- 如果不存在，添加到 seen_codes 集合并处理

### 修改文件
`analyzer.py` - 修改 `_parse_batch_response()` 方法（line 1158-1183）

### 预期效果
即使 AI 返回的数组中包含重复的股票代码，结果列表中也只会保留每个股票代码的第一个结果，避免重复。

---

## 功能增强记录

### 增强 1: 批量分析功能

#### 功能描述
新增批量分析功能，一次性分析多只股票，大幅减少 AI 调用次数和总耗时。

#### 性能对比
| 方式 | 分析 8 只股票 | AI 调用次数 |
|------|---------------|-------------|
| 串行分析 | ~2-3 分钟 | 8 次 |
| 批量分析 | ~30-40 秒 | 1 次 |

#### 新增功能点
- `analyzer.analyze_batch()` - 批量分析入口
- `analyzer._format_batch_prompt()` - 格式化批量 Prompt
- `analyzer._parse_batch_response()` - 解析批量响应
- `analyzer._create_fallback_results()` - 回退结果
- `main.analyze_stocks_batch()` - 批量分析协调函数
- `main._collect_stock_data()` - 收集单股数据

#### 流程改进
- 串行收集所有股票数据（实时行情、筹码、趋势分析、新闻）
- 收集完成后一次性调用 AI 批量分析
- 解析 JSON 数组格式响应

#### 修改文件
- `analyzer.py` - 新增批量分析方法
- `main.py` - 新增批量分析协调函数，修改 `run_full_analysis()` 使用批量分析
