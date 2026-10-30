# 斗地主聊天室（网络编程项目四）

大厅聊天 + 斗地主牌桌的桌面/网页程序，服务端统一裁决，支持观战。
桌面客户端使用 Tkinter，网页版通过 WebSocket 网关接入同一个 TCP 服务端。

## 一、环境

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Python 3.12。桌面客户端使用标准库和 tkinter，网页网关使用 `aiohttp`；
测试使用 pytest。安装依赖：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 二、启动顺序

```powershell
# 1) 服务端（默认 127.0.0.1:9011）
.venv\Scripts\python.exe -m server

# 2) 网页版网关（另开一个终端；默认 0.0.0.0:8080）
.venv\Scripts\python.exe web_gateway.py

# 3) 机器人压测（另开一个终端）
.venv\Scripts\python.exe bots\run_bots.py --clients 10 --rounds 5

# 4) 对局冒烟（本地逻辑，不走网络）：三个机器人自动打完一局
.venv\Scripts\python.exe scripts\smoke_match.py --seed 20261011

# 4b) 走网络的三人对局（服务端需已启动；--spectators 可加观战）
.venv\Scripts\python.exe bots\run_table.py --room 牌桌1 --spectators 1

# 4c) 窗口客户端（可开多个；--auto-login/--room/--auto-ready 为演示参数）
.venv\Scripts\python.exe -m client
.venv\Scripts\python.exe -m client --nickname demoA --auto-login --room 牌桌1 --auto-ready

# 4d) 界面皮肤：默认 CustomTkinter 现代外观；--ui classic 回到美化前的界面
.venv\Scripts\python.exe -m client --ui classic

# 5) 运行全部测试
.venv\Scripts\python.exe -m pytest -q

# 6) AI 陪打：让两个 AI 占住牌桌，自己用窗口客户端打第三个座位
.venv\Scripts\python.exe bots\fill_seats.py --room 牌桌3 --ai 2
.venv\Scripts\python.exe -m client --nickname me --auto-login --room 牌桌3
```

牌桌上三个按钮的用法：**准备**（三人齐且都准备即发牌）、**提示**（本地用规则引擎算一手可出的牌）、
**托管 / 取回控制**（交给服务端自动打，随时取回；掉线时也会自动托管，回来后自动取回）。

## 三、网页版与局域网测试

网页版是现有桌面客户端的浏览器入口，不另写一套服务端。每个浏览器标签页会由
`web_gateway.py` 建立一条到原 TCP 服务端的连接，因此网页、桌面客户端和机器人
共享同一个大厅、房间、私信和牌桌状态。

### 本机测试

```powershell
# 终端 1：TCP 服务端
.venv\Scripts\python.exe -m server

# 终端 2：网页网关
.venv\Scripts\python.exe web_gateway.py

# 浏览器打开
http://127.0.0.1:8080/

# 两个网页客户端联调
.venv\Scripts\python.exe scripts\web_smoke.py

# 三个网页客户端进入牌桌并验证发牌
.venv\Scripts\python.exe scripts\web_table_smoke.py

# 网页群公告、投票、群文件和扫码邀请联调
.venv\Scripts\python.exe scripts\web_group_smoke.py
```

### 手机/局域网测试

网页版默认监听 `0.0.0.0:8080`，后端 TCP 默认连接 `127.0.0.1:9011`。手机只需访问
电脑的局域网 IPv4 地址，例如：

```text
手机浏览器：http://192.168.1.20:8080/
电脑浏览器：http://127.0.0.1:8080/
```

如果电脑桌面客户端也要从另一台电脑通过 TCP 连接，需要让 TCP 服务端监听
`0.0.0.0`：

```powershell
$env:DDZ_HOST="0.0.0.0"
.venv\Scripts\python.exe -m server

# 另一台电脑
.venv\Scripts\python.exe -m client --host 192.168.1.20 --port 9011
```

Windows 防火墙需要放行：

| 端口 | 用途 | 是否必需 |
| --- | --- | --- |
| `8080` | 网页 HTTP + WebSocket | 手机/电脑网页必需 |
| `9011` | 桌面客户端直连 TCP | 仅跨机桌面客户端需要 |

网页网关可改监听地址和后端地址：

```powershell
.venv\Scripts\python.exe web_gateway.py `
  --host 0.0.0.0 --port 8080 `
  --backend-host 127.0.0.1 --backend-port 9011
```

网页版默认是局域网 HTTP 测试入口，不是生产级 HTTPS 部署。账号仍然遵守“同一账号
只能一个在线连接”的限制，因此手机和电脑测试时请使用不同账号，例如
`wanjia1`、`wanjia2`，密码均为 `123456`。

### 群功能

- **群公告**：群主编辑，成员实时看到置顶公告；
- **群名**：群主可以修改显示群名，群名允许重复，内部仍使用独立群 ID；
- **群投票**：群主发起单选投票，成员投票，群主可结束；
- **群文件**：群成员上传、查看图片、下载普通文件和删除自己上传的文件；
- **消息历史**：房间、群聊、公共频道和私信写入 SQLite，重新登录后可继续查看；
- **公共频道**：默认提供 3 个公共频道，管理员统一为 `wanjia1`；管理员可以点击“+ 频道”继续创建；
- **扫码入群**：群工具里生成邀请二维码，扫码登录后提交入群申请，群主审核通过后才进入群聊；
- **群成员邀请**：群成员可以选择在线非成员发起申请，最终由群主审批；
- 群文件默认单文件上限 20 MB，文件保存在 `data/uploads/`，该目录不纳入 Git。

## 四、账号与登录

| 项 | 说明 |
| --- | --- |
| 预置账号 | `wanjia1` ~ `wanjia5`，初始密码 `123456` |
| 默认模式 | **严格**：只认预置账号与 `DDZ_EXTRA_ACCOUNTS` 里的账号；账号拼错会被拒绝并提示最接近的正确写法 |
| 需要更多身份 | 启动服务端时注入：`$env:DDZ_EXTRA_ACCOUNTS="bot0:123456,bot1:123456"; python -m server` |
| 临时放开注册 | `DDZ_AUTO_REGISTER=1`，未预置账号首次登录即注册（密码随首次输入固定） |
| 唯一登录 | 同一账号只能有一个在线连接，重复登录会被拒绝；掉线后 60 秒保护期内同样拒绝 |
| 登出 | 大厅右上角「登出账号」，登出后回到登录页并可换账号 |
| 创建账号 | 登录页「创建账号」按钮：填账号 + 密码（≥4 位）即可，成功后直接进大厅 |
| 服务器地址 | 登录页不再暴露，默认 `127.0.0.1:9011`，可用 `--host/--port` 覆盖 |

## 五、大厅与群聊

* 大厅顶部显示当前账号；右侧是**全服在线玩家**列表，**点一下即可私信**，列表下方有「刷新在线玩家」按钮；
* 选中若干在线用户后点「邀请建群」→ 创建 `群聊N`，被邀请者会被直接拉进群；
* 群里顶部显示群主，**只有群主**能看到「解散群聊」按钮，解散后所有成员回到大厅。
* 私聊：点在线玩家即打开**独立私聊界面**，历史消息保存在 SQLite，重新登录后仍可查看；
  聊过一次后，大厅左侧「私聊会话」列表里会出现这个人的条目，点一下即可继续聊。

房间：`大厅`（登录后自动进入，不可退出）、`聊天房`、`牌桌1/2/3`（三人一桌，每桌可 2 人观战）、
`公共频道·综合/技术/闲聊`（登录用户均可加入）。
端口与上限可以在环境变量里覆盖，例如 `DDZ_PORT=9022`、`DDZ_MAX_FRAME_BYTES=131072`。

## 六、目录

```text
config.py        服务端配置（端口、分帧上限、握手与队列参数）
server/          分帧、会话、房间、消息处理与服务端入口
game/            牌型规则（移植 onestraw/doudizhu，MIT）、对局状态机、托管 AI
client/          客户端：网络层（单读者多路复用）、会话层、Tkinter 界面
web_gateway.py   网页版 HTTP/WebSocket 网关
web/             网页登录、大厅、聊天室和牌桌界面
data/uploads/    网页群文件存储目录（运行期生成，不纳入 Git）
data/app.db      SQLite 账号、群成员、群状态和消息持久化文件
bots/            机器人压测脚本
scripts/         冒烟与演示脚本
tests/           自动化测试
docs/            协议说明、对局状态机、参考项目与许可
evidence/        验证记录
```

## 七、进度

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 分帧 + 多客户端 + 在线列表 + 房间广播 | 完成（10 客户端 100% 到达） |
| M1 | 队列背压策略 + 心跳判活 + 断线清理 + token 重连 | 完成（慢客户端对照实验通过） |
| M2 | 斗地主引擎接入网络 + Tkinter 客户端 | 完成（牌桌/观战/托管/重连均已验证） |
| M3 | 观战 + 故障注入 + 对照实验 + 文档归档 | 部分完成（观战、慢客户端对照、协议和讲解文档已有） |
| WEB | HTTP/WebSocket 网页版 | 完成（登录、房间聊天、私信、牌桌、观战、手机局域网入口） |

## 八、可调参数（环境变量）

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
| `DDZ_DB_PATH` | data/app.db | SQLite 数据文件路径 |
| `DDZ_HISTORY_PAGE_SIZE` | 50 | 房间/私信历史分页大小 |
| `DDZ_SEATS_PER_TABLE` | 3 | 每桌座位数 |
| `DDZ_MAX_SPECTATORS` | 2 | 每桌观战人数上限 |
| `DDZ_AUTO_PLAY_DELAY_MS` | 1000 | 掉线托管后多久自动决策 |
| `WEB_HOST` / `WEB_PORT` | 0.0.0.0 / 8080 | 网页 HTTP/WebSocket 监听地址 |
| `WEB_BACKEND_HOST` / `WEB_BACKEND_PORT` | 127.0.0.1 / 9011 | 网关连接的 TCP 服务端地址 |

慢客户端对照实验（另开终端）：

```powershell
.venv\Scripts\python.exe bots\run_bots.py --clients 10 --rounds 40 --payload 480 --slow 0
.venv\Scripts\python.exe bots\run_bots.py --clients 10 --rounds 40 --payload 480 --slow 1
```

## 九、参考项目

牌型规则移植自 [onestraw/doudizhu](https://github.com/onestraw/doudizhu)（MIT），
许可与引用清单见 `docs/参考项目与许可.md`。
