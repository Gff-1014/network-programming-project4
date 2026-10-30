"""Generate docs/讲解文档-完整代码版.md from the current source tree.

The generated document is intentionally source-heavy: every feature section
contains the complete implementation files used by that feature, and every
project Python file is assigned to exactly one section.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "讲解文档-完整代码版.md"


SECTIONS = [
    {
        "heading": "## 三、P0-1：TCP 分帧、粘包与半包",
        "requirement": """**对应要求**

- TCP 消息必须有长度或等价边界字段。
- 正确处理粘包、半包、非法长度、非法 JSON 和非法 UTF-8。
- 不能假设一次 `recv` 对应一条消息。

**功能与代码的对应关系**

`encode_frame()` 负责发送端编码，`read_frame()` 负责接收端还原消息，
`FrameError` 负责把非法帧转成协议错误。`tests/test_framing.py` 覆盖半包、
粘包、长度为 0、长度超限、非法 JSON 和非对象载荷。

**现场讲解结论**

服务端先精确读取 4 字节长度，再精确读取该长度的消息体；因此 TCP 的字节流
不会把两条消息粘在一起，也不会在消息只到一半时提前解析。长度超限时没有
读取消息体，字节流已经错位，所以必须断开。""",
        "files": [
            "server/framing.py",
            "tests/test_framing.py",
        ],
    },
    {
        "heading": "## 四、P0-2：服务端入口、会话循环、发送队列、背压与心跳",
        "requirement": """**对应要求**

- 服务端同时处理多个客户端。
- 每个连接有独立发送队列。
- 慢客户端不得拖住其他客户端。
- 实现心跳、静默超时和断线清理。
- 非法消息、未知类型和处理器异常不能拖垮整个服务端。

**功能与代码的对应关系**

- `server/main.py`：启动 `asyncio.start_server`，创建心跳扫描任务。
- `server/session.py`：每条连接一个读循环、一个写协程和有界发送队列。
- `Session.send()`：条数与字节数双阈值。
- `Session._writer_loop()`：`drain()` 只阻塞当前连接的写协程。
- `Session._on_drop()` / `_mark_slow_consumer()`：慢客户端判定和断开。
- `heartbeat_sweeper()`：静默连接发 `ping`，超时后清理。
- `config.py`：这些机制的参数集中配置。

**现场讲解结论**

广播只是把消息放入各成员的独立队列，不需要等待所有客户端真正写完网络。
一个只连不读的客户端只会堵住自己的写协程；达到阈值后，服务端清空该连接
积压、返回 `SLOW_CONSUMER` 并断开。其他连接继续工作。""",
        "files": [
            "server/main.py",
            "server/session.py",
            "config.py",
            "tests/test_m1.py",
        ],
    },
    {
        "heading": "## 五、P0-3：协议注册表、账号、房间、私信、群聊与重连",
        "requirement": """**对应要求**

- 客户端可以登录、退出、查看房间、加入/离开房间、房间聊天和私信。
- 房间不存在、消息非法、账号重复登录等情况要返回明确原因。
- 服务端维护用户和房间状态。
- 房间广播不能串到其他房间，私信只能到目标用户。
- 服务端支持 token 重连，重复账号不能同时在线。

**功能与代码的对应关系**

- `server/protocol.py`：消息信封、消息类型表、错误码、处理器注册表。
- `server/hub.py`：全局房间表、在线账号表、私信历史、重连预约。
- `Room.broadcast()`：只遍历本房间成员。
- `Hub.join()`：切换房间并广播两边成员变化。
- `Hub.login()`：校验账号密码与唯一在线限制。
- `Hub.resume()`：凭 token 恢复昵称和房间，并换发新 token。
- `Hub.create_group()` / `delete_group()`：群聊创建和解散。
- `server/handlers.py`：每个协议消息的服务端入口。

**现场讲解结论**

客户端提交的是协议请求，服务端统一查账号、查房间、查目标在线状态并写共享
状态。`Room.broadcast()` 和 `find_by_nickname()` 决定投递范围，因此房间隔离
和私信定向是服务端保证的，不依赖客户端自觉。""",
        "files": [
            "server/protocol.py",
            "server/hub.py",
            "server/handlers.py",
            "tests/test_server.py",
            "tests/test_accounts.py",
        ],
    },
    {
        "heading": "## 六、P0-4：牌桌、服务端裁决、手牌隐私与 request_id 幂等",
        "requirement": """**对应要求**

- 多人互动结果由服务端统一裁决。
- 客户端不能自己修改手牌、轮次或胜负。
- 重复 `request_id` 不得重复叫分、出牌或结算。
- 观战者只能收到公开状态，玩家只收到自己的手牌。

**功能与代码的对应关系**

- `game/engine.py`：对局状态机，检查轮次、手牌、牌型和压牌关系。
- `Match._cached()` / `_remember()`：按 `(玩家, request_id)` 实现幂等。
- `server/table.py`：把引擎接入网络，翻译事件并广播公开状态。
- `Table.public_state()`：观战者和公开广播使用。
- `Table.view_for()`：玩家视角。
- `Table._send_hand()`：手牌只发给本人。

**现场讲解结论**

客户端只能提交“叫分/出牌/过牌”的意图；服务端先检查是否轮到该玩家、牌是否
在手中、牌型是否合法、是否能压过桌面，然后才修改状态并广播结果。手牌走
`seat.send()` 的私有路径，不会进入公开状态。""",
        "files": [
            "game/engine.py",
            "server/table.py",
            "tests/test_engine.py",
            "tests/test_m2.py",
        ],
    },
    {
        "heading": "## 七、P0-5：10 客户端并发、机器人压测与慢客户端对照",
        "requirement": """**对应要求**

- 至少 10 个机器人客户端同时运行。
- 统计广播到达率、延迟和错误。
- 注入慢读、只连不读客户端，验证其他客户端仍可聊天。
- 日志能够定位具体连接。

**功能与代码的对应关系**

- `tests/test_acceptance_m0.py`：10 客户端同房间并发测试。
- `bots/run_bots.py`：机器人登录、发言、统计和慢客户端注入。
- `bots/run_table.py`：走网络完成一局斗地主。
- `bots/fill_seats.py`：用 AI 占住牌桌座位。
- `bots/doudizhu_bot.py`：机器人对局决策。

**实测结果**

- M0：10 客户端 × 20 轮，1800/1800 到达，p50 1.3 ms，p95 1.8 ms。
- M1：无慢客户端 3600/3600，p95 6.6 ms；注入一个只连不读客户端后
  3600/3600，p95 6.8 ms，慢客户端被单独断开。

结果文件见 `evidence/M0验证.md` 和 `evidence/M1验证.md`。""",
        "files": [
            "tests/test_acceptance_m0.py",
            "bots/run_bots.py",
            "bots/run_table.py",
            "bots/fill_seats.py",
            "bots/doudizhu_bot.py",
        ],
    },
    {
        "heading": "## 八、P1：客户端网络复用、会话状态与界面事件桥接",
        "requirement": """**对应要求**

- 客户端能在一条 TCP 连接上同时发送请求和接收服务端推送。
- 请求响应不能被广播消息抢走。
- 客户端状态变化能够转成界面事件。
- 关闭连接、退出登录和异步桥接要正确收尾。

**功能与代码的对应关系**

- `client/net.py`：唯一读循环 `_pump()`，按 `request_id` 分流。
- `Connection.request()`：发送请求并等待对应响应。
- `Connection.wait_for()`：等待某类推送，不阻塞其他消息。
- `client/session.py`：客户端业务状态和协议消息解释。
- `client/bridge.py`：Tkinter 主线程与 asyncio 线程之间的桥。
- `client/main.py`：窗口客户端入口。
- `tests/test_client.py`：聊天、牌桌和观战客户端流程。

**现场讲解结论**

不能用多个协程同时读同一条 TCP 流。项目让 `_pump()` 成为唯一读者：
带有等待中 `request_id` 的帧唤醒对应请求，其余帧进入收件箱作为服务端推送。""",
        "files": [
            "client/net.py",
            "client/session.py",
            "client/bridge.py",
            "client/main.py",
            "tests/test_client.py",
        ],
    },
    {
        "heading": "## 九、P2：斗地主规则、牌编码、AI 与牌局测试",
        "requirement": """**对应要求**

- 完成一种由服务端裁决的多人互动规则。
- 规则实现应与网络层解耦，便于测试和替换。
- 牌型、叫分、出牌、过牌、结算和托管 AI 应可独立验证。

**功能与代码的对应关系**

- `game/rules.py`：牌型识别、牌型比较和候选牌枚举。
- `game/cards.py`：整数牌表示、花色和点数工具。
- `game/text.py`：协议编码、中文显示、大小王显示。
- `game/api.py`：规则引擎的公共封装。
- `game/ai.py`：托管 AI 的叫分和出牌决策。
- `game/__init__.py`：包导出。
- `tests/test_doudizhu.py`、`tests/test_game.py`、`tests/test_card.py`、
  `tests/test_deal.py`、`tests/test_ai.py`：规则和算法验证。

**现场讲解结论**

规则引擎不知道 TCP、房间或发送队列的存在。`Table` 把协议牌编码还原为整数牌，
调用 `Match`，再将引擎事件翻译成广播消息。这样网络层和规则层可以分别测试。""",
        "files": [
            "game/rules.py",
            "game/cards.py",
            "game/text.py",
            "game/api.py",
            "game/ai.py",
            "game/__init__.py",
            "tests/test_doudizhu.py",
            "tests/test_game.py",
            "tests/test_card.py",
            "tests/test_deal.py",
            "tests/test_ai.py",
        ],
    },
    {
        "heading": "## 十、P2：桌面界面、CustomTkinter、牌面绘制与对话框",
        "requirement": """**对应要求**

- 客户端是桌面软件，不是网页。
- 登录、大厅、聊天室、私聊和牌桌能完成完整操作。
- 牌面使用角标显示花色和点数。
- 美化代码与经典界面的回退路径要能说明。

**功能与代码的对应关系**

- `client/app.py`：主窗口、页面切换、异步事件进入 Tk 主线程。
- `client/ui_ctk.py`：Modern UI 皮肤，包括登录页、大厅、聊天室和牌桌容器。
- `client/cards_view.py`：角标牌、手牌选择和牌桌绘制。
- `client/dialogs.py`：创建群聊等文本对话框。
- `client/__init__.py` / `client/__main__.py`：客户端包入口。
- `tests/test_cards_view.py`：牌面绘制相关测试。

**现场讲解结论**

UI 不直接解析 TCP。`ClientSession._handle()` 把协议消息变成业务状态和界面事件，
`ChatApp._drain_events()` 在 Tk 主线程消费事件。界面美化集中在
`client/ui_ctk.py`，可用 `--ui classic` 回退。""",
        "files": [
            "client/app.py",
            "client/ui_ctk.py",
            "client/cards_view.py",
            "client/dialogs.py",
            "client/__init__.py",
            "client/__main__.py",
            "tests/test_cards_view.py",
        ],
    },
    {
        "heading": "## 十一、支撑代码：启动脚本、包入口和测试夹具",
        "requirement": """**对应要求**

- 项目能按 README 启动服务端和客户端。
- 提供本地牌型和整局冒烟脚本。
- 自动化测试能够创建临时服务端和客户端夹具。

**代码内容**

本节包含 scripts 目录、server/bots 包入口和 tests/conftest.py。""",
        "files": [
            "scripts/smoke_cards.py",
            "scripts/smoke_match.py",
            "scripts/generate_full_code_doc.py",
            "tests/conftest.py",
            "server/__init__.py",
            "server/__main__.py",
            "bots/__init__.py",
        ],
    },
]


def source_text(relative_path: str) -> str:
    """Read one source file and normalize line endings."""
    path = ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def file_block(relative_path: str) -> str:
    """Render one complete source file as a Markdown code block."""
    text = source_text(relative_path).rstrip()
    line_count = len(text.splitlines())
    return (
        f"### `{relative_path}`（{line_count} 行，以下为完整代码）\n\n"
        f"````python\n{text}\n````\n\n"
    )


def all_files() -> list[str]:
    files: list[str] = []
    for section in SECTIONS:
        files.extend(section["files"])
    return files


def main() -> None:
    files = all_files()
    if len(files) != len(set(files)):
        raise RuntimeError("同一文件被分配到多个章节，会产生重复代码")

    total_lines = sum(len(source_text(name).splitlines()) for name in files)
    parts: list[str] = [
        "# 项目四讲解文档：功能与完整代码版\n\n",
        "本文档是 `docs/讲解文档.md` 的完整代码版本，原文档保持不变。\n\n",
        "本文档不只列出代码位置：每个功能章节后面都嵌入该功能对应的完整 Python "
        "源码，直到函数、类、测试和启动脚本都包含在文档中。项目当前所有 Python "
        "文件都分配到唯一章节，避免重复贴同一份源码。\n\n",
        f"- 代码文件数：**{len(files)}** 个\n",
        f"- 源码总行数：**{total_lines}** 行\n",
        "- 基线提交：`4941bae`\n",
        "- 最近一次测试：120 项通过\n\n",
        "## 一、讲解优先级与章节索引\n\n",
        "| 优先级 | 功能 | 完整代码章节 | 代码文件 |\n",
        "| --- | --- | --- | --- |\n",
        "| P0-1 | TCP 分帧、粘包、半包 | 三 | `server/framing.py`、`tests/test_framing.py` |\n",
        "| P0-2 | 会话、发送队列、背压、心跳 | 四 | `server/main.py`、`server/session.py`、`config.py` |\n",
        "| P0-3 | 协议、账号、房间、私信、群聊、重连 | 五 | `server/protocol.py`、`server/hub.py`、`server/handlers.py` |\n",
        "| P0-4 | 牌桌裁决、幂等、手牌隐私 | 六 | `game/engine.py`、`server/table.py` |\n",
        "| P0-5 | 10 客户端压测、机器人和慢客户端 | 七 | `tests/test_acceptance_m0.py`、`bots/` |\n",
        "| P1 | 客户端网络复用和事件桥接 | 八 | `client/net.py`、`client/session.py`、`client/bridge.py` |\n",
        "| P2 | 斗地主规则、牌编码和 AI | 九 | `game/` |\n",
        "| P2 | 桌面界面、CustomTkinter、牌面 | 十 | `client/app.py`、`client/ui_ctk.py`、`client/cards_view.py` |\n",
        "| 支撑 | 启动脚本、包入口和测试夹具 | 十一 | `scripts/`、`tests/conftest.py` |\n\n",
        "## 二、先把完整消息链路串起来\n\n",
        "```text\n",
        "客户端界面\n",
        "  -> ClientSession\n",
        "  -> Connection.send\n",
        "  -> encode_frame：4 字节长度头 + UTF-8 JSON\n",
        "  -> TCP\n",
        "  -> server.read_frame\n",
        "  -> Session.run / Session._dispatch\n",
        "  -> @register 注册的 handler\n",
        "  -> Hub / Room / Table\n",
        "  -> Session.send：进入该连接的有界发送队列\n",
        "  -> Session._writer_loop\n",
        "  -> 客户端 Connection._pump\n",
        "  -> ClientSession._handle\n",
        "  -> 界面事件\n",
        "```\n\n",
        "这条链路是讲解的主线。下面每一节先说明功能，再给出该功能对应的全部代码。\n\n",
    ]

    for section in SECTIONS:
        parts.append(section["heading"] + "\n\n")
        parts.append(section["requirement"] + "\n\n")
        parts.append("### 本功能完整代码\n\n")
        for name in section["files"]:
            parts.append(file_block(name))

    parts.extend(
        [
            "## 十二、现场验收建议\n\n",
            "### 10 分钟路线\n\n",
            "1. 先打开本文档第三节的 `encode_frame` 和 `read_frame`，讲长度前缀。\n",
            "2. 打开第四节，讲 `Session.send()`、`_writer_loop()` 和 `_mark_slow_consumer()`。\n",
            "3. 打开第五节，讲 `Room.broadcast()`、`Hub.login()` 和 `Hub.resume()`。\n",
            "4. 打开第六节，讲 `Match.play()`、`Table.view_for()` 和 `_send_hand()`。\n",
            "5. 最后展示第七节的 10 客户端和慢客户端对照结果。\n\n",
            "### 20 分钟路线\n\n",
            "1. 分帧和半包/粘包测试。\n",
            "2. 会话生命周期、队列背压、心跳和断线预约。\n",
            "3. 协议注册表、房间广播、私信和重连。\n",
            "4. 服务端裁决、私有手牌和 `request_id` 幂等。\n",
            "5. 客户端单读者分流和界面事件桥接。\n",
            "6. 10 客户端压测与慢客户端 A/B 对照。\n",
            "7. 现场在 `protocol.py` 和 `handlers.py` 增加一种消息类型。\n\n",
            "### 现场新增消息类型的最小步骤\n\n",
            "1. 在 `server/protocol.py` 的 `C2S_TYPES` 增加类型名。\n",
            "2. 在 `server/handlers.py` 增加 `@register(\"新类型\")` 异步函数。\n",
            "3. 处理器调用 `_require_login()`，然后用 `make()` 返回结果。\n",
            "4. 如果新增服务端推送，再把推送类型加入 `S2C_TYPES`。\n",
            "5. 用 `Connection.send()` 或测试客户端发送一帧验证。\n\n",
            "## 十三、讲解时必须诚实说明的边界\n\n",
            "- 服务端支持 token 重连，但桌面客户端没有完整的自动重连管理器。\n",
            "- 私信历史只保存在内存，服务端重启即清空。\n",
            "- 当前密码存储是课程演示级别，不是生产级密码哈希方案。\n",
            "- 跨机运行需要监听 `0.0.0.0`、开放防火墙并使用正确地址；当前主要验证是本机多进程。\n",
            "- 界面美化是独立层，`--ui classic` 可以回到经典界面；界面不是网络核心评分点。\n",
        ]
    )

    OUTPUT.write_text("".join(parts), encoding="utf-8")
    print(f"generated: {OUTPUT}")
    print(f"files: {len(files)}, source lines: {total_lines}")


if __name__ == "__main__":
    main()
