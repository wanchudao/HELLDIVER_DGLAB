# HELLDIVER_DGLAB Phase 1 执行计划

> 创建：2026-07-18
> 用途：基于当前仓库、已有设计文档和源码现状，整理一份可直接执行的 Phase 1 落地计划。
> 说明：本文件聚焦“怎么做”，不替代 `phase1_plan.md` 的方案设计和 `phase1_algorithm_review.md` 的风险审查。

---

## 1. 目标

- 在 `1920x1080` 固定分辨率下，完成《绝地潜兵 2》玩家血条视觉识别 `Phase 1 MVP`
- 让视觉层稳定输出以下状态：
  - `full_health`
  - `injured`
  - `healing`
  - `death`
  - `unknown`
- 保证 `unknown` 为严格拒识态，不触发任何设备事件
- 在视觉层稳定后，再接入实时运行入口与后续 `trigger / DGLAB` 链路

## 2. 问题定义

- 当前仓库已经具备视觉主链路，但关键问题不是“没有实现”，而是“核心识别不可靠”
- `ROI 定位`、`滤镜检测`、`状态机骨架` 已存在
- `血量检测` 是当前主阻塞点，在红屏和治疗场景下大量误读为 `100%`
- `离线测试基线` 也不干净，当前统计把很多裁剪图混入正式样本，导致调参反馈失真

## 3. 范围

### 3.1 本计划包含

- 离线评估基线整理
- 血量检测修复
- 状态机配置化
- 实时入口打通
- 后续 `trigger / DGLAB` 集成预留

### 3.2 本计划暂不包含

- YOLO
- GUI
- 多分辨率自适应
- 真机强度调优
- 大规模重构项目目录

## 4. 已知事实

- 权威血条坐标来源是 `docs/healthbar_coordinates_report.md`
- 当前视觉主链路入口是 `tests/test_detector.py`
- 当前主要模块为：
  - `src/roi_locator.py`
  - `src/health_detector.py`
  - `src/effect_detector.py`
  - `src/state_machine.py`
  - `src/screen_capture.py`
- 核心配置集中在 `config.ini`
- 当前交接与问题总结最完整的文档是 `docs/handover.md`

## 5. 核心判断

- 项目当前最优路径不是新增模块，而是先修正“评估口径”和“血量检测公式”
- 如果不先清理离线评估，再怎么调 `HealthDetector` 都没有可靠反馈
- 如果 `HealthDetector` 不先修好，后续实时运行、HTTP trigger、DGLAB 联动都会建立在错误输入上

## 6. 总体实施策略

- 采用“先离线、后实时；先视觉、后设备；先正确、后性能”的顺序推进
- 保持现有模块边界不大改：
  - `roi_locator` 继续负责裁剪与锚点
  - `health_detector` 只负责血量
  - `effect_detector` 只负责滤镜与死亡辅助
  - `state_machine` 负责状态与事件
- 所有阈值尽量回收到 `config.ini`，避免调参改代码
- 每完成一阶段都要有明确可验证输出，不做盲改

## 7. 阶段计划

### Phase 0：计划确认

**目标**

- 锁定范围、优先级和验收口径

**输出**

- 当前执行计划文档
- 明确执行顺序
- 明确哪些文件先改、哪些后改

**完成标准**

- 用户确认可以按此计划执行

### Phase 1：离线评估基线修正

**目标**

- 建立可信的离线测试反馈

**主要文件**

- `tests/test_detector.py`

**改动内容**

- 排除 `crop / zoom / thumb / _crop_ / _zoom` 等派生图
- 仅统计原始全屏截图
- 增加“目录名 -> 期望状态”映射
- 输出每类样本的：
  - 总数
  - valid 数
  - 命中数
  - 误判样本清单
- 区分“识别错误”和“unknown 拒识”

**设计原则**

- 不影响主检测流程
- 先改评估，不改算法

**验收标准**

- 离线报告不再混入裁剪图
- 能明确看到 `full_health / injured / death / healing` 四类的真实表现
- 每次调参后可直接比较前后结果

### Phase 2：血量检测修复

**目标**

- 解决 `HealthDetector` 在红屏/治疗场景下普遍误读为 `100%` 的 P0 问题

**主要文件**

- `src/health_detector.py`
- `config.ini`

**问题根因**

- 当前 `filled_score` 中 `redness` 是正向特征
- 在红屏污染下，空槽可能比填充区更红，导致空槽高分

**改动方向**

- 保持 `luma` 作为主特征
- 将 `redness` 改为降权，或变成负向/条件特征
- 提高 `texture / gradient / contrast` 对空槽的惩罚能力
- 必要时增加“列相对亮度”或“对最亮列的相对差值”特征
- 对列分数做轻量 1D 平滑，再找边界
- 让 `_find_boundary()` 真正使用 `cap_ignore_left/right`

**分两步做**

- 第一步先修正确率
- 第二步再做向量化性能优化

**验收标准**

- `full_health` 基本读到 `95%-100%`
- `injured` 明显低于 `75%`
- `death` 接近 `0%`
- `healing` 低血图片不能继续普遍读成 `100%`
- `death_score` 能在低血 + 极红条件下更稳定触发

### Phase 3：状态机配置化与规则收口

**目标**

- 让状态判定逻辑完全受配置驱动，而不是硬编码

**主要文件**

- `src/state_machine.py`
- `config.ini`

**当前问题**

- `unknown`、`death`、`healing`、`injured` 的关键阈值在代码里硬编码

**改动内容**

- 从 `config.ini` 读取：
  - `min_confidence`
  - `min_anchor_score`
  - `death_score_threshold`
  - `healing_cyan_threshold`
  - `healing_health_max`
  - `injured_health_threshold`
- 保持 `unknown` 为最高优先级拦截
- 确保 `invalid / low_confidence / low_anchor` 时直接返回 `unknown`

**验收标准**

- 修改阈值不需要改 Python 代码
- `unknown` 逻辑仍严格生效
- 离线结果对配置变化有可预期响应

### Phase 4：配置统一与参数清洗

**目标**

- 清理配置和文档不一致问题，降低后续维护成本

**主要文件**

- `config.ini`
- `docs/handover.md` 或后续补充文档

**改动内容**

- 统一 `inner_x1/x2` 与权威文档的 1px 偏差
- 统一 `bar_min_x/bar_max_x` 的最终含义
- 确认 `dead_zone_low/high`、`near_zero`、`death_score_threshold` 的层级关系
- 补充状态机相关阈值说明

**验收标准**

- 文档、配置、代码三者一致
- 不再存在“文档有字段但代码未读取”的情况

### Phase 5：性能优化

**目标**

- 在不牺牲稳定性的前提下，把单帧检测时间压到目标范围内

**主要文件**

- `src/health_detector.py`

**改动内容**

- 把逐列 `for` 循环改成向量化：
  - `np.percentile(..., axis=0)`
  - `np.mean(..., axis=0)`
- 尽量避免重复色彩空间转换和冗余数组构造

**验收标准**

- 单帧检测时间明显下降
- 与修复前的正确率保持一致或更好

**注意**

- 该阶段优先级低于正确率修复

### Phase 6：实时入口落地

**目标**

- 把现有离线流程串成实时运行链路

**主要文件**

- `src/screen_capture.py`
- 新增 `src/realtime_runner.py` 或 `src/main_realtime.py`

**改动内容**

- 建立循环：
  - `ScreenCapture.grab()`
  - `RoiLocator.locate()`
  - `HealthDetector.detect()`
  - `EffectDetector.detect()`
  - `StateMachine.update()`
- 输出实时日志
- 可选接入 debug overlay
- 增加分辨率检查，不是 `1920x1080` 时直接警告或拒绝

**验收标准**

- 能持续跑
- 运行中不崩溃
- 连续帧状态切换符合预期
- `unknown` 仍然安全

### Phase 7：Trigger 接口接入

**目标**

- 让视觉层可以通过标准接口把事件送出去

**主要文件**

- 新增 `trigger_server.py` 或独立服务层

**改动内容**

- 设计简单 HTTP 接口，例如 `/trigger`
- 只传递状态与事件，不把视觉逻辑塞进接口层
- 增加事件限流、防抖和 safe stop 预留

**验收标准**

- 视觉层可稳定发出事件
- 接口层不影响视觉层检测速度

### Phase 8：DGLAB SOCKET v2 集成

**目标**

- 接通设备链路

**依赖**

- 参考姊妹项目路径和协议说明，见 `docs/handover.md`

**改动内容**

- 复用现有 SOCKET v2 协议层
- 绑定 `injury_pulse / healing / death / low_health`
- 全部优先走 A 通道
- 加入 safe stop 和异常清零逻辑

**验收标准**

- 事件能映射成协议消息
- 设备侧有安全兜底
- `unknown` 永不触发设备

## 8. 文件级改动计划

- `tests/test_detector.py`
  - 样本过滤
  - 评估输出增强
  - 目录到标签映射
  - 离线准确率统计
- `src/health_detector.py`
  - 重做 `filled_score`
  - 增加边界前平滑
  - 使用 `cap_ignore`
  - 向量化特征提取
- `src/state_machine.py`
  - 读取配置阈值
  - 整理状态判定顺序
  - 保持安全拒识
- `config.ini`
  - 统一坐标
  - 增补判定阈值
  - 保证所有状态机关键字段可配置
- `src/screen_capture.py`
  - 基本保持不动
  - 只在实时阶段补分辨率与异常处理
- `src/realtime_runner.py` 或 `src/main_realtime.py`
  - 新增实时入口
- `trigger_server.py`
  - 新增接口层
- 设备协议层
  - 尽量复用，不从零造协议

## 9. 里程碑

### 里程碑 1：离线基线可信

- 不再混入裁剪图
- 每类样本统计清晰

### 里程碑 2：血量检测可用

- 四类主状态离线结果显著改善

### 里程碑 3：状态机可调

- 调参只改配置

### 里程碑 4：实时入口跑通

- 连续帧稳定输出

### 里程碑 5：事件接口可用

- 能发 trigger

### 里程碑 6：设备链路联通

- 可安全驱动 DGLAB

## 10. 验收标准

- 识别正确率目标沿用现有设计文档：
  - 满血识别准确率 `>= 95%`
  - 受伤识别准确率 `>= 90%`
  - 死亡识别准确率 `>= 99%`
  - 治疗识别准确率 `>= 80%`
- 工程目标：
  - `unknown` 不触发设备
  - 阈值可配置
  - 主流程模块职责清晰
  - 单帧检测耗时朝 `< 50ms` 逼近
- 交付目标：
  - 离线检测稳定
  - 实时入口可跑
  - trigger 层可接
  - 设备联动可扩展

## 11. 风险与对策

- 风险：红屏污染仍导致血量误判
  - 对策：以亮度为主、纹理为辅、红度降权，不靠单一颜色阈值
- 风险：HUD 消失帧拉低稳定性
  - 对策：继续用锚点校验 + `unknown`
- 风险：测试样本不足
  - 对策：先用现有样本建立基线，再逐步补血量档位样本
- 风险：过早接设备，放大误判
  - 对策：先离线、后实时、最后设备
- 风险：调参成本过高
  - 对策：统一阈值到 `config.ini`

## 12. 执行顺序

- 第 1 步：修 `tests/test_detector.py` 的离线评估口径
- 第 2 步：修 `src/health_detector.py` 的血量计算
- 第 3 步：修 `src/state_machine.py` 与 `config.ini` 的配置化
- 第 4 步：做实时入口
- 第 5 步：做 trigger 层
- 第 6 步：接 DGLAB

## 13. 下一步建议

- 如果按此计划执行，最合理的第一项实际工作是先改 `tests/test_detector.py`
- 在这一步完成前，不建议直接调 `health_detector.py`，否则评估反馈仍然会被脏样本污染

