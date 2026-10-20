# 独立版 v1.0.0 Beta 3：多分辨率公开测试版

发布日期：2026-10-20。Windows x64，使用随包官方 DG-LAB SOCKET 后端，**不需要 DGHub**。

[下载完整 ZIP](https://github.com/wanchudao/HELLDIVER_DGLAB/releases/download/main-v1.0.0-beta.3/HELLDIVER_Main_v1.0.0_Beta3_MultiResolution_20261017.zip) · [使用指南](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/main_official_guide.md) · [测试与回退](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/resolution_testing_20261017.md)

## 这次增加了什么

- 在 Beta 1 基础上加入三种死亡反馈模式：定时反馈（默认 5 秒）、持续至复活、一次也不能死。挑战触发后第一次 F8 重置并暂停，第二次才开始；普通鼠标暂停不清除挑战。
- 保留 1920×1080，增加 2560×1440、3840×2160 实验性推算适配。每种分辨率都有 HUD 0.75–1.25、每档 0.05、共 11 档。
- 保留遥遥领先风格 UI、五类反馈、0–100% 整数残血门槛、独立 A/B 强度与上限、截图检查、F8 和灵动岛。独立版没有 DGHub 的直播小按钮。

**1440p／4K 不是实拍校准。** 它们由 1080p 等比推算，实时使用前需要主动打开“允许实验性 2K／4K 适配”，默认关闭。只覆盖完整铺满所选屏幕的上述三种 16:9 尺寸；超宽屏、窗口留边、其他尺寸和 HDR 不在本轮支持范围。

## 怎么开始

1. 先停止并退出旧版，备份完整目录和个人设置。完整解压本版 ZIP，运行 `HELLDIVER_Main.exe`，保留 `_internal`；不要下载 GitHub 自动生成的 Source code 当作软件。
2. 选择游戏所在屏幕和相同 HUD 档位；实际抓屏为 1440p／4K 时再主动开启实验适配。分辨率来自实际抓屏，不是游戏菜单文字或 Windows 字体缩放。
3. 先做截图检查，再用“仅测试识别”在战场内按 F8。没有有效 HUD 时应显示 `NO HUD / HP --` 并停止反馈，不能把识别失败当作死亡。
4. 最后选择与手机同一局域网的电脑地址，连接官方 APP 并扫描新二维码；从低强度和低上限检查反馈、F8 暂停及停止全部。不要与 DGHub 插件同时输出。

设置只能在“停止全部”后修改，下次会话生效；F8 暂停不解锁。两个持续死亡模式也服从无 HUD、失帧和断线停止，不会为了维持惩罚继续使用旧帧。

## 依赖、升级与回退

Python、Node、图像库、官方后端和主要运行库随包，普通用户无需另装 Python、Node 或 OpenCV。需要 Windows x64；未完成所有干净 Windows／删减系统的兼容矩阵，不称为所有系统绝对零依赖。正常启动无需默认管理员权限，连接权限仅在用户明确确认后申请，不自动修改防火墙。

若要保留已放行的后端路径，先备份，在原位置配套更新 EXE 与整个 `_internal`，保留 `feedback_settings.ini`、`hud_settings.ini`、`ui_settings.ini`、sessions 和日志；确认指南显示的 `node.exe` 路径未改变。回退时恢复完整旧包及升级前对应设置，不能只换 EXE；旧版本不认识新的 HUD schema 或死亡字段。

[旧版 Beta 1 发布页](https://github.com/wanchudao/HELLDIVER_DGLAB/releases/tag/main-v1.0.0-beta.1) 继续保留。

## 已验证与未验证

本 ZIP 是 2026-10-17 的已验收完整包，2026-10-18 对同一字节成品追加复测：2353 项 Python 回归、16 项后端、最终 EXE 的 33 组配置、软件协议、停止和两段既有离线视频检查通过。2026-10-20 作者反馈本机两版测试无问题；未提供分辨率、宿主或实体设备逐项数据，不把它扩写成完整实机矩阵通过。

真实 1440p／4K HUD、抓屏性能、全部 DPI／物理多显示器、手机和实体设备仍未完成全面验收。1080p 的 1.05–1.20 也仍是插值档。开发环境曾出现原生异常诊断，追加复测未复现，但根因未确认，不宣称已修复。自动回归、软件模拟 APP 和桌面窗口测试不能代替这些真实场景。

详见[本轮验收](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/resolution_acceptance_20261017.md)、[追加复测](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/postgame_retest_20261018.md)及[本机人工反馈说明](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/releases/user_acceptance_20261020.md)。

## 下载校验

`HELLDIVER_Main_v1.0.0_Beta3_MultiResolution_20261017.zip`

SHA-256：`2c4c5b686ee46784a2dc6c8bcdf2c251d380de968db88c69a3960f9405416df9`

本次上传不重新编译或改写 ZIP，包内构建日期及“本地交付”记录保留当时语境；公开发布状态以本说明为准。校验值只证明下载字节一致，不是代码签名或绝对安全保证。运行库许可见随包 LICENSES 和 THIRD_PARTY_NOTICES.md。
