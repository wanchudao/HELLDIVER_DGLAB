# 代码与方案审查 Prompt（给 ChatGPT / TRAE Work）

请复制以下文字粘贴到 ChatGPT 或 TRAE Work：

---

你是一个资深计算机视觉工程师，专长是游戏画面分析与实时检测系统。请审查以下 Helldivers 2 血条识别 Phase 1 MVP 代码和方案。

## 项目背景

HELLDIVER_DGLAB 目标：读取 HD2 1920x1080 画面中的玩家血条状态，通过本地 Python 服务转发到 DG-LAB 电击设备。触发事件：受伤脉冲、残血持续、死亡、治疗确认。

## 已验证的血条真实特征（通过 GPT-4o 视觉分析）

- 血条 ROI: (97,943)-(307,967) — 但此坐标仅在 6/18 PNG 截图中验证，6/21 JPG 截图中该位置无血条
- 6/18 PNG 实际血条位置: (52,808)-(270,835)
- 血条是连续长条，从右往左消耗
- 满血：近白 RGB(220,218,198)，无滤镜
- 受伤：粉红红 RGB(125,50,48)，红色滤镜
- 死亡：全暗红 RGB(132,77,72)，极红滤镜 + "阵亡"文字 + 骷髅图标
- 治疗：少量亮残余，青蓝滤镜，注射动作

## 代码结构

```
config.ini              # 所有可调参数（ROI、权重、阈值、采样区、EMA 参数）
src/
  screen_capture.py     # 屏幕截图（mss + Mock 离线传入）
  roi_locator.py        # 外层 ROI + inner ROI + HUD 锚点校验
  health_detector.py    # 主条列级混合特征提取 + 血量计算 + EMA 平滑
  effect_detector.py    # 红屏/青蓝滤镜/死亡辅助检测，输出 score
  state_machine.py      # 跨帧状态机 + 事件生成 + unknown 拒识
  debug_overlay.py      # 调试可视化：ROI 框、血量边界、状态文字
tests/
  test_detector.py      # 离线测试：遍历 screenshots/ 输出检测结果
```

## 核心算法

### health_detector.py
- 转换到 Lab 色彩空间
- 对 inner ROI 每列提取：p90 亮度 + redness(R-max(G,B)) + 纹理能量(Sobel) + 梯度强度
- 计算 filled_score = w_luma * luma + w_redness * redness + w_texture * texture - w_gradient * gradient
- 从右往左找最后一个 filled 列 → 血量边界 → health_pct
- EMA 平滑：health_stable = alpha * raw + (1-alpha) * prev

### effect_detector.py
- 全屏 4 个边缘区域采样（上下左右）
- 红像素：R - max(G,B) > red_threshold
- 青蓝像素：B - R > cyan_threshold
- 死亡辅助：health_near_zero + red_extreme 同时满足 → death_score

### state_machine.py
- unknown 拒识：valid=False 或 confidence<0.55 或 anchor_score<0.50 → unknown
- 状态优先级：death(death_score>=0.7) > healing(cyan>=0.30 & hp<0.95) > injured(hp<0.75) > full_health
- 受伤脉冲：0.2s 窗口合并，deque + timestamp
- debounce=2帧确认，cooldown=5帧冷却

## 离线测试结果（在 6/21 JPG 截图上运行）

```
death/      → hp=100%  state=unknown/full_health  ❌ 全错
injured/    → hp=100%  state=full_health           ❌ 全错
full_health/→ hp=100%  state=full_health           ✅ 正确
healing/    → hp=100%  state=full_health           ❌ 全错
```

**问题分析**：ROI (97,943)-(307,967) 在 6/21 JPG 截图中不是血条位置（HUD 不一致），导致 ROI 捕捉到随机画面，全部输出 100%。红屏/青蓝滤镜检测正常（death red=0.98, healing cyan=1.00）。

## 请你重点审查

### 1. ROI 坐标问题
- 当前 ROI (97,943)-(307,967) 来自 6/18 PNG 截图分析，但 6/21 JPG 截图中该位置无血条
- 如何设计 ROI 校准流程？是否需要支持多分辨率 profile？
- HUD 锚点校验（A1 端帽检测）能否解决 ROI 偏移问题？

### 2. 血量检测逻辑
- 列级混合特征（p90_luma + redness + texture - gradient）是否合理？
- filled_threshold=0.55, empty_threshold=0.30 的设置是否合理？
- 从右往左找最后一个 filled 列的策略在红屏下会不会失效？
- EMA alpha=0.3 是否合适？

### 3. 红屏/滤镜检测
- 红像素定义 R-max(G,B)>18 是否太宽松或太严格？
- 青蓝像素定义 B-R>10 是否合理？
- 死亡辅助判定（health_near_zero + red_extreme）是否足够？
- 全屏 4 区域采样是否足够？会不会被战场画面（爆炸火焰）干扰？

### 4. 状态机设计
- unknown 拒识条件（confidence<0.55, anchor_score<0.50）是否合理？
- 状态优先级 death > healing > injured > full_health 是否正确？
- 0.2s 合并窗口 + debounce=2帧 + cooldown=5帧 的参数组合是否合理？
- 治疗判定需要"血量回升 + 青蓝滤镜"两项同时满足，会不会太严格？

### 5. 代码质量
- screen_capture.py 的 BGRA→BGR 转换是否正确？
- roi_locator.py 的锚点校验逻辑是否可靠？
- health_detector.py 的列级特征提取是否有性能瓶颈？
- state_machine.py 的事件合并逻辑是否有竞态问题？

### 6. 风险与改进
- 当前方案最大的风险是什么？
- 如果 ROI 校准后仍然误判率高，应该优先改什么？
- 有没有遗漏的关键边界情况？

请逐条回答，给出具体的技术建议、代码级优化和风险评级（高/中/低）。
