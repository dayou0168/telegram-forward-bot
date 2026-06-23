# 宝塔 Docker Compose 镜像部署

这个文档用于在宝塔面板的 Docker Compose 容器编排里部署机器人。

机器人不需要开放端口，也不需要反向代理。它使用 Telegram long polling，只要服务器能访问 Telegram API 和 GHCR 即可。

## 1. 部署方式

项目使用 GitHub Actions 自动构建 Docker 镜像，并推送到 GitHub Container Registry：

```text
ghcr.io/dayou0168/telegram-forward-bot:0.2.3
```

宝塔模板 `compose.baota.yaml` 会直接拉这个镜像运行：

- 不需要服务器提前 clone 项目。
- 不需要服务器已有 `Dockerfile`。
- 不需要在容器启动时下载源码。
- 不需要额外创建 env 文件。
- 不需要手动创建或授权 `data/` 目录。
- 数据库保存在 Docker 命名卷 `tg_forward_notice_data`。

## 2. 确认镜像已发布

每次推送 `main` 分支或 `v*.*.*` 版本标签后，GitHub Actions 会运行 `Docker Image` 工作流。

在 GitHub 仓库里查看：

```text
Actions -> Docker Image
```

工作流成功后，镜像地址是：

```text
ghcr.io/dayou0168/telegram-forward-bot:0.2.3
```

如果宝塔拉镜像时报 `unauthorized` 或 `denied`，说明 GHCR 包还不是公开可拉取。进入 GitHub：

```text
仓库主页 -> 右侧 Packages -> telegram-forward-bot -> Package settings -> Change visibility -> Public
```

## 3. 在宝塔添加 Compose 模板

进入宝塔面板：

```text
Docker -> Compose模板 -> 添加
```

模板名可以填：

```text
telegram-forward-bot-notice
```

模板内容复制 `compose.baota.yaml`，或者直接复制下面内容：

```yaml
name: tg-forward-notice-bot

services:
  tg-forward-bot:
    image: ghcr.io/dayou0168/telegram-forward-bot:0.2.3
    container_name: tg-forward-notice-bot
    restart: unless-stopped
    environment:
      BOT_TOKEN: "替换为你的BotFather令牌"
      OWNER_USER_IDS: "替换为你的Telegram数字UID"
      DATABASE_URL: "sqlite+aiosqlite:///./data/notice-bot.db"
      UNAUTHORIZED_REPLY: "true"
      REPLY_AUTO_EDIT_ORIGINAL: "true"
      REPLY_ORIGINAL_REPLACEMENT_TEXT: "已收到回复，原投递内容已隐藏。"
      SEND_DELAY_SECONDS: "0.08"
    volumes:
      - tg_forward_notice_data:/app/data

volumes:
  tg_forward_notice_data:
    name: tg_forward_notice_data
```

只需要改两行：

```yaml
BOT_TOKEN: "替换为你的BotFather令牌"
OWNER_USER_IDS: "替换为你的Telegram数字UID"
```

如果宝塔提示 `project name must not be empty`，创建 Compose 项目时项目名称填写：

```text
tg-forward-notice-bot
```

## 4. 创建 Compose 项目

进入：

```text
Docker -> Compose -> 添加Compose项目
```

选择刚创建的 Compose 模板。

项目名建议填：

```text
tg-forward-notice-bot
```

创建后宝塔会拉取 GHCR 镜像并启动容器。

注意：数据库保存在 Docker 命名卷 `tg_forward_notice_data`。重建/重启容器不会丢数据，但如果在宝塔里删除了这个数据卷，分组、操作人、已登记群组都会丢失。

## 5. 查看日志

在宝塔里进入：

```text
Docker -> Compose -> 对应项目 -> 日志
```

或者在服务器终端执行：

```bash
docker logs -f tg-forward-notice-bot
```

看到机器人正常启动后，私聊机器人发送：

```text
/start
```

## 6. 多机器人部署

同一台服务器跑多个机器人时，不要复用同一个项目名、服务名、容器名、数据库文件名和数据卷名。

例如第二个机器人：

```yaml
name: tg-forward-customer-bot

services:
  tg-forward-customer-bot:
    image: ghcr.io/dayou0168/telegram-forward-bot:0.2.3
    container_name: tg-forward-customer-bot
    restart: unless-stopped
    environment:
      BOT_TOKEN: "第二个机器人的BotFather令牌"
      OWNER_USER_IDS: "第二个机器人的宿主Telegram数字UID"
      DATABASE_URL: "sqlite+aiosqlite:///./data/customer-bot.db"
      UNAUTHORIZED_REPLY: "true"
      REPLY_AUTO_EDIT_ORIGINAL: "true"
      REPLY_ORIGINAL_REPLACEMENT_TEXT: "已收到回复，原投递内容已隐藏。"
      SEND_DELAY_SECONDS: "0.08"
    volumes:
      - tg_forward_customer_data:/app/data

volumes:
  tg_forward_customer_data:
    name: tg_forward_customer_data
```

## 7. 升级代码

代码推送到 GitHub 并创建版本标签后，`Docker Image` 工作流会自动构建并推送对应版本镜像。

升级服务器上的机器人：

```text
宝塔 -> Docker -> Compose -> 对应项目 -> 重建/重启
```

如果宝塔没有自动拉取新镜像，可以先删除旧镜像：

```bash
docker rmi ghcr.io/dayou0168/telegram-forward-bot:0.2.3
```

然后再在宝塔里重建 Compose 项目。

## 8. 备份数据库

数据库在 Docker 命名卷里。可以通过临时容器打包备份：

```bash
docker run --rm \
  -v tg_forward_notice_data:/data \
  -v "$PWD":/backup \
  busybox \
  tar czf /backup/tg_forward_notice_data.tar.gz -C /data .
```

恢复时：

```bash
docker run --rm \
  -v tg_forward_notice_data:/data \
  -v "$PWD":/backup \
  busybox \
  sh -c "cd /data && tar xzf /backup/tg_forward_notice_data.tar.gz"
```

## 9. 常见问题

### 拉取镜像 unauthorized / denied

GHCR 包可能还是 private。把 package visibility 改成 Public，或在宝塔服务器上先登录 GHCR：

```bash
echo "你的GitHub Token" | docker login ghcr.io -u dayou0168 --password-stdin
```

### manifest unknown

镜像还没有构建成功。去 GitHub：

```text
Actions -> Docker Image
```

确认工作流成功后再启动宝塔 Compose。

### 容器启动后立刻退出

先看日志。常见原因：

- `BOT_TOKEN` 没填或填错。
- `OWNER_USER_IDS` 不是 Telegram 数字 UID。
- Telegram API 在当前服务器网络不可达。

### 重新安装后看不到机器人已经在的群

这是 Telegram Bot API 的限制：机器人不能主动列出自己已经加入过的全部群。群记录来自三类情况：

- 机器人被新拉入群时的入群事件。
- 群里有人发消息，机器人收到后自动登记当前群。
- 群里有人发送 `/register`，机器人主动登记当前群。

如果重装时换了新数据库或删除了 Docker 数据卷，旧群记录就会消失；Telegram 不会因为机器人“已经在群里”再次推送入群事件。

想尽量自动恢复，可以在 BotFather 里关闭 Group Privacy：

```text
/setprivacy
选择你的机器人
Disable
```

关闭后，已经有机器人的群里只要有人发一条消息，机器人就会自动重新登记。没有任何新消息的群，仍需要发送：

```text
/register
```

机器人回复“已登记当前群组”后，回到机器人私聊，在「分组管理」把这个群重新添加到对应分组。

以后升级或重建容器时，不要删除 Docker 命名卷：

```text
tg_forward_notice_data
```

这个卷保存 SQLite 数据库。
