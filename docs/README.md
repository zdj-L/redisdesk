# RedisDesk · Redis 缓存排查工作台 · 设计文档索引

> 产品形态：Windows 单机桌面应用（Python 3.11 + PySide6 + Nuitka 打包），无服务端、无云端依赖。
> 主要用途：查看缓存内容、执行命令、日常运维排查。
> 规模目标：单实例 100 万 key 流畅浏览，1000 万 key 可浏览（带进度与取消），禁止任何 `KEYS *` 式全量阻塞读取。
> 安全基线：本地三角色身份、强制 TLS、凭据 DPAPI、敏感值默认遮罩、审计哈希链（fail-closed）。

| 项 | 内容 |
| --- | --- |
| 文档集版本 | v2.0（企业级安全） |
| 日期 | 2026-11-10 |
| 状态 | 设计已完成，待评审（评审通过后进入 P0 编码）；**当前无功能代码** |
| 代码根目录 | `<project-root>` |
| 参考工程 | `<reference-project dbsync>`（只读参考，**不修改其任何文件**） |

---

## 文档清单

| 文件 | 主题 | 什么时候读 |
| --- | --- | --- |
| [01-产品定位与范围.md](./01-产品定位与范围.md) | 目标用户、使用场景、功能清单（P0~P3）、明确不做什么、与同类工具差异 | **先读**。判断"要不要做这个功能"时回来查 |
| [02-架构与技术选型.md](./02-架构与技术选型.md) | 分层与依赖方向、模块与目录骨架、线程模型（UI 不阻塞）、`RedisGateway` 抽象、领域模型、错误分类、**默认参数总表（全文档唯一数值来源）**、Nuitka 打包 | 编码前**必读**；所有数值以本文附件 A 为准 |
| [03-连接与凭据.md](./03-连接与凭据.md) | 四种连接形态、**强制 TLS 与明文例外**、SSH 指纹固定、三种凭据存储模式、`SecretLease`、**连接级授权**、三套 Redis ACL 示例、连接测试状态机 | 实现连接层、配置生产连接时 |
| [04-Key浏览与值查看器.md](./04-Key浏览与值查看器.md) | SCAN 游标分页状态机、过滤策略与成本、虚拟滚动、六种类型渲染、**敏感值遮罩与临时显示**、导出脱敏、集群差异、性能预算 | 实现浏览与值查看器时**边写边对照** |
| [05-命令控制台.md](./05-命令控制台.md) | 命令解析、风险 L1~L4 与完整命令清单、**角色 × 风险矩阵**、判定顺序、拦截/二次确认、审计 fail-closed、结果表格与遮罩、历史与回放 | 实现控制台、安全评审时 |
| [06-运维诊断.md](./06-运维诊断.md) | 慢查询、客户端列表与 KILL、INFO 分节与健康判定、内存分析、大 key 扫描（可选）；**批量删除/KILL/审计导出仅 Admin** | 实现诊断页、排障时 |
| [07-UI设计规范.md](./07-UI设计规范.md) | 布局、语义色 token（对齐 dbsync）、组件规格、**登录/首启/锁屏/角色徽标/明文红色标识/断链横幅**、交互与状态机 | 画界面、验收 UI 时 |
| [08-安全与风险清单.md](./08-安全与风险清单.md) | **权威安全文档**：边界声明、STRIDE、三角色矩阵、凭据/传输/数据保护、审计哈希链与断链流程、签名与 ACL、等保/ISO 映射、强制项代价与例外、风险 R-01~R-41、残余风险 | 安全评审 / 上线前 |
| [09-分期计划与待决问题.md](./09-分期计划与待决问题.md) | P0~P3 范围与验收、依赖与交付物、**Q1~Q8 已定结论表**、新增待决 Q9~Q13 | 排期 / 评审会 |

---

## 阅读路径

| 角色 | 建议顺序 |
| --- | --- |
| 产品 / 评审 | 01 → 09（§7.1 结论表）→ 05（§2、§3）→ 08（§1、§2） |
| 开发（P0） | 02（§11、附件 A）→ 03 → 04 → 07（§6.6/§6.7、§7.1）→ 05 |
| 开发（P1/P2） | 03（Sentinel/Cluster/SSH）→ 04（§3.6 集群差异）→ 06 |
| 测试 | 02（附件 A）→ 05（§9）→ 04（§10）→ 09（退出条件） |
| 安全评审 | 08 全篇 → 03（§3、§7、§9）→ 05（§3.0、§4.3） |
| 运维 / DBA | 03（§8 授权、§9 ACL）→ 06 → 08（§10 强制项与例外） |

---

## 关键设计决策（速览）

| 编号 | 决策 |
| --- | --- |
| D1 | 只读优先：新建连接默认 `confirm` 档，标注 `env=prod` 的连接强制 `readonly` 档，只读档在客户端断开写命令（不依赖服务端 ACL） |
| D2 | 浏览唯一入口是 `SCAN`，全应用禁止 `KEYS`（L4 阻断，仅 dev/test 显式解锁后可用） |
| D3 | 一切集合读取按页：`HSCAN/SSCAN/ZSCAN` 或索引区间 `LRANGE/ZRANGE`，禁止 `HGETALL`、`LRANGE 0 -1`、`SMEMBERS`、`ZRANGE 0 -1` |
| D4 | 连接是中立资源：`ConnectionProfile` 只有 `name/topology/transport/endpoint/...`，**没有 source/target 角色字段**；拓扑（direct/sentinel/cluster）与传输（tcp/ssh）两个正交维度组合出四种连接形态 |
| D5 | 凭据只走 Windows DPAPI（`CryptProtectData`，scope=当前用户），密文落本地 SQLite，日志/审计/导出永不含明文密码 |
| D6 | 所有 Redis IO 在后台线程：每连接一个 `ConnectionWorker`，扫描任务独立线程 + `CancellationToken`，UI 线程零阻塞 |
| D7 | 命令风险四级：L1 只读直通 / L2 写入确认 / L3 高危二次确认+输入连接名 / L4 默认阻断（FLUSHALL、SHUTDOWN、DEBUG 等） |
| D8 | 写命令与全部 L3/L4 尝试（含被拦截）写审计表，敏感参数脱敏，保留 180 天 |
| D9 | 类型过滤优先用服务端 `SCAN ... TYPE`（Redis ≥ 6.0）；TTL/大小过滤必须客户端 pipeline 补全，且受"扫描预算"约束（默认 30s / 2,000,000 次游标步进） |
| D10 | 大 key 默认不自动加载：String > 10MB 用 `GETRANGE` 分块；集合 > 10,000 元素先提示成本，按页读取 |
| D11 | Cluster 语义单独实现：逐 master 扫描、跨槽多 key 按槽分组、事务/多 key 原子操作在跨槽时直接拒绝并提示 |
| D12 | 明确不做：值编辑器（写回）、数据迁移/同步、监控大盘与告警、订阅消息客户端、RDB/AOF 解析、**跨机协作/共享会话**（本机多账号与角色是有的，见 D15）、Linux 客户端 |
| D13 | 复用 dbsync 的 UI 设计系统（`theme.py` 的语义 token + `string.Template` QSS、`widgets.py` 的 Card/Badge/DataTable/EmptyState），仅复制并扩展，不修改其文件 |
| D14 | 数值收敛到 [02-架构与技术选型.md](./02-架构与技术选型.md) 附件 A《默认参数总表》，其它文档只引用参数名，不另立默认值 |
| D15 | **本地三角色**：`viewer` / `operator` / `admin`，能力矩阵见 08 §3.2；无默认账号，首次启动强制创建管理员并展示 24 位恢复码；SSO 仅留 `IdentityProvider` 扩展点，本期不实现 |
| D16 | **连接级授权**：`connection_grant(connection_id, role)`；未授权连接"可见但不可连接"（灰色 + 徽标），Admin 可改为完全隐藏 |
| D17 | **授权单一入口**：`AuthzService.check(session, action, resource)` 在风险判定之前执行；UI 置灰只是体验，快捷键/历史回放/批量任务/诊断动作走同一入口（R7） |
| D18 | **强制 TLS**：`tls_required` 默认开、`tls_version_min=TLSv1.2`、`verify_hostname` 固定 true；明文需 Admin + 理由 ≥10 字符 + 红色标识 + 审计；SSH 主机指纹固定，无"忽略继续" |
| D19 | **Redis 侧最小权限**：连接测试探测 `default` 账号可用性与高危 ACL，命中即告警；提供 `redisdesk_ro` / `redisdesk_rw` / `redisdesk_admin` 三套可直接执行的 ACL（03 §9） |
| D20 | **敏感数据默认遮罩**：key/字段/值命中内置规则即遮罩；"临时显示"60 秒、≤10 次/分钟、计审计；导出默认脱敏（`export_default_masked` 固定开），去脱敏需 Admin + 二次口令 + 理由 |
| D21 | **审计哈希链 + fail-closed**：`record_hash = SHA256(canonical_json \|\| prev_hash)`，`audit_chain_genesis` 为 64 个 `0`；审计不可写时拒绝写命令与建连；断链进入只读应急模式 |
| D22 | **会话锁定**：空闲 `session_idle_lock_minutes=10` 锁定、绝对时长 `session_absolute_hours=8`；联动 Windows 锁屏（`WTSRegisterSessionNotification`）立即锁定；切换连接不继承解锁状态 |
| D23 | **安装与更新完整性**：Authenticode 签名 + 更新清单 RSA-3072-PSS-SHA256；拒绝未签名与降级；数据目录 `icacls /inheritance:r` 收紧 ACL；单实例互斥量 `Global\RedisDesk` 兼任审计链单写者 |
| D24 | **明确不承诺**：不是 Redis 网关、不能替代网络隔离与 ACL、不防本机管理员/内存 dump、审计非 WORM；MFA/SSO/集中日志不在本期（08 §9、§13） |

---

## 术语约定（全文档一致）

| 术语 | 含义 |
| --- | --- |
| 拓扑 / `topology` | `direct`（单机或主从只读端点）、`sentinel`、`cluster` |
| 传输 / `transport` | `tcp`（直连或经 TLS）、`ssh`（本地端口转发） |
| 连接形态 | 拓扑 × 传输的组合，见 03 文档第 1 节四种形态 |
| 连接 / `ConnectionProfile` | 一条可命名的中立连接配置，不含角色语义 |
| 保护档位 / `protection` | `readonly` / `confirm` / `unrestricted`，见 03 §8.1 |
| 风险等级 / `RiskLevel` | `L1`~`L4`，见 05 §2.1 |
| 角色 / `role` | `viewer` / `operator` / `admin`，能力矩阵见 08 §3.2 |
| 会话 / `SessionContext` | 登录后的身份 + 角色 + 解锁状态 + 绝对到期时间，见 02 §11.1 |
| 连接授权 / `connection_grant` | "某角色可使用某连接"的显式授予记录，见 08 §3.3 |
| 敏感规则 / `SensitiveRule` | 按 key 名/字段名/值特征判定是否遮罩的规则，见 08 §6.1 |
| 哈希链 / `audit_chain` | 审计记录的 `seq/prev_hash/record_hash` 链式结构，见 08 §7.3 |
| 扫描会话 / `ScanSession` | 一次"条件 + 游标 + 缓冲"的浏览过程，可暂停/继续/取消 |
| 扫描预算 / `scan_budget` | 单次扫描允许的时间与游标步进上限，见 04 文档第 3 节 |
| 大 key / Big Key | 按类型阈值判定，见 04 文档第 6 节 |
| 大元素 / Big Element | 集合类中单个 value/field 超过 1 MB |
| 审计 / Audit | 写命令与 L3/L4 尝试的本地留痕记录 |
| P0~P3 | 交付阶段，见 09 文档 |

---

## 参数速查（完整表见 02 文档附件 A）

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `scan_count` | 1000 | `SCAN` 的 COUNT 提示值，50~10000 |
| `key_page_size` | 500 | Key 列表每页行数 |
| `value_page_size` | 100 | Hash/Set/ZSet 每页、List 每次区间长度 |
| `meta_pipeline_batch` | 100 | TTL/类型等元数据批量读取条数，上限 200 |
| `scan_time_budget_s` | 30 | 带过滤扫描的单次时间预算 |
| `socket_timeout_s` | 5 | 浏览/诊断类命令读写超时 |
| `console_timeout_s` | 10 | 控制台单条命令超时，可临时调 1~120 |
| `string_inline_limit_kb` | 256 | String 直接整体加载的上限 |
| `batch_delete_max_keys` | 500 | 单次批量删除确认上限 |
| `batch_delete_total_max` | 10000 | 单次批量删除任务总量上限 |
| `memory_usage_samples_batch` | 5 | 批量内存估算采样数 |
| `audit_retention_days` | 180 | 审计保留天数 |
| `tls_version_min` | TLSv1.2 | 允许的最低 TLS 版本（不可下调） |
| `plaintext_reason_min_chars` | 10 | 明文例外理由最少字符数 |
| `password_min_length` | 12 | 本地账号口令最小长度 |
| `login_fail_lock_threshold` / `_minutes` | 5 次 / 15 分钟 | 连续登录失败锁定 |
| `session_idle_lock_minutes` | 10 | 空闲自动锁定 |
| `session_absolute_hours` | 8 | 会话绝对时长 |
| `recovery_code_length` | 24 | 首次启动恢复码长度 |
| `master_password_min_length` | 12 | 主密码最小长度 |
| `sensitive_reveal_ttl_s` | 60 | 敏感值临时显示时长 |
| `audit_hash_algo` | SHA-256 | 审计链哈希算法 |

---

## 决策状态

**已定（v2.0，按"从严"取值）**：Q1~Q8 全部拍板，结论表见 [09 §7.1](./09-分期计划与待决问题.md)，原始选项与理由保留在 §7.2 作为背景。

| 编号 | 结论一句话 |
| --- | --- |
| Q1 | `env=prod` 强制 `readonly`；要写另建 `env=test` 连接 |
| Q2 | 不做值编辑，写只走控制台 |
| Q3 | Cluster 默认全 master 扫描（并发 2） |
| Q4 | `SHUTDOWN/DEBUG/订阅/阻塞` 永久阻断；`FLUSHALL/FLUSHDB` 生产永久阻断 |
| Q5 | 压缩解包/图片预览 P3，默认关闭 |
| Q6 | 审计 180 天，导出仅 Admin |
| Q7 | 仅中文 |
| Q8 | 大 key 扫描 P3 采样版 |

**待定（Q9~Q13，不阻塞 P0，已给默认行为）**：未授权连接可见性、临时显示最低角色、是否引入异步审批、断链后全停还是只读应急、主密码是否强制。详见 [09 §7.3](./09-分期计划与待决问题.md)。

---

## 变更记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.0 | 2026-11-09 | 首版：10 篇文档，覆盖定位、架构、连接、浏览、控制台、诊断、UI、安全、分期 |
| v2.0 | 2026-11-10 | 企业级安全升级：本地三角色与连接级授权（D15~D17）、强制 TLS 与明文例外（D18）、Redis 最小权限与 ACL（D19）、敏感遮罩与导出脱敏（D20）、审计哈希链与 fail-closed（D21）、会话与锁屏（D22）、签名与目录 ACL（D23）、边界声明（D24）；Q1~Q8 拍板，新增 Q9~Q13 |
