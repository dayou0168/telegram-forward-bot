# 宝塔 Docker Compose 部署

这个文档用于在宝塔面板的 Docker Compose 容器编排里部署机器人。

机器人不需要开放端口，也不需要反向代理。它使用 Telegram long polling，只要服务器能访问 Telegram API 即可。

`compose.baota.yaml` 已经做成宝塔一键模板：不需要额外创建 env 文件，也不需要手动设置数据库目录权限。只要项目源码已经放到 `build.context` 指定目录，宝塔里粘贴模板、改 `BOT_TOKEN` 和 `OWNER_USER_IDS` 两行，然后创建 Compose 项目即可。

如果宝塔提示 `project name must not be empty`，说明创建 Compose 项目时项目名称为空。模板里已经内置：

```yaml
name: tg-forward-notice-bot
```

如果宝塔界面仍然要求填写「项目名称」，就填：

```text
tg-forward-notice-bot
```

## 1. 准备项目目录

建议固定放在：

```bash
/www/wwwroot/telegram-forward-bot
```

如果仓库是 Private，可以先在服务器终端里用 GitHub token 拉取代码：

```bash
read -rsp "GitHub token: " GITHUB_TOKEN
echo

mkdir -p /www/wwwroot
cd /www/wwwroot
git clone "https://x-access-token:${GITHUB_TOKEN}@github.com/dayou0168/telegram-forward-bot.git" telegram-forward-bot
unset GITHUB_TOKEN
cd /www/wwwroot/telegram-forward-bot
```

如果不想在服务器使用 token，也可以在本地下载项目压缩包，然后通过宝塔「文件」上传并解压到 `/www/wwwroot/telegram-forward-bot`。

## 2. 在宝塔添加 Compose 模板

进入宝塔面板：

```text
Docker -> Compose模板 -> 添加
```

模板名可以填：

```text
telegram-forward-bot-notice
```

模板内容使用项目里的 `compose.baota.yaml`，或者直接复制下面内容：

```yaml
name: tg-forward-notice-bot

services:
  tg-forward-bot:
    build:
      context: /www/wwwroot/telegram-forward-bot
      dockerfile: Dockerfile
    image: tg-forward-bot:notice-bot
    container_name: tg-forward-notice-bot
    restart: unless-stopped
    environment:
      BOT_TOKEN: "替换为你的BotFather令牌"
      OWNER_USER_IDS: "替换为你的Telegram数字UID"
      DATABASE_URL: "sqlite+aiosqlite:///./data/notice-bot.db"
      UNAUTHORIZED_REPLY: "true"
      SEND_DELAY_SECONDS: "0.08"
    volumes:
      - tg_forward_notice_data:/app/data

volumes:
  tg_forward_notice_data:
```

只需要改这两行：

```yaml
BOT_TOKEN: "替换为你的BotFather令牌"
OWNER_USER_IDS: "替换为你的Telegram数字UID"
```

数据会保存在 Docker 命名卷 `tg_forward_notice_data`，不需要手动创建 `data/` 目录，也不需要手动设置目录权限。

## 3. 创建 Compose 项目

进入：

```text
Docker -> Compose -> 添加Compose项目
```

选择刚创建的 Compose 模板。

项目名建议填：

```text
tg-forward-notice-bot
```

创建后宝塔会构建镜像并启动容器。

## 4. 查看日志

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

## 5. 多机器人部署

同一台服务器跑多个机器人时，不要复用同一个服务名、镜像标签、容器名、数据库文件名和数据卷名。

例如第二个机器人：

把 Compose 模板中的这些字段改掉：

```yaml
name: tg-forward-customer-bot

services:
  tg-forward-customer-bot:
    build:
      context: /www/wwwroot/telegram-forward-bot
      dockerfile: Dockerfile
    image: tg-forward-bot:customer-bot
    container_name: tg-forward-customer-bot
    restart: unless-stopped
    environment:
      BOT_TOKEN: "第二个机器人的BotFather令牌"
      OWNER_USER_IDS: "第二个机器人的宿主Telegram数字UID"
      DATABASE_URL: "sqlite+aiosqlite:///./data/customer-bot.db"
      UNAUTHORIZED_REPLY: "true"
      SEND_DELAY_SECONDS: "0.08"
    volumes:
      - tg_forward_customer_data:/app/data
```

底部数据卷也要对应新增：

```yaml
volumes:
  tg_forward_customer_data:
```

## 6. 升级代码

如果是 git 拉取的项目：

```bash
cd /www/wwwroot/telegram-forward-bot
git pull
```

然后在宝塔里重建/重启 Compose 项目。

如果是上传压缩包部署的项目，直接上传新代码覆盖项目文件。机器人数据在 Docker 命名卷里，覆盖代码目录不会覆盖数据库。

然后在宝塔里重建/重启 Compose 项目。

## 7. 常见问题

### 容器启动后立刻退出

先看日志。常见原因：

- `BOT_TOKEN` 没填或填错。
- `OWNER_USER_IDS` 不是 Telegram 数字 UID。
- `build.context` 路径不对，宝塔构建镜像时找不到项目。

### 宝塔里构建镜像失败

确认 `compose.baota.yaml` 里的路径存在：

```bash
ls -la /www/wwwroot/telegram-forward-bot
ls -la /www/wwwroot/telegram-forward-bot/Dockerfile
```

如果项目放在别的目录，需要同步修改 Compose 模板里的：

```yaml
build:
  context: /你的项目目录
```

### 怎么备份数据库

使用 Docker 命名卷后，数据库不在项目目录里。可以通过临时容器打包备份：

```bash
docker run --rm \
  -v tg_forward_notice_data:/data \
  -v "$PWD":/backup \
  busybox \
  tar czf /backup/tg_forward_notice_data.tar.gz -C /data .
```
