# DGHub 共享核心离线验证版 — 0.5.0-offline

> 本页是旧离线入口的历史记录，下面的限制只描述旧入口，不是当前 1.0.7 的使用说明。当前完整插件见[根 README](../README.md)、[插件指南](../docs/dghub_plugin_guide.md)和[构建说明](../docs/build_dghub.md)。

2026-09-17 新增独立 SDK v1 协议开发预览：五类宿主预设配置、A/B 百分比映射、协议状态机与串行写入测试。早期开发／验收原记录保留在开发工作区；当前公开构建流程见[构建说明](../docs/build_dghub.md)。它没有解除下方 0.5.0 入口的注册禁用，也不是可直接导入使用的正式宿主版；无需重新收集 HUD 截图，下一步需要核对 RS 指南／宿主停止接口。

此版本只做本地 Mock，不连接、注册 DGHub，不输出设备协议。没有真机可用声明；请勿把旧版 ZIP/EXE 当成当前源码的构建产物。

`main.py` → `offline_core.py` → 项目根目录的 `src.vision_pipeline`、`src.trigger`、`src.feedback_mixer`。没有第二套血量或反馈算法；仅模拟 A 通道，B 预留。

## 本地测试

在项目根目录执行（不需要启动 DGHub）：

```powershell
.\.venv\Scripts\python.exe dghub_plugin\main.py --self-check
.\.venv\Scripts\python.exe -O dghub_plugin\main.py --self-check
.\.venv\Scripts\python.exe -m pytest tests\test_plugin_offline.py dghub_plugin\tests -q
```

自检只用合成血量输入，验证 0.2 秒累计伤害、低于 75% 持续反馈、治疗确认和持续舒缓、每生命一次死亡、复活治疗、无效帧归零、暂停后迟到帧拒绝、关闭不可复活。自检不抓屏、不创建网络连接；普通和 `-O` 模式执行相同检查，任何检查失败都返回非零退出码。

需要实时看自己的屏幕时，可由操作者显式运行：

```powershell
.\.venv\Scripts\python.exe dghub_plugin\main.py --offline
```

它打开主版本同一个安全面板，启动时暂停，F8 或按钮启停；始终强制 Mock。可选 `--config 路径`、`--preview`、`--fps 30`。修改配置的 `sender_mode`、通道或旧 `[dghub]` 字段也不能启用设备输出。截图/MP4 的纯文件测试请使用主版本离线测试器，而不是此实时屏幕面板。

不带参数运行，或由 DGHub 启动，会说明宿主不满足安全前提并以退出码 `2` 退出；不会进行 hello 注册。若已构建 EXE，可用同样的 `--self-check`、`--offline` 参数。`--self-check` 的输出包含共享核心路径和硬件禁用状态。

## 生命周期与测试替代

`MockVisionSession` 在抓屏前取得代次票据。暂停、再次启用、抓屏失败和关闭会使旧票据失效；耗时检测完成后也必须再次校验。无效视觉帧立即清除 Mock 反馈和血量基线。关闭/宿主 stop 是永久终态，迟到 `running=true` 不能重启旧实例。

历史 `tests/test_freeze_recovery.py` 测过已经删除的独立 DXGI 和 `_vision_is_stalled`。现在替换为主版本有界 `BufferedScreenCapture` 和共享会话回归：原生抓屏卡住时主线程仍能返回并关闭；迟到帧不进入新一代会话。完整主抓屏单元测试仍在根目录 `tests/test_capture_worker.py`。

`src/` 子目录仅保留为旧版源码历史，当前入口及 freezer 不使用它。旧可执行文件和 ZIP 同样不会自动更新。构建会核对所有 `src.*` 的来源，并拒绝包含设备发送器/WebSocket/DXGI 模块。

## 宿主仍需修复

SDK v1 没有本项目可验证的租约取消/零基线生命周期。已审计宿主可能在暂停清零或注销后由迟到租约恢复非零基线；仅改用 rollback 或发送 `set_strength=0` 不构成修复。详见根目录 `docs/dghub_host_safety_audit.md`。

拿到匹配的宿主源码或经验证的新协议后，才可实现和测试取消/等待租约、作废实例代次、清理待发消息与强度层的真实适配器。当前没有任何配置项可解除注册与硬件输出禁用。
