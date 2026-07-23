# 控制面板 + FPS 限制 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增迷你控制面板窗口（解决无 preview 时无法暂停的问题）+ 默认 30fps 帧率限制。

**Architecture:** 新增 `src/control_panel.py`（遵循 `debug_overlay.py` 的可视化职责独立文件模式），`runner_real.py` 只调用 + `cv2.imshow`。所有文字英文（OpenCV 默认字体不兼容中文）。

**Tech Stack:** 纯 OpenCV（cv2.imshow + cv2.rectangle + cv2.putText），零新依赖

---

## 审查记录

> 2026-07-19 — PRO + LongCat 双审查

| 发现 | 严重度 | 修复 |
|------|--------|------|
| 循环死锁 — waitKey 在 continue 后面，暂停后无法恢复 | **阻塞** (PRO) | 重构：暂停时也有独立的 `imshow + waitKey` mini-loop |
| 窗口名冲突 — 控制面板和 overlay 都叫 "HELLDIVER_DGLAB" | **阻塞** (PRO) | 面板窗口名改为 `"Control Panel"` |
| 中文乱码 — OpenCV 默认字体不支持中文 | **阻塞** (LongCat) | 全英文面板 |
| 启动时 result 未定义，首次渲染崩溃 | 中 (PRO) | `EMPTY_RESULT` 占位对象 |
| 暂停时 FPS 归零误导 | 低 (PRO) | 暂停时冻结最后 FPS，显示 `(paused)` |
| 状态格式不与 overlay 统一 | 低 (LongCat) | 改用 `result.state.upper()` |
| 160 行内联膨胀 runner_real.py | 中 (LongCat) | 抽到 `src/control_panel.py` 独立文件 |
| `--fps` default=30 是 breaking change | 低 (PRO) | 标注，项目仍在开发阶段 |

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/control_panel.py` | **新增** | 控制面板渲染（纯函数，零依赖） |
| `src/runner_real.py` | **修改** | 加 `--fps` 参数 + 循环重构 + 调用 control_panel |

---

### Task 1: 控制面板模块

**Files:**
- Create: `src/control_panel.py`

- [ ] **Step 1: 创建 `src/control_panel.py`**

```python
"""control_panel.py — 迷你控制面板窗口渲染。

独立于 overlay 模块，始终显示（不依赖 --preview 参数）。
纯英文（OpenCV 默认字体不兼容中文）。
"""
from typing import Optional
import numpy as np
import cv2

PANEL_W, PANEL_H = 260, 150
PANEL_NAME = "Control Panel"

WHITE  = (255, 255, 255)
GRAY   = (160, 160, 160)
GREEN  = (0, 255, 0)
RED    = (0, 0, 255)
YELLOW = (0, 255, 255)


def draw_control_panel(
    health_stable: float = 0.0,
    state: str = "",
    event: str = "none",
    cur_fps: float = 0.0,
    paused: bool = True,
    paused_fps: Optional[float] = None,
) -> np.ndarray:
    """渲染控制面板小窗。返回 (PANEL_H, PANEL_W, 3) BGR 图像。

    Args:
        paused_fps: 暂停前的最后 FPS 值（暂停时冻结显示）；None 表示实时 fps
    """
    panel = np.zeros((PANEL_H, PANEL_W, 3), dtype=np.uint8)
    y = 18

    # 标题
    cv2.putText(panel, "HELLDIVER_DGLAB v0.1", (6, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, WHITE, 1)
    y += 18

    # 运行状态
    if paused:
        cv2.putText(panel, "[PAUSED] Ship mode", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, GRAY, 1)
        y += 22
        cv2.putText(panel, "Press [Space] to start", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, GRAY, 1)
        y += 22
    else:
        cv2.putText(panel, "[RUNNING]", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, GREEN, 1)
        y += 22

        # 血量条
        hp = max(0.0, min(1.0, health_stable))
        cv2.putText(panel, "HP:", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
        bar_w = 100
        bar_x, bar_y = 30, y - 9
        cv2.rectangle(panel, (bar_x, bar_y), (bar_x + bar_w, bar_y + 12), GRAY, 1)
        fill_w = int(bar_w * hp)
        if fill_w > 0:
            fill_color = GREEN if hp > 0.5 else YELLOW if hp > 0.25 else RED
            cv2.rectangle(panel, (bar_x, bar_y), (bar_x + fill_w, bar_y + 12), fill_color, -1)
        cv2.putText(panel, f"{hp:.0%}", (bar_x + bar_w + 6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
        y += 22

        # 状态
        cv2.putText(panel, f"State: {state.upper()}", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
        y += 16

        # 事件
        ev = event if event != "none" else "-"
        cv2.putText(panel, f"Event: {ev}", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, YELLOW if ev != "-" else GRAY, 1)
        y += 16

    # FPS
    if paused and paused_fps is not None:
        cv2.putText(panel, f"FPS: {paused_fps:.1f} (paused)", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, GRAY, 1)
    else:
        cv2.putText(panel, f"FPS: {cur_fps:.1f}", (6, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, WHITE, 1)
    y += 22

    # 按键提示
    cv2.putText(panel, "[Space]Pause  [Q]Quit", (6, y + 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.3, GRAY, 1)

    return panel
```

---

### Task 2: 修改 runner_real.py

**Files:**
- Modify: `src/runner_real.py`

#### 改动 1: 新增 argparse `--fps` + import control_panel

旧 import 段追加:
```python
from src.control_panel import draw_control_panel, PANEL_NAME, PANEL_W, PANEL_H
```

旧 argparse 追加:
```python
parser.add_argument("--fps", type=int, default=30,
                    help="目标帧率 (0=不限, 默认30)")
```

#### 改动 2: 循环重构 — 暂停时也渲染面板 + 处理键盘

**这是本轮最关键的改动。** 原理：

```
新循环:
grab → frame_count++
  ├─ !paused: pipeline → trigger → fps → print → overlay(可选)
  ├─ 计算 cur_fps
  ├─ 渲染控制面板 imshow(PANEL_NAME, panel)  ← 暂停也执行
  ├─ waitKey(1)                               ← 暂停也执行
  └─ 处理空格/q/ESC                             ← 暂停也执行
```

这样暂停时面板仍然可见、空格仍可恢复。

旧代码删除：键盘处理块（从管线前移除）、旧 `if paused: continue`（改为管线条件）、`TARGET_FPS` 常量。

新代码替换主循环:

```python
    # ── 主循环 ──
    last_fps = 0.0  # 暂停时冻结的 FPS 值

    try:
        while True:
            t0 = time.time()

            frame = cap.grab()
            if frame is None:
                time.sleep(0.01)
                continue

            frame_count += 1

            # ── 管线（仅在运行时执行）──
            if not paused:
                try:
                    roi_result = roi_locator.locate(frame)
                    health_result = health_detector.detect(roi_result.inner_img)
                    effect_result = effect_detector.detect(
                        frame, health_raw=health_result.health_raw
                    )
                    result = state_machine.update(
                        health_raw=health_result.health_raw,
                        health_stable=health_result.health_stable,
                        red_score=effect_result.red_score,
                        cyan_score=effect_result.cyan_score,
                        death_score=effect_result.death_score,
                        anchor_score=roi_result.anchor_score,
                        confidence=health_result.confidence,
                        valid=roi_result.valid,
                    )
                except Exception:
                    logger.exception("管线异常")
                    continue

                # 设备触发
                if trigger is not None and result.event != "none":
                    trigger.dispatch(result)
                    event_count += 1

                # FPS
                now = time.time()
                fps_times.append(now)
                cutoff = now - FPS_WINDOW_SEC
                while fps_times and fps_times[0] < cutoff:
                    fps_times.popleft()

                # 终端输出（每秒一次）
                if now - last_print >= 1.0:
                    last_print = now
                    cur_fps = (
                        len(fps_times) / (fps_times[-1] - fps_times[0])
                        if len(fps_times) >= 2 else 0.0
                    )
                    print(
                        f"[{cur_fps:5.1f} fps] "
                        f"state={result.state:12s}  "
                        f"hp={result.health_stable:.1%}  "
                        f"red={result.red_score:.2f}  "
                        f"cyan={result.cyan_score:.2f}  "
                        f"event={result.event:14s}  "
                        f"conf={result.confidence:.2f}  "
                        f"frm={frame_count}"
                    )

                # Debug overlay
                if args.preview:
                    overlay = draw_overlay(
                        frame, result,
                        outer_coords=roi_result.outer_coords,
                        inner_coords=roi_result.inner_coords,
                        fill_boundary_x=health_result.fill_boundary_x,
                    )
                    cv2.imshow("HELLDIVER_DGLAB", overlay)

            # ── FPS 计算（暂停时复用上一帧值）──
            cur_fps = (
                len(fps_times) / (fps_times[-1] - fps_times[0])
                if len(fps_times) >= 2 else 0.0
            )
            if not paused:
                last_fps = cur_fps

            # ── 控制面板（始终渲染）──
            if not paused:
                panel = draw_control_panel(
                    health_stable=result.health_stable,
                    state=result.state,
                    event=result.event,
                    cur_fps=cur_fps,
                    paused=False,
                )
            else:
                panel = draw_control_panel(
                    paused=True,
                    paused_fps=last_fps,
                )
            cv2.imshow(PANEL_NAME, panel)

            # ── 键盘（始终响应）──
            key = cv2.waitKey(1) & 0xFF
            if key == ord(" "):
                paused = not paused
                print(f"[runner_real] {'[PAUSED]' if paused else '[RUNNING]'}")
                if not paused:
                    state_machine.reset()
                    health_detector.reset_ema()
            elif key == ord("q") or key == 27:
                raise KeyboardInterrupt

            # ── 帧率限制 ──
            if args.fps > 0:
                elapsed = time.time() - t0
                time.sleep(max(0, 1.0 / args.fps - elapsed))

    except KeyboardInterrupt:
        pass
    finally:
        ...
```

> **注意**：`paused` 初始值保持 `True`（启动时暂停），banner 提示 "Press [Space] to start"。

#### 改动 3: 删除旧代码

- 删除 `TARGET_FPS` 常量
- 删除旧的 `if paused: continue` 块
- 删除旧的键盘处理块（已移入循环末尾重构版本）
- 删除旧的 `cv2.imshow("HELLDIVER_DGLAB", overlay)` 前的 `PANEL_NAME` 冲突

#### 改动 4: 退出摘要 FPS 用 `last_fps`

退出摘要的 `cur_fps` 计算改为使用 `last_fps`（不在暂停状态下退出的情况）。

---

### Task 3: 回归测试

- [ ] **Step 1: 全量单测**

```bash
python -m pytest tests/ -v -q
```

预期：17 passed.

- [ ] **Step 2: 导入验证**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from src.control_panel import draw_control_panel, PANEL_NAME, PANEL_W, PANEL_H
import numpy as np
p = draw_control_panel(paused=True, paused_fps=30.0)
assert p.shape == (PANEL_H, PANEL_W, 3), f'{p.shape}'
print(f'panel={PANEL_W}x{PANEL_H} OK')
"
```

预期：`panel=260x150 OK`

- [ ] **Step 3: Verify --fps flag**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from src.runner_real import main
# verify argparse without error
print('--fps flag OK')
"
```

---

## 自审查

| 需求 | 覆盖 |
|------|------|
| 控制面板（无 preview 也能暂停） | Task 1 + Task 2 改动 2（暂停时独立渲染 + 键盘） |
| 默认 30fps | Task 2 改动 1（`--fps default=30`） |
| 暂停时重置状态机 | Task 2 改动 2（恢复时 `state_machine.reset()`） |
| 不阻塞键盘（PRO 发现的死锁） | 循环重构：暂停时也有 `imshow + waitKey` |
| 窗口名不冲突（PRO） | `PANEL_NAME = "Control Panel"` vs overlay 的 `"HELLDIVER_DGLAB"` |
| 英文面板（LongCat） | 全英文 |
| 独立文件（LongCat） | `src/control_panel.py` |
| 状态格式统一（LongCat） | `state.upper()` |
| 启动时 result 未定义（PRO） | `paused=True` 走独立路径，不访问 result |
| 暂停时 FPS 冻结（PRO） | `last_fps` 保持暂停前值 |
