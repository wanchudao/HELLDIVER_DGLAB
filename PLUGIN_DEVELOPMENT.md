# DGHub 插件开发指南

DGHub 插件可以用**任意语言**编写，只要会发 JSON over WebSocket。
你的插件以独立进程运行，DGHub 主程序通过 token 鉴权 + 协议消息控制它。

> 本文档对应 SDK v1（握手时 `manifest.sdk` 字段填 `"1"`）。
> SDK 升级不兼容时，主程序会在 `hello_ack` 中返回 `accepted: false` 与拒绝原因。

---

## 1. 5 分钟最小可行示例

最简单的 Python 插件，~30 行：

```python
import asyncio, json, os, websockets

async def main():
    host  = os.environ["DGHUB_HOST"]
    port  = os.environ["DGHUB_PORT"]
    token = os.environ["DGHUB_TOKEN"]

    async with websockets.connect(f"ws://{host}:{port}/ws/plugin?token={token}") as ws:
        # 1. 握手
        await ws.send(json.dumps({
            "op": "hello",
            "token": token,
            "manifest": {
                "id": "hello_world",
                "name": "Hello World",
                "version": "0.1.0",
                "sdk": "1",
            },
        }))
        ack = json.loads(await ws.recv())
        assert ack["accepted"], ack.get("reason")

        # 2. 触发一次：强度 +50%，播 1.5 秒波形，自动回正
        await ws.send(json.dumps({
            "op": "trigger",
            "action": "both",
            "delta_pct": 50,
            "strength_mode": "rollback",
            "duration_s": 1.5,
            "preset": "CS2-受伤",
            "channel": "both",
            "label": "Hello World!",
        }))

        # 3. 等主程序请求停止
        async for raw in ws:
            if json.loads(raw).get("op") == "stop":
                return

asyncio.run(main())
```

把这段代码 + 一个 `manifest.json` 放进 `plugins/hello_world/`，重启 DGHub
即可在「插件中心 → 外部插件」看到它。

---

## 2. 目录结构

```
plugins/
  my_plugin/
    manifest.json      # 必需 — 插件元信息 + 配置 schema
    main.py            # 入口（manifest.entry 指向）
    vendor/            # 可选 — 第三方依赖自包含目录（见下文）
    其它资源/...        # 你想要的任何文件
```

**外部插件准则**：

- 插件必须是一个自包含目录，`manifest.json` 放在插件根目录。
- `manifest.entry` 必须是相对插件根目录的路径，不允许依赖用户机器上的绝对路径。
- DGHub 启动插件时，工作目录固定为插件根目录。
- Python 插件由 DGHub 保证可导入以下目录：entry 所在目录、插件根目录、entry 同级 `vendor/`、插件根目录 `vendor/`。
- 插件自己的模块应放在 entry 同级或插件根目录，例如 `main.py` 可以直接 `import memory_reader`。
- 插件特有的第三方库必须随插件一起分发，推荐放入 `vendor/`；DGHub 不会联网或自动 pip install 插件依赖。
- 不要要求用户手动复制文件到 DGHub 安装目录、系统 Python 或全局 `site-packages`。

**第三方依赖处理（vendor 模式）**：

如果你的插件需要第三方库（如 `pymem`、`vdf` 等），请使用 vendor 模式实现自包含：

1. **创建 vendor 目录**：在插件根目录下创建 `vendor/` 子目录
2. **复制依赖源码**：把第三方库的源码复制到 `vendor/` 下（例如 `vendor/pymem/`）
3. **正常导入依赖**：DGHub 启动 `.py` 插件时会把 `vendor/` 放入导入路径，插件代码直接 `import pymem` 即可

**为什么需要 vendor 模式**：
- 用户导入插件压缩包后应该能直接运行，不需要手动安装依赖
- DGHub 主程序不会自动安装插件的第三方依赖
- vendor 模式确保插件完全自包含，分发更简单

**哪些依赖需要 vendor**：
- **插件特有的依赖**（如 `pymem`、`vdf` 等）：需要 vendor
- **DGHub 基础依赖**（如 `websockets`）：不需要 vendor，DGHub 主程序环境已提供
- **Python 标准库**：不需要 vendor

**最佳实践**：
- 只 vendor 插件特有的第三方依赖
- 不要 vendor DGHub 已提供的基础依赖（避免重复和冲突）
- 如果插件依赖的库体积很大（>1MB），考虑是否真的需要，或者寻找轻量替代
- 如果要脱离 DGHub 手动运行插件调试，请从插件根目录运行，或自行设置 `PYTHONPATH` 包含插件根目录和 `vendor/`

**如何获取依赖源码**：
```bash
# 方法 1：从已安装的 site-packages 复制
python -c "import shutil, os, pymem; shutil.copytree(os.path.dirname(pymem.__file__), 'vendor/pymem')"

# 方法 2：用 pip 下载源码包
pip download --no-deps pymem -d .
tar -xzf pymem-*.tar.gz
mv pymem-*/pymem vendor/
```

**多语言支持**：

- `entry: "main.py"`            → 开发环境用本机 Python；安装包内由主程序 `DGLab-Console.exe --plugin-runner main.py` 子模式执行（不会再弹第二个桌面窗口）
- `entry: "my_plugin.exe"`     → 直接 Popen（Go / Rust / C# 都可以编译成 exe）
- `entry: "node_app/server.js"` → 当前 SDK 不直接支持，建议你写一个 `.bat` 启动脚本作 entry

---

## 3. manifest.json 完整字段

```jsonc
{
  "id": "my_plugin",          // 必需，^[a-z][a-z0-9_-]{1,31}$
  "name": "我的插件",          // 必需，UI 显示名
  "version": "0.1.0",         // 必需，语义化版本
  "sdk": "1",                 // 必需，兼容的 SDK 主版本
  "author": "你的名字",
  "description": "一句话简介",
  "homepage": "https://...",
  "entry": "main.py",         // 主程序 spawn 时用，不写则只能用户手动启动
  "capabilities": {           // 可选能力声明
    "startup_check": true     // 配置页显示“启动检查”按钮
  },
  "config_schema": [          // 配置 UI；不写则前端只显示开关
    {
      "section": "基础",
      "fields": [
        { "key": "intensity", "type": "percent", "label": "默认强度", "default": 30 },
        { "key": "channel",   "type": "channel", "label": "通道",     "default": "both" }
      ]
    }
  ]
}
```

### config_schema 字段类型

| type | 渲染为 | 字段值类型 |
|---|---|---|
| `bool` | 开关 | bool |
| `percent` | 0–100 滑块 | int |
| `duration` | 数字输入 + "秒" | float |
| `number` | 通用数字框（min/max/step） | int/float |
| `text` | 单行文本 | string |
| `select` | 下拉框（用 `options`） | 任意 |
| `channel` | a/b/both 三选 | "a" / "b" / "both" |
| `preset` | 波形预设下拉，自动从主程序拉 | string |
| `path` | 路径输入（目前简化为文本） | string |

每个字段除 `key/type/label` 外可选：`default / description / min / max / step / options`。

### capabilities

| key | 含义 |
|---|---|
| `startup_check` | 声明插件会通过 `op: status` 上报 `startup_check`。声明后，外部插件配置页会显示“启动检查”按钮。 |

未声明 `capabilities.startup_check: true` 的插件即使上报了 `startup_check`，前端也不会显示启动检查入口。

---

## 4. 启动机制

DGHub 在用户 toggle 启用插件时，通过环境变量传入：

| 环境变量 | 含义 |
|---|---|
| `DGHUB_HOST` | 主程序 host（一般 127.0.0.1） |
| `DGHUB_PORT` | 主程序 API 端口 |
| `DGHUB_TOKEN` | 会话 token，每次主程序启动重新生成，**握手必须携带** |
| `DGHUB_PLUGIN_ID` | 本插件 id（与 manifest.id 一致） |
| `DGHUB_PLUGIN_ROOT` | 插件根目录绝对路径 |

子进程被加入 Windows Job Object，主程序退出时内核会自动 kill 你的插件，
不会有孤儿进程。

> 在调试期间也可以**不**通过 DGHub spawn —— 自己启动插件进程，
> 从 `GET /api/plugins/_session_token` 拉 token 即可手动接入。

---

## 5. 协议消息（按 op 字段分发）

### 客户端 → 服务端

| op | 用途 |
|---|---|
| `hello` | 握手，第一条必须是它，带 manifest + token |
| **`trigger`** | **推荐**统一触发：一条消息同时控制强度 + 波形 + 模式（rollback / permanent）+ 通道；可参与全局高强度事件音效判断 |
| `event` | 一次性事件 — 只显示倒计时卡片，**不会自动改强度，也不会触发高强度事件音效** |
| `pulse` | 触发一段预设波形（不改强度，比 trigger 更底层） |
| `set_strength` | 绝对设置该插件的强度层（百分比） |
| `adjust_strength` | 增减该插件的强度层 |
| `status` | 持续状态字段，主程序合并到 `plugins_status[id]` |
| `log` | 转发日志到主程序日志面板 |
| `set_config` | 持久化插件自己的运行时数据到配置 |

### 服务端 → 客户端

| op | 触发时机 |
|---|---|
| `hello_ack` | 握手完成（accepted=true/false） |
| `config` | 握手后立刻推一次全量配置 |
| `config_changed` | 用户在前端改了配置 |
| `device_info` | 设备连接 / 类型 / max 强度变化 |
| `stop` | 主程序请求插件优雅退出 |
| `ping` / `pong` | 保活 |

各消息的完整字段参见下面的「应用层消息参考」小节。

---

## 6. 强度模型 + `trigger` 详解

### 强度模型

DGHub 每个插件维护**自己的一层**强度，设备 A/B 通道分别取所有插件层的最大值，绝不相加。

每个插件还有一个 **baseline**（基础锚点）：
- 启动时从 `config.idle_strength`（默认 0）读
- `permanent` 模式触发会**修改**它（持久化写盘）
- `rollback` 模式触发会**临时**挤掉它，duration 结束后自动回正

### `trigger` —— 推荐的核心消息

一条 `trigger` 同时覆盖了 Bilibili 直播弹幕规则的全部行为：

```jsonc
{
  "op": "trigger",
  "action": "both",            // both / strength / waveform
  "delta_pct": 50,             // 相对 baseline 的增量百分比，可正负
  "strength_mode": "rollback", // rollback / permanent
  "duration_s": 1.5,
  "preset": "CS2-受伤",         // 用主程序原生波形预设的名字
  "channel": "both",           // a / b / both
  "label": "弹幕命中：电击",    // UI / OBS 显示
  "username": "用户名"          // 可选
}
```

| 字段组合 | 效果 |
|---|---|
| `action=both, mode=rollback, delta=50, duration=1.5, preset=X` | layer 设到 baseline+50%，播 X 波形 1.5 秒，1.5s 后回 baseline（**直播规则典型行为**） |
| `action=strength, mode=permanent, delta=10` | baseline 永久 +10%，立即应用，写入配置（**节奏游戏「血量越掉越强」用法**） |
| `action=waveform, preset=X, duration=3` | 不改强度，只播 3 秒波形（**纯通知触感**） |
| `action=both, mode=permanent, delta=-5, preset=X` | baseline 永久 -5% 同时播一次波形 |

### 何时用低层消息

`set_strength` / `adjust_strength` / `pulse` 仍然保留，**几乎不用，除非**：
- 你有自己的强度状态机（不需要 baseline / lease 模型）
- 你只想播波形不更新事件流
- 你想精确控制每个通道的绝对值（不基于 baseline）

否则一律用 `trigger`。

### 外部插件与高强度事件音效

用户可在 DGHub 全局设置中开启“高强度事件音效”。对外部插件，只有包含强度目标的
`trigger` 会参与判断：服务端根据插件当前 baseline 与 `delta_pct` 计算本次事件的
目标强度；该值大于或等于用户阈值时才可能播放。这里判断的是事件自身的目标值，
不是多个插件强度层取最大值后的设备输出。

- `event` 只是展示消息，不触发音效。
- `set_strength` / `adjust_strength` / `pulse` 是底层命令，不被当成一次完整事件。
- 同一事件最多请求一次；双通道不会让它播放两次。
- 即使该事件被其他插件的更高强度层覆盖，仍按自身目标强度判断。
- 设备未连接时不播放；已有音效播放时，新的音效请求会被忽略且不排队，但游戏事件、
  强度和波形命令照常执行。

如果插件需要强度、波形、事件流和高强度提示保持同一语义，请使用 `trigger`。

---

## 7. 配置自描述 + 自动 UI

只要你在 `manifest.config_schema` 里声明字段，主程序前端会**自动**生成一个配置页面，
你不用写任何 UI 代码。用户改值后通过 `config_changed` 消息实时通知你的插件。

示范：主程序随软件附带一个示范插件，位于 `plugins/demo_external/`，可直接参考完整 Python 实现。

---

## 8. 应用层消息参考

所有消息都是单行 JSON，第一行字段固定为 `op`。除特别标注外，**字段缺失 = 用默认值**。
若发了不认识的字段，主程序会拒绝（`extra = "forbid"`）。

### 8.1 客户端 → 服务端

#### `hello`（必发，第一条）
```jsonc
{
  "op": "hello",
  "token": "...",          // 启动时主程序通过 DGHUB_TOKEN / --token 传入，必填
  "manifest": {            // 必填，与 manifest.json 内容一致
    "id": "my_plugin",     // ^[a-z][a-z0-9_-]{1,31}$
    "name": "我的插件",
    "version": "0.1.0",    // 语义化版本
    "sdk": "1",            // 必填，主版本必须等于 SDK_MAJOR
    "author": "",          // 可选
    "description": "",     // 可选
    "homepage": "",        // 可选
    "entry": "",           // 可选（spawn 入口；用户已手动启动时可不填）
    "capabilities": { "startup_check": true }, // 可选
    "config_schema": []    // 可选；不填则前端只显示开关
  }
}
```

#### `trigger`（**推荐**统一触发）
```jsonc
{
  "op": "trigger",
  "action": "both",            // "both" | "strength" | "waveform"，默认 both
  "delta_pct": 50,             // -100 ~ 100，默认 0；action=waveform 时忽略
  "strength_mode": "rollback", // "rollback" | "permanent"，默认 rollback
  "duration_s": 1.5,           // 0 ~ 300，默认 1.0
  "preset": "CS2-受伤",         // 波形预设名；action 含 waveform 时必填
  "channel": "both",           // "a" | "b" | "both"，默认 both
  "label": "",                 // 可选，UI 显示用
  "username": ""               // 可选
}
```

#### `event`（一次性事件 — 只显示倒计时卡片，不会自动改强度 / 播波形）
```jsonc
{
  "op": "event",
  "label": "受击",                // 必填，事件简称
  "name": "BOSS 重击 -25HP",      // 必填，详细描述
  "username": "",                // 可选，触发者
  "strength_pct": null,          // 可选 int，仅展示
  "duration": 1.5,               // 倒计时秒数，默认 1.0
  "event_id": null               // 可选，去重用
}
```

#### `pulse`（只播波形，不改强度）
```jsonc
{
  "op": "pulse",
  "preset": "CS2-受伤",   // 必填
  "channel": "both"      // 默认 both
}
```

#### `set_strength` / `adjust_strength`（直接读写该插件的强度层）
```jsonc
{ "op": "set_strength",    "channel": "both", "pct": 50 }      // 0 ~ 100
{ "op": "adjust_strength", "channel": "a",    "delta_pct": 10 } // -100 ~ 100
```

#### `status`（持续状态字段）
```jsonc
{
  "op": "status",
  "fields": {                   // 任意 key/value
    "display_status": "运行中",  // 特殊键：驱动桌面悬浮窗状态卡片
    "tick": 42
  }
}
```

##### 启动检查状态

如果 manifest 声明了 `capabilities.startup_check: true`，可以在 `status.fields` 里主动上报 `startup_check`：

```jsonc
{
  "op": "status",
  "fields": {
    "display_status": "等待外部程序连接",
    "startup_check": {
      "title": "我的插件启动检查",
      "steps": [
        {
          "key": "plugin",
          "title": "启动插件",
          "state": "ok",
          "detail": "插件进程已连接 DGHub"
        },
        {
          "key": "source",
          "title": "外部数据源",
          "state": "pending",
          "detail": "等待外部程序连接",
          "hint": "确认外部程序已启动，并允许本插件读取数据"
        }
      ]
    }
  }
}
```

`state` 可选值：

| state | 含义 |
|---|---|
| `idle` | 尚未开始或暂无数据 |
| `pending` | 已开始，等待外部条件 |
| `ok` | 正常 |
| `warn` | 可运行但需要注意 |
| `fail` | 当前步骤失败 |

字段约定：

- `detail` 优先写当前状态，例如“尚未收到游戏数据”。
- `hint` 可选，写简短建议，例如“启动游戏并进入一局对局后会自动更新”。
- 设备连接由 DGHub 前端统一追加，插件不要重复上报。
- 启动检查只做提示，不会阻止插件启用。很多用户会先启用插件再启动游戏。
- 主程序会宽松清洗外部插件上报：只保留 `title/updated_at/steps[].key/title/state/detail/hint`，非法 `state` 降级，超长文本截断，格式错误的步骤丢弃。

#### `log`（转发日志到主程序日志面板）
```jsonc
{
  "op": "log",
  "level": "info",        // "debug" | "info" | "warning" | "error"，默认 info
  "message": "已就绪"
}
```

#### `set_config`（持久化插件自己的运行时数据）
```jsonc
{ "op": "set_config", "key": "total_runs", "value": 42 }
```

`target_id` 由主程序的目标切换事务管理，外部插件通过 `set_config` 修改该键会被拒绝；单次行为需要指定设备时，请使用控制消息自身的 `target_id` 字段。

### 8.2 服务端 → 客户端

#### `hello_ack`
```jsonc
{
  "op": "hello_ack",
  "sdk_version": "1.0",
  "accepted": true,         // 失败时 false
  "reason": null            // 失败原因
}
```

#### `config`（握手后立刻推一次全量）
```jsonc
{ "op": "config", "data": { "channel": "both", "intensity": 30 } }
```

#### `config_changed`（用户在前端改了配置）
```jsonc
{ "op": "config_changed", "key": "intensity", "value": 50 }
```

#### `device_info`（设备状态变化）
```jsonc
{
  "op": "device_info",
  "connected": true,
  "device_type": "v3",      // "v2" | "v3" | ""
  "max_strength_a": 200,
  "max_strength_b": 200
}
```

#### `stop`（主程序请求插件优雅退出）
```jsonc
{ "op": "stop", "reason": "manager.stop_plugin" }
```

#### `ping` / `pong`
```jsonc
{ "op": "ping", "t": 1234567890.123 }
{ "op": "pong", "t": 1234567890.123 }
```

---

## 9. 打包发布

把整个插件目录 zip 起来分发：

```
my_plugin.zip
├── manifest.json
├── main.py
├── vendor/          # 可选，插件特有的第三方 Python 依赖
└── 其它资源...
```

zip 根目录直接放文件，**或者**所有文件包在唯一一级子目录里（两种都支持）。

用户在 DGHub 前端 → 插件中心 → 外部插件 → 「导入 zip」即可安装。

---

## 10. 常见问题

**Q: 我的插件需要额外 Python 依赖怎么办？**
A: 优先把插件特有依赖放进插件目录的 `vendor/`，随 zip 一起分发。
DGHub 启动 `.py` 插件时会自动把 `vendor/` 加入导入路径。
如果依赖体积很大、包含复杂原生库，推荐把插件打包成 `.exe` 再把 `entry` 指向该 exe。

**Q: token 会泄漏吗？**
A: token 只暴露在 `127.0.0.1:port` 上，不开外网。每次主程序启动重新生成。
同机任何进程都可以请求 `/api/plugins/_session_token` 拿到 token —— 这主要是为了方便调试，请勿在不可信任的环境运行未知插件。

**Q: 我的插件崩了怎么办？**
A: WebSocket 断开后主程序会自动清理插件状态，前端显示「未运行」；
用户再点 toggle 会重新启动进程。子进程崩溃不会影响主程序。

**Q: 插件能调用其它插件吗？**
A: 当前 SDK v1 不支持。所有插件只跟主程序对话；
跨插件协作需要通过主程序中转（例如一边写 `set_config`、另一边听 `config_changed`）。

---

## 11. 参考实现

在 DGHub **插件中心 → 外部插件** 页面：

1. 点 **「另存示范插件…」** 保存 `demo_external.zip`，用「导入 zip」安装，或解压到本机插件目录；
2. 点 **「另存为…」** 通过系统对话框保存本指南的 `.md` 副本，离线查阅；
3. 安装后源码位于插件目录下的 `demo_external/`（与 `manifest.json`、`main.py`），可复制该文件夹改名为自己的插件。

> 协议字段的权威定义以主程序内置 SDK 为准；本文档与发版包同步更新，无需访问任何 Git 仓库。
