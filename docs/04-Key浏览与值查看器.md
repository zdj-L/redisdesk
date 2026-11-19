# 04 · Key 浏览与值查看器

> 文档集版本 v2.0（企业级安全） ｜ 2026-11-10 ｜ 参数定义见 [02 附件 A](./02-架构与技术选型.md#附件-a--默认参数总表唯一数值来源) ｜ 风险联动见 [05](./05-命令控制台.md)、[08 §6 数据保护](./08-安全与风险清单.md)

---

## 1. 设计原则

| 编号 | 原则 |
| --- | --- |
| B1 | **应用内不存在 `KEYS` 调用路径**。浏览唯一入口是 `SCAN` 游标 |
| B2 | 任何集合读取都按页：`HSCAN/SSCAN/ZSCAN` 或索引区间 `LRANGE/ZRANGE`；禁止 `HGETALL`、`LRANGE 0 -1`、`SMEMBERS`、`ZRANGE 0 -1` |
| B3 | 所有阻塞风险操作先算成本再执行：打开 key 前先取元素数/字节数，超阈值必须提示 |
| B4 | 浏览结果不是快照：`SCAN` 期间的增删、过期都会造成"列表里有但打开是 nil"；UI 必须友好处理而非报错 |
| B5 | UI 只在主线程渲染，扫描与取值全部在后台线程（见 02 §6） |
| B6 | 集群不做"看起来像单机"的假象：节点、槽、跨槽限制都要在界面可见 |
| B7 | **敏感数据默认遮罩**：命中规则的 key/字段/值一律先遮罩，需要时临时显示并留痕；不提供"全局关闭遮罩"开关 |
| B8 | 浏览也受授权约束：未授权连接不可扫描；角色不足时列表与值区都不可进入 |

---

## 2. 页面结构

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 顶栏：连接切换 [订单/生产/只读 ▾]  环境[生产] 保护[只读]  节点[全部 ▾]  刷新  │
├──────────────────────────────────────────┬───────────────────────────────────┤
│ 过滤栏：名称模式 [user:*        ] 类型[全部▾] │  值查看器                        │
│         TTL [≥ ___ 秒 ≤ ___ 秒] 大小[≥ ☐ ≤ ☐]│  key: user:1001  String  3.2 KB   │
│         [开始扫描] [暂停] [重新扫描]        │  TTL 3600s  [修改 TTL] [删除]     │
├──────────────────────────────────────────┤  ┌ 文本 ┐ JSON ┐ HEX ┐           │
│ Key 列表（虚拟滚动，斑马纹）                │  │ {"name":"张三","level":3}       │
│ ┌───────┬──────────┬─────┬───────┬──────┐│  └───────────────────────────────┘│
│ │ Key   │ 类型     │ TTL │ 大小  │ 节点 ││  分页：第 1 页 / 每页 100  [上一页]│
│ │user:1 │ string   │3600 │ 3.2KB │  —   ││        [下一页] 总数 1,204        │
│ │user:2 │ hash     │  -1 │ 12KB  │  —   ││                                  │
│ └───────┴──────────┴─────┴───────┴──────┘│  （Hash/List/Set/ZSet/Stream 表格）│
│ 已扫描 12,000 · 命中 3,214 · 用时 2.1s   │                                  │
│ [上一页] [下一页] 游标 0x1f3a             │                                  │
└──────────────────────────────────────────┴───────────────────────────────────┘
```

- 左栏宽度默认 420 px（可拖拽 320~640），右栏自适应；分栏用 `QSplitter`。
- Key 列表列：`Key`（可省略 + tooltip）、`类型`、`TTL`、`大小`、`节点`（仅集群显示）。行高 30 px。
- 底部状态：`已扫描 / 命中 / 用时 / 当前游标`，游标值十六进制，便于排障时对照。
- 空状态：未连接 → "请先选择连接"；已连接未扫描 → "输入名称模式或直接开始扫描"；无命中 → "本次扫描无匹配，建议放宽模式或类型"。

### 2.1 敏感遮罩与临时显示

判定与展示分两层：**key 名级**（列表、面包屑、历史）与**内容级**（字段名、值）。

| 层 | 命中规则示例 | 展示 |
| --- | --- | --- |
| key 名级 | `*token*`、`*secret*`、`*password*`、`*credential*`、`*id_card*`、`*phone*`、`*session*` | `user:***:token`（保留可辨识前缀与后缀） |
| 内容级 | 字段名命中同上；值匹配手机号/身份证/邮箱/银行卡/`AKIA...` 等内置正则 | `••••••`（固定 6 点，不泄露长度） |

规则与优先级：

| 项 | 约定 |
| --- | --- |
| 规则来源 | [08 §6.1](./08-安全与风险清单.md) 内置规则表；`sensitive_rules_builtin` 固定开启，不提供用户自定义正则（避免误伤与性能问题） |
| 匹配时机 | 值加载完成后、渲染前；`sensitive_match_timeout_ms=5`，超时按命中处理（宁可多遮） |
| 临时显示 | 仅对"当前选中行/当前字段"生效；显示 `sensitive_reveal_ttl_s=60` 秒并显示倒计时；到点自动回遮罩 |
| 角色 | `viewer` 无临时显示入口；`operator` / `admin` 可用，合计 ≤ `sensitive_reveal_max_per_minute=10` 次/分钟 |
| 留痕 | 每次临时显示写审计 `event=reveal_sensitive`（账号、连接、key 名摘要、字段名、角色）；**不记录值本身** |
| 切换连接 | 清空值缓存、清空所有临时显示状态；不继承上一连接的解锁 |
| 锁定/重登 | 立即回遮罩并清空缓存 |
| 复制 | 复制遮罩内容时复制的是遮罩后的文本；复制明文须先临时显示（同样留痕） |
| 搜索/过滤 | 名称过滤输入框不回显命中值；列表 tooltip 同样遮罩 |
| 导出 | 默认脱敏（`export_default_masked` 固定开）；去脱敏仅 Admin + 二次口令 + 理由 ≥10 字符，审计 `event=export_unmasked`（流程见 05 §4.2） |

为什么不做"全局关闭遮罩"：一旦存在开关，绝大多数用户会永久关掉，等于默认不遮；按需临时显示把"看到明文"变成一个有意识、有记录的动作。

---

## 3. 扫描引擎

### 3.1 状态机（`ScanSession.state`）

```
idle ──start──> running ──够一页──> running（持续）
                  │  ├─ 用户点暂停 ──> paused ──继续──> running
                  │  ├─ 游标归 0 ──> finished（可重新扫描）
                  │  ├─ 达扫描预算 ──> paused（预算耗尽，需用户决定）
                  │  └─ 出错/取消 ──> error / cancelled（保留已扫结果）
```

- **只前进不回退**：`SCAN` 游标不可逆。UI 的"上一页"仅在内存缓冲（`scan_buffer_max_rows=3000`，即 6 页 × 500）内有效；超出缓冲提示"较早页已释放，请重新扫描"。
- **重新扫描**：游标归 0、清空缓冲、保留过滤条件；明确提示"结果可能变化"。
- **暂停**：停止推进游标，已扫描结果可继续查看；不释放连接。

### 3.2 填充一页的流程

```python
while buffered < key_page_size:
    cursor, batch = gateway.scan(node, cursor, match=pattern,
                                 count=scan_count, key_type=type_filter)
    steps += len(batch)                     # 计入预算
    batch = dedupe(batch)                   # 最近 5000 个 key 的去重窗口
    if need_meta:                           # 默认开启 TTL 展示
        meta = gateway.pipeline([["TTL", k] for k in batch],
                                batch=meta_pipeline_batch)   # 100 条/批
    rows = apply_filters(batch, meta)       # 类型/TTL/大小过滤
    buffer.extend(rows)
    if cursor == 0: finished = True; break
    if steps >= scan_budget_cursor_steps or elapsed >= scan_time_budget_s:
        state = paused(预算耗尽); break
    if cancelled: state = cancelled; break
    emit_rows_in_batches(ui_emit_batch_rows=200)
```

关键点：

| 点 | 说明 |
| --- | --- |
| `SCAN` 可能返回空批 | 必须继续推进，不能把"空批"当作结束；单次空批不做 UI 提示 |
| 重复 key | `SCAN` 在 rehash 期间可能重复返回，用最近 5000 个 key 的集合去重 |
| 并发 | 单机/哨兵：1 条扫描线程；集群：`cluster_scan_concurrency=2` 个 master 并行，各自独立游标 |
| 元数据 | TTL 用 pipeline 批量，`meta_pipeline_batch=100`；大小默认不取（见 §3.4） |
| UI 刷新 | 每 200 行 emit 一次，避免信号风暴；表格数据模型增量插入 |

### 3.3 过滤策略与成本

| 过滤项 | 实现位置 | 命令/说明 | 成本 | 默认 |
| --- | --- | --- | --- | --- |
| 名称模式 | 服务端 | `SCAN MATCH <pattern> COUNT 1000` | `MATCH` 在服务端逐 key 比对，不减少扫描总量，只减少返回量 | 开启（可空） |
| 类型 | 服务端（Redis ≥ 6.0） | `SCAN TYPE string\|hash\|list\|set\|zset\|stream` | 无额外往返 | 开启 |
| 类型（< 6.0 降级） | 客户端 | pipeline `TYPE`，100 条/批 | 每 100 key 多 1 次往返 | 自动降级并提示 |
| TTL | 客户端 | pipeline `TTL`，100 条/批 | 每 100 key 多 1 次往返；1000 万 key ≈ 10 万次往返 | 展示 TTL 默认开；TTL **过滤**默认关 |
| 大小 | 客户端 | `MEMORY USAGE key SAMPLES 5`，200 条/批 | 单 key O(N) 采样，10 万 key 级不可接受 | 默认关 |

**扫描预算**（仅带客户端过滤时生效）：

| 项 | 值 | 达到后的行为 |
| --- | --- | --- |
| 时间 | `scan_time_budget_s=30` | 暂停，弹条："已扫描 1,204,300 个 key，命中 8,921，用时 30s" |
| 游标步进 | `scan_budget_cursor_steps=2000000` | 同上 |
| 操作 | — | 三个按钮：继续 30s / 扩大到 120s / 结束扫描 |

> 明确写清：**在 1000 万 key 实例上启用 TTL 过滤，全量扫描需要 10 万次以上 pipeline 往返（同机房约 100~300 秒），且会持续给服务端压力**。UI 必须先显示该提示，建议先用 `MATCH` 前缀把范围缩小到 10 万以内再开过滤。

### 3.4 大小列与"按大小过滤"

- 默认**不计算大小**，`大小` 列显示 `—`，避免为每行付出 `MEMORY USAGE`。
- 用户点击"计算大小"（或启用大小过滤）时，才按 `memory_usage_batch_keys=200`、`SAMPLES 5` 批量估算，结果标注 `≈`，tooltip 说明"采样估算，非精确值"。
- 单 key 详情页使用 `MEMORY USAGE key SAMPLES 0`（精确），仅对当前打开的 key 执行。
- 大 key 阈值判断只能用"元素数/字节数"这类廉价命令（`STRLEN/HLEN/LLEN/SCARD/ZCARD/XLEN`），不使用 `MEMORY USAGE` 做全量判定。

### 3.5 虚拟滚动与内存

| 项 | 设计 |
| --- | --- |
| 数据模型 | `QAbstractTableModel` 持有 `list[KeyMeta]`，`rowCount` 为缓冲长度 |
| 渲染 | 只渲染可视行（约 20 行），单元格文本按 `value_preview_chars=200` 省略 |
| 缓冲 | 上限 `scan_buffer_max_rows=3000`；达到上限时暂停游标，用户"继续"时丢弃最早 1000 行（提示"较早结果已释放"） |
| 自动翻页 | 滚动条触底且 `state=running` 时预取下一页（不重复点击） |
| 排序 | 默认"发现顺序"；提供"仅当前缓冲排序"（按名称/TTL/大小），标注"非全局排序"；不做全量排序 |
| 内存目标 | 3000 行 × 每行 < 400 B ≈ 1.2 MB，可忽略 |

### 3.6 集群浏览

| 项 | 行为 |
| --- | --- |
| 节点枚举 | 连接时 `CLUSTER SLOTS` 得到 master 列表，写入 `node_id = host:port`；超过 `cluster_max_nodes=32` 拒绝扫描并提示 |
| 扫描单位 | 每个 master 一条游标，`cluster_scan_concurrency=2` 并发；单节点完成即标记，全部完成才 `finished` |
| 列表合并 | 行带 `节点` 列与 `slot`（tooltip）；按到达顺序合并，不做跨节点全局排序 |
| 进度 | 每节点显示 `已扫描 x · 完成/进行中`；总进度 = 各节点步数之和（`DBSIZE` 逐节点求分母，标注"估算"） |
| `DBSIZE` | 逐节点执行，不使用 `CLUSTER COUNTKEYSINSLOT` 全槽遍历（16384 次调用成本高） |
| 大小/内存 | 只对 key 所在节点执行（RedisCluster 自动路由；逐节点扫描客户端需按 node 定向） |
| 授权 | 扫描虽属 L1，仍须过会话 + 角色 + 连接授权；未授权连接不显示"开始扫描"入口 |
| 不做 | 不支持"跨节点一致性快照"；不保证两次扫描看到同一批 key |

---

## 4. 值查看器加载顺序

打开任意 key 的统一流程：

```
0 授权检查            → 会话/角色/连接授权（05 §3.0 步骤 1~3）；不通过则不发起任何请求
1 TYPE key            → 决定渲染器；type=none → 提示"key 已不存在（可能已过期/被删除）"
2 TTL key             → -1 永久 / -2 不存在 / 秒数
3 元素数或字节数       → STRLEN / HLEN / LLEN / SCARD / ZCARD / XLEN
4 阈值判断            → 大 key（§6）→ 显示成本卡片，用户确认后才加载首页
5 拉取首页            → 按类型命令（§5）
6 遮罩判定            → 命中规则的值打码（§2.1）
7 渲染                → 文本/表格；二进制自动切 HEX；超限显示"截断"
```

| 规则 | 内容 |
| --- | --- |
| 单 key 在飞 | 同一时刻只允许一个值加载请求；切换 key 时取消上一个（`CancellationToken`） |
| 过期即时反馈 | 加载过程中 key 消失（`TYPE=none` 或空结果）→ 右侧显示"该 key 已不存在"，左侧行标灰并提示刷新 |
| 不缓存跨 key | 只缓存当前 key 的已访问页（最近 20 页），切换 key 清空 |
| 关闭不清空 | 右侧面板可关闭（Esc），下次打开同一 key 重新加载首页 |
| 只读语义 | 值查看器**没有编辑入口**；只能通过控制台写（见 01 §5 非目标） |
| 授权 | 未授权连接不可打开值；判定在服务层完成，页面不得自行放行 |
| 遮罩一致性 | 文本 / JSON / HEX / 表格 / 抽屉 / tooltip 全部先过 §2.1；HEX 视图命中时显示"已遮罩 · N 字节"，不展示原始字节 |

---

## 5. 分类型渲染规则

### 5.1 通用表结构

| 类型 | 表格列 | 分页方式 | 总数字段 | 排序能力 |
| --- | --- | --- | --- | --- |
| String | 不适用（文本区） | 分块加载 | `STRLEN` | 不适用 |
| Hash | field / value / 操作 | `HSCAN` 游标 | `HLEN` | 仅当前页排序（field 名） |
| List | 序号 / value | `LRANGE` 索引区间 | `LLEN` | 正序 / 倒序 |
| Set | member | `SSCAN` 游标 | `SCARD` | 仅当前页排序 |
| ZSet | 排名 / member / score | `ZRANGE` 索引区间 | `ZCARD` | score 正序 / 倒序 / 按 score 区间 |
| Stream | ID / 时间 / 字段数 / 预览 | `XRANGE` 区间（ID 游标） | `XLEN` | 按 ID 正序（默认）/ 倒序 |

### 5.2 String

| 项 | 规则 |
| --- | --- |
| 命令 | `STRLEN` → ≤ `string_inline_limit_kb=256` 用 `GET`；否则 `GETRANGE key offset offset+65535`（`string_chunk_kb=64`） |
| 视图 | `文本` / `JSON` / `HEX` 三个页签 |
| JSON | 仅当首字符为 `{` 或 `[` 且 `json.loads` 成功，且长度 ≤ `json_pretty_limit_kb=1024`；格式化 2 空格缩进，可折叠/展开（只做只读树，不渲染链接/HTML） |
| 大文本 | 首屏只加载 64 KB，显示"已加载 64 KB / 共 12.4 MB · [加载下一段] · [按需下载]"，`按需下载`需二次确认 |
| 空值检测 | `STRLEN=0` 视为空字符串（不是 key 不存在） |
| TTL 修改 | 面板提供"修改 TTL"（`EXPIRE`/`PERSIST`，走 L2 确认），成功后即时刷新 |

### 5.3 Hash

| 项 | 规则 |
| --- | --- |
| 命令 | `HLEN` 取总数；`HSCAN key cursor COUNT value_scan_count(200) [MATCH field模式]`，填满 `value_page_size=100` 行即止 |
| 禁止 | 任何情况下不调用 `HGETALL`（含"小 hash 直接全取"的优化） |
| 翻页 | 游标前进；已访问游标的页缓存（20 页）支持"上一页"；超出缓存只能"回到第 1 页"重新扫描 |
| 搜索 | field 模式匹配在服务端 `MATCH`；提示"匹配不减少扫描量，只减少返回" |
| 大元素 | value 字节数 > `big_element_bytes=1 MB` 时，行内不展示全文，双击抽屉单独 `HGET key field` 加载 |
| 详情抽屉 | 显示 field、字节数、文本/HEX、复制按钮；不提供编辑 |

### 5.4 List

| 项 | 规则 |
| --- | --- |
| 命令 | `LLEN` + `LRANGE key start stop`，`stop = start + value_page_size - 1` |
| 分页 | 索引分页，可跳页（"第 3 页"直接 `LRANGE 200 299`）；末页按 `LLEN` 计算 |
| 倒序 | 客户端将区间结果反转；或 `LRANGE -1-(n-1)*100 -1-n*100` 后反转，注意空区间处理 |
| 禁止 | `LRANGE 0 -1`；单次 `stop-start+1` 不得超过 500（即 `value_page_size` 上限） |
| 大列表 | `LLEN > 10,000` 时标注大 key；分页照常（索引分页成本 O(offset)，偏移越大越慢，UI 提示"深页较慢"） |
| 元素 | 单个 value > 1 MB → 抽屉加载（`LINDEX`），列表行只显示前 200 字符 |

> `LRANGE` 的复杂度是 O(S+N)，`start` 很大时仍会遍历前面元素。深页（如第 5000 页）在百万级 List 上会明显变慢：UI 在页码 > 1000 时先弹一次"深页访问可能耗时数百毫秒"提示。

### 5.5 Set

| 项 | 规则 |
| --- | --- |
| 命令 | `SCARD` + `SSCAN key cursor COUNT 200 [MATCH member模式]` |
| 提示 | 顶部常驻提示"集合无序，分页顺序不代表稳定顺序；两次扫描结果可能不同" |
| 搜索 | member 模式匹配（服务端 `MATCH`） |
| 排序 | 只提供"当前页排序"，不承诺全局有序 |
| 禁止 | `SMEMBERS`（尤其是大集合） |

### 5.6 ZSet

| 项 | 规则 |
| --- | --- |
| 命令 | `ZCARD`；默认 `ZRANGE key start stop WITHSCORES`（索引分页，可跳页） |
| 排序 | `score 正序`（`ZRANGE`）/ `score 倒序`（`ZREVRANGE` 或 `ZRANGE ... REV`，按服务端版本选择）；显示"排名"列（`start + 行号 + 1`） |
| score 区间 | 输入 `min`/`max`（支持 `-inf`/`+inf`/`(` 开区间），走 `ZRANGEBYSCORE key min max [LIMIT offset count] WITHSCORES`，每页 100 |
| 超大集合 | `ZCARD > 100,000` 且用户选择"游标模式"时改用 `ZSCAN`（顺序不稳定，标注）；默认仍用索引分页 |
| score 精度 | 原样展示服务端返回的字符串（不二次格式化，避免 `1.0` 变 `1` 的误解） |
| 禁止 | `ZRANGE 0 -1`；`ZRANGEBYSCORE` 不带 `LIMIT` |

### 5.7 Stream

| 项 | 规则 |
| --- | --- |
| 概览 | `XLEN` + `XINFO STREAM key`：`length`、`radix-tree-keys`、`first-entry`/`last-entry` 的 ID 与时间、`groups` 数量；Redis 7 额外展示 `entries-added`、`max-deleted-entry-id` |
| 条目 | 首页 `XRANGE key - + COUNT 50`；翻页 `XRANGE key (last_id + COUNT 50`（左开区间，避免重复）；倒序用 `XREVRANGE` |
| 条目行 | `ID`、时间（ID 前段毫秒转本地时间）、字段数、`field=value` 预览（前 200 字符） |
| 条目详情 | 抽屉展示全部 field-value（`XRANGE key id id`），二进制字段走 HEX |
| 消费组 | `XINFO GROUPS key` 表格：`name`、`consumers`、`pending`、`last-delivered-id`、`entries-read`、`lag`（字段缺失显示 `—`，兼容 Redis 5/6/7 差异） |
| Pending 摘要 | 选中组后 `XPENDING key group` 返回 `count/min/max/consumers`；展开明细时 `XPENDING key group - + 20`，不一次拉全量 |
| 限制 | 不执行 `XADD/XACK/XGROUP` 等写命令（除非用户走控制台并确认） |

---

## 6. 二进制、压缩与大 key

### 6.1 二进制判定与展示

| 项 | 规则 |
| --- | --- |
| 判定 | 取已加载字节（至少前 512 字节）：含 `0x00`，或不可打印字节（`<0x20` 且非 `\t\n\r`）占比 ≥ `binary_nonprint_ratio=0.30` → 二进制 |
| 展示 | 自动切到 `HEX` 视图：偏移 + 十六进制 + ASCII 对照，每行 16 字节；顶部显示"二进制 · N 字节" |
| 文本视图 | 仍可切换，但控制字符以 `\xNN` 转义显示，避免破坏布局 |
| 复制 | 只允许复制 HEX 或转义文本；不做"自动 base64 猜测" |
| 安全 | 不解析、不渲染任何来自值的 HTML/链接/图片；`JSON` 视图只输出纯文本树 |

### 6.2 压缩数据（P3，待决 Q5）

| 项 | 规则 |
| --- | --- |
| 自动解压 | **永不自动**（避免解压炸弹与内存峰值） |
| 手动识别 | 提供"尝试识别并解压"按钮：gzip 魔数 `1f 8b`、zstd 魔数 `28 b5 2f fd`、zlib 头校验 |
| 上限 | 解压后 ≤ 10 MB，超出中止并提示；解压失败只提示，不改原视图 |
| 适用范围 | 仅当前查看的单个 String 值；不批量解压 |

### 6.3 大 key 处理

| 阈值 | 值 | 打开时行为 |
| --- | --- | --- |
| String 字节数 | `big_string_bytes=10 MB` | 黄色成本卡片："该 String 12.4 MB，将分块加载（64 KB/段），预计 N 次请求"；默认只加载首段 |
| 集合元素数 | `big_collection_items=10,000` | 蓝色提示："该 Hash 有 512,000 个 field，按页加载，不影响服务端；深页较慢" |
| 单元素字节 | `big_element_bytes=1 MB` | 行内只显示预览，详情需单独请求 |
| Stream 条目数 | 同 10,000 | 提示"消费组可能积压，查看 Pending 摘要" |

**大 key 的成本边界**：集合类分页读取不会一次性拉全量，服务端每次只处理 `COUNT 200` 或 100 个元素；真正危险的是"用户手动点了 5000 次下一页"或"跨页批量导出"。因此：

- 连续翻页超过 50 页时，弹出一次"已加载 5,000 条，继续翻页会持续产生请求，是否继续？"
- 不提供"导出全部集合"功能（P2 若有需求，按 `HSCAN` 流式写入本地文件，且需显式确认）。

---

## 7. 集群差异一览（不可假设单机语义通用）

| 能力 | 单机 / Sentinel | Cluster |
| --- | --- | --- |
| `SCAN` | 单游标扫全库 | **每 master 一条游标**；单次 `SCAN` 只作用于一个节点 |
| `DBSIZE` | 全库 | 单节点；总数为逐节点求和（估算） |
| `TYPE/TTL/MEMORY USAGE` | 全局 | 必须在 key 所在节点执行；按 `CRC16(key) % 16384` 定位 |
| 多 key 命令（`MGET/DEL/EXISTS`） | 一次完成 | 同槽才可一次；跨槽需按槽分组执行后合并（见 05 §4.1） |
| 事务 `MULTI/EXEC/WATCH` | 支持 | 仅同节点同槽；跨槽直接拒绝（E05） |
| `SELECT db` | 支持 | 不支持，`db` 强制 0 |
| `FLUSHALL` | 清当前实例 | 需逐节点执行（L4，生产永久阻断） |
| 键命名建议 | 无 | 同一业务多 key 用 hash tag `{user:1001}:name` 保证同槽 |

---

## 8. 性能预算

假设：同机房 RTT 0.5~1 ms，跨网 3~5 ms；`scan_count=1000`。

| 场景 | 往返次数 | 同机房预计 | 跨网预计 | 说明 |
| --- | --- | --- | --- | --- |
| 100 万 key，首屏 500 行 | 1 次 `SCAN`（最多返回 1,000 个 key）+ 约 10 次 TTL pipeline（100/批） | < 0.1 s | < 0.3 s | 首屏只填一页 |
| 100 万 key，完整浏览一遍 | 1,000 次 `SCAN` | 1~2 s | 3~5 s | 不取大小 |
| 1000 万 key，完整浏览 | 10,000 次 | 10~20 s | 30~60 s | UI 显示进度、可暂停/取消 |
| 1000 万 key + TTL 过滤 | 10,000 次 `SCAN` + 100,000 次 pipeline | 100~200 s | 300 s+ | 触发扫描预算，必须分段 |
| 1000 万 key + 大小过滤 | 额外 50,000 次 `MEMORY USAGE`（200/批） | 不可接受 | — | 默认关闭；仅建议在小范围 `MATCH` 下使用 |
| 500 万元素 Hash 打开首页 | 1 次 `HLEN` + 约 1~2 次 `HSCAN` | < 10 ms | < 30 ms | 分页安全 |
| 500 万元素 List 第 1 页 | `LLEN` + `LRANGE 0 99` | < 5 ms | < 20 ms | — |
| 500 万元素 List 第 5000 页 | `LRANGE 499900 499999`，O(offset) | 20~100 ms | 300 ms+ | 深页提示 |
| 单 key `MEMORY USAGE SAMPLES 0`（100 MB） | 1 次，O(N) 采样 | 50~200 ms | 同 | 仅详情页使用 |

**容量自检**：扫描缓冲 3000 行 ≈ 1.2 MB；值页缓存 20 页 × 100 项 × 平均 2 KB ≈ 4 MB；整体常驻内存目标 < 200 MB（含 Qt）。

---

## 9. 禁止命令清单（浏览/值查看器代码内不得出现）

| 禁止 | 替代 |
| --- | --- |
| `KEYS pattern` | `SCAN MATCH pattern COUNT 1000` |
| `HGETALL key` | `HSCAN` 分页 + `HLEN` 计数 |
| `LRANGE key 0 -1` | `LRANGE key start stop`，`stop-start+1 ≤ 500` |
| `SMEMBERS key` | `SSCAN` 分页 + `SCARD` |
| `ZRANGE key 0 -1` / 无 `LIMIT` 的 `ZRANGEBYSCORE` | 索引分页 / 带 `LIMIT` 的区间查询 |
| `SCAN ... COUNT 100000` | `scan_count` 上限 10000，默认 1000 |
| `DEBUG OBJECT` / `DEBUG SLEEP` | 使用 `MEMORY USAGE` / `OBJECT ENCODING`（只读） |
| `MONITOR` | 诊断页明确不提供（05 §2） |
| `SORT` 大集合 | 不提供排序命令；排序在客户端对当前页进行 |
| `RANDOMKEY` 循环取样 | 不做随机抽样浏览；大 key 扫描见 06 §4.4 |

---

## 10. 验收要点

| 编号 | 用例 | 通过标准 |
| --- | --- | --- |
| V1 | 100 万 key 实例输入 `user:*` | 3 秒内出现首屏；`commandstats` 中 `keys` 调用次数为 0 |
| V2 | 500 万元素 Hash 打开 | 不出现 `HGETALL`；首页 < 500 ms；内存增长 < 20 MB |
| V3 | 扫描中点击"取消" | 1 秒内停止推进；已扫结果仍可查看；状态标记 `cancelled` |
| V4 | 在扫描过程中删除 key | 列表项打开时提示"key 已不存在"，不抛异常 |
| V5 | 3 主 3 从集群浏览 | 6 个节点中 3 个 master 全部被扫描；列表带节点列；无跨槽报错 |
| V6 | 12 MB String 打开 | 只加载 64 KB；显示总长度与"加载下一段"；不整段 `GET` |
| V7 | 二进制值（含 `0x00`） | 自动切 HEX；文本视图转义显示，无乱码、无崩溃 |
| V8 | 带 TTL 过滤扫描 1000 万 key | 30 秒到预算后暂停并给出三个选项，不无限跑 |
| V9 | 深页 List（第 5000 页） | 出现一次成本提示，仍可继续，超时给出 E03 |
| V10 | 集合翻页超过 50 页 | 弹出一次确认，取消后停止加载 |
| V11 | 打开 `user:1001:token` | key 名在列表与标题中遮罩为 `user:***:token`；值默认 `••••••` |
| V12 | `viewer` 打开敏感 key | 看不到"临时显示"按钮；tooltip 说明"需要 Operator 角色" |
| V13 | `operator` 点"临时显示" 60 秒后 | 自动回遮罩；审计出现 `reveal_sensitive`；1 分钟内第 11 次被拒 |
| V14 | 查看敏感值后切换连接 | 右侧面板清空；切回原连接时仍是遮罩状态 |
| V15 | 在 HEX 视图查看敏感二进制值 | 显示"已遮罩 · N 字节"，不出现原始字节 |
| V16 | 未授权连接点"开始扫描" | 入口不可用；提示"当前角色无该连接权限" |
