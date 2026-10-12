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

# 3) 对局冒烟（本地逻辑，不走网络）：三个机器人自动打完一局
.venv\Scripts\python.exe scripts\smoke_match.py --seed 20261011

# 3b) 走网络的三人对局（服务端需已启动；--spectators 可加观战）
.venv\Scripts\python.exe bots\run_table.py --room 牌桌1 --spectators 1

# 3c) 窗口客户端（可开多个；--auto-login/--room/--auto-ready 为演示参数）
.venv\Scripts\python.exe -m client
.venv\Scripts\python.exe -m client --nickname demoA --auto-login --room 牌桌1 --auto-ready

# 4) 运行全部测试
.venv\Scripts\python.exe -m pytest -q

# 5) AI 陪打：让两个 AI 占住牌桌，自己用窗口客户端打第三个座位
.venv\Scripts\python.exe bots\fill_seats.py --room 牌桌3 --ai 2
.venv\Scripts\python.exe -m client --nickname me --auto-login --room 牌桌3
```

牌桌上三个按钮的用法：**准备**（三人齐且都准备即发牌）、**提示**（本地用规则引擎算一手可出的牌）、
**托管 / 取回控制**（交给服务端自动打，随时取回；掉线时也会自动托管，回来后自动取回）。

房间：`大厅`（登录后自动进入，不可退出）、`聊天房`、`牌桌1/2/3`（三人一桌，每桌可 2 人观战）。
端口与上限可以在环境变量里覆盖，例如 `DDZ_PORT=9022`、`DDZ_MAX_FRAME_BYTES=131072`。

## 三、目录

```text
config.py        服务端配置（端口、分帧上限、握手与队列参数）
server/          分帧、会话、房间、消息处理与服务端入口
game/            牌型规则（移植 onestraw/doudizhu，MIT）、对局状态机、托管 AI
client/          客户端：网络层（单读者多路复用）、会话层、Tkinter 界面
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
| M1 | 队列背压策略 + 心跳判活 + 断线清理 + token 重连 | 完成（慢客户端对照实验通过） |
| M2 | 斗地主引擎接入网络 + Tkinter 客户端 | 引擎已接入网络（牌桌/观战/托管/重连均已验证）；Tkinter 客户端待做 |
| M3 | 观战 + 故障注入 + 对照实验 + 文档归档 | 待开始 |

## 六、可调参数（环境变量）

| 变量 | 默认 | 说明 |
| --- | ---: | --- |
| `DDZ_HOST` / `DDZ_PORT` | 127.0.0.1 / 9011 | 监听地址 |
| `DDZ_MAX_FRAME_BYTES` | 65536 | 单帧上限 |
| `DDZ_SEND_QUEUE_MAX` | 128 | 每连接发送队列条数上限 |
| `DDZ_SEND_QUEUE_MAX_BYTES` | 65536 | 每连接发送队列字节上限 |
| `DDZ_SLOW_CONSUMER_DROP_LIMIT` | 50 | 窗口内丢弃多少条判定慢客户端 |
| `DDZ_SLOW_CONSUMER_WINDOW_MS` | 10000 | 慢客户端判定窗口 |
| `DDZ_PING_INTERVAL_MS` | 10000 | 静默多久后服务端主动 ping |
| `DDZ_IDLE_TIMEOUT_MS` | 30000 | 静默多久判定失效 |
| `DDZ_SWEEP_INTERVAL_MS` | 5000 | 心跳扫描周期 |
| `DDZ_RECONNECT_GRACE_MS` | 60000 | 重连宽限期 |
| `DDZ_HANDSHAKE_TIMEOUT_MS` | 10000 | 握手超时 |
| `DDZ_MAX_TEXT_CHARS` | 500 | 单条文本上限 |
| `DDZ_SEATS_PER_TABLE` | 3 | 每桌座位数 |
| `DDZ_MAX_SPECTATORS` | 2 | 每桌观战人数上限 |
| `DDZ_AUTO_PLAY_DELAY_MS` | 1000 | 掉线托管后多久自动决策 |

慢客户端对照实验（另开终端）：

```powershell
.venv\Scripts\python.exe bots\run_bots.py --clients 10 --rounds 40 --payload 480 --slow 0
.venv\Scripts\python.exe bots\run_bots.py --clients 10 --rounds 40 --payload 480 --slow 1
```

## 五、参考项目

牌型规则移植自 [onestraw/doudizhu](https://github.com/onestraw/doudizhu)（MIT），
许可与引用清单见 `docs/参考项目与许可.md`。
