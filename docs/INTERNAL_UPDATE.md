# RedisDesk 内部预览的应用内更新（未签名）

这是 **未代码签名的内部预览** 更新通道，不是正式发行或自动静默升级。应用不会把更新包当作已签名；Windows 仍可能提示未知发布者。请不要绕过安全提示或关闭杀毒软件。

## 用户怎么用

在「设置 → 应用更新」中查看当前版本并点击「检查更新」。也可勾选「启动后自动检查更新」（默认开启，每 24 小时最多一次，且仅已打包的应用会自动检查；源码运行不会自动联网）。

发现新版本后会显示版本号、安装包名称、大小和 SHA-256。只有你确认后才会下载。下载后必须通过大小与 SHA-256 校验才会启动安装向导；摘要缺失或哈希不一致会删除文件并拒绝安装。

安装程序就是现有的 Inno Setup `setup.exe`，不是便携版 ZIP。安装程序不会强制结束正在运行的 RedisDesk，也不会附带连接、密码或用户数据。替换程序文件前需要自行退出应用。数据目录仍为内部预览隔离路径：

`%LOCALAPPDATA%\RedisDesk-Internal\1.0.0`

1.0.x 补丁更新继续使用该目录，不会改到开发环境的 `%LOCALAPPDATA%\RedisDesk`。

## 发布格式（GitHub Actions）

更新通道读取 GitHub 仓库 `zdj-L/redisdesk` 的 **Releases**（`/releases/latest`）。Windows 安装包由 `.github/workflows/windows-installer.yml` 打出：推送 `v*` 标签，或在 Actions 里手动 `workflow_dispatch`。工作流在 `windows-latest` 上跑既有的 Nuitka / Inno 脚本，编完 exe 后现算 SHA-256 再编译安装包，并在 CI 里刷新 source allowlist。不做代码签名。

发布成功后会上传：

1. `RedisDesk-<版本>-internal-win64-setup.exe`
2. `latest.json`
3. `SHA256SUMS.txt`

标签与 `latest.json` 中的下载地址对齐为 `v<版本>`（例如当前 1.0.0 为 `v1.0.0`）。应用内检查仅当 Release 的版本 **新于** 当前安装版本时才会提示；同为 1.0.0 的 Release 不会被认成有新版本。请发布为正式 Release（非 draft、非 prerelease），否则 `/releases/latest` 找不到。

`latest.json` 示例：

```json
{
  "name": "RedisDesk",
  "version": "1.0.0",
  "channel": "internal-preview",
  "unsigned": true,
  "notes": "中文更新说明",
  "asset": {
    "name": "RedisDesk-1.0.0-internal-win64-setup.exe",
    "url": "https://github.com/zdj-L/redisdesk/releases/download/v1.0.0/RedisDesk-1.0.0-internal-win64-setup.exe",
    "size": 12345678,
    "sha256": "64位十六进制摘要"
  }
}
```

若没有 `latest.json`，应用会尝试解析 Release 里的 `setup.exe` 与 `SHA256SUMS.txt`。没有 SHA-256 时会拒绝安装。只提供便携版 exe/zip 时不会作为更新包。

仍可在本机用既有脚本打包，然后运行 `python scripts/write_update_manifest.py release/RedisDesk-<版本>-internal-win64-setup.exe --version <版本>` 生成清单并人工上传。该命令不上传、不签名、不读取密钥。本机编译安装包时可以 `scripts/build_installer.ps1 -ExpectedExeSha <当前 exe 的 SHA-256>`，以避免被旧的硬编码哈希卡住。
