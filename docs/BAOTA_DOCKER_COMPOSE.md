# 宝塔 Docker Compose 部署

这个文档用于在宝塔面板的 Docker Compose 容器编排里部署机器人。

机器人不需要开放端口，也不需要反向代理。它使用 Telegram long polling，只要服务器能访问 Telegram API 即可。

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

## 2. 创建机器人 env

```bash
cd /www/wwwroot/telegram-forward-bot
cp deploy/envs/example-bot.env deploy/envs/notice-bot.env
nano deploy/envs/notice-bot.env
```

填写：

```env
BOT_TOKEN=你的BotFather令牌
OWNER_USER_IDS=你的Telegram用户ID
DATABASE_URL=sqlite+aiosqlite:///./data/notice-bot.db
UNAUTHORIZED_REPLY=true
SEND_DELAY_SECONDS=0.08
```

保存后设置权限：

```bash
mkdir -p data
chown -R 10001:10001 data
chmod 750 data
chmod 600 deploy/envs/notice-bot.env
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

模板内容使用项目里的 `compose.baota.yaml`，或者直接复制下面内容：

```yaml
services:
  tg-forward-bot:
    build:
      context: /www/wwwroot/telegram-forward-bot
      dockerfile: Dockerfile
    image: tg-forward-bot:notice-bot
    container_name: tg-forward-notice-bot
    restart: unless-stopped
    env_file:
      - /www/wwwroot/telegram-forward-bot/deploy/envs/notice-bot.env
    volumes:
      - /www/wwwroot/telegram-forward-bot/data:/app/data
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

创建后宝塔会构建镜像并启动容器。

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

同一台服务器跑多个机器人时，不要复用同一个 env 和容器名。

例如第二个机器人：

```bash
cp deploy/envs/example-bot.env deploy/envs/customer-bot.env
nano deploy/envs/customer-bot.env
```

把 Compose 模板中的这些字段改掉：

```yaml
image: tg-forward-bot:customer-bot
container_name: tg-forward-customer-bot
env_file:
  - /www/wwwroot/telegram-forward-bot/deploy/envs/customer-bot.env
```

数据库也建议改成：

```env
DATABASE_URL=sqlite+aiosqlite:///./data/customer-bot.db
```

## 7. 升级代码

如果是 git 拉取的项目：

```bash
cd /www/wwwroot/telegram-forward-bot
git pull
```

然后在宝塔里重建/重启 Compose 项目。

如果是上传压缩包部署的项目，先备份：

```bash
deploy/envs/*.env
data/*.db
```

再上传新代码覆盖项目文件，最后在宝塔里重建/重启 Compose 项目。

## 8. 常见问题

### 容器启动后立刻退出

先看日志。常见原因：

- `BOT_TOKEN` 没填或填错。
- `OWNER_USER_IDS` 不是 Telegram 数字 UID。
- `data/` 目录权限不对，SQLite 数据库无法写入。

修复数据目录权限：

```bash
cd /www/wwwroot/telegram-forward-bot
chown -R 10001:10001 data
chmod 750 data
```

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
env_file:
  - /你的项目目录/deploy/envs/notice-bot.env
volumes:
  - /你的项目目录/data:/app/data
```
