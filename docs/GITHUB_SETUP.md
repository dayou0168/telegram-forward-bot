# GitHub 固化与上传流程

本地项目目录：

```text
D:\Documents\Telegream机器人
```

## 1. 不要上传的内容

这些文件包含密钥或运行数据，已经在 `.gitignore` 中排除：

- `.env`
- `deploy/envs/*.env`
- `data/`
- `.venv/`
- `__pycache__/`

只保留 `deploy/envs/example-bot.env` 作为模板。

## 2. 创建 GitHub 仓库

建议在 GitHub 创建一个 Private 仓库，例如：

```text
telegram-forward-bot
```

仓库创建时可以不要勾选 README、`.gitignore`、License，因为项目本地已经有这些基础文件。

## 3. Windows 本地安装 Git

如果本机没有 Git，请安装：

```text
https://git-scm.com/download/win
```

安装完成后重新打开 PowerShell，确认：

```powershell
git --version
```

## 4. 首次上传

进入项目目录：

```powershell
cd "D:\Documents\Telegream机器人"
```

初始化仓库并提交：

```powershell
git init
git add .
git commit -m "Initial telegram forward bot"
```

绑定 GitHub 远程仓库。把下面地址换成你的 GitHub 用户名和仓库名：

```powershell
git branch -M main
git remote add origin https://github.com/YOUR_NAME/telegram-forward-bot.git
git push -u origin main
```

## 5. 以后更新代码

每次本地改完后：

```powershell
git status
git add .
git commit -m "Describe your change"
git push
```

服务器升级：

```bash
cd /opt/tg-forward-bots/telegram-forward-bot
git pull
./deploy/run-bot.sh notice-bot up
```

## 6. 换新对话窗口时怎么继续

在新对话里直接说：

```text
请先阅读 PROJECT_CONTEXT.md 和 README.md，然后继续维护这个 Telegram 转发机器人项目。
```

这样新对话能快速接上项目目标、权限规则、部署方式和当前功能状态。
