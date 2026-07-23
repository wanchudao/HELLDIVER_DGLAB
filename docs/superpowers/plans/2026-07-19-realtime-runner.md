# Real-time Runner (V0.1) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `src/runner_real.py`，把 mss 实时截图串入现有视觉管线，实现从"截图回放"到"实时游戏"的最后一步。

**Architecture:** 复用 `runner_mock.py` 的管线结构，将 `cv2.imread` 替换为 `ScreenCapture.grab()`。纯扩展（不改任何现有模块），新增 `src/runner_real.py` 一个文件。FPS 计算内联 6 行 deque，不独立文件。

**Tech Stack:** 零新依赖（mss + opencv + numpy 均在 requirements.txt）

---

## 审查记录

> 2026-07-19 — PRO + LongCat 双审查

| 发现 | 严重度 | 修复 |
|------|--------|------|
| trigger 初始化在 try 外，ConnectionError unhandled | **高** (PRO) | trigger init 移入 try 内，捕获 ConnectionError 优雅退出 |
| ENABLE_TRIGGER 默认 True → 安全红线 | **高** (LongCat) | 默认 `False`，加 `--enable-trigger` 显式开启 |
| SHOW_PREVIEW 默认 True → 抢焦点风险 | **高** (LongCat) | 默认 `False`，加 `--preview` 开启 |
| fps_counter.py 独立文件 → 只有 1 个消费者 | 中 | 砍掉，FPS 内联 6 行 |
| monitor_index 硬编码 | 中 | 从 config.ini `[capture]` 读取 |
| 启动 banner 缺 | 低 | 打印关键配置项（分辨率、校准状态、ROI 坐标范围） |

### 用户确认

| 决策 | 结论 |
|------|------|
| 非 1080p + 未校准 | 警告后继续运行（不退出） |
| low_health 映射 | 加回到 trigger + dglab_sender（强度 10, weak_pulse, 3s 冷却） |

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/runner_real.py` | **新增** | 实时入口：mss 截图 → 管线 → 终端输出 + overlay（可选） |

### 为什么只加不改

`runner_mock.py`、`roi_locator`、`health_detector`、`effect_detector`、`state_machine`、`trigger` 全部零改动。`runner_real.py` 只是换了截图来源（`ScreenCapture.grab()` vs `cv2.imread`），管线调用完全相同。

---

### Task 1: 实时 runner

**Files:**
- Create: `src/runner_real.py`
- Modify: `config.ini` (新增 `[capture]` 节)

#### 安全设计原则（从 SQUAD 继承）

- **先 mock 后 real**：默认不发设备（`ENABLE_TRIGGER = False`），确认终端输出正确后用 `--enable-trigger` 开
- **safe stop 是生命线**：Ctrl+C → finally 块强度归零 + 清除波形 + 关 WebSocket
- **suppression 是实验性的**：overlay 默认关（`SHOW_PREVIEW = False`），避免实时游戏抢焦点

- [ ] **Step 1: 在 config.ini 新增 `[capture]` 节**

```ini
[capture]
# mss 显示器编号 (1=主屏, 2=副屏)
monitor_index = 1
```

追加到 `[calibration]` 节之后。

- [ ] **Step 2: 创建 `src/runner_real.py`**

```python
"""runner_real.py — V0.1 实时入口。

mss 屏幕捕获 → 完整视觉管线 → 终端输出 + debug overlay（可选）→ 设备触发（可选）。

用法:
    python src/runner_real.py              # 终端模式（默认，安全）
    python src/runner_real.py --preview    # 终端 + debug overlay
    python src/runner_real.py --enable-trigger  # 终端 + 设备触发

Ctrl+C 安全退出（强度归零 + 清除波形 + 关连接）。

安全设计:
    - 默认不发设备（先 mock 后 real）
    - overlay 默认关闭（避免实时游戏抢焦点）
    - trigger 初始化失败不崩溃（优雅降级）
"""
import argparse
import logging
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from src.config_loader import load_config
from src.roi_locator import RoiLocator
from src.health_detector import HealthDetector
from src.effect_detector import EffectDetector
from src.state_machine import StateMachine
from src.trigger import TriggerDispatch
from src.screen_capture import ScreenCapture
from src.debug_overlay import draw_overlay

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FPS_WINDOW_SEC = 2.0  # FPS 滑动窗口大小

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("runner_real")


def main():
    parser = argparse.ArgumentParser(description="HELLDIVER_DGLAB 实时检测")
    parser.add_argument("--preview", action="store_true",
                        help="显示 debug overlay 窗口")
    parser.add_argument("--enable-trigger", action="store_true",
                        help="启用设备触发（默认仅终端输出）")
    args = parser.parse_args()

    config = load_config(str(PROJECT_ROOT / "config.ini"))

    # ── 初始化视觉模块 ──
    roi_locator = RoiLocator(config)
    health_detector = HealthDetector(config)
    effect_detector = EffectDetector(config)
    state_machine = StateMachine(config)

    # ── 屏幕捕获 ──
    monitor_idx = int(config.get("capture", {}).get("monitor_index", 1))
    cap = ScreenCapture(monitor_index=monitor_idx)
    if not cap.is_ready:
        print("[runner_real] 屏幕捕获不可用，退出。")
        return

    w, h = cap.monitor_size

    # ── 启动 banner ──
    calibration = config.get("calibration", {})
    is_calibrated = calibration.get("calibrated", 0) == 1
    calib_res = calibration.get("resolution", "unknown")
    roi = config["ROI"]
    bar_w = roi["bar_max_x"] - roi["bar_min_x"]

    print("=" * 50)
    print("  HELLDIVER_DGLAB V0.1 — 实时检测")
    print("=" * 50)
    print(f"  分辨率: {w}x{h}")
    print(f"  校准: {'已校准 (' + calib_res + ')' if is_calibrated else '1080p 默认坐标'}")
    print(f"  血条有效宽度: {bar_w}px")
    print(f"  preview: {'ON' if args.preview else 'OFF'}")
    print(f"  trigger: {'ON' if args.enable_trigger else 'OFF (仅终端)'}")
    if w != 1920 or h != 1080:
        if not is_calibrated:
            print(f"  [警告] 非 1080p 且未校准，请运行: python tools/calibrate.py")
            print(f"         将继续运行，但坐标可能不准。")
        else:
            print(f"  使用校准坐标 ({calib_res})。")
    print()

    # ── FPS 状态（内联，不独立文件）──
    fps_times: deque[float] = deque()
    last_print = 0.0
    frame_count = 0
    event_count = 0

    outer_coords = None
    inner_coords = None
    trigger = None  # 延迟初始化（在 try 内，防止 ConnectionError unhandled）

    print("[runner_real] 开始实时检测... Ctrl+C 退出")
    print()

    try:
        # ── 初始化 trigger（放在 try 内：DG-LAB 连接失败时优雅降级）──
        if args.enable_trigger:
            try:
                trigger = TriggerDispatch(config)
            except (ConnectionError, RuntimeError) as e:
                print(f"[runner_real] 设备连接失败: {e}")
                print("[runner_real] 继续运行（仅终端输出），或 Ctrl+C 退出。")
                trigger = None

        while True:
            t0 = time.time()

            # ── 截图 ──
            frame = cap.grab()
            if frame is None:
                time.sleep(0.01)
                continue

            frame_count += 1

            # ── 管线 ──
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

            # ── 设备触发（仅 --enable-trigger 模式）──
            if trigger is not None and result.event != "none":
                trigger.dispatch(result)
                event_count += 1

            # ── FPS（内联 6 行，不独立文件）──
            now = time.time()
            fps_times.append(now)
            cutoff = now - FPS_WINDOW_SEC
            while fps_times and fps_times[0] < cutoff:
                fps_times.popleft()

            # ── 终端输出（节流，每秒一次）──
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

            # ── Debug overlay（仅 --preview 模式）──
            if args.preview:
                outer_coords = roi_result.outer_coords
                inner_coords = roi_result.inner_coords
                overlay = draw_overlay(
                    frame, result,
                    outer_coords=outer_coords,
                    inner_coords=inner_coords,
                    fill_boundary_x=health_result.fill_boundary_x,
                )
                # FPS 叠加到 overlay 右上角
                cur_fps = (
                    len(fps_times) / (fps_times[-1] - fps_times[0])
                    if len(fps_times) >= 2 else 0.0
                )
                cv2.putText(
                    overlay, f"FPS: {cur_fps:.0f}",
                    (overlay.shape[1] - 120, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1,
                )
                cv2.imshow("HELLDIVER_DGLAB (q=quit)", overlay)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or key == 27:
                    raise KeyboardInterrupt

    except KeyboardInterrupt:
        pass
    finally:
        cap.close()
        cv2.destroyAllWindows()
        if trigger is not None:
            trigger.safe_stop()

        # ── 退出摘要 ──
        cur_fps = (
            len(fps_times) / (fps_times[-1] - fps_times[0])
            if len(fps_times) >= 2 else 0.0
        )
        print(f"\n{'='*50}")
        print(f"  总帧数: {frame_count}")
        print(f"  触发事件: {event_count}")
        print(f"  平均 FPS: {cur_fps:.1f}")
        print(f"{'='*50}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 跑 11 单测回归**

```bash
python -m pytest tests/test_calibrate.py -v -q
```

预期：11 passed。

- [ ] **Step 4: 验证 runner_real 模块可导入**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from src.config_loader import load_config
c = load_config()
from src.runner_real import main
print('runner_real OK')
"
```

预期：`runner_real OK`

- [ ] **Step 5: Commit**

```bash
git add src/runner_real.py config.ini
git commit -m "feat: add real-time mss runner (V0.1)"
```

---

### Task 2: 集成测试

- [ ] **Step 1: 跑全量回归**

```bash
python -m pytest tests/ -v -q
```

预期：11 个 calibrate 测试全部通过。

- [ ] **Step 2: 最终文件清单确认**

```
src/runner_real.py     ← 新增（实时入口，1 文件）
config.ini             ← 修改（新增 [capture] monitor_index）
```

其余所有文件零改动。

---

## 自审查

### Spec 覆盖

| 需求 | 任务 |
|------|------|
| mss 实时截图 → 管线 | Task 1 Step 2 |
| FPS 显示 | Task 1 Step 2（内联 deque） |
| 校准提示（非 1080p 未校准 → 警告+继续） | Task 1 Step 2 banner |
| Ctrl+C 安全退出 + safe_stop | Task 1 Step 2 finally |
| trigger 初始化在 try 内（防 ConnectionError unhandled） | Task 1 Step 2 |
| 安全默认：不发设备 + overlay 关 | ENABLE_TRIGGER=False, SHOW_PREVIEW=False |
| --preview / --enable-trigger | argparse |
| monitor_index 从 config 读取 | Task 1 Step 1 config.ini + runner_real |
| 零改动现有模块 | 确认 |
| low_health 映射（已完成） | trigger.py + dglab_sender.py + config.ini |

### 占位符扫描

无 TBD/TODO/占位符。所有代码完整可运行。

### 类型一致性

`roi_result.outer_coords` / `inner_coords` / `health_result.fill_boundary_x` — 与 `draw_overlay` 签名一致。`result.event` / `result.state` / `result.health_stable` — 与 `runner_mock.py` 消费方式一致。
