/* 斗地主聊天室网页版：只负责浏览器交互，所有 TCP 协议由 web_gateway.py 处理。 */

const state = {
  socket: null,
  connected: false,
  account: null,
  nickname: null,
  room: null,
  roomKey: null,
  roomName: null,
  role: null,
  rooms: [],
  roomUsers: [],
  onlineUsers: [],
  conversations: [],
  currentPeer: null,
  privateMessages: {},
  hand: [],
  table: {},
  autoPlay: false,
  selected: new Set(),
  chat: [],
  privateOpen: false,
  groupState: {},
  fileToken: null,
  lanUrls: [],
  groupPanelOpen: false,
  groupTab: "files",
  pendingInvite: null,
  inviteSent: false,
  loggingOut: false,
  historyBefore: null,
  historyHasMore: false,
  historyLoading: false,
  privateHasMore: false,
  privateBefore: null,
  privateLoading: false,
};

let reconnectTimer = null;
let toastTimer = null;

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function showToast(message, isError = false) {
  const box = $("toast");
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    box.hidden = true;
  }, 3500);
}

function setAuthStatus(message, isError = false) {
  const box = $("auth-status");
  box.textContent = message;
  box.style.color = isError ? "var(--red)" : "var(--yellow)";
}

function showAuth() {
  $("auth-screen").hidden = false;
  $("workspace").hidden = true;
}

function showWorkspace() {
  $("auth-screen").hidden = true;
  $("workspace").hidden = false;
}

function connectSocket() {
  clearTimeout(reconnectTimer);
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  state.socket = socket;

  socket.onopen = () => {
    state.connected = true;
    setAuthStatus("已连接服务端");
    send("hello");
  };

  socket.onmessage = (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch (error) {
      console.error("无法解析网关消息", error, event.data);
    }
  };

  socket.onerror = () => {
    if (!state.connected) {
      setAuthStatus("无法连接网关，请确认 web_gateway.py 已启动", true);
    }
  };

  socket.onclose = () => {
    state.connected = false;
    if (!$("workspace").hidden) {
      showToast("网页连接已断开，正在重试…", true);
    } else {
      setAuthStatus("连接断开，正在重试…", true);
    }
    reconnectTimer = setTimeout(connectSocket, 1500);
  };
}

function send(type, data = {}) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    showToast("连接尚未建立，请稍后重试", true);
    return false;
  }
  state.socket.send(JSON.stringify({ type, data }));
  return true;
}

function handleMessage(message) {
  if (!message || typeof message !== "object") return;
  if (message.type === "snapshot") {
    applySnapshot(message.data || {});
    return;
  }
  if (message.type === "event") {
    handleEvent(message.kind, message.data || {});
    return;
  }
  if (message.type === "error") {
    const label = message.message || message.code || "操作失败";
    showToast(label, true);
    if (!$("auth-screen").hidden) setAuthStatus(label, true);
    return;
  }
  if (message.type === "ack") {
    return;
  }
  if (message.type === "hello") {
    return;
  }
}

function applySnapshot(data) {
  state.account = data.account || null;
  state.nickname = data.nickname || null;
  state.roomKey = data.room_key || data.room || null;
  state.roomName = data.room_name || data.room || null;
  state.room = state.roomName;
  state.role = data.role || null;
  state.rooms = Array.isArray(data.rooms) ? data.rooms : [];
  state.roomUsers = Array.isArray(data.room_users) ? data.room_users : [];
  state.onlineUsers = Array.isArray(data.online_users) ? data.online_users : [];
  state.conversations = Array.isArray(data.conversations) ? data.conversations : [];
  state.currentPeer = data.current_peer || null;
  state.hand = Array.isArray(data.hand) ? data.hand : [];
  state.table = data.table && typeof data.table === "object" ? data.table : {};
  state.autoPlay = Boolean(data.auto_play);
  state.groupState = data.group_state && typeof data.group_state === "object"
    ? data.group_state
    : {};
  state.fileToken = data.file_token || state.fileToken || null;
  state.lanUrls = Array.isArray(data.lan_urls) ? data.lan_urls : state.lanUrls;
  if (!state.chat.length && Array.isArray(data.room_history)) {
    state.chat = data.room_history.map(historyLine);
    state.historyBefore = data.room_history_next_before || null;
    state.historyHasMore = Boolean(data.room_history_has_more);
  }

  if (data.private_history && state.currentPeer) {
    state.privateMessages[state.currentPeer] = data.private_history;
  }
  if (state.nickname) {
    showWorkspace();
    if (state.pendingInvite && !state.inviteSent) {
      state.inviteSent = true;
      send("join_invite", { token: state.pendingInvite });
    }
  } else {
    showAuth();
  }
  renderAll();
}

function handleEvent(kind, data) {
  switch (kind) {
    case "logged_in":
      state.account = data.account || data.nickname || state.account;
      state.nickname = data.nickname || state.account;
      state.roomKey = data.room_key || data.room || state.roomKey;
      state.roomName = data.room_name || data.room || state.roomName;
      state.room = state.roomName;
      state.rooms = Array.isArray(data.rooms) ? data.rooms : state.rooms;
      showWorkspace();
      break;
    case "logged_out":
      state.loggingOut = false;
      resetState();
      showAuth();
      setAuthStatus("已登出");
      break;
    case "disconnected":
      if (!$("workspace").hidden && !state.loggingOut) {
        showToast("与 TCP 服务端的连接已断开", true);
      }
      break;
    case "room_list":
      state.rooms = Array.isArray(data.rooms) ? data.rooms : [];
      break;
    case "room_users":
      if (!data.room || data.room === state.roomKey) {
        state.roomUsers = Array.isArray(data.users) ? data.users : [];
      }
      break;
    case "user_list":
      state.roomUsers = Array.isArray(data.users) ? data.users : [];
      state.onlineUsers = Array.isArray(data.online) ? data.online : [];
      break;
    case "conversations":
      state.conversations = Array.isArray(data.items) ? data.items : [];
      break;
    case "joined":
      if (state.roomKey !== (data.room_key || data.room)) {
        state.groupState = {};
      }
      state.roomKey = data.room_key || data.room || state.roomKey;
      state.roomName = data.room_name || data.room || state.roomName;
      state.room = state.roomName;
      state.role = data.role || state.role;
      state.chat = [];
      state.historyBefore = null;
      state.historyHasMore = false;
      state.historyLoading = false;
      if (state.pendingInvite) {
        state.pendingInvite = null;
        state.inviteSent = false;
        history.replaceState({}, "", "/");
      }
      break;
    case "left":
      state.roomKey = data.to || "大厅";
      state.roomName = data.to_name || data.to || "大厅";
      state.room = state.roomName;
      state.role = "player";
      state.table = {};
      state.hand = [];
      state.selected.clear();
      state.chat = [];
      state.historyBefore = null;
      state.historyHasMore = false;
      break;
    case "group_created":
      state.roomKey = data.room || state.roomKey;
      state.roomName = data.room_name || data.room || state.roomName;
      state.room = state.roomName;
      appendChat(data._line || `已创建 ${data.room}`, true);
      break;
    case "channel_created":
      appendChat(data._line || `已创建频道 ${data.room_name}`, true);
      showToast(`频道已创建：${data.room_name}`);
      break;
    case "group_deleted":
      if (state.roomKey === data.room) {
        state.roomKey = "大厅";
        state.roomName = "大厅";
        state.room = "大厅";
      }
      state.groupState = {};
      appendChat(data._line || `群聊 ${data.room} 已解散`, true);
      break;
    case "group_invite_requested":
      appendChat(data._line || "收到一条入群申请", true);
      break;
    case "group_invite_pending":
      appendChat(data._line || "入群申请已提交，等待群主审核", true);
      showToast("入群申请已提交，等待群主审核");
      break;
    case "group_invite_rejected":
      appendChat(data._line || "入群申请被拒绝", true);
      showToast("群主拒绝了入群申请", true);
      break;
    case "group_renamed":
      state.roomKey = data.room || state.roomKey;
      state.roomName = data.room_name || state.roomName;
      state.room = state.roomName;
      state.groupState = {
        ...state.groupState,
        room: state.roomKey,
        room_name: state.roomName,
      };
      state.rooms = state.rooms.map((room) => (
        room.room_key === state.roomKey
          ? { ...room, name: state.roomName, display_name: state.roomName }
          : room
      ));
      appendChat(data._line || `群名已改为 ${state.roomName}`, true);
      break;
    case "group_state":
      state.groupState = data && typeof data === "object" ? data : {};
      state.roomKey = data.room || state.roomKey;
      state.roomName = data.room_name || state.roomName || state.room;
      state.room = state.roomName;
      break;
    case "room_history":
      {
        const lines = (data.messages || []).map(historyLine);
        if (state.historyLoading && state.chat.length) {
          state.chat = [...lines, ...state.chat];
        } else {
          state.chat = lines;
        }
        state.historyBefore = data.next_before || null;
        state.historyHasMore = Boolean(data.has_more);
        state.historyLoading = false;
      }
      break;
    case "announcement_updated":
      state.groupState = {
        ...state.groupState,
        announcement: data.announcement || {},
      };
      appendChat(data._line || "群公告已更新", true);
      break;
    case "poll_created":
    case "poll_updated":
    case "poll_closed":
      if (data.poll) {
        const polls = state.groupState.polls || [];
        const next = polls.filter((item) => item.poll_id !== data.poll.poll_id);
        next.push(data.poll);
        state.groupState = { ...state.groupState, polls: next };
      }
      appendChat(data._line || "群投票已更新", true);
      break;
    case "group_file_added":
      {
        const files = state.groupState.files || [];
        const next = files.filter((item) => item.file_id !== data.file_id);
        next.push({ ...data });
        state.groupState = { ...state.groupState, files: next };
        appendChat(data._line || `群文件已上传：${data.name || ""}`, true);
      }
      break;
    case "group_file_deleted":
      state.groupState = {
        ...state.groupState,
        files: (state.groupState.files || []).filter(
          (item) => item.file_id !== data.file_id
        ),
      };
      appendChat(data._line || "群文件已删除", true);
      break;
    case "private_opened":
      state.currentPeer = data.peer || null;
      state.privateOpen = true;
      state.privateMessages[state.currentPeer] = Array.isArray(data.messages)
        ? data.messages
        : [];
      state.privateBefore = data.next_before || null;
      state.privateHasMore = Boolean(data.has_more);
      state.privateLoading = false;
      break;
    case "private_history_more":
      if (state.currentPeer && data.peer === state.currentPeer) {
        state.privateMessages[state.currentPeer] = [
          ...(data.messages || []),
          ...(state.privateMessages[state.currentPeer] || []),
        ];
        state.privateBefore = data.next_before || null;
        state.privateHasMore = Boolean(data.has_more);
        state.privateLoading = false;
      }
      break;
    case "private_msg":
      handlePrivateMessage(data);
      break;
    case "room_msg":
      appendChat(data._line || `${data.from || "?"}: ${data.text || ""}`);
      break;
    case "user_joined":
    case "user_left":
    case "user_reconnecting":
    case "spectating":
      appendChat(data._line || kind, true);
      break;
    case "game_start":
    case "hand":
      state.hand = Array.isArray(data.hand) ? [...data.hand] : [];
      state.selected.clear();
      appendChat(data._line || (kind === "game_start" ? "发牌完成" : "拿到底牌"), true);
      break;
    case "table_state":
      state.table = data && typeof data === "object" ? data : {};
      state.autoPlay = Boolean(
        state.nickname && (state.table.auto_players || []).includes(state.nickname)
      );
      break;
    case "landlord":
      state.table.landlord = data.landlord;
      state.table.bottom = Array.isArray(data.bottom) ? data.bottom : state.table.bottom;
      appendChat(data._line || `地主：${data.landlord || "?"}`, true);
      break;
    case "bid":
      appendChat(data._line || `${data.player} ${data.score ? `叫 ${data.score} 分` : "不叫"}`, true);
      break;
    case "play":
      if (data.player === state.nickname && Array.isArray(data.cards)) {
        const played = new Set(data.cards);
        state.hand = state.hand.filter((card) => !played.has(card));
        state.selected.clear();
      }
      appendChat(data._line || `${data.player}: ${data.cards_text || ""}`);
      break;
    case "pass":
      appendChat(data._line || `${data.player} 过牌`, true);
      break;
    case "table_cleared":
      state.table.table = [];
      state.table.table_owner = null;
      appendChat(data._line || "一轮结束", true);
      break;
    case "timeout":
      appendChat(data._line || `${data.player} 托管自动行动：${data.action || ""}`, true);
      break;
    case "game_over":
      state.table.result = data.result || data;
      state.hand = [];
      state.selected.clear();
      appendChat(data._line || "对局结束", true);
      break;
    case "auto_play":
      state.autoPlay = Boolean(data.enabled);
      appendChat(data._line || (state.autoPlay ? "已开启托管" : "已取回控制"), true);
      break;
    case "error":
      showToast(data.message || data.code || "操作失败", true);
      break;
    default:
      if (data._line) appendChat(data._line, true);
      break;
  }
  renderAll();
}

function resetState() {
  state.account = null;
  state.nickname = null;
  state.room = null;
  state.roomKey = null;
  state.roomName = null;
  state.role = null;
  state.rooms = [];
  state.roomUsers = [];
  state.onlineUsers = [];
  state.conversations = [];
  state.currentPeer = null;
  state.privateMessages = {};
  state.hand = [];
  state.table = {};
  state.autoPlay = false;
  state.selected.clear();
  state.chat = [];
  state.privateOpen = false;
  state.groupState = {};
  state.fileToken = null;
  state.groupPanelOpen = false;
  state.groupTab = "files";
  state.inviteSent = false;
  state.loggingOut = false;
  state.historyBefore = null;
  state.historyHasMore = false;
  state.historyLoading = false;
  state.privateHasMore = false;
  state.privateBefore = null;
  state.privateLoading = false;
}

function historyLine(row) {
  const speaker = row.from === state.nickname ? "我" : (row.from || "?");
  return {
    text: `${speaker}: ${row.text || ""}`,
    system: false,
  };
}

function appendChat(text, system = false) {
  if (!text) return;
  state.chat.push({ text: String(text), system });
  if (state.chat.length > 300) state.chat.shift();
  renderChat();
}

function handlePrivateMessage(data) {
  const mine = Boolean(data.self) || data.from === state.nickname;
  const peer = mine ? data.to : data.from;
  if (!peer) return;
  if (!state.privateMessages[peer]) state.privateMessages[peer] = [];
  state.privateMessages[peer].push({
    from: data.from,
    to: data.to,
    text: data.text,
    ts_ms: data.ts_ms,
  });
  if (state.currentPeer === peer) renderPrivate();
  if (data._line && state.currentPeer !== peer) showToast(data._line);
}

function currentRoom() {
  return state.rooms.find(
    (room) => (room.room_key || room.name) === state.roomKey
  ) || null;
}

function renderAll() {
  renderRooms();
  renderUsers();
  renderConversations();
  renderRoomHeader();
  renderGroupFeatures();
  renderTable();
  renderChat();
  renderPrivate();
  $("create-channel-btn").hidden = state.nickname !== "wanjia1";
  if (state.nickname) {
    $("whoami").textContent = `当前账号：${state.nickname}`;
  }
}

function renderRooms() {
  const box = $("room-list");
  if (!state.rooms.length) {
    box.innerHTML = '<div class="muted">暂无房间</div>';
    return;
  }
  box.innerHTML = state.rooms.map((room) => {
    const roomKey = room.room_key || room.name;
    const active = roomKey === state.roomKey ? " active" : "";
    const kind = room.kind === "doudizhu"
      ? "牌桌"
      : room.kind === "group"
        ? "群聊"
        : room.kind === "channel"
          ? "公共频道"
          : "聊天";
    return `
      <button class="list-item${active}" data-room-key="${escapeHtml(roomKey)}">
        <span>${escapeHtml(room.name)}</span>
        <span class="sub">${kind} · ${room.count ?? 0}</span>
      </button>`;
  }).join("");
}

function renderUsers() {
  const box = $("user-list");
  const users = state.onlineUsers.length ? state.onlineUsers : state.roomUsers;
  if (!users.length) {
    box.innerHTML = '<div class="muted">暂无在线玩家</div>';
    return;
  }
  box.innerHTML = users.map((name) => {
    const self = name === state.nickname;
    return `
      <button class="list-item" data-peer="${escapeHtml(name)}">
        <span><span class="online-dot"></span>${escapeHtml(name)}${self ? "（我）" : ""}</span>
        <span class="sub">${self ? "" : "私聊"}</span>
      </button>`;
  }).join("");
}

function renderConversations() {
  const box = $("conversation-list");
  if (!state.conversations.length) {
    box.innerHTML = '<div class="muted">还没有私聊</div>';
    return;
  }
  box.innerHTML = state.conversations.map((item) => {
    const active = item.peer === state.currentPeer ? " active" : "";
    return `
      <button class="list-item${active}" data-conversation="${escapeHtml(item.peer)}">
        <span>${escapeHtml(item.peer)}</span>
        <span class="sub">${item.count || 0} 条</span>
      </button>`;
  }).join("");
}

function renderRoomHeader() {
  const room = currentRoom();
  $("room-title").textContent = state.roomName || state.room || "大厅";
  $("room-meta").textContent = room
    ? `${room.kind === "doudizhu"
      ? "牌桌"
      : room.kind === "group"
        ? "群聊"
        : room.kind === "channel"
          ? `公共频道 · 管理员 ${room.owner || "wanjia1"}`
          : "聊天"} · ${room.count ?? 0} 人`
    : "";
  $("leave-room-btn").hidden = !state.roomKey || state.roomKey === "大厅";
  const isGroup = Boolean(room && room.kind === "group");
  $("delete-group-btn").hidden = !isGroup || room.owner !== state.nickname;
  $("rename-group-btn").hidden = !isGroup || room.owner !== state.nickname;
  $("group-tools-btn").hidden = !isGroup;
  $("group-qr-btn").hidden = !isGroup;
}

function formatFileSize(size) {
  const value = Number(size) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function fileUrl(fileId) {
  const token = state.fileToken ? `?token=${encodeURIComponent(state.fileToken)}` : "";
  return `/api/attachments/${encodeURIComponent(fileId)}${token}`;
}

function renderGroupFeatures() {
  const room = currentRoom();
  const isGroup = Boolean(room && room.kind === "group");
  const group = state.groupState || {};
  const announcement = group.announcement || {};

  $("announcement-card").hidden = !isGroup || !announcement.text;
  $("announcement-text").textContent = announcement.text || "";
  $("announcement-meta").textContent = announcement.updated_by
    ? `${announcement.updated_by} · 第 ${announcement.revision || 0} 版`
    : "";

  if (!isGroup) {
    state.groupPanelOpen = false;
    $("group-panel").hidden = true;
    return;
  }

  $("group-panel").hidden = !state.groupPanelOpen;
  document.querySelectorAll(".group-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.groupTab === state.groupTab);
  });
  $("group-files-tab").hidden = state.groupTab !== "files";
  $("group-polls-tab").hidden = state.groupTab !== "polls";
  $("group-announcement-tab").hidden = state.groupTab !== "announcement";

  const owner = room.owner === state.nickname;
  $("edit-announcement-btn").hidden = !owner;
  $("create-poll-btn").hidden = !owner;
  $("announcement-panel-text").textContent = announcement.text || "暂无群公告";

  const pendingInvites = Array.isArray(group.pending_invites)
    ? group.pending_invites
    : [];
  $("group-invite-pending").hidden = !owner || !pendingInvites.length;
  $("group-pending-list").innerHTML = pendingInvites.map((item) => `
    <div class="pending-row">
      <div class="pending-main">
        ${escapeHtml(item.account || "?")}
        <span class="sub">
          ${escapeHtml(item.requested_by || "?")} 发起 · ${escapeHtml(item.source || "申请")}
        </span>
      </div>
      <div class="pending-actions">
        <button class="mini" data-approve-invite="${escapeHtml(item.account || "")}">同意</button>
        <button class="mini danger" data-reject-invite="${escapeHtml(item.account || "")}">拒绝</button>
      </div>
    </div>
  `).join("");

  const files = Array.isArray(group.files) ? group.files : [];
  $("file-list").innerHTML = files.length
    ? files.map((item) => {
      const canDelete = item.uploader === state.nickname || owner;
      const preview = item.kind === "image"
        ? `<img class="file-image-preview" src="${fileUrl(item.file_id)}"
                 alt="${escapeHtml(item.name || "图片")}" loading="lazy">`
        : "";
      return `
        <div class="file-item">
          <div class="file-main">
            <div class="file-name">${escapeHtml(item.name || "未命名文件")}</div>
            <div class="file-meta">
              ${escapeHtml(item.uploader || "?")} · ${formatFileSize(item.size)}
              · ${escapeHtml(item.mime || "file")}
            </div>
            ${preview}
          </div>
          <div class="file-actions">
            <a class="mini" href="${fileUrl(item.file_id)}"
               ${item.kind === "image" ? "target=_blank" : "download"}>${item.kind === "image" ? "查看" : "下载"}</a>
            ${canDelete ? `<button class="mini danger" data-file-delete="${escapeHtml(item.file_id)}">删除</button>` : ""}
          </div>
        </div>`;
    }).join("")
    : '<div class="muted">暂无群文件，上传一个试试。</div>';

  const polls = Array.isArray(group.polls) ? group.polls : [];
  $("poll-list").innerHTML = polls.length
    ? polls.map((poll) => {
      const closed = Boolean(poll.closed);
      const options = (poll.options || []).map((option) => {
        const mine = poll.my_vote === option.id;
        return `
          <button class="poll-option${mine ? " mine" : ""}"
                  data-poll-vote="${escapeHtml(poll.poll_id)}"
                  data-poll-option="${escapeHtml(option.id)}"
                  ${closed ? "disabled" : ""}>
            <span>${escapeHtml(option.text)}${mine ? " ✓" : ""}</span>
            <span class="poll-count">${option.count || 0} 票</span>
          </button>`;
      }).join("");
      return `
        <div class="poll-card">
          <div class="poll-title">${escapeHtml(poll.title)}</div>
          <div class="poll-meta">
            ${escapeHtml(poll.created_by || "?")} 发起 · ${poll.total_votes || 0} 人投票
            ${closed ? " · 已结束" : ""}
          </div>
          <div class="poll-options">${options}</div>
          ${owner && !closed ? `<button class="mini danger"
            data-poll-close="${escapeHtml(poll.poll_id)}">结束投票</button>` : ""}
        </div>`;
    }).join("")
    : '<div class="muted">暂无投票。</div>';
}

function renderChat() {
  const box = $("chat-log");
  $("load-more-history").hidden = !state.historyHasMore;
  if (!state.chat.length) {
    box.innerHTML = '<div class="muted">进入房间后，在这里发送消息。</div>';
    return;
  }
  box.innerHTML = "";
  for (const line of state.chat) {
    const item = document.createElement("div");
    item.className = `chat-line${line.system ? " system" : ""}`;
    item.textContent = line.text;
    box.appendChild(item);
  }
  box.scrollTop = box.scrollHeight;
}

function isRedCard(code) {
  return code.endsWith("h") || code.endsWith("d") || code === "CJ";
}

function cardLabel(code) {
  if (code === "BJ") return "小王";
  if (code === "CJ") return "大王";
  const suit = code.slice(-1);
  const rank = code.slice(0, -1);
  const suits = { s: "♠", h: "♥", d: "♦", c: "♣" };
  return `${rank}${suits[suit] || ""}`;
}

function miniCards(codes) {
  if (!Array.isArray(codes) || !codes.length) return '<span class="muted">—</span>';
  return codes.map((code) => `
    <span class="card${isRedCard(code) ? " red" : ""}">${escapeHtml(cardLabel(code))}</span>
  `).join("");
}

function renderTable() {
  const room = currentRoom();
  const show = room && room.kind === "doudizhu";
  $("table-panel").hidden = !show;
  if (!show) return;

  const table = state.table || {};
  const players = Array.isArray(table.players) ? table.players : [];
  const seats = Array.isArray(table.seats) ? table.seats : [];
  const names = seats.length ? seats.map((seat) => seat.nickname) : players;
  const counts = table.hand_counts || {};
  const ready = new Set(Array.isArray(table.ready) ? table.ready : []);

  $("table-state").textContent = table.state || "WAITING";
  $("table-current").textContent = state.role === "spectator"
    ? "当前为观战者：牌桌已满，退出后可加入空牌桌"
    : (table.current ? `轮到：${table.current}` : "");
  $("table-multiplier").textContent = table.multiplier
    ? `倍数 ×${table.multiplier}${table.bombs ? ` · 炸弹 ${table.bombs}` : ""}`
    : "";

  $("seat-grid").innerHTML = Array.from({ length: 3 }).map((_, index) => {
    const name = names[index] || "空位";
    const seatInfo = seats[index];
    const online = seatInfo ? (seatInfo.online ? "在线" : "离线") : "等待";
    const active = name === table.current ? " current" : "";
    const landlord = name && name === table.landlord ? " · 地主" : "";
    const auto = name && (table.auto_players || []).includes(name) ? " · 托管" : "";
    const readyMark = name && ready.has(name) ? " · 已准备" : "";
    return `
      <div class="seat${active}">
        <div class="seat-name">${escapeHtml(name)}</div>
        <div class="seat-meta">${online}${readyMark}${landlord}${auto} · ${counts[name] ?? 0} 张</div>
      </div>`;
  }).join("");

  $("bottom-cards").innerHTML = miniCards(table.bottom);
  $("table-cards").innerHTML = miniCards(table.table);
  renderHand();

  const isMyTurn = table.current === state.nickname;
  const isBidding = table.state === "BIDDING";
  const isPlaying = table.state === "PLAYING";
  const waiting = !table.state || table.state === "WAITING";
  const myReady = Boolean(state.nickname && ready.has(state.nickname));
  $("btn-ready").hidden = state.role !== "player" || !waiting;
  $("btn-ready").textContent = myReady ? "已准备" : "准备";
  $("btn-ready").disabled = myReady;
  document.querySelectorAll(".bid-button").forEach((button) => {
    button.hidden = !(isBidding && isMyTurn);
  });
  $("btn-play").hidden = !(isPlaying && isMyTurn);
  $("btn-pass").hidden = !(isPlaying && isMyTurn);
  $("btn-auto").hidden = state.role !== "player";
  $("btn-auto").textContent = state.autoPlay ? "取回控制" : "托管";

  const result = table.result;
  $("game-result").textContent = result
    ? `赢家：${result.winner || "—"} · 倍数 ×${result.multiplier || 1}`
    : "";
}

function renderHand() {
  const box = $("hand-area");
  if (!state.hand.length) {
    box.innerHTML = `<span class="muted">${state.role === "spectator" ? "观战中，无手牌" : "暂无手牌"}</span>`;
    return;
  }
  box.innerHTML = state.hand.map((code) => {
    const selected = state.selected.has(code) ? " selected" : "";
    return `
      <button class="card${isRedCard(code) ? " red" : ""}${selected}"
              data-card="${escapeHtml(code)}">${escapeHtml(cardLabel(code))}</button>`;
  }).join("");
}

function renderPrivate() {
  const panel = $("private-panel");
  if (!state.privateOpen || !state.currentPeer) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  $("private-title").textContent = state.currentPeer;
  const log = $("private-log");
  const messages = state.privateMessages[state.currentPeer] || [];
  $("load-more-private").hidden = !state.privateHasMore;
  if (!messages.length) {
    log.innerHTML = '<div class="muted">暂无历史消息</div>';
    return;
  }
  log.innerHTML = "";
  for (const message of messages) {
    const line = document.createElement("div");
    line.className = "chat-line";
    line.textContent = `${message.from === state.nickname ? "我" : message.from}: ${message.text}`;
    log.appendChild(line);
  }
  log.scrollTop = log.scrollHeight;
}

function openPrivate(peer) {
  if (!peer || peer === state.nickname) return;
  state.currentPeer = peer;
  state.privateOpen = true;
  state.privateBefore = null;
  state.privateHasMore = false;
  state.privateLoading = false;
  if (!state.privateMessages[peer]) state.privateMessages[peer] = [];
  renderPrivate();
  send("private_open", { peer });
}

function loadMoreHistory() {
  if (!state.historyHasMore || !state.historyBefore || state.historyLoading) return;
  state.historyLoading = true;
  send("load_room_history", { before_id: state.historyBefore });
}

function loadMorePrivate() {
  if (!state.currentPeer || !state.privateHasMore || !state.privateBefore || state.privateLoading) {
    return;
  }
  state.privateLoading = true;
  send("load_private_history", {
    peer: state.currentPeer,
    before_id: state.privateBefore,
  });
}

function joinRoom(room) {
  if (room === state.roomKey) return;
  send("join_room", { room });
}

function createGroup() {
  const names = (state.onlineUsers.length ? state.onlineUsers : state.roomUsers)
    .filter((name) => name !== state.nickname);
  const list = $("group-member-list");
  list.innerHTML = names.length
    ? names.map((name) => `
      <label class="choice-row">
        <input type="checkbox" data-group-member="${escapeHtml(name)}">
        <span class="choice-name">${escapeHtml(name)}</span>
      </label>`).join("")
    : '<div class="muted">当前没有其他在线玩家，无法邀请建群。</div>';
  $("group-create-modal").hidden = false;
}

function submitGroupCreate() {
  const members = [...document.querySelectorAll("[data-group-member]:checked")]
    .map((input) => input.dataset.groupMember);
  if (!members.length) {
    showToast("请至少选择一位在线玩家", true);
    return;
  }
  send("create_group", { members });
  $("group-create-modal").hidden = true;
}

function openGroupInviteModal() {
  const room = currentRoom();
  if (!room || room.kind !== "group") return;
  const members = new Set(state.roomUsers);
  const names = (state.onlineUsers.length ? state.onlineUsers : [])
    .filter((name) => name !== state.nickname && !members.has(name));
  const list = $("group-invite-member-list");
  list.innerHTML = names.length
    ? names.map((name) => `
      <label class="choice-row">
        <input type="checkbox" data-group-invite-member="${escapeHtml(name)}">
        <span class="choice-name">${escapeHtml(name)}</span>
      </label>`).join("")
    : '<div class="muted">当前没有可邀请的非群成员在线。</div>';
  $("group-invite-modal").hidden = false;
}

function submitGroupInvite() {
  const members = [...document.querySelectorAll("[data-group-invite-member]:checked")]
    .map((input) => input.dataset.groupInviteMember);
  if (!members.length) {
    showToast("请至少选择一位非群成员", true);
    return;
  }
  send("invite_group_member", { members });
  $("group-invite-modal").hidden = true;
}

function renameGroup() {
  const current = state.roomName || state.room || "";
  const name = window.prompt("输入新的群名（允许重复）：", current);
  if (name === null) return;
  send("rename_group", { name });
}

function openChannelCreate() {
  if (state.nickname !== "wanjia1") return;
  $("channel-name").value = "";
  $("channel-create-modal").hidden = false;
  $("channel-name").focus();
}

function submitChannelCreate() {
  const name = $("channel-name").value.trim();
  if (!name) {
    showToast("请输入频道名称", true);
    return;
  }
  send("create_channel", { name });
  $("channel-create-modal").hidden = true;
}

function showGroupPanel(tab = "files") {
  state.groupTab = tab;
  state.groupPanelOpen = true;
  renderGroupFeatures();
}

function openGroupQr() {
  const token = state.groupState.invite_token;
  if (!token) {
    showToast("群邀请信息还没有同步，请刷新后重试", true);
    send("group_state");
    return;
  }
  const baseUrl = state.lanUrls[0] || location.origin;
  const inviteUrl = `${baseUrl}/join/${encodeURIComponent(token)}`;
  $("invite-url").value = inviteUrl;
  $("qr-image").src = `/api/qr?data=${encodeURIComponent(inviteUrl)}`;
  $("qr-modal").hidden = false;
}

async function uploadFile() {
  const file = $("file-input").files[0];
  const room = currentRoom();
  if (!file || !room || !room.group_id) {
    showToast("请先进入群聊并选择文件", true);
    return;
  }
  if (!state.fileToken) {
    showToast("网页文件会话尚未建立", true);
    return;
  }
  const form = new FormData();
  form.append("file", file);
  showToast("正在上传 " + file.name + "…");
  try {
    const response = await fetch(
      `/api/groups/${encodeURIComponent(room.group_id)}/files?token=${encodeURIComponent(state.fileToken)}`,
      { method: "POST", body: form },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "上传失败");
    showToast("上传完成：" + data.name);
    $("file-input").value = "";
    showGroupPanel("files");
  } catch (error) {
    showToast(error.message || "上传失败", true);
  }
}

function updatePollOptionRows() {
  const rows = [...document.querySelectorAll(".option-input")];
  rows.forEach((row, index) => {
    row.querySelector(".option-index").textContent = `选项 ${index + 1}`;
    row.querySelector(".option-remove").hidden = rows.length <= 2;
  });
}

function addPollOptionRow(value = "") {
  const rows = document.querySelectorAll(".option-input");
  if (rows.length >= 10) {
    showToast("最多添加 10 个投票选项", true);
    return;
  }
  const list = $("poll-option-inputs");
  const row = document.createElement("div");
  row.className = "option-input";
  row.innerHTML = `
    <span class="option-index"></span>
    <input class="poll-option-input" maxlength="80" placeholder="输入选项内容">
    <button class="option-remove" type="button" aria-label="删除选项">×</button>`;
  row.querySelector("input").value = value;
  row.querySelector(".option-remove").addEventListener("click", () => {
    if (document.querySelectorAll(".option-input").length <= 2) return;
    row.remove();
    updatePollOptionRows();
  });
  list.appendChild(row);
  updatePollOptionRows();
}

function openPollModal() {
  $("poll-title").value = "";
  $("poll-deadline").value = "";
  $("poll-option-inputs").innerHTML = "";
  addPollOptionRow();
  addPollOptionRow();
  $("poll-create-modal").hidden = false;
  $("poll-title").focus();
}

function submitPollCreate() {
  const title = $("poll-title").value.trim();
  const options = [...document.querySelectorAll(".poll-option-input")]
    .map((input) => input.value.trim())
    .filter(Boolean);
  if (!title) {
    showToast("请输入投票标题", true);
    return;
  }
  if (options.length < 2) {
    showToast("至少填写两个投票选项", true);
    return;
  }
  const minutesRaw = $("poll-deadline").value;
  const deadline = minutesRaw ? Number(minutesRaw) : null;
  send("poll_create", {
    title,
    options,
    deadline_minutes: Number.isFinite(deadline) && deadline > 0 ? deadline : null,
  });
  $("poll-create-modal").hidden = true;
}

function editAnnouncement() {
  const current = state.groupState.announcement?.text || "";
  const text = window.prompt("编辑群公告：", current);
  if (text === null) return;
  send("set_announcement", { text });
}

async function deleteAttachment(fileId) {
  if (!confirm("确定删除这个群文件吗？")) return;
  try {
    const response = await fetch(
      `/api/attachments/${encodeURIComponent(fileId)}?token=${encodeURIComponent(state.fileToken || "")}`,
      { method: "DELETE" },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "删除失败");
    showToast("文件已删除");
  } catch (error) {
    showToast(error.message || "删除失败", true);
  }
}

function bindUi() {
  $("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    setAuthStatus("正在登录…");
    send("login", {
      account: $("login-account").value.trim(),
      password: $("login-password").value,
    });
  });

  $("register-form").addEventListener("submit", (event) => {
    event.preventDefault();
    setAuthStatus("正在创建账号…");
    send("register", {
      account: $("register-account").value.trim(),
      password: $("register-password").value,
    });
  });

  $("show-register").addEventListener("click", () => {
    $("login-form").hidden = true;
    $("register-form").hidden = false;
    $("show-register").hidden = true;
    setAuthStatus("创建账号后直接进入大厅");
  });

  $("cancel-register").addEventListener("click", () => {
    $("login-form").hidden = false;
    $("register-form").hidden = true;
    $("show-register").hidden = false;
    setAuthStatus("请登录");
  });

  $("logout-btn").addEventListener("click", () => {
    state.loggingOut = true;
    if (!send("logout")) state.loggingOut = false;
  });
  $("refresh-btn").addEventListener("click", () => send("refresh_all"));
  $("refresh-users-btn").addEventListener("click", () => send("refresh_users"));
  $("create-group-btn").addEventListener("click", createGroup);
  $("create-channel-btn").addEventListener("click", openChannelCreate);
  $("confirm-channel-create").addEventListener("click", submitChannelCreate);
  $("cancel-channel-create").addEventListener("click", () => {
    $("channel-create-modal").hidden = true;
  });
  $("channel-create-modal").addEventListener("click", (event) => {
    if (event.target === $("channel-create-modal")) $("channel-create-modal").hidden = true;
  });
  $("confirm-group-create").addEventListener("click", submitGroupCreate);
  $("cancel-group-create").addEventListener("click", () => {
    $("group-create-modal").hidden = true;
  });
  $("group-create-modal").addEventListener("click", (event) => {
    if (event.target === $("group-create-modal")) $("group-create-modal").hidden = true;
  });
  $("invite-group-member-btn").addEventListener("click", openGroupInviteModal);
  $("confirm-group-invite").addEventListener("click", submitGroupInvite);
  $("cancel-group-invite").addEventListener("click", () => {
    $("group-invite-modal").hidden = true;
  });
  $("group-invite-modal").addEventListener("click", (event) => {
    if (event.target === $("group-invite-modal")) $("group-invite-modal").hidden = true;
  });
  $("group-pending-list").addEventListener("click", (event) => {
    const approve = event.target.closest("[data-approve-invite]");
    if (approve) {
      send("approve_group_invite", { account: approve.dataset.approveInvite });
      return;
    }
    const reject = event.target.closest("[data-reject-invite]");
    if (reject) send("reject_group_invite", { account: reject.dataset.rejectInvite });
  });
  $("rename-group-btn").addEventListener("click", renameGroup);
  $("group-tools-btn").addEventListener("click", () => {
    state.groupPanelOpen = true;
    renderGroupFeatures();
  });
  $("group-qr-btn").addEventListener("click", openGroupQr);
  $("close-group-panel").addEventListener("click", () => {
    state.groupPanelOpen = false;
    renderGroupFeatures();
  });
  $("upload-file-btn").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", uploadFile);
  $("create-poll-btn").addEventListener("click", openPollModal);
  $("add-poll-option").addEventListener("click", () => addPollOptionRow());
  $("confirm-poll-create").addEventListener("click", submitPollCreate);
  $("cancel-poll-create").addEventListener("click", () => {
    $("poll-create-modal").hidden = true;
  });
  $("poll-create-modal").addEventListener("click", (event) => {
    if (event.target === $("poll-create-modal")) $("poll-create-modal").hidden = true;
  });
  $("edit-announcement-btn").addEventListener("click", editAnnouncement);
  $("close-qr").addEventListener("click", () => {
    $("qr-modal").hidden = true;
  });
  $("qr-modal").addEventListener("click", (event) => {
    if (event.target === $("qr-modal")) $("qr-modal").hidden = true;
  });
  document.querySelectorAll(".group-tab").forEach((button) => {
    button.addEventListener("click", () => showGroupPanel(button.dataset.groupTab));
  });
  $("file-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-file-delete]");
    if (button) deleteAttachment(button.dataset.fileDelete);
  });
  $("poll-list").addEventListener("click", (event) => {
    const vote = event.target.closest("[data-poll-vote]");
    if (vote) {
      send("poll_vote", {
        poll_id: vote.dataset.pollVote,
        option_id: vote.dataset.pollOption,
      });
      return;
    }
    const close = event.target.closest("[data-poll-close]");
    if (close) send("poll_close", { poll_id: close.dataset.pollClose });
  });
  $("leave-room-btn").addEventListener("click", () => send("leave_room"));
  $("delete-group-btn").addEventListener("click", () => {
    if (state.roomKey) send("delete_group", { room: state.roomKey });
  });

  $("room-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-room-key]");
    if (button) joinRoom(button.dataset.roomKey);
  });

  $("user-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-peer]");
    if (button) openPrivate(button.dataset.peer);
  });

  $("conversation-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-conversation]");
    if (button) openPrivate(button.dataset.conversation);
  });

  $("hand-area").addEventListener("click", (event) => {
    const button = event.target.closest("[data-card]");
    if (!button) return;
    const code = button.dataset.card;
    if (state.selected.has(code)) state.selected.delete(code);
    else state.selected.add(code);
    renderHand();
  });

  $("btn-ready").addEventListener("click", () => send("ready"));
  $("btn-play").addEventListener("click", () => {
    const cards = state.hand.filter((card) => state.selected.has(card));
    if (!cards.length) {
      showToast("先点击选择要出的牌", true);
      return;
    }
    send("play", { cards });
  });
  $("btn-pass").addEventListener("click", () => send("pass"));
  $("btn-auto").addEventListener("click", () => send("set_auto", {
    enabled: !state.autoPlay,
  }));
  document.querySelectorAll(".bid-button").forEach((button) => {
    button.addEventListener("click", () => send("bid", {
      score: Number(button.dataset.score),
    }));
  });

  $("chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("chat-input").value.trim();
    if (!text) return;
    send("room_msg", { text });
    $("chat-input").value = "";
  });
  $("load-more-history").addEventListener("click", loadMoreHistory);
  $("load-more-private").addEventListener("click", loadMorePrivate);

  $("private-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("private-input").value.trim();
    if (!text) return;
    send("private_msg", { text });
    $("private-input").value = "";
  });

  $("close-private").addEventListener("click", () => {
    state.privateOpen = false;
    renderPrivate();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const match = location.pathname.match(/^\/join\/([^/]+)\/?$/);
  state.pendingInvite = match ? decodeURIComponent(match[1]) : null;
  bindUi();
  connectSocket();
});
