# RedisDesk

Windows 单机桌面 Redis 排查客户端。查看缓存、执行命令、日常运维排查。无服务端、无云端依赖。

设计文档见 [`docs/README.md`](docs/README.md)（文档集 v2.0）。本 README 只说明如何安装、运行与测试当前代码。

## 环境

- Windows 10 21H2+ / Windows 11
- Python 3.11 或更高（当前开发机若只有 3.13，与「≥ 3.11」要求兼容）
- 建议使用项目内虚拟环境 `.venv`

## 安装

在项目根目录 `<project-root>`：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

依赖锁定在 `pyproject.toml`：PySide6-Essentials 6.8.2.1、redis 5.2.1、paramiko 3.5.1、cryptography ≥ 42。

## 运行

```powershell
python -m redisdesk.app.main
```

按用户决策，应用启动后直接进入主窗口，不再显示应用登录、首次管理员引导或锁屏。默认以本地单操作员（管理员角色）打开；授权、策略与审计后端仍然生效。

数据目录：`%LOCALAPPDATA%\RedisDesk\`（可用环境变量 `REDISDESK_APPDATA` 覆盖，便于测试）。

## 第一次连接

1. 打开应用后进入「连接」页，新建 `direct + tcp` 连接（默认本机管理员会话）。
2. 默认必须启用 TLS（TLSv1.2+，校验证书主机名）。自签 CA 请填写 CA 路径。
3. 未开 TLS 的实例默认连不上。仅 Admin 可勾选「允许明文」并填写不少于 10 字的理由；列表会显示红色「明文」标识，并写入审计。
4. `env=prod` 的连接会被强制设为只读档（`readonly`），不能写、不能开 `allow_l4`。要写入请另建 `env=test` 连接。
5. 新建连接会自动授予 Admin；Operator / Viewer 需要 Admin 显式授权后才能连接。未授权连接默认可见但不可连接。

## 只读保护（务必了解）

「只读」有三层，是「与」关系，只改一层不会生效：

| 层 | 含义 |
| --- | --- |
| 角色 | Viewer 不能执行写命令；Operator 不能执行 L4；Admin 也不能绕过永久阻断项 |
| 保护档位 | 生产连接强制 `readonly`，只读档拒绝全部写命令 |
| 命令风险 | `KEYS` / `FLUSHALL` 等按 L4 处理；`SHUTDOWN` / `DEBUG` / 订阅 / 阻塞命令对所有人永久阻断 |

写操作只走控制台，浏览页没有值编辑器。浏览只使用 `SCAN`，不会调用 `KEYS`。审计哈希链 fail-closed：审计不可写或断链时，拒绝写命令与建连，进入只读应急模式。

## 测试

```powershell
python -m pytest
```

核心单测（parser / policy / codec / scan / authz / 审计链）不依赖真实 Redis。带 TLS 的 Redis ≥ 6.0 与代码签名证书用于 P0 验收项（百万 key 浏览、安装包签名等），需要时再补集成测试。

## 打包

Windows x64 内部预览使用独立的 `.build-venv`、Nuitka 4.3.5 和本机已安装的 VS2022 MSVC 工具链。不要把打包依赖装进开发用的 `.venv`。

```powershell
python -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install . "Nuitka==4.3.5" ordered-set zstandard
.\scripts\build_nuitka.ps1
```

独立运行产物：`build/portable/portable_entry.dist/RedisDesk.exe`。请保留整个目录。允许清单归档与 ZIP/SHA256 由 `scripts/stage_portable.py` 生成；流程与验收限制见 [内部便携说明](docs/INTERNAL_PORTABLE.md)。构建/发布/工件目录只留在本机，不纳入版本库。

Windows 安装包（未签名内部预览）由 `scripts/build_installer.ps1` 和 `scripts/redisdesk-installer.iss` 从上述独立运行目录生成，输出 `release/RedisDesk-1.0.0-internal-win64-setup.exe`。说明、限制和数据目录见 [内部安装包说明](docs/INTERNAL_INSTALLER.md)。

这是未代码签名的内部预览。已打包的应用可在「设置 → 应用更新」中检查 GitHub Releases 上的新安装包（需确认后下载，并校验 SHA-256；未签名，不会当作已签名）。尚未发布首个 Release 前，检查结果会说明没有新版本。发布格式见 [内部更新说明](docs/INTERNAL_UPDATE.md)。另一台未安装 Python/VS 的干净 Windows 机器仍需单独验收。不要绕过 Windows 安全提示或关闭杀毒软件。便携入口默认数据目录为 `%LOCALAPPDATA%\RedisDesk-Internal\1.0.0`，与开发数据分离。

## 当前实现状态

内部预览已包含连接管理、只读异步 INFO 诊断（七组指标）、目录 SCAN/值浏览、策略门控控制台、可选 SSH 跳板转发、本地会话/审计保护，以及主题与设置持久化。慢查询和客户端列表仍延期；Sentinel/Cluster 不作为已验收的生产能力。

发布前回归：源码测试 166 项通过；打包后的 EXE 使用一次性回环 RESP、注入的假 SSH、DPAPI 和临时设置完成冒烟。这不是真实生产 Redis/SSH、代码签名或干净机器验收。`docs/` 下部分历史设计文档可能仍描述更早计划。

