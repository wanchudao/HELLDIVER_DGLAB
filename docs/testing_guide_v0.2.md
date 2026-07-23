# HELLDIVER_DGLAB V0.2 真机测试完整指南

> 适用版本: V0.2 (WebSocketApp 架构重写 + 控制面板置顶)
> 日期: 2026-07-20

---

## 前置条件检查

### 1. Python 依赖

```bash
cd e:\HELLDIVER_DGLAB
pip install -r requirements.txt
pip install qrcode        # QR 码弹窗（可选，没有也能用）
```

### 2. Node.js 后端依赖

```bash
cd e:\HELLDIVER_DGLAB\official_v2\socket\v2\backend
npm install
```

### 3. DG-LAB APP

手机上已安装 DG-LAB APP，设备已绑定到 APP。

### 4. 游戏设置

- 分辨率: 1920x1080
- 显示模式: **无边框窗口**（不是独占全屏！独占全屏下控制面板置顶无效）
- HUD Scale: 100%

### 5. 确认配置

打开 `e:\HELLDIVER_DGLAB\config.ini`，检查：

```ini
[trigger]
sender_mode = dglab          # ← 必须是 dglab，不是 mock

[sender]
ws_host = 127.0.0.1          # ← 后端在本机运行
ws_port = 9999
```

---

## 测试步骤（按顺序执行）

### 步骤 1: 启动后端

打开**第一个终端**：

```bash
cd e:\HELLDIVER_DGLAB\official_v2\socket\v2\backend
npm start
```

看到以下输出表示启动成功：
```
info: WebSocket 服务器启动，监听端口：9999
info: 服务器启动完成
```

**保持这个终端开着**，不要关。

---

### 步骤 2: 启动 runner

打开**第二个终端**：

```bash
cd e:\HELLDIVER_DGLAB
python src/runner_real.py --enable-trigger
```

如果 QR 码库已安装，会自动弹出二维码图片。没装的话终端会打印链接，手动复制到浏览器打开。

**此时终端会显示：**
```
==================================================
  HELLDIVER_DGLAB V0.1 — 实时检测
==================================================
  ...
[runner_real] 按 P 开始检测 ... Ctrl+C 或 Q 退出
```

控制面板小窗会弹出，显示 `[PAUSED] Ship mode`。

---

### 步骤 3: APP 扫码配对

1. 打开 DG-LAB APP
2. 扫描终端弹出的二维码
3. 终端应显示：`[DGLAB] OK 配对成功! APP 已连接设备。`
4. 后端终端应显示：`配对成功：xxx <-> yyy`

**如果 2 分钟内没扫码**，会显示 "配对超时"。重新 `python src/runner_real.py --enable-trigger` 即可。

**控制面板此时应置顶在屏幕上** — 这就是 always-on-top 功能。

---

### 步骤 4: 进入游戏

1. 启动 Helldivers 2
2. 进入任意任务（不要在飞船上，飞船的经验条不是血条）
3. **按 P 键** 开始检测

控制面板从 `[PAUSED]` 变为 `[RUNNING]`，显示实时 HP 和 FPS。

---

### 步骤 5: 验证设备反馈

#### 5a. 受伤测试

找个敌人挨一枪。预期：
- 控制面板 HP 下降
- 后端终端显示：`[xxx] 波形消息已发送：通道A, 次数2, 时长2s`
- 设备有电击感（强度 20，2 秒）

#### 5b. 死亡测试

被敌人打死。预期：
- 控制面板 HP 降到 0%
- 后端终端显示：`[xxx] 波形消息已发送：通道A, 次数5, 时长5s`
- 设备有强力电击（强度 40，5 秒）

#### 5c. 治疗测试

使用治疗针。预期：
- 控制面板 HP 回升
- 设备有舒缓脉冲（强度 15，2 秒）

#### 5d. 残血测试

血量降到 50% 以下且没有受击/治疗/死亡时：
- 每 3 秒一次低频持续刺激（强度 10）

---

### 步骤 6: 安全退出

**按 Ctrl+C** 退出。预期：
- 终端显示 `dglab sender: safe_stop done`
- 设备立即停止所有反馈
- 两个终端窗口正常关闭

按 Q 键或 ESC 同样可以退出。

---

## 常见问题

| 症状 | 可能原因 | 解决方法 |
|------|----------|----------|
| 后端启动报错 "port 9999 already in use" | 上次的后端没关 | 关掉残留的 Node 进程再重试 |
| APP 扫码后无反应 | LAN IP 不对 | 检查终端打印的 IP 是否和手机在同一网段 |
| 配对成功但设备没电 | 强度太低或设备没开机 | 先用 `python tools/test_pairing.py` 单独测试，会发 15→20→40 递增脉冲 |
| 控制面板不置顶 | 开了独占全屏 | 改为无边框窗口模式 |
| 控制面板一直显示 unknown | 没进任务（飞船经验条） | 进任务再按 P |
| 非 1080p 分辨率 | 坐标偏移 | 先跑一次 `python tools/calibrate.py` 校准 |
| HP 读数不准确 | 校准问题或场景特殊 | 在飞船满血状态跑一次校准 |

---

## 快速诊断：test_pairing.py

如果 runner 模式下波形不送达，先用独立脚本隔离测试：

```bash
cd e:\HELLDIVER_DGLAB
python tools/test_pairing.py
```

流程：
1. 连后端 → 弹 QR 码
2. 等待 APP 扫码配对
3. 自动发送 3 个测试脉冲（heal→injury→death），每个之间有 3 秒倒计时警告
4. 安全退出

如果 test_pairing.py 设备有反馈但 runner 没有 → 视觉管线问题（没检测到事件）
如果 test_pairing.py 设备也没反馈 → 后端或网络问题

---

## 后端日志解读

**正常日志：**
```
[clientId] 波形消息已发送：通道A, 次数2, 时长2s    ← 波形在排队发送
[clientId-A] 消息发送中，剩余次数：1                 ← 定时器在发
[clientId-A] 消息发送完毕                            ← 波形发送完成
```

**异常日志：**
```
配对关系无效 (402)   ← clientId/targetId 不匹配，APP 可能断连了
channel is empty (406) ← 波形消息没带 channel 字段
清除了 0 个定时器      ← 连接关闭时没有活跃的波形（说明之前没成功发过）
```
