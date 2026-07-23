# 血条检测 v3 — 灰度+纹理混合算法 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 v2 灰度算法在高血量时（空槽灰白条纹）误判为 filled 的问题。升级为 v3 混合特征：列均值灰度 + 列内标准差（纹理）。

**Architecture:** 改动局限在 `health_detector.py` 的 `_extract_column_features` 和 `_compute_filled_scores` 两个函数。接口不变——`detect()` 对外接口零改动。std 惩罚分母 50 硬编码（Phase 1 不暴露为 config 参数，数据支撑充分）。

**Tech Stack:** numpy（向量化），无新依赖

---

## 审查记录

> 2026-07-19 — PRO + LongCat 双审查

| 发现 | 严重度 | 修复 |
|------|--------|------|
| `std_penalty_weight` 是幽灵参数（声明但代码未实现） | 中 (LongCat) | 删掉，硬编码 50。Phase 1 不需要 |
| 死亡场景 std 假设错误（计划写"std 应该也低"，实际 std≈27） | 中 (PRO) | 修正为正确验算：`(75/255)×(1-27/50)=0.14` |
| 极低血量估计矛盾（计划写 ~0.30，实际应为 ~0.55） | 中 (PRO) | 修正为 ~0.55（基于受伤填充数据外推） |
| 缺 v3 单元测试 | **高** (双方) | 新增 Task 3：`_compute_filled_scores` 纯函数测试 4 个 |
| 线性公式 vs 平方/sigmoid | PRO 确认 | 线性正确——平方反而会让灰白空槽超 filled_threshold |

---

## 问题根因

v2 只用列均值灰度 `gray/255`：

| 场景 | 填充分 | 空槽分 | 分离度 | 问题 |
|------|--------|--------|--------|------|
| 低血量 | ~0.59 | ~0.30 | 2x | ✅ 可区分 |
| 高血量（灰白条纹） | ~0.99 | ~0.55 | 1.8x | ❌ 空槽 > filled_threshold(0.42) |

灰白条纹列的均值接近填充列，但**列内标准差高 30-50 倍**。v2 忽略了纹理信号。

---

## 数据基础

从 4 张实际截图提取的左/右半区数据（1080p 校准坐标）：

| 截图 | 左（真有血） | 右（空槽） | mean差 | **std差** |
|------|-----------|----------|--------|----------|
| 受伤 32% | mean=150 std=0.8 | mean=75 std=27 | 2x | **34x** |
| 受伤 21% | mean=150 std=0.7 | mean=71 std=28 | 2x | **40x** |
| 治疗（高血）| mean=253 std=0.5 | mean=139 std=25 | 1.8x | **50x** |
| 满血 | mean=254 std=0.5 | mean=254 std=0.4 | 1x | 1.2x |

**规律：空槽列 std ≥ 25（无论灰白还是暗红），填充分 std ≤ 0.8。std 在所有场景下都是可靠区分器。**

---

## 算法

### v3 公式

```
filled_score = (col_mean / 255) × (1 − col_std / 50)
              ^^^^^^^^^^^^^^^^    ^^^^^^^^^^^^^^^^^^^^^
              亮度项              纹理惩罚项（std≥50 时归零）
```

### 验算

| 场景 | 填充 `mean=150 std=0.8` | 空槽 | 分离度 |
|------|------------------------|------|--------|
| 受伤 | 0.58 | 0.14 (暗红 `mean=75 std=27`) | 4.3x |
| 治疗 | 0.98 | 0.27 (灰白 `mean=139 std=25`) | 3.6x |
| 满血 | 0.99 | 0.99 (`mean=254 std=0.4`) | — ✅ |

灰白空槽从旧公式的 0.55 → 新公式的 0.27，远低于 filled_threshold。

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/health_detector.py` | **修改** | `_extract_column_features` 返回 (mean, std)；`_compute_filled_scores` 融合纹理惩罚 |
| `tools/scan_features.py` | **修改** | 2 行：`_extract_column_features` / `_compute_filled_scores` v3 签名 |
| `config.ini` | **修改** | `[features]` 阈值调整（0.42/0.35 → 0.38/0.30）+ 注释更新 |
| `tests/test_health_detector.py` | **新增** | v3 公式 6 个纯函数测试 |

---

### Task 1: 升级 health_detector.py 到 v3

**Files:**
- Modify: `src/health_detector.py` (3 处改动)

#### 改动 1: `_extract_column_features` — 返回 mean 和 std

**旧代码** (L69-77):
```python
def _extract_column_features(self, img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return np.mean(gray, axis=0)
```

**新代码**:
```python
def _extract_column_features(self, img_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """提取每列均值灰度 + 列内标准差（纹理）。

    Returns:
        col_mean: (W,) 每列平均灰度 0-255
        col_std:  (W,) 每列标准差（检测灰白条纹空槽）
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return np.mean(gray, axis=0), np.std(gray, axis=0)
```

#### 改动 2: `_compute_filled_scores` — 融合纹理惩罚

**旧代码** (L81-89):
```python
def _compute_filled_scores(self, col_gray_mean: np.ndarray) -> np.ndarray:
    scores = np.clip(col_gray_mean / 255.0, 0.0, 1.0)
    scores = self._smooth_scores(scores)
    return np.clip(scores, 0.0, 1.0)
```

**新代码**:
```python
def _compute_filled_scores(self, col_mean: np.ndarray, col_std: np.ndarray) -> np.ndarray:
    """v3 混合特征：亮度 × 纹理惩罚。

    亮度项: mean/255（同 v2）
    纹理惩罚: 1 - std/50（std≥25 的灰白条纹空槽列被大幅降权）
    """
    # 亮度项 (0-1)
    luma = col_mean / 255.0
    # 纹理惩罚: std 越高越不像有血（灰白条纹、暗红斜纹都是高 std）
    penalty = 1.0 - col_std / 50.0
    penalty = np.clip(penalty, 0.0, 1.0)  # std≥50 → 惩罚=0%
    # 融合
    scores = luma * penalty
    scores = self._smooth_scores(scores)
    return np.clip(scores, 0.0, 1.0)
```

#### 改动 3: `tools/scan_features.py` — v2→v3 签名适配

**旧代码** (L35-36):
```python
col_gray = detector._extract_column_features(inner)
scores = detector._compute_filled_scores(col_gray)
```

**新代码**:
```python
col_mean, col_std = detector._extract_column_features(inner)
scores = detector._compute_filled_scores(col_mean, col_std)
```

> `scan_features.py` 是独立诊断工具，不参与任何自动化链路。但直接调用 `_` 私有方法，v3 签名变化会崩溃。改动 2 行。

#### 改动 4: `detect()` — 调用签名适配

**旧代码**:
```python
col_gray = self._extract_column_features(inner_img)
filled_scores = self._compute_filled_scores(col_gray)
```

**新代码**:
```python
col_mean, col_std = self._extract_column_features(inner_img)
filled_scores = self._compute_filled_scores(col_mean, col_std)
```

---

#### `detect()` 方法中的具体变更位置

位于 `health_detector.py` L213-217。替换 3 行：

```python
        # 1. 列均值灰度 + 标准差（v3 混合特征）
        col_mean, col_std = self._extract_column_features(inner_img)

        # 2. 归一化 → filled_score（v3: 亮度×纹理惩罚）
        filled_scores = self._compute_filled_scores(col_mean, col_std)
```

---

### Task 2: 更新 config.ini 阈值

**Files:**
- Modify: `config.ini`

v3 的 filled_score 因为纹理惩罚整体偏低（尤其空槽列被压到 0.13-0.27），v2 的阈值不再适用。

**旧值**:
```ini
[features]
gray_filled_threshold = 0.42
gray_empty_threshold = 0.35
```

**新值**:
```ini
[features]
# v3 混合特征阈值（亮度×纹理惩罚后，分数 0-1）
# filled: 填充分均值 ~0.58~0.99（受伤~满血）
# empty:  空槽分均值 ~0.13~0.27（暗红~灰白条纹）
gray_filled_threshold = 0.38
gray_empty_threshold = 0.30
```

**阈值设计依据**：

| 场景 | 填充分范围 | 空槽分范围 | 推荐 filled_thr | 推荐 empty_thr |
|------|-----------|-----------|----------------|----------------|
| 满血 | 0.99 | 0.99 | — | — |
| 治疗（高血量） | 0.98 | 0.27 | <0.98 | >0.27 |
| 受伤（21-32%） | 0.58 | 0.14 | <0.58 | >0.14 |
| 极低血量（<5%） | ~0.55 | ~0.14 | <0.55 | >0.14 |

> **极低血量 `~0.55` 的推导**：填充分的 mean 和 std 与受伤时一致（HD2 血条颜色是连续渐变，不是分段；坐标报告确认 RGB 从满到空连续变化），因此即使只剩几列填充，每列的分数仍约 0.55。`~0.30` 的旧估计属于错误，已修正。
> **死亡场景验算修正**：死亡截图的空槽 RGB 与受伤完全相同（坐标报告证实），std 应为 ~27 而非"低 std"。正确值：`(75/255)×(1-27/50)=0.14`，远低于 empty_threshold 0.30。死亡判定还另有 death_score 双保险（红屏检测），不受影响。

取安全边际：`filled_threshold = 0.38`（> 空槽 max 0.27, < 填充分 min 0.58），`empty_threshold = 0.30`（> 空槽 max 0.27, < 填充分 min 0.58）。中间 0.27-0.58 为 uncertainty 区域。

---

### Task 3: v3 公式单元测试

**Files:**
- Create: `tests/test_health_detector.py`

纯函数测试 `_compute_filled_scores` 的输入输出——给定已知 (mean, std)，验证得分在预期范围内。不依赖截图，不依赖 config。

- [ ] **Step 1: 创建测试文件**

```python
"""test_health_detector.py — v3 _compute_filled_scores 纯函数测试。"""
import sys
from pathlib import Path
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import load_config
from src.health_detector import HealthDetector


def _make_detector():
    """创建一个 HealthDetector 实例用于测试。"""
    config = load_config(str(PROJECT_ROOT / "config.ini"))
    return HealthDetector(config)


class TestComputeFilledScores:
    """v3 公式: score = (mean/255) x (1 - std/50)"""

    def test_filled_column(self):
        """真有血：高 mean + 低 std → 高分"""
        det = _make_detector()
        mean = np.array([150.0], dtype=np.float32)
        std = np.array([0.8], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert 0.55 < scores[0] < 0.62, f"expected ~0.58, got {scores[0]:.3f}"

    def test_empty_darkred_column(self):
        """暗红空槽：低 mean + 高 std → 低分"""
        det = _make_detector()
        mean = np.array([75.0], dtype=np.float32)
        std = np.array([27.0], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert 0.10 < scores[0] < 0.18, f"expected ~0.14, got {scores[0]:.3f}"

    def test_empty_graywhite_column(self):
        """灰白条纹空槽：中 mean + 高 std → 低分"""
        det = _make_detector()
        mean = np.array([139.0], dtype=np.float32)
        std = np.array([25.0], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert 0.23 < scores[0] < 0.32, f"expected ~0.27, got {scores[0]:.3f}"

    def test_full_health_column(self):
        """满血：高 mean + 低 std → 极高分"""
        det = _make_detector()
        mean = np.array([254.0], dtype=np.float32)
        std = np.array([0.5], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert scores[0] > 0.95, f"expected ~0.99, got {scores[0]:.3f}"

    def test_max_penalty(self):
        """std=50 时惩罚归零"""
        det = _make_detector()
        mean = np.array([200.0], dtype=np.float32)
        std = np.array([50.0], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        assert scores[0] < 0.05, f"expected ~0, got {scores[0]:.3f}"

    def test_smooth_does_not_distort(self):
        """平滑不改变分数排序：高→低→高 的形状应保留"""
        det = _make_detector()
        mean = np.array([254, 139, 75, 139, 254], dtype=np.float32)
        std  = np.array([0.5, 25, 27, 25, 0.5], dtype=np.float32)
        scores = det._compute_filled_scores(mean, std)
        # 两端高，中间低
        assert scores[0] > scores[1] > scores[2]
        assert scores[4] > scores[3] > scores[2]
```

- [ ] **Step 2: 运行测试**

```bash
python -m pytest tests/test_health_detector.py -v
```

预期：6 passed。

- [ ] **Step 3: Commit**

```bash
git add tests/test_health_detector.py
git commit -m "test: add v3 _compute_filled_scores unit tests"
```

---

### Task 4: 回归测试

- [ ] **Step 1: 运行离线评估**

```bash
python tests/test_detector.py
```

预期改善项 vs v2 基线：
- `healing/group_two` 从 0% accuracy 提高（灰白空槽不再被误判为 filled）
- `injured` 中间几帧（hp=0.0% 但 state=death 的）保持 death 判定
- `full_health` 不变

- [ ] **Step 2: 运行单元测试**

```bash
python -m pytest tests/test_calibrate.py -v -q
```

预期：11 passed（calibrate 不受影响）

- [ ] **Step 3: 用 --image 模式逐张验伤**

```bash
python src/runner_mock.py
```

预期：healing 场景 hp 读数不再 100%，injured 场景读数合理。

---

## 自审查

### 改动范围

| 文件 | 改动行数 | 类型 |
|------|---------|------|
| `health_detector.py` | ~10 行 | 3 处函数签名/逻辑 |
| `config.ini` | ~6 行 | 阈值 + 注释 |

### 接口稳定性

`detect()` 返回 `HealthResult` —— 不变。`roi_locator`、`effect_detector`、`state_machine`、`runner_*` 全部零改动。v2→v3 对调用方完全透明。

### 占位符扫描

无 TBD/TODO/占位符。所有阈值有数据支撑。
