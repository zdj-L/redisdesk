# RedisDesk 内部预览的应用内更新（未签名）

这是 **未代码签名的内部预览** 更新通道，不是正式发行或自动静默升级。应用不会把更新包当作已签名；Windows 仍可能提示未知发布者。请不要绕过安全提示或关闭杀毒软件。

## 用户怎么用

在「设置 → 应用更新」中查看当前版本并点击「检查更新」。也可勾选「启动后自动检查更新」（默认开启，每 24 小时最多一次，且仅已打包的应用会自动检查；源码运行不会自动联网）。

发现新版本后会显示版本号、安装包名称、大小和 SHA-256。只有你确认后才会下载。下载后必须通过大小与 SHA-256 校验才会启动安装向导；摘要缺失或哈希不一致会删除文件并拒绝安装。

安装程序就是现有的 Inno Setup `setup.exe`，不是便携版 ZIP。安装程序不会强制结束正在运行的 RedisDesk，也不会附带连接、密码或用户数据。替换程序文件前需要自行退出应用。数据目录仍为内部预览隔离路径：

`%LOCALAPPDATA%\RedisDesk-Internal\1.0.0`

1.0.x 补丁更新继续使用该目录，不会改到开发环境的 `%LOCALAPPDATA%\RedisDesk`。

## 发布格式（人工发布第一个 Release）

更新通道读取 GitHub 仓库 `zdj-L/redisdesk` 的 **Releases**（`/releases/latest`）。本仓库目前还没有可用的首个 Release，也还没有能在 CI 里可靠打出 Windows Nuitka 安装包的工作流。请在本机用既有脚本构建后再人工发布：

1. 按 [内部便携说明](./INTERNAL_PORTABLE.md) 和 [内部安装包说明](./INTERNAL_INSTALLER.md) 在本机构建 `setup.exe`（不要把用户数据打进安装包）。
2. 生成清单（不上传、不签名、不读取密钥）：

```
python scripts/write_update_manifest.py release/RedisDesk-<版本>-internal-win64-setup.exe --version <版本>
```

这会在安装包旁写出 `latest.json` 和 `SHA256SUMS.txt`。

3. 在 GitHub 创建 tag（如 `v1.0.1`）和 Release，至少上传：
   - `RedisDesk-<版本>-internal-win64-setup.exe`
   - `latest.json`
   - `SHA256SUMS.txt`

`latest.json` 示例：

```json
{
  "name": "RedisDesk",
  "version": "1.0.1",
  "channel": "internal-preview",
  "unsigned": true,
  "notes": "中文更新说明",
  "asset": {
    "name": "RedisDesk-1.0.1-internal-win64-setup.exe",
    "url": "https://github.com/zdj-L/redisdesk/releases/download/v1.0.1/RedisDesk-1.0.1-internal-win64-setup.exe",
    "size": 12345678,
    "sha256": "64位十六进制摘要"
  }
}
```

若没有 `latest.json`，应用会尝试解析 Release 里的 `setup.exe` 与 `SHA256SUMS.txt`。没有 SHA-256 时会拒绝安装。只提供便携版 exe/zip 时不会作为更新包。

本任务没有添加会覆盖 `main` 或在 CI 里编译 Nuitka 的 Actions 工作流：Windows 打包仍依赖本机工具链，且发布需要维护者上传安装包。代码签名、GitHub Release 权限和正式发行流程仍待人工处理。
