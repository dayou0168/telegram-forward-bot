# Telegram 群组转发机器人

这是一个私用 Telegram 转发机器人。机器人被拉入群后会自动登记群名称和 `chat_id`，宿主可以授权操作人，宿主和操作人可以在私聊里创建分组、把指定群加入或移出分组，并把私聊给机器人的单条消息复制发送到指定分组内的所有群。

## 功能

- 机器人入群自动登记群组。
- 宿主由服务器环境变量固定指定。
- 宿主可以添加、禁用操作人。
- 宿主可以给每个操作人分配可访问分组；操作人只能看到、管理、发送到已授权分组。
- 宿主创建的操作人可以再创建一层下级操作人，并只能把自己有权限的分组授权给下级；下级操作人不能继续创建操作人。
- 操作人可以创建、删除、重命名分组。
- 操作人可以给分组添加或删除指定群组。
- 操作人添加群组时，只能看到自己已经加入的群组；宿主可以看到全部已登记群组。
- 宿主和操作人可以选择分组，确认后群发消息。
- 非宿主、非操作人的私聊和按钮操作无效。
- 发送时使用 `copyMessage`，目标群不会显示原始转发来源。

## Ubuntu 一键部署

项目现在提供两种服务器部署方式，并支持直接 `curl` GitHub 上的脚本完成安装，不需要先手动 clone 项目。脚本会自动安装基础依赖、拉取或更新项目代码、创建 env 文件，然后启动机器人。

当前仓库是 Public，服务器可以直接读取安装脚本和代码。如果以后把仓库改回 Private，再使用 GitHub token 读取 raw 脚本和 clone 代码。

如果服务器上已经有重要业务，想跳过系统升级，可以加：

```bash
--skip-system-upgrade
```

### 方式一：curl 原生 Linux + systemd

适合不想使用 Docker 的服务器。脚本会自动安装 git、Python、venv 和依赖，把项目放到 `/opt/tg-forward-bots/telegram-forward-bot`，并创建 systemd 服务，服务名类似 `tg-forward-notice_bot.service`。

```bash
curl -fsSL https://raw.githubusercontent.com/dayou0168/telegram-forward-bot/main/deploy/bootstrap.sh \
  | sudo bash -s -- \
  --mode native \
  --bot-name notice-bot \
  --bot-token "你的BotFather令牌" \
  --owner-user-ids "你的Telegram用户ID"
```

查看日志：

```bash
bash deploy/run-native-bot.sh notice-bot logs
```

重启：

```bash
bash deploy/run-native-bot.sh notice-bot restart
```

停止：

```bash
bash deploy/run-native-bot.sh notice-bot stop
```

### 方式二：curl Docker Compose

适合希望隔离更干净、后续升级更省心的服务器。脚本会自动安装 git、Docker Engine 和 Docker Compose 插件，把项目放到 `/opt/tg-forward-bots/telegram-forward-bot`，创建 env 文件，然后启动对应机器人实例。

```bash
curl -fsSL https://raw.githubusercontent.com/dayou0168/telegram-forward-bot/main/deploy/bootstrap.sh \
  | sudo bash -s -- \
  --mode docker \
  --bot-name notice-bot \
  --bot-token "你的BotFather令牌" \
  --owner-user-ids "你的Telegram用户ID"
```

查看日志：

```bash
./deploy/run-bot.sh notice-bot logs
```

重启：

```bash
./deploy/run-bot.sh notice-bot restart
```

停止：

```bash
./deploy/run-bot.sh notice-bot down
```

同一台服务器要跑多个机器人，只需要换不同的 `--bot-name`、`--bot-token` 和数据库 env 文件。例如 `notice-bot`、`customer-bot` 会分别使用 `deploy/envs/notice-bot.env`、`deploy/envs/customer-bot.env`。

如果以后把 GitHub 仓库改成 Public，命令可以去掉 token，直接这样安装：

```bash
curl -fsSL https://raw.githubusercontent.com/dayou0168/telegram-forward-bot/main/deploy/bootstrap.sh \
  | sudo bash -s -- \
  --mode docker \
  --bot-name notice-bot \
  --bot-token "你的BotFather令牌" \
  --owner-user-ids "你的Telegram用户ID"
```

### 已经 clone 项目的备用方式

如果项目代码已经在服务器上，也可以进入目录后直接运行本地安装脚本：

```bash
cd /opt/tg-forward-bots/telegram-forward-bot
sudo bash deploy/install-native.sh --bot-name notice-bot
sudo bash deploy/install-docker.sh --bot-name notice-bot
```

## 单机器人快速启动

1. 复制环境变量文件：

```bash
cp .env.example .env
```

2. 编辑 `.env`：

```env
BOT_TOKEN=你的BotFather令牌
OWNER_USER_IDS=你的Telegram用户ID
DATABASE_URL=sqlite+aiosqlite:///./data/my-bot.db
```

多个宿主 ID 用英文逗号分隔：

```env
OWNER_USER_IDS=123456789,987654321
```

3. Docker 启动：

```bash
docker compose -f compose.yaml up -d --build
```

4. 查看日志：

```bash
docker compose -f compose.yaml logs -f
```

## 同一台服务器部署多个机器人

推荐使用同一份代码，通过不同的 env 文件启动多个实例。这个项目使用 long polling，不开放端口，所以多个机器人不会抢端口。

目录结构：

```text
deploy/envs/
- notice-bot.env
- customer-bot.env
- test-bot.env
```

每个 env 文件都要有自己的 `BOT_TOKEN`、`OWNER_USER_IDS` 和独立数据库文件：

```env
BOT_TOKEN=第一个机器人Token
OWNER_USER_IDS=123456789
DATABASE_URL=sqlite+aiosqlite:///./data/notice-bot.db
UNAUTHORIZED_REPLY=true
SEND_DELAY_SECONDS=0.08
```

如果使用一键脚本，不需要手动复制模板。脚本会自动创建对应 env 文件。

复制模板：

```bash
cp deploy/envs/example-bot.env deploy/envs/notice-bot.env
cp deploy/envs/example-bot.env deploy/envs/customer-bot.env
```

启动某个机器人：

```bash
chmod +x deploy/run-bot.sh
./deploy/run-bot.sh notice-bot up
./deploy/run-bot.sh customer-bot up
```

如果使用原生 systemd 部署：

```bash
sudo bash deploy/install-native.sh --bot-name notice-bot
bash deploy/run-native-bot.sh notice-bot logs
```

如果使用 Docker Compose 部署：

```bash
sudo bash deploy/install-docker.sh --bot-name notice-bot
./deploy/run-bot.sh notice-bot logs
```

查看日志：

```bash
./deploy/run-bot.sh notice-bot logs
```

重启、停止：

```bash
./deploy/run-bot.sh notice-bot restart
./deploy/run-bot.sh notice-bot down
```

脚本内部会使用不同的 Compose project 名，例如 `tg_forward_notice_bot` 和 `tg_forward_customer_bot`，所以容器、网络互相隔离；数据库都保存在本项目的 `data/` 目录下，但文件名不同。

不用脚本也可以直接运行：

```bash
BOT_ENV_FILE=deploy/envs/notice-bot.env docker compose -f compose.yaml -p tg_forward_notice_bot up -d --build
BOT_ENV_FILE=deploy/envs/customer-bot.env docker compose -f compose.yaml -p tg_forward_customer_bot up -d --build
```

项目同时保留 `docker-compose.yml` 兼容旧习惯，但标准 Compose 入口是 `compose.yaml`。部署脚本会显式使用 `compose.yaml`。

## 宝塔 Docker Compose 部署

如果要在宝塔面板的 Docker Compose 容器编排里部署，推荐使用项目里的：

```text
compose.baota.yaml
```

宝塔 Compose 模板更适合使用绝对路径，所以这个文件默认项目目录为：

```text
/www/wwwroot/telegram-forward-bot
```

这个宝塔模板直接拉取 GitHub Container Registry 镜像：

```text
ghcr.io/dayou0168/telegram-forward-bot:latest
```

`BOT_TOKEN`、`OWNER_USER_IDS` 直接写在 Compose 环境变量里，并使用 Docker 命名卷保存 SQLite 数据库。宝塔里只需要改机器人 token 和 UID 两行，然后创建 Compose 项目即可，不需要额外创建 env 文件、上传源码目录、准备 Dockerfile 或手动设置 `data/` 权限。

如果宝塔拉镜像时报 `unauthorized` 或 `denied`，需要在 GitHub Packages 里把 `telegram-forward-bot` 容器包设置为 Public，或在服务器上登录 GHCR。

如果宝塔提示 `project name must not be empty`，创建 Compose 项目时项目名称填写：

```text
tg-forward-notice-bot
```

详细步骤见：

```text
docs/BAOTA_DOCKER_COMPOSE.md
```

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m bot.main
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m bot.main
```

## 使用流程

1. 把机器人拉入需要转发的 Telegram 群。
2. 宿主私聊机器人发送 `/start`。
3. 进入「权限管理」添加、删除操作人，或编辑操作人备注。
4. 在「权限管理」里点击某个操作人，进入「分组权限」，勾选他可以访问的分组。
5. 进入「分组管理」创建分组。
6. 进入某个分组，点击「添加群组」或「批量添加」把已登记的群加入分组。
7. 点击「发送消息」，选择分组，发送需要投递的单条消息。
8. 点击「确认发送」，机器人会把这条消息复制到分组内所有群。

## 快捷发送

如果觉得按钮确认流程太繁琐，推荐使用主菜单里的「快捷发送」按钮：

```text
/menu
→ 快捷发送
→ 选择分组
→ 发下一条 / 连续发送
```

为了适配 Telegram 电脑版，机器人命令菜单里默认只保留 `/start` 和 `/menu`。`/quick`、`/to`、`/cancel` 仍然支持手动输入，但不会放在左下角命令菜单里，避免点了命令还要继续补参数。

备用中文消息如下。

一次性快捷发送：

```text
发送到 分组名
```

然后发送下一条消息，机器人会直接投递到这个分组，不再二次确认。发送完成后自动退出快捷发送。

连续快捷发送：

```text
快捷发送 分组名
```

进入连续快捷发送后，你私聊机器人发送的每条单条消息都会自动投递到这个分组，直到发送：

```text
取消
```

也可以先把内容发给机器人，然后回复那条内容消息：

```text
发送到 分组名
```

机器人会把你回复的那条消息直接发送到指定分组。

英文备用命令 `/to 分组名`、`/quick 分组名`、`/cancel` 仍然可用，但不放在 Telegram 电脑版命令菜单里。

## UID 查询

添加操作人需要 Telegram 数字 UID。宿主或可创建下级操作人的操作人，可以在主菜单点击「查询UID」，机器人会发出一个「选择用户」按钮，点击后会打开 Telegram 原生用户选择界面，选择用户后机器人会返回该用户 UID 和可复制的添加格式。

也可以私聊发送：

```text
查询UID
/id
```

未授权用户发送 `/id` 或「查询UID」时，只会返回自己的 UID。

## 权限升级兼容

如果你是在已有数据上升级到“分组权限”版本，机器人启动时会做一次兼容初始化：当分组权限表为空时，会把已有操作人授权到已有全部分组，保持旧版本行为。

初始化只会在权限表为空时执行一次。之后你在「权限管理」里取消某个操作人对某个分组的权限，系统不会再自动加回去。

## 项目固化与 GitHub

项目根目录里的 `PROJECT_CONTEXT.md` 记录了当前业务设计、权限规则、部署方式和后续维护注意事项。以后换新的对话窗口时，先让助手阅读 `PROJECT_CONTEXT.md` 和本 `README.md`，就能快速接上上下文。

上传到 GitHub 的具体步骤见：

```text
docs/GITHUB_SETUP.md
```

注意不要提交 `.env`、`deploy/envs/*.env` 和 `data/*.db`。这些文件包含机器人 token、宿主 UID 或运行数据，已经在 `.gitignore` 中排除。

## 群内回复通知

机器人发送消息到群后，如果群成员直接回复机器人发出的那条消息，机器人会把这条回复私聊通知给宿主，并通知当时触发发送的操作人。

纯文字回复会直接合并到通知内容里，不再额外复制一条原消息。图片、视频、文件、语音等媒体回复会优先复制原媒体，并把群名、发送人、任务号和内容预览放进媒体说明里，快捷按钮也会挂在同一条消息下面；贴纸、位置等不适合带说明的消息会发送一条摘要通知，再附上原消息。

通知消息下面会带快捷按钮：

- 快速回复：点击后，私聊机器人发送下一条消息，机器人会把它发回原群，并回复那条群消息。
- 定位回复消息：跳转到群里对方回复的那条消息。
- 定位原投递消息：跳转到机器人最初发送到群里的消息。

跳转链接要求你自己的 Telegram 账号能进入对应群。公开群和私有超级群通常可以打开；普通旧群可能没有 Telegram 可用链接。

注意：如果群成员只是普通发言，没有点“回复”机器人消息，机器人通常看不到这条消息，除非你在 BotFather 里关闭 Group Privacy 或让机器人具备读取群消息的权限。

## 注意事项

- 第一版支持单条 Telegram 消息投递，包括文本、图片、视频、文件等；媒体相册建议拆成单条发送。
- 机器人必须仍在目标群内，且有发送消息权限。
- 如果需要按操作人是否入群来过滤群组，建议把机器人设为这些群的管理员；Telegram Bot API 的 `getChatMember` 对其他用户只有在机器人是群管理员时才保证可用。
- 群组升级为超级群时，机器人会记录新的 `chat_id`，旧群记录会标记为 `migrated`。
- SQLite 适合第一版和中小规模使用；后续可切换 PostgreSQL。
