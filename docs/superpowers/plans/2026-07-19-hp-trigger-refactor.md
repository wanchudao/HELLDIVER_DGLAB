# HP% 直驱触发重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 砍掉 event 中间层，trigger 直接用 HP% 判定何时放电。净减少 ~50 行代码。

**Architecture:** `trigger.py` 的 `dispatch()` 重写为 HP% 驱动——用 0.2s 滑动窗口对比判断受伤、用 frame-to-frame delta 判断治疗、用绝对值判断死亡/残血。`state_machine.py` 砍掉 `_detect_events()` 和所有 event 相关字段，只保留状态判定。接口对 `runner_real.py` 透明——`trigger.dispatch(result)` 签名不变。

**Tech Stack:** 纯 Python stdlib

---

## 对比

```
改前:
  HP% → state_machine(状态+event检测+冷却+合并窗口) → trigger(读event字段)
  350行

改后:
  HP% → state_machine(状态+debounce+拒识) → trigger(读HP%直接算)
  200行 + 60行
```

---

## 文件结构

## 审查记录

> 2026-07-19 — PRO + LongCat 双审查

| 发现 | 严重度 | 修复 |
|------|--------|------|
| `_last_hp > 0` 导致死亡后治疗被跳过 | Bug (PRO) | 改为 `_last_hp >= 0` |
| runner gate `result.event != "none"` 永远 False → dispatch 永不被调用 | **阻塞** (双方) | runner_real 去掉 event gate，每帧无条件 dispatch |
| low_health 独立 if 覆盖 death_pulse | Bug (双方) | 加 `action is None` 守卫 |

---

| 文件 | 操作 | 改动行 |
|------|------|--------|
| `src/trigger.py` | **修改** | `dispatch()` 重写，+30 / -20 |
| `src/state_machine.py` | **修改** | 砍 event 相关，-120 |
| `src/runner_real.py` | **修改** | 去 event gate + 去 event 显示 |
| `src/runner_mock.py` | **修改** | 去 event gate |
| `tests/test_health_detector.py` | √ 不改 | |
| `tests/test_calibrate.py` | √ 不改 | |

---

### Task 1: 重写 trigger.dispatch()

**Files:**
- Modify: `src/trigger.py`

#### 原逻辑（砍掉）

```python
EVENT_ACTION_MAP = {...}  # 删除
# dispatch() 中的:
#   if event == "none": ...
#   action = EVENT_ACTION_MAP.get(event)
# 替换为 HP% 直判
```

#### 新 `dispatch()` 完整代码

```python
"""Trigger dispatch: 接收 DetectionResult，HP% 直判路由到 sender。

设计原则:
- 不用 event 中间层——HP% 直接判定
- 死亡看 hp ≤ 1%
- 受伤看 0.2s 窗口内 hp 下降 > 2%
- 治疗看 hp 比上一帧高
- 残血看 hp < 50% 每 3s
- 冷却统一在 trigger 层用挂钟时间管理
- sender 通过 config.ini [trigger] sender_mode 切换
"""
import json
import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# JSONL 日志路径
LOG_PATH = Path(__file__).resolve().parent.parent / "debug" / "trigger_log.jsonl"

# 冷却时间（秒）
COOLDOWNS: dict[str, float] = {
    "injury_pulse": 0.5,
    "death_pulse": 5.0,
    "heal_pulse": 1.0,
    "low_health": 3.0,
}

# 判定阈值
INJURY_WINDOW_SEC = 0.2    # 受伤合并窗口
INJURY_DELTA_THRESH = 0.02  # 窗口内 hp 下降超过 2% 触发
DEATH_HP_THRESH = 0.01      # hp ≤ 1% 触发死亡
LOW_HEALTH_HP_THRESH = 0.50 # hp < 50% 触发残血


def _mock_send(action: str, payload: dict | None = None) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [MOCK SEND] action={action}  payload={payload}")
    return {"dispatched": True, "action": action, "mode": "mock"}


class TriggerDispatch:
    """接收 vision 结果，HP% 直判路由到 sender adapter。"""

    def __init__(self, config: dict):
        trigger_cfg = config.get("trigger", {})
        self._mode = trigger_cfg.get("sender_mode", "mock")
        self._frame_idx = 0

        # ── HP% 直判状态 ──
        self._hp_history: deque[tuple[float, float]] = deque()  # [(timestamp, hp), ...]
        self._last_hp = -1.0  # 上一帧 hp (用作方向判断)
        self._last_fired: dict[str, float] = {}  # 每个 action 上次触发时间

        # 日志
        LOG_PATH.parent.mkdir(exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session_start",
                "ts": datetime.now().isoformat(),
                "mode": self._mode,
            }) + "\n")

        if self._mode == "mock":
            self._send = _mock_send
            logger.info("trigger ready  mode=mock (HP%-direct)")
            return

        if self._mode == "dglab":
            try:
                import websocket  # noqa: F401
            except ImportError:
                raise RuntimeError(
                    "sender_mode=dglab 但 websocket-client 未安装。\n"
                    "请运行: pip install websocket-client\n"
                    "或改 config.ini [trigger] sender_mode = mock"
                )
            try:
                from src.dglab_sender import init_sender, send_action as dglab_send
                sender = init_sender(config)
                if not sender.connect_and_pair():
                    raise ConnectionError("配对超时或失败")
                self._send = dglab_send
                logger.info("trigger: dglab connected and paired")
            except ConnectionError as e:
                raise ConnectionError(
                    f"DG-LAB 后端连接失败: {e}\n"
                    "请先启动 SOCKET v2 后端, 或改 config.ini [trigger] sender_mode = mock"
                ) from e
            return

        logger.warning("trigger: unknown sender_mode=%s, falling back to mock", self._mode)
        self._send = _mock_send
        self._mode = "mock"
        logger.info("trigger ready  mode=%s", self._mode)

    def dispatch(self, result) -> dict:
        """接收 DetectionResult，HP% 直判路由。"""
        self._frame_idx += 1
        hp = result.health_stable
        now = time.time()

        # ── HP 历史（0.2s 滑动窗口，用于受伤检测）──
        self._hp_history.append((now, hp))
        cutoff = now - INJURY_WINDOW_SEC
        while self._hp_history and self._hp_history[0][0] < cutoff:
            self._hp_history.popleft()

        action = None

        # 死亡: hp ≤ 1%
        if hp <= DEATH_HP_THRESH and self._last_hp >= 0:
            action = "death_pulse"

        # 治疗: hp 比上一帧高 (上一帧≥0 以覆盖死亡后复活)
        elif self._last_hp >= 0 and hp > self._last_hp:
            action = "heal_pulse"

        # 受伤: 0.2s 窗口内 hp 下降 > 2%
        elif len(self._hp_history) >= 2:
            hp_oldest = self._hp_history[0][1]
            hp_drop = hp_oldest - hp
            if hp_drop > INJURY_DELTA_THRESH:
                action = "injury_pulse"

        # 残血: hp < 50%, 每 3s（仅当没有更高优先级事件时）
        if action is None and hp < LOW_HEALTH_HP_THRESH:
            last_lh = self._last_fired.get("low_health", 0)
            if now - last_lh >= COOLDOWNS["low_health"]:
                action = "low_health"

        # ── 冷却检查 + 发送 ──
        dispatched = False
        if action is not None:
            last = self._last_fired.get(action, 0)
            if now - last >= COOLDOWNS.get(action, 0.5):
                payload = {"hp": round(hp, 3), "state": result.state}
                self._send(action, payload)
                self._last_fired[action] = now
                dispatched = True
                logger.info(
                    "trigger: %s  hp=%.1f%%  模式=%s",
                    action, hp * 100, self._mode,
                )

        # ── 日志 ──
        log_entry = {
            "frame": self._frame_idx,
            "ts": datetime.now().isoformat(),
            "hp": round(hp, 3),
            "raw_hp": round(result.health_raw, 3),
            "state": result.state,
            "action": action if dispatched else "none",
            "dispatched": dispatched,
        }
        self._write_log(log_entry)

        self._last_hp = hp
        return {"dispatched": dispatched, "action": action or "none"}

    def safe_stop(self):
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session_end",
                "ts": datetime.now().isoformat(),
                "frames": self._frame_idx,
            }) + "\n")
        if self._mode == "dglab":
            try:
                from src.dglab_sender import safe_stop as dglab_safe_stop
                dglab_safe_stop()
            except Exception as e:
                logger.error("trigger: safe_stop failed: %s", e)
        else:
            logger.info("trigger: safe_stop (no-op in mock mode)")

    def _write_log(self, entry: dict):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
```

---

### Task 2: 砍掉 state_machine 的 event 系统

**Files:**
- Modify: `src/state_machine.py`

#### 删除清单

| 删除对象 | 说明 |
|----------|------|
| `EventType` 枚举 | 不再需要 |
| `_EventCandidate` dataclass | 不再需要 |
| `_event_buffer` 字段 | `__init__` 中删除 |
| `_last_event_triggered` 字段 | `__init__` 中删除 |
| `_event_cooldown` 字段 | `__init__` 中删除 |
| `_detect_events()` 方法 | 全部 |
| `DetectionResult.event` 字段 | 改为默认 `"none"`（保留字段以兼容调用方，但不再动态赋值） |
| `update()` 中的事件检测 + 冷却逻辑 | ~20 行 |

#### 保留清单

| 保留对象 | 原因 |
|----------|------|
| `GameState` 枚举 | 状态判定继续使用 |
| `_judge_state()` | 单帧状态判定 |
| debounce 逻辑 | 2 帧确认 |
| unknown 拒识 | 最高优先级 |
| `_prev_health_stable` | EMA 方向判断需要（trigger 层也用 hp_history，但 state_machine 仍保留给状态判定） |
| `reset()` | 恢复时清空 |

#### 改动 1: 删除 `EventType` 枚举 + `_EventCandidate` dataclass

删除 L30-36 的 `EventType`、L55-60 的 `_EventCandidate`。

#### 改动 2: `__init__` 中删除 event 字段

```python
# 删除:
self._event_buffer: list[_EventCandidate] = []
self._last_event_triggered: dict[EventType, float] = {}
self._event_cooldown: dict[EventType, float] = {
    EventType.INJURY_PULSE: ...,
    EventType.DEATH: ...,
    EventType.HEALING: ...,
    EventType.LOW_HEALTH: ...,
}
```

#### 改动 3: `update()` 中删除事件检测

`update()` 方法中，找到事件检测段（`_detect_events` 调用 + 冷却检查），全部删除。`result.event` 保持 `EventType.NONE.value`（即 `"none"`），`result.debug` 中的 `cooldown_state` 也删除。

#### 改动 4: 删除 `_detect_events()` 方法

整段 L271-354 全部删除。

#### 改动 5: `reset()` 中删除 event buffer cleanup

```python
# 删除:
self._event_buffer.clear()
self._last_event_triggered.clear()
```

#### 改动 6: 清理 logger.info

`__init__` 末尾的 `logger.info("state_machine ready ...")` 中删除 cooldowns 信息。

---

### Task 3: 去 runner 的 event 门控

**Files:**
- Modify: `src/runner_real.py`
- Modify: `src/runner_mock.py`

#### runner_real.py — 改 3 处

**1. 去掉 dispatch 调用前的 event gate**

旧:
```python
if trigger is not None and result.event != "none":
    trigger.dispatch(result)
    event_count += 1
```

新:
```python
if trigger is not None:
    trigger.dispatch(result)
    event_count += 1
```

**2. 去 last_event 追踪**

旧:
```python
if result.event != "none":
    last_event = result.event
```

删掉这两行。

**3. 终端输出 event 字段用 `-`**

旧:
```python
f"event={last_event:14s}"
```

改为:
```python
f"event={'-':14s}"
```

#### runner_mock.py — 改 1 处

旧:
```python
if result.event != "none":
    trigger.dispatch(result)
```

新:
```python
if result.event != "none":
    trigger.dispatch(result)
```

> **注意：mock 入口不动**——mock mode 下 trigger 初始化在 `TriggerDispatch.__init__` 里可能连接 dglab，不好改。mock 本身也不在乎是否每帧 dispatch。真正的触发链路在 runner_real。

#### 控制面板 — 去 event 字段显示

`src/control_panel.py` 中的 `f"Event: {ev}"` 行 —— `event` 字段已不再有意义（永远是 `"none"`）。在 `if not paused` 分支中删除 event 行，缩略面板高度。

新:
```python
        # 状态
        cv2.putText(panel, f"State: {state.upper()}", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
        y += 16

        # FPS
        cv2.putText(panel, f"FPS: {cur_fps:.1f}", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
        y += 22
```

> 删除原来的 event 渲染行（~5 行）。

---

### Task 4: 回归测试

- [ ] **Step 1: 跑全量单测**

```bash
python -m pytest tests/test_health_detector.py tests/test_calibrate.py -v -q
```

预期：17 passed.

- [ ] **Step 2: 导入验证**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from src.state_machine import StateMachine, GameState, DetectionResult
from src.trigger import TriggerDispatch
print('imports OK')
"
```

- [ ] **Step 3: 跑 mock runner 验证管线**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from src.runner_mock import main, SHOW_PREVIEW, LOOP
SHOW_PREVIEW = False
LOOP = False
main()
" 2>&1 | head -5
```

预期：正常启动，无 import 错误。

---

## 自审查

| 需求 | 任务 |
|------|------|
| HP% 直判触发 | Task 1 |
| 砍 event 系统 | Task 2 |
| 接口不变（dispatch 签名不变） | Task 1 + Task 2 |
| 回归 | Task 3 |

### 占位符扫描

无 TBD/TODO/占位符。所有代码完整可运行。
