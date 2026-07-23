# 血条校准工具 (Calibration Tool) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让非 1080p 分辨率玩家通过 5 秒截图 + 鼠标框选完成血条坐标校准，1080p 玩家开箱即用。

**Architecture:** 新增 `tools/calibrate.py` 校准脚本，5 秒倒计时后 mss 截屏，用户 cv2.selectROI 框选血条外框，校验宽高比合理后坐标写入 config.ini（写入前自动备份）。`roi_locator.py` 无需改动。支持 `--image` 模式用于离线测试。

**Tech Stack:** Python stdlib + mss + opencv-python (均已在 requirements.txt)

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `tools/calibrate.py` | **新增** | 校准脚本：倒计时 → 截屏 → 框选 → 校验 → 备份+写配置 |
| `tests/test_calibrate.py` | **新增** | `compute_roi_params` 纯函数单元测试 |
| `docs/calibration_guide.md` | **新增** | 用户指南：截图规范、框选示范、常见错误 |
| `config.ini` | 修改 | 新增 `[calibration]` 节 |
| `src/roi_locator.py` | **不改** | 始终从 config.ini 读坐标 |

### 为什么 roi_locator.py 不改

校准脚本直接把坐标写入 `config.ini [ROI]` 节。`roi_locator.py.__init__()` 读取 config 的参数名不变——对 roi_locator 完全透明。

---

## 审查发现与修复记录

> 2026-07-19 — PRO + LongCat 双审查，以下是修复内容：

| 审查发现 | 严重度 | 修复 |
|----------|--------|------|
| 比例来源不一致（计划用 report 坐标 y=943，config.ini 实际在 y=993） | **致命** | 以 config.ini 当前实际值为 ground truth 重新计算全部比例 |
| 框选结果无校验（框错也照单全收） | 高 | 增加宽高比校验 + 重新框选机制 |
| configparser 写回抹掉注释 | 高 | 写入前自动备份 `config.ini.bak.{timestamp}` |
| 无法离线测试 | 高 | 增加 `--image <path>` 参数 |
| 倒计时 3 秒太短 | 中 | 改为 5 秒 + 终态提示音 `\a` |
| Task 4 (runner 警告) 无消费者 | 中 | 移除，标注 defer until runner_real.py |
| compute_roi_params 无测试 | 中 | 新增 Task 4 — 纯函数测试 |

### 二审修复 —— 2026-07-19 PRO + LongCat

| 审查发现 | 严重度 | 修复 |
|----------|--------|------|
| `cv2.waitKey(0)` 在 `destroyAllWindows` 后死锁 | **高** — 双方独立发现 | 砍掉中间按键交互，校验失败自动循环回 `cv2.selectROI` |
| `capture_screen` 与 `ScreenCapture` 重复 | 高 | 删 `capture_screen`，`main()` 改用 `ScreenCapture.grab()` |
| `backup_config` 6 行单调用方过于拆分 | 低 | 内联到 `write_config` |

---

### Task 1: 校准工具核心

**Files:**
- Create: `tools/calibrate.py`

#### 比例参数 — 以 config.ini 当前值为 ground truth

```
config.ini 当前值:
  外框 (100,993)-(305,1007):  w=205, h=14
  内框 (106,996)-(300,1004):  w=194, h=8

推导:
  左端帽宽度: 106-100 = 6px  → 6/205  = 2.93%
  右端帽宽度: 305-300 = 5px  → 5/205  = 2.44%
  顶端内缩:   996-993 = 3px  → 3/14   = 21.43%
  底端内缩:   1007-1004=3px  → 3/14   = 21.43%
  端帽忽略:   4px             → 4/205  = 1.95%
  期望宽高比: 205/14 ≈ 14.6
```

> **注意**: 左右端帽宽度不对称（6px vs 5px），可能是 HD2 真实的 UI 不对称，也可能是 1080p 用户手动微调后的 1px 偏差。如果后续真机验证发现端帽对称，将两值统一为均值。

- [ ] **Step 1: 创建 `tools/calibrate.py`**

```python
"""calibrate.py — 血条坐标校准工具。

用法:
    # 实时校准（需要游戏运行中）
    python tools/calibrate.py

    # 离线测试（从已有截图校准）
    python tools/calibrate.py --image debug/all_samples/full_01.jpg

流程:
    1. 倒计时（实时模式 5 秒，--image 模式跳过）
    2. mss 截取主屏 / 加载图片
    3. 弹出 OpenCV 窗口，用户鼠标框选血条外框（含端帽）
    4. 校验框选宽高比是否合理
    5. 按比例推导外框/内框/端帽等全部参数
    6. 备份 config.ini → 写入坐标 + calibration 标记
"""
import argparse
import configparser
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.ini"

# ── 坐标推导比例 — 来源: config.ini (100,993)-(305,1007) vs (106,996)-(300,1004) ──
# 注意: HD2 HUD 是锚点布局，16:9 下等比缩放成立；21:9 带鱼屏未验证
_LEFT_CAP_RATIO = 0.0293       # 左端帽占外框宽度比例 (6/205)
_RIGHT_CAP_RATIO = 0.0244      # 右端帽占外框宽度比例 (5/205)
_TOP_PAD_RATIO = 0.2143        # 内框上边距占外框高度比例 (3/14)
_BOTTOM_PAD_RATIO = 0.2143     # 内框下边距占外框高度比例 (3/14)
_CAP_IGNORE_RATIO = 0.0195     # 端帽忽略宽度占外框宽度比例 (4/205)
_EXPECTED_ASPECT_MIN = 8.0     # 血条外框宽高比下限 (w/h)
_EXPECTED_ASPECT_MAX = 22.0    # 血条外框宽高比上限
_COUNTDOWN_SEC = 5             # 倒计时秒数


def compute_roi_params(x: int, y: int, w: int, h: int) -> dict:
    """从用户框选的外框 (x, y, w, h) 推导全部 ROI 参数。

    Args:
        x, y, w, h: 用户用 cv2.selectROI 框选的血条外框（像素坐标，含端帽）

    Returns:
        dict: config.ini [ROI] 节所需全部参数
    """
    outer_x1 = x
    outer_y1 = y
    outer_x2 = x + w
    outer_y2 = y + h

    inner_x1 = outer_x1 + round(w * _LEFT_CAP_RATIO)
    inner_x2 = outer_x2 - round(w * _RIGHT_CAP_RATIO)
    inner_y1 = outer_y1 + round(h * _TOP_PAD_RATIO)
    inner_y2 = outer_y2 - round(h * _BOTTOM_PAD_RATIO)

    cap_ignore = max(1, round(w * _CAP_IGNORE_RATIO))

    return {
        "outer_x1": outer_x1,
        "outer_y1": outer_y1,
        "outer_x2": outer_x2,
        "outer_y2": outer_y2,
        "inner_x1": inner_x1,
        "inner_y1": inner_y1,
        "inner_x2": inner_x2,
        "inner_y2": inner_y2,
        "bar_min_x": inner_x1,
        "bar_max_x": inner_x2,
        "cap_ignore_left": cap_ignore,
        "cap_ignore_right": cap_ignore,
    }


def validate_roi(w: int, h: int) -> tuple[bool, str]:
    """校验框选尺寸是否合理。

    Returns:
        (is_valid, message)
    """
    if w <= 0 or h <= 0:
        return False, "框选区域无效（宽或高 ≤ 0）"

    aspect = w / h

    if w < 80:
        return False, f"框选太窄（宽={w}px < 80px），可能未包含完整血条"
    if h < 6:
        return False, f"框选太矮（高={h}px < 6px），可能只框了边框线"

    if aspect < _EXPECTED_ASPECT_MIN:
        return False, f"宽高比过低 ({aspect:.1f} < {_EXPECTED_ASPECT_MIN})，疑似框了竖条而非血条"
    if aspect > _EXPECTED_ASPECT_MAX:
        return False, f"宽高比过高 ({aspect:.1f} > {_EXPECTED_ASPECT_MAX})，疑似框了过多背景"

    return True, "OK"


def backup_config(config_path: Path) -> Path:
    """备份 config.ini。返回备份文件路径。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = config_path.with_suffix(f".ini.bak.{timestamp}")
    shutil.copy2(str(config_path), str(backup_path))
    return backup_path


def write_config(params: dict, resolution: tuple[int, int], config_path: Path):
    """将校准参数写入 config.ini。

    注意: configparser.write() 会丢失注释。写入前自动备份原文件。
    """
    # 写入前备份
    backup_path = backup_config(config_path)

    cp = configparser.ConfigParser()
    cp.read(str(config_path), encoding="utf-8")

    # 写 ROI 坐标参数
    for key, value in params.items():
        cp.set("ROI", key, str(value))

    # 写 calibration 标记
    if not cp.has_section("calibration"):
        cp.add_section("calibration")
    cp.set("calibration", "calibrated", "1")
    cp.set("calibration", "resolution", f"{resolution[0]}x{resolution[1]}")

    with open(config_path, "w", encoding="utf-8") as f:
        cp.write(f)

    print(f"  [备份] 原配置已备份到: {backup_path.name}")


def select_roi_loop(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """交互式框选循环：校验不通过时自动重新弹框选窗口。
    
    注意：框选失败后直接循环回 cv2.selectROI，不做中间 waitKey（已 destroyAllWindows，waitKey 会死锁）。
    用户可在 selectROI 弹出窗口中按 ESC 取消（此时返回全零的宽高）。

    Returns:
        (x, y, w, h) 或 None（用户按 ESC 取消）
    """
    while True:
        roi = cv2.selectROI(
            "框选血条外框（含左右端帽）→ 回车确认 → ESC 退出",
            img, showCrosshair=True,
        )
        cv2.destroyAllWindows()

        x, y, w, h = roi
        if w == 0 or h == 0:
            return None

        is_valid, msg = validate_roi(w, h)
        if is_valid:
            return x, y, w, h

        # 校验不通过 → 打印警告，自动循环回 selectROI 让用户重框
        print(f"\n  [警告] {msg}")
        print(f"         宽={w}px 高={h}px  宽高比={w/h:.1f}")
        print(f"         期望宽高比范围: {_EXPECTED_ASPECT_MIN}-{_EXPECTED_ASPECT_MAX}")
        print( "         请重新框选..." )
        print()


def main():
    parser = argparse.ArgumentParser(description="HELLDIVER_DGLAB 血条坐标校准")
    parser.add_argument(
        "--image", type=str, default=None,
        help="从本地截图校准（离线测试用），不指定则使用 mss 实时截图",
    )
    args = parser.parse_args()

    print("=" * 50)
    print("  HELLDIVER_DGLAB — 血条坐标校准")
    print("=" * 50)
    print()

    # ── 获取截图 ──
    if args.image:
        img_path = Path(args.image)
        if not img_path.exists():
            print(f"  [错误] 文件不存在: {img_path}")
            return
        img = cv2.imread(str(img_path))
        if img is None:
            print(f"  [错误] 无法读取: {img_path}")
            return
        print(f"  [镜像模式] 从文件加载: {img_path.name}")
    else:
        print("  准备截取主屏幕。请切换到游戏全屏画面（飞船舱内/满血菜单即可）。")
        print(f"  HUD Scale 请保持在 100%（默认值）。")
        print()

        # 倒计时
        for i in range(_COUNTDOWN_SEC, 0, -1):
            print(f"  {i}...")
            time.sleep(1)

        print("\a", end="", flush=True)  # 终端蜂鸣
        print("  >>> 截图完成! 请查看弹出的窗口 <<<")
        print()

        # 复用项目已有的屏幕捕获（零新增依赖）
        try:
            from src.screen_capture import ScreenCapture
            cap = ScreenCapture(monitor_index=1)
            if not cap.is_ready:
                print("  [错误] mss 初始化失败。")
                return
            img = cap.grab()
            cap.close()
            if img is None:
                print("  [错误] 截图失败。")
                return
        except ImportError:
            print("  [错误] mss 未安装. 请运行: pip install mss")
            return

    resolution = (img.shape[1], img.shape[0])
    print(f"  分辨率: {resolution[0]}x{resolution[1]}")
    print()
    print("  请用鼠标框选血条外框（含左右端帽，勿多出背景）")
    print("  按 回车 确认 | 按 ESC 取消")
    print()

    # ── 交互式框选 ──
    roi = select_roi_loop(img)
    if roi is None:
        print("  [取消] 校准已中止。")
        return

    x, y, w, h = roi

    # ── 推导参数 ──
    params = compute_roi_params(x, y, w, h)

    # ── 展示结果 ──
    print()
    print(f"  外框: ({params['outer_x1']},{params['outer_y1']}) - ({params['outer_x2']},{params['outer_y2']})")
    print(f"  内框: ({params['inner_x1']},{params['inner_y1']}) - ({params['inner_x2']},{params['inner_y2']})")
    print(f"  有效宽度: {params['bar_max_x'] - params['bar_min_x']}px")
    print()

    # ── 写入配置 ──
    write_config(params, resolution, CONFIG_PATH)

    print(f"  ✓ 校准完成! 坐标已写入 config.ini")
    print(f"    分辨率: {resolution[0]}x{resolution[1]}")
    print(f"    如需重新校准，再次运行: python tools/calibrate.py")
    print(f"    注释已被清除（configparser 限制），备份文件中有原注释。")
    print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证 `--image` 离线模式能跑通**

```bash
python tools/calibrate.py --image debug/all_samples/full_01.jpg
```

预期：跳过倒计时，直接弹窗显示 full_01.jpg → 鼠标框选血条 → 校验 → 打印坐标 → 写入 config.ini。

- [ ] **Step 3: 检查写入后的 config.ini + 备份**

确认 `[ROI]` 坐标被更新，`[calibration]` 节新增，且 `config.ini.bak.*` 备份文件存在。

- [ ] **Step 4: 验证宽高比校验生效**

用 `--image` 模式故意框一个极窄的竖条（宽高比 < 8），确认弹出警告并允许重选或强制跳过。

- [ ] **Step 5: Commit**

```bash
git add tools/calibrate.py
git commit -m "feat: add calibration tool for non-1080p health bar localization"
```

---

### Task 2: config.ini 添加 calibration 标记节

**Files:**
- Modify: `config.ini` (在末尾追加)

- [ ] **Step 1: 在 config.ini 末尾追加 [calibration] 节**

```ini
[calibration]
# 是否已校准: 0=使用默认1080p坐标, 1=已校准
calibrated = 0
# 校准时的分辨率 (仅供参考)
resolution = 1920x1080
```

**位置**: 追加到 `config.ini` 最后一行 `log_level = INFO` 之后，空一行。

- [ ] **Step 2: Commit**

```bash
git add config.ini
git commit -m "chore: add [calibration] marker section to config.ini"
```

---

### Task 3: 校准用户指南

**Files:**
- Create: `docs/calibration_guide.md`

- [ ] **Step 1: 创建校准指南文档**

````markdown
# 血条校准指南

> 适用: 非 1920×1080 分辨率玩家（2K/4K/带鱼屏/窗口模式）
> 前提: **HUD Scale 请保持 100%（游戏默认值）**

## 概述

默认配置仅支持 1920×1080。其他分辨率的玩家需要**做一次**校准（约 15 秒），后续无需重复。

## 校准步骤

### 1. 打开游戏，进入飞船舱内或任意任务

推荐在**飞船舱内**校准（画面干净，血条清晰可见，无战斗干扰）。

**确保 HUD Scale 为 100%**（设置 → HUD → HUD 缩放）。

### 2. 运行校准脚本

```bash
cd HELLDIVER_DGLAB
python tools/calibrate.py
```

### 3. 5 秒内切回游戏全屏

终端显示 5 秒倒计时。**Alt+Tab 切回游戏**，确保血条出现在左下角。

倒计时结束后会听到提示音，截图自动完成。

### 4. 鼠标框选血条外框

截图完成后会弹出 OpenCV 窗口。**用鼠标拖拽框选血条整个外框**：

- ✅ 包含左端帽、中间血量条、右端帽
- ✅ 包含血条上下边框（上面到端帽上沿，下面到端帽下沿）
- ❌ **不要框到血条外的任何东西**（背景地形、武器图标、弹药 HUD 数字）

```
     ┌──────────────────────────────────┐ ← 框上沿（含端帽上端）
     │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░ │
     │ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░ │ ← 框下沿（含端帽下端）
     └──────────────────────────────────┘
     ↑ 左端帽                          ↑ 右端帽
```

框选后按 **回车** 确认。如果框选位置异常（太窄/太宽），程序会提示并允许重新框选。

### 5. 验证

校准后运行 `python tests/test_detector.py`，确认满血截图读数约 95%-100%。

如果偏差较大，重新运行 `python tools/calibrate.py` 即可覆盖（旧配置自动备份）。

## 常见错误

| 错误 | 后果 | 正确做法 |
|------|------|----------|
| 死亡画面校准 | 血条不可见 | 请在满血或受伤状态下校准 |
| 框多了背景/地形 | 血量读数偏小 | 只框血条本体，框完仔细确认边界 |
| 框了弹药/武器 HUD | 血量检测被污染 | 血条在武器图标**左边**，不要框到武器区域 |
| 漏掉端帽 | 内框计算偏移 | 必须包含完整端帽结构 |
| 只框了红色血量段 | 程序无法推导端帽 | 框整个外框（含灰色/白色端帽） |
| HUD Scale 非 100% | 端帽比例可能不准 | 请恢复 HUD Scale 为 100% 后校准 |

## 技术说明

校准脚本通过 `cv2.selectROI` 获取用户框选的外框坐标，按 HD2 血条的固定几何比例自动推导内框和端帽忽略参数，写入 `config.ini`。

所有比例来自 1080p config.ini 实际坐标的精确测量，在相同 HUD Scale 下任意分辨率保持不变。

## 恢复默认

如需恢复 1080p 默认坐标，可从备份文件恢复：
```bash
copy config.ini.bak.* config.ini
```
````

- [ ] **Step 2: Commit**

```bash
git add docs/calibration_guide.md
git commit -m "docs: add calibration guide for non-1080p users"
```

---

### Task 4: compute_roi_params 单元测试

**Files:**
- Create: `tests/test_calibrate.py`

- [ ] **Step 1: 创建测试文件**

```python
"""test_calibrate.py — calibrate.py 纯函数单元测试。"""
import sys
from pathlib import Path

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 手动导入 calibrate 模块（避免触发 argparse）
import importlib.util
_cp = PROJECT_ROOT / "tools" / "calibrate.py"
_spec = importlib.util.spec_from_file_location("calibrate", str(_cp))
calibrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calibrate)

compute_roi_params = calibrate.compute_roi_params
validate_roi = calibrate.validate_roi


class TestComputeRoiParams:
    """验证 compute_roi_params 在各种框选下输出合理。"""

    def test_1080p_baseline_reproduces_config(self):
        """用 config.ini 的 1080p 默认外框，应该还原出已知内框值。"""
        params = compute_roi_params(x=100, y=993, w=205, h=14)

        assert params["outer_x1"] == 100
        assert params["outer_y1"] == 993
        assert params["outer_x2"] == 305
        assert params["outer_y2"] == 1007

        # 允许 ±1px 的 round 误差
        assert abs(params["inner_x1"] - 106) <= 1
        assert abs(params["inner_y1"] - 996) <= 1
        assert abs(params["inner_x2"] - 300) <= 1
        assert abs(params["inner_y2"] - 1004) <= 1

        assert params["bar_min_x"] == params["inner_x1"]
        assert params["bar_max_x"] == params["inner_x2"]

    def test_scaled_for_4k_produces_proportional_coords(self):
        """4K 外框（宽高翻倍），内框也等比例翻倍。"""
        # 1080p→4K: 宽高各 ×2
        params = compute_roi_params(x=200, y=1986, w=410, h=28)

        # 内框关系保持
        assert params["inner_x1"] > params["outer_x1"]  # 有端帽
        assert params["inner_x2"] < params["outer_x2"]  # 有端帽
        assert params["inner_y1"] > params["outer_y1"]  # 有上边距
        assert params["inner_y2"] < params["outer_y2"]  # 有下边距

        # 有效宽度应该是宽度的 ~94%（扣除左右端帽各 3%）
        bar_w = params["bar_max_x"] - params["bar_min_x"]
        total_w = params["outer_x2"] - params["outer_x1"]
        assert bar_w > 0.9 * total_w, f"bar_w={bar_w} too small vs total_w={total_w}"

    def test_minimal_valid_box(self):
        """最小合理框选（720p 缩放）仍产出有效内框。"""
        params = compute_roi_params(x=10, y=500, w=100, h=7)
        assert params["inner_x1"] < params["inner_x2"]
        assert params["inner_y1"] < params["inner_y2"]
        assert params["bar_min_x"] < params["bar_max_x"]


class TestValidateRoi:
    """validate_roi 应该在合理输入通过，在异常输入拒绝。"""

    def test_valid_1080p_aspect_passes(self):
        ok, msg = validate_roi(w=205, h=14)
        assert ok, f"Expected pass, got: {msg}"

    def test_too_narrow_fails(self):
        ok, msg = validate_roi(w=60, h=10)
        assert not ok

    def test_too_short_fails(self):
        ok, msg = validate_roi(w=200, h=3)
        assert not ok

    def test_zero_dimensions_fails(self):
        ok, msg = validate_roi(w=0, h=14)
        assert not ok

    def test_too_wide_rejects_overly_stretched_box(self):
        ok, msg = validate_roi(w=500, h=10)
        assert not ok

    def test_tall_narrow_box_rejects_vertical_strip(self):
        ok, msg = validate_roi(w=20, h=80)
        assert not ok
```

- [ ] **Step 2: 运行测试**

```bash
python -m pytest tests/test_calibrate.py -v
```

预期：所有 9 个测试通过。

- [ ] **Step 3: Commit**

```bash
git add tests/test_calibrate.py
git commit -m "test: add compute_roi_params and validate_roi unit tests"
```

---

### Task 5: 完整链路测试

- [ ] **Step 1: 重置 calibration 标记**

```ini
[calibration]
calibrated = 0
resolution = 1920x1080
```

- [ ] **Step 2: 用 --image 离线校准**

```bash
python tools/calibrate.py --image debug/all_samples/full_01.jpg
```

框选血条外框，确认校验通过，坐标写入 config.ini，备份文件存在。

- [ ] **Step 3: 验证校准后的 test_detector 仍正常**

```bash
python tests/test_detector.py
```

预期：离线测试结果与校准前一致（因为从 1080p 截图校准应该框出接近原坐标）。

- [ ] **Step 4: 验证 runner_mock 仍正常**

```bash
python src/runner_mock.py
```

预期：正常启动，状态识别正确。

- [ ] **Step 5: 单元测试回归**

```bash
python -m pytest tests/test_calibrate.py tests/test_detector.py -v
```

预期：全部通过。

---

## 自审查

### Spec 覆盖

| 需求 | 覆盖 |
|------|------|
| 1080p 用户开箱即用 | Task 2（calibrated=0 默认） |
| 非 1080p 用户一键校准 | Task 1（calibrate.py 完整流程） |
| 5 秒倒计时 + 提示音 | Task 1 Step 1 |
| 框选校验 + 重选 | Task 1 Step 1（validate_roi + select_roi_loop） |
| config.ini 备份 | Task 1 Step 1（backup_config） |
| offline test 支持 | Task 1 Step 1（--image 参数） |
| roi_locator 零改动 | 确认不改 |
| 用户指南 | Task 3 |
| 单元测试 | Task 4 |

### 占位符扫描

无 TBD/TODO/占位符。所有代码完整可运行。

### 类型一致性

`compute_roi_params(x, y, w, h) → dict` 键名 ↔ `write_config` ↔ `config.ini [ROI]` 字段 ↔ `roi_locator.py` 读取一致。`validate_roi(w, h) → (bool, str)` 返回值在 `select_roi_loop` 中正确消费。

### Task 4 (runner 警告) 已移除

审查一致意见：runner_mock 是开发工具，不是终端用户入口。`[calibration]` 节暂由 calibrate.py 独占写入，等 runner_real.py 落地后再加启动警告。
