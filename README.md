# 斗地主聊天室（网络编程项目四）

大厅聊天 + 斗地主牌桌的桌面程序，服务端统一裁决，支持观战。
运行期只依赖 Python 标准库（含 tkinter），测试期使用 pytest。

## 一、环境

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Python 3.12。`requirements.txt` 只有 pytest（测试用），运行时无第三方依赖。

## 二、启动顺序

```powershell
# 1) 服务端（默认 127.0.0.1:9011）
.venv\Scripts\python.exe -m server

# 2) 机器人压测（另开一个终端）
.venv\Scripts\python.exe bots\run_bots.py --clients 10 --rounds 5

# 3) 对局冒烟：三个机器人自动打完一局斗地主
.venv\Scripts\python.exe scripts\smoke_match.py --seed 20261011

# 4) 运行全部测试
.venv\Scripts\python.exe -m pytest -q
```

房间：`大厅`（登录后自动进入，不可退出）、`聊天房`。
端口与上限可以在环境变量里覆盖，例如 `DDZ_PORT=9022`、`DDZ_MAX_FRAME_BYTES=131072`。

## 三、目录

```text
config.py        服务端配置（端口、分帧上限、握手与队列参数）
server/          分帧、会话、房间、消息处理与服务端入口
game/            牌型规则（移植 onestraw/doudizhu，MIT）、对局状态机、托管 AI
client/          客户端网络层（单读者多路复用），Tkinter 界面在 M2 接入
bots/            机器人压测脚本
scripts/         冒烟与演示脚本
tests/           自动化测试
docs/            协议说明、对局状态机、参考项目与许可
evidence/        验证记录
```

## 四、进度

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 分帧 + 多客户端 + 在线列表 + 房间广播 | 完成（10 客户端 100% 到达） |
| M1 | 队列背压策略 + 心跳判活 + 断线清理 + token 重连 | 待开始 |
| M2 | 斗地主引擎接入网络 + Tkinter 客户端 | 引擎与状态机已完成，待接入 |
| M3 | 观战 + 故障注入 + 对照实验 + 文档归档 | 待开始 |

## 五、参考项目

牌型规则移植自 [onestraw/doudizhu](https://github.com/onestraw/doudizhu)（MIT），
许可与引用清单见 `docs/参考项目与许可.md`。
