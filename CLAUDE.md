# CLAUDE.md

## 项目说明

HELLDIVER_DGLAB 是将《绝地潜兵2》(Helldivers 2) 游戏画面中的角色状态识别结果，通过本地 Python Trigger 服务转发到 DG-LAB SOCKET v2 通信层，实现基于视觉识别的外部设备反馈的项目。

本项目是 SQUAD_DGLAB（已发布 v1.0.3）的姊妹项目，复用了经过真机验证的架构模式：vision 层 → Python Trigger 层 → DG-LAB SOCKET v2 后端 → 实体设备。

当前项目处于**新建/规划阶段**。任何 AI 助手在处理本项目时，必须优先保护现有结构、注释风格、日志风格和稳定性。

## 总体原则

1. 修改前必须先阅读相关代码和上下文。
2. 修改前必须先说明计划，不允许直接大范围改代码。
3. 每次只处理一个明确任务。
4. 不允许无理由重写整个文件。
5. 不允许无理由删除已有注释。
6. 不允许无理由删除已有日志。
7. 不允许修改无关文件。
8. 不允许引入新依赖，除非先说明原因并得到确认。
9. 不允许修改密钥、账号、token、证书、私有配置等敏感内容。
10. 所有修改必须能通过人工 review。

## Python 代码规则

1. 保持与 SQUAD_DGLAB 一致的 Python 代码风格。
2. 保持清晰的函数命名、模块划分和目录结构。
3. 修改函数逻辑时，必须同步检查调用方。
4. 修改异常处理时，必须检查日志是否仍然足够定位问题。
5. 新增代码应尽量小而清晰，不要过度设计。
6. 不要随意改变 public API、配置字段或外部调用方式。

## JavaScript 代码规则

1. 保持与 SQUAD_DGLAB official_v2 一致的代码风格。
2. 不要随意引入新的前端框架或构建工具。
3. 如果修改前后端交互逻辑，必须说明接口字段变化。
4. 不要改动与当前任务无关的 UI、样式或脚本。

## 注释规则

1. 保留解释业务逻辑、协议逻辑、设备逻辑、异常原因的注释。
2. 如果代码行为改变，必须同步更新相关注释。
3. 不要添加无意义的显而易见注释。
4. 对复杂逻辑，应解释"为什么这样做"，而不是只解释"代码做了什么"。

## 日志规则

1. 参考 SQUAD_DGLAB 的日志格式和日志等级习惯。
2. 不要删除关键路径上的日志。
3. 修改异常处理、设备连接、网络请求、文件读写、状态切换等逻辑时，必须检查日志是否完整。
4. 不要在日志中输出密码、token、密钥、私有配置或敏感数据。
5. 新增错误处理时，应加入有助于排查问题的日志。

## Git 工作流

1. 不要直接在 main 分支上工作。
2. AI 修改应优先在 ai-setup-test 或其他 ai-* 分支上进行。
3. 每次修改后必须说明：
   - 修改了哪些文件
   - 为什么修改
   - 是否影响现有功能
   - 如何测试
4. 修改后必须建议运行对应测试或启动脚本。
5. 不允许自动提交代码，除非用户明确要求。

## AI 工作流程

每次任务必须按以下流程执行：

1. 先分析相关文件。
2. 再给出修改计划。
3. 等待用户确认。
4. 只修改计划中明确提到的文件。
5. 修改后展示变更摘要。
6. 提供测试步骤。
7. 等待用户 review。

## 需求讨论与记录流程（最高优先级）— 2026-06-20 确立

任何设计/方案/路线图/需求讨论必须按以下统一流程执行。

### 讨论阶段：苏格拉底式引导

1. **像苏格拉底一样引导**：通过提问帮助用户梳理需求，引导用户自己澄清想法，而非直接给出结论。
2. **一次只问一个问题**：每次只提一个带编号选项的问题，等待用户回答后再继续下一个。
3. **逐步形成完整 SPEC**：通过连续问答，最终产出包含以下章节的完整 SPEC：
   - **Problem Statement**（问题陈述）
   - **Proposed Solution**（方案描述）
   - **Technical Constraints**（技术约束）
   - **Non-goals**（明确不做的事）
   - **Success Criteria**（成功标准）
4. **SPEC 完成前不写代码**：SPEC 未定稿前，禁止任何代码编写或修改。

### 记录阶段：四步存档

1. **阶段性确认**：每完成一轮有意义的讨论，总结结论等待用户确认。
2. **准入 CLAUDE.md**：用户确认后，所有讨论结果精炼存入 CLAUDE.md（存全部）。
3. **报告关键点**：提炼最重要的决策报告给用户。
4. **准入记忆体**：经用户批准后，仅将最重要、几乎不会大改的指令/架构/规则存入记忆体。

### 原则

- 苏格拉底式问答是"怎么谈"，四步流程是"谈完怎么存"
- SPEC 是苏格拉底问答的输出产物
- CLAUDE.md 存一切，记忆体只存宪法级内容

## 禁止行为

1. 禁止大范围格式化整个项目。
2. 禁止删除历史注释和日志。
3. 禁止修改与任务无关的文件。
4. 禁止猜测配置含义后直接改配置。
5. 禁止把临时调试代码长期留在项目中。
6. 禁止把本地路径、账号、密钥写死进代码。
7. 禁止在没有说明原因的情况下改变项目结构。

## 推荐提示词

当用户要求修改代码时，AI 应优先回应：

"我会先阅读相关文件并给出计划，暂不修改代码。"

当用户要求审查代码时，AI 应优先检查：

1. 是否破坏现有注释
2. 是否破坏现有日志
3. 是否引入不必要依赖
4. 是否影响现有运行脚本
5. 是否存在异常处理遗漏
6. 是否有敏感信息泄露风险

## Windows 批处理文件注意事项

从 SQUAD_DGLAB 的踩坑经验中继承：

1. `.bat` 文件必须以 **GBK/ANSI** 编码保存（中文 Windows 兼容），不能用 UTF-8。
2. 文件开头不能有 BOM 字节（`EF BB BF` 会被 cmd 当成乱码命令）。
3. 行尾符必须是 **CRLF**（`0D 0A`），不能是 LF。
4. `.gitattributes` 必须把 `*.bat` / `*.cmd` 标记为 `binary`，防止 git 修改字节。
5. 用 `goto` 跳转结构代替嵌套 `if` 块，避免 cmd 解析问题。
6. `@echo off` 前面不能有 BOM，BOM 头会吃掉第一行。

## 从 SQUAD_DGLAB 继承的关键经验

1. **先 Mock 后 Real**：始终先用 Mock 模式完成完整链路测试，再切换到真实设备模式。
2. **safe stop 是生命线**：所有设备动作必须有自动归零机制 + per-channel token 防误伤。
3. **suppression 是实验性的**：画面特效检测（模糊、色差）有误触发风险，默认关闭。
4. **配置文件集中管理**：强度/时长参数放在 config.ini，运行时无需改代码。
5. **install.bat 默认行为必须安全**：不可逆的危险操作必须显式确认，默认行为是退出而非冒险。
6. **写脚本前先看实际目录**：不要凭印象推断路径、入口文件名、端口号。
7. **依赖扫描要全代码库 import**：不要靠记忆列依赖清单。
8. **vision 层不直接发设备协议**：vision 只做识别和逻辑事件判断，通过 /trigger 接口走 Trigger 层统一发送。
9. **不要开新战线**：当前任务没完成前不要跳到下一个话题。

---

# AI 工作记录

## 2026-06-20 — 项目初始化

**模型**: deepseek-v4-pro
**分支**: 无（项目尚未初始化 git）

---

### 背景

HELLDIVER_DGLAB 是 SQUAD_DGLAB 的姊妹项目。SQUAD_DGLAB 经过 10 天开发（2026-04-28 ~ 2026-05-24）从 alpha 走到 v1.0.3 正式发布，核心链路（bleeding / incap / death 识别 → /trigger → dglab_sender → SOCKET v2 → APP → 实体设备）已通过真机验证。

HELLDIVER_DGLAB 目标是将同样的架构应用于《绝地潜兵2》，需要分析两款游戏的差异并制定适配方案。

### 已完成

- 完整阅读 SQUAD_DGLAB 10 份开发日志（2026-04-28 ~ 2026-05-24）
- 完整阅读 SQUAD_DGLAB CLAUDE.md（AI 协作规则 + 工作记录）
- 完整阅读 SQUAD_DGLAB README.md（v1.0.3 项目文档）
- 创建 HELLDIVER_DGLAB CLAUDE.md（本文件）

### 用户需求确认（2026-06-20）

**核心需求**：读取 Helldivers 2 游戏内屏幕血条，在以下三种事件触发不同波形和强度：

| 事件 | 触发条件 | DG-LAB 响应 |
|------|----------|-------------|
| 受伤 (injured) | 血量下降 | 对应波形 + 强度 |
| 死亡 (death) | 血量归零 | 对应波形 + 强度 |
| 治疗 (healing) | 使用治疗针恢复 | 对应波形 + 强度 |

**起点**：从零开始，空项目目录。SQUAD_DGLAB 作为参考项目，开发步骤复用其流程。

**技术路线差异**：
- SQUAD_DGLAB 用 YOLO 检测画面特效（流血滤镜、黑白死亡画面）
- HELLDIVER_DGLAB 需要检测 **UI 血条**（ROI 裁剪 + 像素/颜色分析，可能不需要 YOLO）

### 当前阶段：Phase 0 — 截图分析（进行中）

**截图需求**：满血 / 不同程度受伤 / 死亡画面 / 使用治疗针

**截图分析方式（已废弃）**：~~通过 Agent 工具调用 Claude Sonnet（有视觉能力）直接读取本地截图~~ — Agent + Sonnet 实际走 deepseek，非真正 Claude 视觉。此路径已确认不可行。当前无可用视觉分析方案。

---

### 2026-06-20 会话 — SPEC 确立 + 截图首轮分析

**流程建立**：用户要求将"苏格拉底式问答"和"四步存档流程"统一为《需求讨论与记录流程》，已写入 CLAUDE.md 和记忆体。

**SPEC 核心事件架构（9 轮苏格拉底问答确立）：**

| 事件 | 触发条件 | 反馈方式 |
|------|----------|----------|
| 受伤脉冲 | 任何血量下降 | 瞬时脉冲，0.2s 窗口内累积伤害合并为一次，强度按累积量 |
| 残血持续 | 血量 < 75% | 持续低频刺激，与脉冲可叠加并行 |
| 死亡 | 血量归零 (HD2 无倒地状态，直接死亡等增援) | 死亡波形 |
| 治疗 | 血量回升 (不关心回血来源) | 确认脉冲 + 持续舒缓叠加 |

**技术决策：**

| 决策 | 结论 |
|------|------|
| 连击处理 | 0.2 秒累积窗口，合并脉冲 |
| 通道分配 | 全部 A 通道，B 预留 |
| 强度参数 | 先复用 SQUAD config.ini，Phase 3 真机再调 |
| 配置界面 | 纯 config.ini，不做 GUI |
| DG-LAB 协议 | 复用 F:\SQUAD_DGLAB\SQUAD_DGLAB\official_v2\ 的 SOCKET v2 |

**SOCKET v2 协议分析（已读取 official_v2 全部源码）：**
- WebSocket 端口 9999，JSON 消息格式 `{type, clientId, targetId, message}`
- 波形格式：`pulse-{channel}:{waveform_json}`，波形为 hex 字符串数组
- 强度格式：`strength-{channel}+{type}+{strength}`，channel A=1, B=2
- SQUAD 波形模板：weak_pulse (12段渐进), strong_pulse (20段), death_pulse (20段平坦最大)
- SQUAD config.ini：weak=10, strong=20, death=40，时长 2s/4s/5s

**截图首轮分析：**

分析截图：`20260618194909_1.png` (1920x1080, 受伤状态)

⚠️ **注意**：当前模型（LongCat-2.0 / deepseek-v4-pro）均无视觉能力，无法直接分析截图。以下精确像素数据（血条坐标、RGB 值、分段宽度等）来源不可靠，可能为模型编造，需通过 PIL 程序化扫描或真正的多模态模型重新验证。

⚠️ **待确认**：血条消耗方向、满血/死亡/治疗状态截图分析

### 用户明确规则

1. **没有用户的批准，不可以写或修改任何代码** — 这是最高优先级规则，覆盖所有其他行为准则
2. 可以先分析、讨论、规划，但涉及 Write/Edit 工具时必须先得到确认
3. 开发步骤参考 SQUAD_DGLAB 的流程（但是是 HELLDIVER 不是 SQUAD）
4. ~~可通过 Agent + Sonnet 模型绕过 deepseek-v4-pro 的无视觉限制~~ — **已确认不可行**：Agent + Sonnet 实际走 deepseek，非真正的 Claude 视觉能力

---

### 2026-06-21 会话 — 截图分类 + 多轮 Sonnet 分析

**模型**: deepseek-v4-pro / LongCat-2.0（均无视觉能力）
**状态**: Phase 0 截图分析，未写代码

---

#### 完成事项

1. 创建 4 个截图分类文件夹：
   - `screenshots/full_health/` — 2 张
   - `screenshots/injured/` — 8 张
   - `screenshots/death/` — 5 张
   - `screenshots/healing/group_one/` + `group_two/` — 5+3 张

2. ⚠️ 声称的"4 轮 Sonnet 视觉分析"实际走 deepseek，精确像素数据不可靠，已标记为待验证

3. ⚠️ 逐段血条分析（空血段/过渡段/当前血量/全高红段精确坐标）为 fake-Sonnet 产物，已删除，需 PIL 重新扫描验证

#### 关键发现

**6月21日受伤截图**：
- 前 4 张（13:54:31-32）有血条（1070-3854 红色像素）
- 后 4 张（13:54:34-45）血条区域 0 红色像素，HUD 消失
- 用户确认同局任务、Steam F12 截图、始终显示

**6月21日满血截图**：
- 血条区域（y=808-835）无 HUD 渲染，仅地形（PIL 验证：0 红色像素）
- 底部存在白色面板和金色图标（用户确认是地表任务中、手持武器时截的），但不是红色分段血条
- ⚠️ 白色面板/金色图标的精确坐标来自 fake-Sonnet，待验证

**6月21日死亡截图**：
- 血条不可见（被全屏红色死亡滤镜吞没）
- 5 帧呈现红色消退现象（用户确认）
- ⚠️ R/G 比值序列（1.81 → 1.48 → 1.22 → 1.07 → 0.95）来自 fake-Sonnet，待 PIL 验证

**治疗截图**：
- 尚未分析（下一步待办）

#### 未解决的关键问题

6月18日 vs 6月21日截图之间 HUD 表现不一致：
- 6月18日 PNG：血条清晰在 y=808-835
- 6月21日 JPG：同位置无血条（满血）或仅短暂出现后消失（受伤）
- 可能原因：动态 HUD 行为、HUD 缩放设置变化、或截图时 HUD 被隐藏——尚未确定

#### 流程验证

- ~~Agent + Sonnet 视觉分析路径已验证可行~~ — **已确认不可行**：Agent + Sonnet 实际走 deepseek，非真正的 Claude 视觉能力
- PIL 像素扫描可复现，但纯程序化方案已被用户确认不可行（无法精确到像素级分析）
- **当前状态：视觉分析能力缺失，需要真正的多模态模型或新的方案**

#### 下一步

1. 解决视觉分析能力问题（真正的多模态模型、或其他可行方案）
2. 确认 6月18日 vs 6月21日截图环境差异（HUD 设置、分辨率、画面模式）
3. 基于验证后的血条特征进入 Phase 1 算法设计

---

### 2026-07-18 会话 — Phase 6-8 完成 + 治疗检测修复

**模型**: deepseek-v4-pro / LongCat-2.0 (代码实现)

---

#### 完成事项

**Phase 5 (收尾)**: 将 `load_config()` 从 4 个文件提取到 `src/config_loader.py`

**Phase 6 (实时入口)**: 
- 创建 `src/runner_mock.py` — Mock 实时循环播放截图
- 按期望状态分组，组间 reset state_machine + EMA
- 支持 SHOW_PREVIEW (debug overlay) 和 LOOP 循环
- 状态转换追踪 + Ctrl+C 退出摘要

**Phase 7 (Trigger 层)**:
- 创建 `src/trigger.py` — 事件→action 映射 + sender 适配器模式
- `sender_mode = mock` 打印 [MOCK SEND]；`sender_mode = dglab` 连接真机
- 零新依赖（只用 stdlib）

**Phase 8 (DG-LAB SOCKET v2 真机)**:
- 创建 `src/dglab_sender.py` — WebSocket 客户端，V3 波形
- LAN IP 自动检测 + QR 码自动生成打开
- safe_stop: 强度归零 + 清除波形队列
- 创建 `requirements.txt`

**state_machine 帧计数冷却 → 挂钟时间冷却**:
- 删除 `_cooldown_counter`、`cooldown_frames`
- 新增 `_last_event_triggered: dict[EventType, float]`
- 冷却在 mock(2fps) 和 real(60fps) 下行为一致

**治愈检测修复 (最重要)**:
- 旧: `cyan_score >= 0.30 AND health_stable < 0.95` — 依赖青蓝滤镜颜色
  - 雪地地图天然有青色 → 假阳性
  - 黄色实验注射剂没有青色 → 假阴性
- 新: `health_stable > self._prev_health_stable` — 纯亮度方向判断
- 原理: HD2 中血量上升只有两个来源 — 打针和复活，两者都是 SPEC 定义的"治疗"
- 删除 `healing_gap_threshold`（中间方案，被废弃）
- 保留 `healing_cyan_threshold` 仅供 effect_detector 调试参考

#### 关键设计决策

| 决策 | 结论 |
|------|------|
| 治愈检测 | 血量回升 = 治疗。不关心颜色、gap、状态守卫 |
| 冷却机制 | 挂钟时间 > 帧计数（跨帧率一致） |
| Trigger 架构 | 直接函数调用，不用 FastAPI（无独立进程边界） |
| 文件数量 | 7 文件计划砍到 2 文件（3 模型审查一致） |
| 复活 | 复活 = 血量从 0 回升 = 也是治疗（SPEC 原文"不关心回血来源"） |

#### 多次子代理审查的教训

1. 初始 Phase 6 方案过度设计（diff-mode, JSONL, pipeline.py）→ 3 模型一致砍
2. 治愈检测 gap 方案被 V4 PRO 发现致命漏洞（DEATH 守卫只挡 2 帧）→ 废弃
3. "最简单=最正确"：用户反复引导回归原始 SPEC → 方向判断才是正确答案
4. 3 模型审查模式有效：PRO 发现逻辑漏洞, FLASH 砍过度工程, LONGCAT 提 UX 想法

#### Phase 8 待验证

- 真机波形格式未验证（SQUAD 也只用了 mock）
- QR 码手机扫描测试未完成
- 实时 mss 屏幕捕获 runner 未实现（当前只有 mock 截图播放）



#### 血条坐标最终确认（权威来源：`docs/healthbar_coordinates_report.md`）

> ⚠️ 以下坐标经 TRAE Work (GPT-4o) 视觉分析验证，5 张截图完全一致。
> 旧文档中记录的 `y=808-835` 和 `(52,808)-(270,835)` 为 fake-Sonnet 编造数据，已废弃。

| 参数 | 值 | 说明 |
|------|-----|------|
| **外框 ROI** | `(97, 943) - (307, 967)` | 含端帽，5 张截图一致 |
| **内框主条** | `(106, 949) - (298, 958)` | 只含血量填充区 |
| **血量有效区间** | `x = 106 到 298` | 宽 192px |
| **血条类型** | 连续长条 | 非分段 |
| **消耗方向** | 从右往左 | 剩余血量在左侧 |

#### Phase 1 MVP 代码结构

```
config.ini              # 所有可调参数（ROI、权重、阈值）
src/
  screen_capture.py     # mss 截图（已修复 BGRA→BGR bug）
  roi_locator.py        # ROI 裁剪 + HUD 锚点校验
  health_detector.py    # 列级混合特征 + 血量计算 + EMA
  effect_detector.py    # 红屏/青蓝滤镜/死亡辅助检测
  state_machine.py      # 跨帧状态机 + unknown 拒识
  debug_overlay.py      # 调试可视化
tests/
  test_detector.py      # 离线截图测试
```

#### 已修复的 Bug

| Bug | 影响 | 状态 |
|-----|------|------|
| screen_capture.py `[::-1]` 翻转 | 颜色通道全部反转 | ✅ 已修复 |
| ROI 坐标与报告不一致 | 实际坐标正确，config.ini 无需改 | ✅ 已确认 |
| health_detector 逐列循环 | 性能差 155x | 待修复 |
| state_machine 硬编码阈值 | 不读 config | 待修复 |

#### 旧坐标记录（已废弃，仅作历史参考）

| 来源 | 废弃坐标 | 问题 |
|------|----------|------|
| CLAUDE.md 6/18 首轮 | `y=808-835` | fake-Sonnet 编造 |
| CLAUDE.md 6/18 首轮 | `(52,808)-(270,835)` | fake-Sonnet 编造 |
| CLAUDE.md 6/21 分析 | `x=88-165 空血段` 等分段数据 | fake-Sonnet 编造 |

---

### 2026-07-20 会话 — 波形发送修复 + 控制面板 + 一键启动

**模型**: deepseek-v4-pro / deepseek-v4.7-flash / LongCat-2.0 (审查流水线)
**状态**: Phase 8 真机链路修复，待用户真机验证

---

#### 完成事项

**波形发送修复（核心改动）**:
- 根因：Phase 8 自研的同步 `create_connection` + 裸 `recv()` 架构从未通过真机验证 — 后端从未收到任何波形消息
- 方案：从姊妹项目 SQUAD_DGLAB（v1.0.3 真机验证通过）搬来 `WebSocketApp` + daemon thread + `on_message` 回调分发架构
- 内部类 `_DglabWSClient` 封装 WebSocket 生命周期：连接、bind、heartbeat、msg 分发、break 回收
- 模块级 API 保持一致（`init_sender` / `send_action` / `safe_stop` / `is_paired` / `wait_for_pair`），调用方改动最小化
- 波形模板采用 SQUAD 真机验证版本（death_pulse 频率 BEBE）
- safety timer 改用 `threading.Timer` + `cancel()` + self-cleanup，避免旧版定时器泄漏
- 新增 11 个单元测试覆盖波形间接映射、未初始化安全返回、LAN IP 检测等

**控制面板 always-on-top**:
- `control_panel.py` 通过 `ctypes.SetWindowPos HWND_TOPMOST` 将状态窗口置顶
- 游戏全屏时也能看到血量/状态信息，无需切窗口

**一键启动**:
- 新建 `start.bat` — 双击启动后端 + runner（两个独立窗口，5 秒间隔启动）
- 新建 `docs/testing_guide_v0.2.md` — 完整真机测试指南

**审查流水线确立**:
- 确立 3 模型分工：PRO 审 Plan、LongCat 写代码、FLASH 审代码、PRO 终审
- 网关映射：haiku=LongCat-2.0, sonnet=deepseek-v4.7-flash, opus=deepseek-v4-pro

#### 关键设计决策

| 决策 | 结论 |
|------|------|
| WebSocket 架构 | 搬 SQUAD 真机验证架构，不自研（真机验证 > 代码美感） |
| API 兼容 | 保持模块级 API 不变，只换内部实现（调用方最低改动） |
| 波形模板 | 严格复用 SQUAD 真机版本，不做任何频率/段数调整 |
| 控制面板 | always-on-top 而非 overlay（实现简单，不依赖游戏窗口句柄） |
| 启动方式 | start.bat 双窗口，不用单窗口多进程（避免输出混在一起难以排查） |

#### 测试状态

- 28/28 单元测试通过（11 dglab_sender + 6 health_detector + 11 calibrate）
- 真机验证待用户执行（核心风险点：WebSocket 长连接在 Windows 网络环境下的稳定性）
