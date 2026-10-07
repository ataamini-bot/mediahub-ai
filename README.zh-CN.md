# MediaHub AI

[فارسی](README.fa.md) · [English](README.en.md) · [Русский](README.ru.md) · **简体中文**

可部署在自有服务器上的 Telegram 机器人，支持媒体下载、音频提取、文件格式转换和订阅管理。机器人界面提供波斯语和英语。

MediaHub 将媒体工具和管理功能整合到 Telegram 中：用户发送链接并选择所需输出，管理员则通过机器人管理套餐、付款、用户和报告。

本 README 提供波斯语、英语、俄语和简体中文版本。机器人界面目前支持波斯语和英语，安装程序使用英语提示。

## 项目状态

**版本 1 的候选发布版：`v1.0.0-rc.1`。** 当前实现位于 [`feature/admin-foundation`](https://github.com/ataamini-bot/mediahub-ai/tree/feature/admin-foundation) 分支。本 README 描述的是该候选版本；仓库默认分支可能仍包含较早版本。

下方安装命令固定使用提交 `f793c3fe06dafcce0239922577bf7bb64eb756ce`，该提交已通过 [CI 检查](https://github.com/ataamini-bot/mediahub-ai/actions/runs/36895762828)。版本 1 的稳定发布仍需完成实际环境验收和部署检查。

## 功能

### 媒体工具

- 从受支持的公开链接下载媒体，包括 YouTube、Instagram 公开内容、TikTok、Facebook、X、Pinterest 和 Threads。
- 根据源内容和套餐配置选择可用的视频画质，最高支持 4K。
- 在源内容提供相应信息时提取音频、封面和描述。
- 将上传的媒体转换为支持的视频或音频格式。
- 使用原始媒体标题为发送的文件命名。
- 在已发送文件下方提供详情、重新下载及其他相关输出按钮。
- 显示下载进度、传输速度、文件大小和预计剩余时间。

可用媒体和格式取决于来源网站。Instagram 仅支持公开内容，不使用 Instagram 登录 Cookie 或个人登录会话。

### 订阅和付款

- 可配置的免费及付费套餐、订阅期限和使用额度。
- 银行卡及 USDT 付款流程，由管理员手动审核。
- 在机器人的私聊管理面板中审核付款凭证和交易 ID。
- 将已批准付款的报告发送到 Payments 话题。
- 分别管理伊朗土曼（Toman）和 USDT 的内部余额，保留交易记录和冲正记录。
- 支持有效期、适用条件和使用次数限制的优惠码。

服务运营者通过机器人配置价格、收款信息、套餐设置和优惠规则。

### 管理功能

- 波斯语和英语用户界面。
- 根据用户选择的机器人语言，要求加入对应频道。
- 用户搜索、账号封禁、订阅管理和额度控制。
- 管理员角色、权限及操作日志。
- 群发消息：选择受众和语言、预览、确认、持久化发送队列和投递报告。
- 下载和付款统计。
- 用于报告的 Monitoring、Payments、Backups、System 和 Support 话题。
- 定时加密备份和恢复验证。

## 在新的 Ubuntu 服务器上安装

安装程序适用于 **amd64/x86_64 架构的 Ubuntu Server 22.04、24.04 或 26.04**，需要 root 或 sudo 权限。安装程序会安装必要的主机工具和 Docker 组件，然后通过 Docker Compose 启动应用服务。

开始前请准备以下信息：

| 配置项 | 获取方式 |
| --- | --- |
| Bot Token | 通过 [@BotFather](https://t.me/BotFather) 创建自己的机器人 |
| Telegram API ID 和 API Hash | 在 [my.telegram.org/apps](https://my.telegram.org/apps) 注册自己的应用 |
| Superadmin Telegram ID | 用作机器人超级管理员的 Telegram 账号数字 ID |
| 时区 | 默认值：`Asia/Tehran` |

**Bot Token 决定本次安装运行哪个机器人。** 超级管理员 ID 是 Telegram 用户的数字 ID，不是手机号码、机器人 ID 或 API ID。Local Bot API 服务需要 API ID 和 API Hash。

在尚不存在 `/opt/mediahub-ai` 目录的新服务器上执行：

```bash
(
  set -euo pipefail
  release='f793c3fe06dafcce0239922577bf7bb64eb756ce'
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
  installer="$(mktemp /tmp/mediahub-install.XXXXXX)"
  trap 'rm -f "$installer"' EXIT
  curl --proto '=https' -fsSL \
    "https://raw.githubusercontent.com/ataamini-bot/mediahub-ai/${release}/install.sh" \
    -o "$installer"
  sudo bash "$installer" "$release" install
)
```

该命令直接开始安装。未打标签的候选版本会在服务器上构建应用镜像。请按照提示操作；进入 Telegram 群组配置步骤前，应看到 `INSTALLATION=OK`。

安装程序会生成数据库密码和应用密钥。请在服务器之外安全保存 `/opt/mediahub-ai/.mediahub/recovery-key.txt` 的副本，并与备份文件分开存放；恢复时需要此密钥来解密备份。

安装完成后，使用超级管理员账号在 Telegram 中打开机器人，点击 **Start**，然后进入管理面板。

## 配置报告群组

1. 创建一个 Telegram 私密群组，例如 **MediaHub Reports**。
2. 在群组设置中启用 **Topics / 话题**。
3. 添加与安装时所填 Token 对应的同一个机器人。
4. 将机器人设为管理员，并授予 **Manage Topics / 管理话题** 权限。
5. 安装程序询问 `Supergroup ID` 时，输入群组的数字 ID。
6. 确认显示的群组名称无误后，同意创建报告话题。

例如，私密群组消息链接 `https://t.me/c/1234567890/5` 对应的群组 ID 为 **`-1001234567890`**。末尾的 `5` 是消息 ID。请使用自己群组的 ID。

安装程序会创建 **Monitoring**、**Payments**、**Backups**、**System** 和 **Support** 话题，并保存其 ID。之后可以添加需要用户加入的频道，并将受众设为 `fa`、`en` 或 `all`；机器人也必须是这些频道的管理员。

如需稍后继续配置群组：

```bash
sudo mediahub telegram
```

## 管理服务器

打开服务器管理菜单：

```bash
sudo mediahub
```

终端菜单和提示使用英语。机器人本身支持波斯语和英语。

| 选项 | 操作 |
| --- | --- |
| 1 | 安装 MediaHub |
| 2 | 更新至稳定版本 |
| 3 | 创建并导出备份 |
| 4 | 导入备份并恢复 |
| 5 | 配置 Telegram 群组、话题和必加频道 |
| 6 | 检查系统健康状态 |
| 7 | 回滚应用版本 |
| 8 | 配置更新源 |
| 9 | 删除容器并保留数据 |
| 0 | 退出 |

通过命令行获取健康检查报告：

```bash
cd /opt/mediahub-ai
sudo bash scripts/check_launch.sh
```

`SERVER_CHECK=OK` 表示服务器自动检查已通过。请单独验证机器人的实际运行情况：点击 Start、下载一个小文件，并检查管理面板和报告话题。

## 备份、恢复和更新

备份包含 PostgreSQL 数据、应用环境配置以及应用 `secrets` 目录中的文件，不包含下载的媒体文件、Redis 队列或 Docker 镜像。备份归档经过加密，并通过隔离环境中的恢复测试进行验证。

选择 **Create and export backup** 创建可迁移的备份，选择 **Import backup and restore** 执行恢复。请保存 `.mhb` 归档、与其匹配的 `.json` 清单文件以及恢复密钥。将同一个机器人迁移至新服务器前，请停止旧安装实例。

稳定更新需要已发布的版本标签及对应的容器镜像。更新源设置支持 HTTPS Git 仓库和容器镜像仓库。更新流程会在数据库迁移前创建并验证备份，同时保留旧应用镜像，以便在兼容时回滚。应用回滚不会自动将数据库结构降级。

完整操作流程，以及在现有安装上恢复和在新服务器上恢复的区别，请参阅[波斯语安装与恢复指南](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/INSTALLATION.md)。

## 媒体清理与保留的数据

下载和生成的媒体属于临时工作文件，会在发送后删除。后台清理负责处理中断或失败任务遗留且符合清理条件的文件；部分恢复流程及缓存清理流程设有保留时间窗口。

源链接和下载元数据会保留在数据库中，用于运营日志、额度统计和管理。付款、订阅和内部余额记录也会保留。用户界面不提供下载历史菜单，已发送文件的详情中也不会显示完整源链接。数据库备份包含这些保留记录。

删除服务器上的文件不会删除已经发送到 Telegram 的副本。具体行为请参阅[波斯语媒体文件清理说明](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/MEDIA_FILE_CLEANUP.md)。

## 系统架构

| 服务 | 职责 |
| --- | --- |
| Bot | 基于 aiogram 的 Telegram 界面、用户操作流程和管理面板 |
| Backend | FastAPI 应用、业务规则和数据库访问 |
| Worker | 执行下载及媒体处理的 Celery 任务 |
| Monitor | 服务监控和运行报告 |
| Backup | 定时加密备份及恢复验证 |
| PostgreSQL | 应用持久化数据 |
| Redis | 任务队列和临时状态 |
| Local Bot API | 用于本地媒体传输的 Telegram API 服务 |

媒体处理使用 yt-dlp 和 FFmpeg，数据库迁移由 Alembic 管理，部署配置由 Docker Compose 定义。

## 常见问题排查

| 消息或现象 | 检查方法 |
| --- | --- |
| `Telegram getChat: Bad Request: chat not found` | 确认群组 ID 正确，且已安装的机器人加入了该群组。然后重新运行 `sudo mediahub telegram`。 |
| Topics 或管理员权限错误 | 启用群组话题，并授予机器人 Manage Topics 权限。 |
| `Another installation or recovery operation is running` | 之前的菜单或操作仍持有锁。对于等待选择的旧菜单，输入 `0` 退出；对于正在执行的操作，等待其完成。 |
| 聊天列表中没有出现机器人 | 打开与所配置 Bot Token 对应的机器人用户名，然后点击 Start。 |
| 点击 Start 后没有回复 | 运行健康检查，并确认没有其他运行中的安装实例使用同一个机器人 Token。 |
| `Destination exists` | 使用现有安装的管理工具；首次安装引导程序不会覆盖已有目录。 |

## 文档与验证

以下详细指南目前使用波斯语：

- [安装、Telegram 配置、更新和恢复](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/INSTALLATION.md)
- [上线操作和验收检查](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/LAUNCH_OPERATIONS.md)
- [媒体文件名和相关输出](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/MEDIA_OUTPUTS.md)
- [媒体转换](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/MEDIA_CONVERSION.md)
- [消息群发](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/BROADCASTS.md)
- [内部余额](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/INTERNAL_CREDIT.md)
- [优惠码](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/COUPONS.md)
- [媒体清理和活动记录](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/MEDIA_FILE_CLEANUP.md)

CI 覆盖 Backend、Bot 和安装程序测试，并执行隔离的 Docker 安装及备份恢复测试。Telegram 的实际行为和来源网站的可用性需要单独验收。

请通过 [GitHub Issues](https://github.com/ataamini-bot/mediahub-ai/issues) 报告可复现的问题，注明应用提交、受影响功能和错误消息。分享日志前，请移除 Token、API Hash、加密密钥、私密链接和付款信息。
