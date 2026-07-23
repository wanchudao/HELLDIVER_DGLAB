# HELLDIVER_DGLAB 交接文档

> 更新：2026-07-20
> 阶段：V0.2 完成 — 波形发送架构修复，真机波形送达待验证

---

## 1. 今天做了什么

| 改动 | 说明 |
|------|------|
| **dglab_sender 重写（P0 修复）** | 旧架构 `create_connection` + 裸 `recv()` 从未通过真机验证。改为 `WebSocketApp` + daemon thread + `on_message` 回调分发（从 SQUAD_DGLAB v1.0.3 搬来，真机验证通过） |
| **trigger 异步化** | `init_sender()` + `wait_for_pair()` 改为异步，适配新 sender 架构 |
| **control_panel 置顶** | 新增 `set_panel_topmost()`（ctypes Windows API），`runner_real.py` 恢复运行时自动置顶 |
| **start.bat** | 一键启动（后端 + runner，双击即可） |
| **testing_guide_v0.2.md** | 完整测试指南文档 |
| **test_dglab_sender.py** | 新增 11 个单测，覆盖新 sender 架构 |
| **test_pairing.py 适配** | 适配新 sender API |
| **runner_mock.py 加固** | 加 try/except 异常处理 |

---

## 2. 架构

```
mss/截图 → roi_locator → health_detector(v3) → effect_detector
                                    ↓                    ↓
                              state_machine (状态+debounce+拒识)
                                    ↓
                              trigger.py (HP% 直判 → 冷却 → sender)
                                    ↓
                              dglab_sender (WebSocketApp + daemon thread → SOCKET v2 → APP → 设备)
```

**波形发送新架构**：
```
trigger.send()
    ↓
dglab_sender.send_pulse()
    ↓
WebSocketApp (daemon thread)
    ↓
on_message 回调分发
    ↓
SOCKET v2 后端 → APP → 设备
```

**15 个源文件，28 个单元测试通过。**

---

## 3. 怎么用

```bash
# 一键启动（双击即可）
start.bat

# 1080p 用户 — 开箱即用
python src/runner_real.py                    # 终端模式
python src/runner_real.py --preview          # + overlay
python src/runner_real.py --enable-trigger   # + 放电（需先启动后端）

# 非 1080p 用户 — 先校准一次
python tools/calibrate.py                    # 5 秒倒计时 → 框选血条 → 回车

# Mock 回放
python src/runner_mock.py

# 测试
pytest tests/ -q                              # 28 passed
python tests/test_detector.py                 # 离线评估
```

**启动后默认暂停**（舰船上有经验条不是血条），进任务按 **P** 开始检测。

---

## 4. 关键决策

| 决策 | 结论 |
|------|------|
| 血条检测 | v3 混合特征：luma × 纹理惩罚。所有阈值可配置 |
| 触发逻辑 | HP% 直驱，不用 event 中间层 |
| 治愈判定 | hp 回升 > 2%（防噪点误触发） |
| 死亡判定 | hp ≤ 1% |
| 校准 | 鼠标框选 + 固定比例推导（端帽 2.93%/2.44%，上下边距 21.43%） |
| 安全设计 | 启动默认暂停、trigger 默认关、safe_stop 归零 |
| 波形发送架构 | WebSocketApp + daemon thread + on_message 回调（从 SQUAD v1.0.3 继承） |
| 子代理映射 | sonnet=DS V4 FLASH, opus=DS V4 PRO, haiku=LongCat-2.0 |

---

## 5. 已知未完成

| 项目 | 优先级 | 详情 |
|------|--------|------|
| **真机波形送达验证** | **P0** | 架构已修复（28/28 单测通过），但真机波形是否实际送达设备尚未验证。这是最关键的一步 |
| 真机强度调优 (20/40/15) | P0 | 从未真机测过 |
| health_detector header 过时 | P1 | docstring 写 "v2"，实际是 v3 混合特征 |
| 断线重连 | P1 | WebSocket 掉线无恢复 |
| test_detector.py 2 个 fixture error | P1 | `test_single_image`/`test_directory` 命名被 pytest 误收集 |
| 多分辨率支持 | P2 | 已通过 calibrate.py 解决，但 21:9 带鱼屏未验证 |

---

## 6. 下次启动

```bash
# 方式一：一键启动（双击 start.bat）
# 自动启动后端 + runner

# 方式二：手动分步
# 1. 启动 DG-LAB SOCKET v2 后端
cd F:\SQUAD_DGLAB\SQUAD_DGLAB\official_v2\socket\v2\backend
npm start

# 2. 确认 config.ini sender_mode = dglab

# 3. 跑 runner（带 trigger）
python src/runner_real.py --enable-trigger
#    → QR 码弹出 → APP 扫码配对 → 进任务 → 按 P 开始
#    → 看后端日志：[clientId] 收到 clientMsg: channel=A, time=2
#    → 如果出现这行 → 波形到了后端，检查 `消息已排队发送`
#    → 如果不出现 → clientMsg 根本没到后端

# 4. Mock 模式（不需后端）
python src/runner_real.py              # 看 hp 读数是否跟随游戏变化
```

---

## 7. 子代理流水线

| 角色 | 模型 | 说明 |
|------|------|------|
| Plan 审查 | PRO + LongCat | 方案可行性审查 |
| 写代码 | LongCat | 实现 |
| 审代码 | FLASH (DS V4 FLASH) | 代码质量审查 |
| 终审 | PRO (DS V4 PRO) | 最终把关 |
| 网关映射 | haiku=LongCat-2.0, sonnet=DS V4 FLASH, opus=DS V4 PRO | — |

---

## 8. 参考项目

- SQUAD_DGLAB: `F:\SQUAD_DGLAB\SQUAD_DGLAB\` (v1.0.3)
- SOCKET v2 后端: `F:\SQUAD_DGLAB\SQUAD_DGLAB\official_v2\socket\v2\backend\`
- 波形参考: `F:\SQUAD_DGLAB\SQUAD_DGLAB\official_v2\socket\DG_WAVES_V2_V3_simple.js`
