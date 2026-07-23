# 控制面板 Always-On-Top 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 `runner_real.py` 的控制面板窗口始终置顶（always-on-top），用户不需要切出游戏就能看到 HP/状态/FPS。

**Architecture:** `control_panel.py` 新增 `set_panel_topmost()`，内部用 `ctypes` 调 `SetWindowPos(HWND_TOPMOST)`。`runner_real.py` 首帧渲染后调用一次。零新依赖，非 Windows 静默降级。

**限制:** 游戏用**独占全屏**（非无边框窗口）时 HWND_TOPMOST 被 DWM 覆盖，此功能不生效。

**Tech Stack:** ctypes（Python stdlib）、Windows User32 API

---

## 审查历史

> 2026-07-20 — PRO + LongCat 双审。砍 Task 2（mock 无控制面板）、`_set_topmost()` 提到 control_panel.py。

| 发现 | 严重度 | 修复 |
|------|--------|------|
| runner_mock.py 根本没有控制面板窗口 | **致命** (PRO) | 砍 Task 2 |
| `_set_topmost()` 在两个文件复制粘贴 | 中 (LongCat) | 提到 control_panel.py，单一定义 |
| `except: pass` 静默吞异常 | 低 (PRO) | 加 `logger.debug` |

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/control_panel.py` | **修改**（+9 行） | 新增 `set_panel_topmost()`，用 ctypes 设 HWND_TOPMOST |
| `src/runner_real.py` | **修改**（+2 行） | 首帧后调 `set_panel_topmost(PANEL_NAME)` |

### 不改的文件

`src/runner_mock.py`（没有控制面板）、其他全部文件 — 零改动。

---

### Task 1: control_panel.py — 新增 `set_panel_topmost()`

**Files:**
- Modify: `src/control_panel.py`

- [ ] **Step 1: 在文件末尾追加函数**

```python
def set_panel_topmost(window_title: str = PANEL_NAME):
    """将 OpenCV 窗口设为始终置顶（Windows only）。
    
    非 Windows 平台或窗口尚未创建时静默降级。
    独占全屏游戏模式下由 DWM 控制，此设置可能不生效。
    """
    import ctypes
    import logging
    _log = logging.getLogger(__name__)
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, window_title)
        if hwnd:
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001,  # HWND_TOPMOST, SWP_NOMOVE|SWP_NOSIZE
            )
        else:
            _log.debug("set_panel_topmost: window not found: %s", window_title)
    except Exception:
        _log.debug("set_panel_topmost: not available on this platform")
```

- [ ] **Step 2: 验证模块可导入**

```bash
cd e:\HELLDIVER_DGLAB
python -c "
from src.control_panel import draw_control_panel, PANEL_NAME, set_panel_topmost
print('OK: PANEL_NAME=%r  set_panel_topmost=%r' % (PANEL_NAME, set_panel_topmost))
"
```

预期: `OK: PANEL_NAME='Control Panel'  set_panel_topmost=<function ...>`

---

### Task 2: runner_real.py — 首帧后调用置顶

**Files:**
- Modify: `src/runner_real.py`

- [ ] **Step 1: 更新 control_panel import**

把:
```python
from src.control_panel import draw_control_panel, PANEL_NAME
```
改为:
```python
from src.control_panel import draw_control_panel, PANEL_NAME, set_panel_topmost
```

- [ ] **Step 2: 在首次渲染后调用一次（恢复运行状态时）**

找到 `if not paused:` 分支的恢复代码（约 L223-225）:
```python
                    if not paused:
                        state_machine.reset()
                        health_detector.reset_ema()
```
追加一行:
```python
                    if not paused:
                        state_machine.reset()
                        health_detector.reset_ema()
                        set_panel_topmost(PANEL_NAME)
```

这样确保每次从暂停恢复时窗口都是置顶的（窗口重建后需要重新设置）。

- [ ] **Step 3: 验证语法**

```bash
cd e:\HELLDIVER_DGLAB
python -c "
import sys; sys.path.insert(0, '.')
with open('src/runner_real.py', 'r', encoding='utf-8') as f:
    compile(f.read(), 'runner_real.py', 'exec')
print('runner_real.py syntax OK')
"
```

---

## 自审查

| 需求 | 任务 |
|------|------|
| 控制面板 always-on-top | Task 1 (`set_panel_topmost`) + Task 2 Step 2 |
| 零新依赖 | ctypes stdlib |
| 非 Windows 降级 | try/except + logger.debug |
| 窗口未找到时降级 | `if hwnd` else logger.debug |
| 独占全屏限制文档化 | Architecture 段已标注 |
| 无代码重复 | 函数定义在 control_panel.py，两处 import 使用 |

### 占位符扫描

无 TBD/TODO。

### 类型一致性

`set_panel_topmost(window_title: str = PANEL_NAME) -> None` — 有默认值，可直接无参调用。
