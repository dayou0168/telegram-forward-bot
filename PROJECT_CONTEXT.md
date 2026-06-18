# 项目交接上下文

这个文件用于“固化”项目背景。以后即使换了新的对话窗口，先让助手阅读本文件和 `README.md`，就能继续开发。

## 项目目标

这是一个私用 Telegram 群组转发机器人。宿主把机器人拉入多个 Telegram 群后，机器人会记录群名和 `chat_id`。宿主和被授权的操作人可以在私聊里创建分组，把指定群加入分组，并把一条私聊消息复制发送到分组内所有群。

## 当前技术栈

- Python 3.12 兼容。
- aiogram 3.x，使用 long polling，不开放公网端口。
- SQLAlchemy async + SQLite，默认数据库位于 `data/*.db`。
- Docker Compose 部署。
- 同一台服务器可通过不同 env 文件运行多个机器人实例。

## 核心文件

- `bot/main.py`：启动入口，初始化数据库、Bot、Dispatcher 和命令菜单。
- `bot/handlers.py`：主要业务流程，包括菜单、分组管理、权限管理、快捷发送、群内回复通知。
- `bot/keyboards.py`：Inline/Reply keyboard。
- `bot/repositories.py`：数据库读写封装。
- `bot/models.py`：SQLAlchemy 表模型。
- `bot/states.py`：FSM 状态。
- `bot/config.py`：环境变量配置。
- `deploy/run-bot.sh`：多机器人实例部署脚本。
- `deploy/envs/example-bot.env`：单个机器人 env 模板。

## 权限规则

- `OWNER_USER_IDS` 是宿主 Telegram 数字 UID，可配置多个。
- 宿主拥有全部权限。
- 宿主可以添加一级操作人。
- 一级操作人可以添加一层下级操作人。
- 下级操作人不能继续添加操作人。
- 操作人只能看到、管理、发送到自己被授权的分组。
- 一级操作人给下级授权时，只能授予自己已有权限的分组。
- 非宿主、非操作人的私聊和按钮操作无效。

## 分组与群组规则

- 机器人被拉入群后会登记群组。
- 操作人添加群组到分组时，只能看到自己已经加入的群组；宿主可以看到全部已登记群组。
- 分组支持创建、重命名、删除。
- 分组内群组支持单个添加、删除和批量添加。

## 发送规则

- 发送使用 Telegram `copyMessage`，目标群不会显示原始转发来源。
- 支持确认发送、一次性快捷发送、连续快捷发送。
- Telegram 电脑版菜单里只保留 `/start`、`/menu`、`/id`，避免 `/quick`、`/to` 这类命令点开后还要补参数。
- 中文快捷指令仍支持：
  - `发送到 分组名`
  - `快捷发送 分组名`
  - `连续发送 分组名`
  - `取消` / `停止` / `退出`

## 群内回复通知

当群成员回复机器人投递到群内的那条消息时，机器人会私聊通知宿主和当时发送的操作人。

- 纯文字回复会合并进一条通知，不再额外复制原消息。
- 图片、视频、文件、语音等媒体回复会优先复制原媒体，并把群名、发送人、任务号和内容预览放入媒体说明，按钮也挂在同一条媒体消息下面。
- 贴纸、位置等不适合带说明的内容会发送摘要通知，再附上原消息。
- 按钮包括：
  - `快速回复`
  - `定位回复消息`
  - `定位原投递消息`

## UID 查询

添加操作人需要 Telegram 数字 UID。宿主或可创建下级操作人的操作人可以点击 `查询UID`，机器人会使用 Telegram 原生用户选择界面返回选中用户 UID。

## 部署模型

同一台 Ubuntu 服务器建议只保留一份代码，例如：

```bash
/opt/tg-forward-bots/telegram-forward-bot
```

不同机器人用不同 env 文件：

```bash
deploy/envs/notice-bot.env
deploy/envs/customer-bot.env
```

启动：

```bash
./deploy/run-bot.sh notice-bot up
```

查看日志：

```bash
./deploy/run-bot.sh notice-bot logs
```

## 重要安全约束

- 不要提交 `.env`、`deploy/envs/*.env`、`data/*.db`。
- `deploy/envs/example-bot.env` 可以提交，因为里面不应该放真实 token。
- GitHub 仓库建议先设为 Private。
- 生产服务器上数据库要定期备份 `data/*.db`。

## 后续开发前检查

开发前建议先运行：

```bash
python -m compileall bot
```

如果是在服务器上升级，更新代码后执行：

```bash
./deploy/run-bot.sh notice-bot up
```

`up --build` 行为已经包含在脚本的 `up` 动作里。
