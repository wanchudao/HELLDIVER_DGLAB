# DGHub 插件 1.0.7：多分辨率与死亡模式公开测试版

发布日期：2026-10-20。Windows x64，目标为 **DGHub 1.9.7／外部插件 SDK v1**；未实测的宿主版本不标为兼容通过。插件 ID 保持 `helldiver_dglab`。

[下载完整插件 ZIP](https://github.com/wanchudao/HELLDIVER_DGLAB/releases/download/dghub-v1.0.7/HELLDIVER_DGHub_1.0.7_MultiResolution_20261017.zip) · [完整指南](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/dghub_plugin_guide.md) · [测试与回退](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/resolution_testing_20261017.md)

## 相比公开版 1.0.5 新增

- 三种死亡反馈模式：定时反馈（默认 5 秒）、持续至复活、一次也不能死。挑战触发后复活不解除，第一次 F8 重置并暂停，第二次才开始。
- 极简直播模式：收起后只剩 32×32 逻辑像素展开按钮，单击只展开，拖动不误启动。它不接入弹幕／礼物，不隐藏 DGHub、宿主事件卡片或保证 OBS 不录到按钮。
- 在 1920×1080 外增加 2560×1440、3840×2160 实验性推算配置，各有 HUD 0.75–1.25、每档 0.05、共 11 档。
- 保留五类反馈、0–100% 残血门槛、独立 A/B 强度和上限、完整波形周期、短时强度租约、F8、灵动岛和截图检查。

**2K／4K 由 1080p 推算，未完成高分辨率实机验收，实时实验开关默认关闭。** 仅支持上述完整铺满所选屏幕的 16:9 尺寸；超宽屏、其他尺寸、窗口留边和 HDR 不在本轮范围。分辨率读取实际抓屏像素，不能只看游戏菜单。

## 安装与测试

1. 停用旧插件，先备份旧完整 ZIP／程序目录、对应宿主插件配置及外置会话。**同 ID 导入会替换程序目录**，不是并排安装；不要只换 EXE。
2. 在 DGHub 中导入本完整 ZIP，确认配置页为 1.0.7。插件入口需要宿主启动参数，不要直接双击 `helldiver_dglab.exe`。
3. 选择正确显示器及游戏相同 HUD 档位；实际为 1440p／4K 时主动打开“允许实验性 2K／4K 适配”。本插件待机强度必须为 0；“允许输出”只是保险，打开不会自动开始。
4. 启用后等待准备就绪，默认仍暂停。可先不连接设备，用 CHECK 检查原始截图，再进战场按 F8／浮窗开始识别；无设备显示 `VISION ONLY`，实际 A/B 请求为零且不发送波形。
5. 最后才连接 APP／设备，从低强度、低上限检查反馈与停止。接入或断开设备会暂停清空基线，需要重新按 F8 确认。不要和独立版同时输出。

HUD、实验开关、死亡模式、波形及强度修改只在停用／重新启用后应用；F8 暂停不切换配置。首次有效画面已为零不能凭空触发死亡；无 HUD 必须停止输出。普通按钮暂停不清挑战，结束会话清除挑战标记。

## 依赖与宿主边界

Python、OpenCV、NumPy、Pillow、MSS、WebSocket 客户端及主要运行库随包，普通用户无需安装开发环境；仍需自己的 DGHub，由宿主管理 APP 和设备。本包不包含或启动独立版官方 Node 后端，不修改防火墙或宿主全局设置。

强度稳定时仍每 0.10 秒续发 0.30 秒自动回退请求，可能产生宿主事件卡片和高强度音效。多插件强度按每通道最大值合成；本插件归零只清自己的强度层。SDK v1 没有设备波形缓存清空确认，租约不是设备端绝对停止上界。

## 验收范围与当前用户报告

最终成品有 33 组配置、实拍／派生图交叉拒识、最终 EXE 软件协议和隔离原生 DGHub 1.9.7 导入／配置验收记录。2026-10-18 对同一 ZIP 追加复测，22 个软件协议场景一次完整运行通过，2353 项源码回归、16 项后端及既有视频检查通过。

2026-10-20 作者反馈本机 DGHub 版与独立版测试无问题，但没有逐项分辨率、宿主、手机或设备明细。真实 1440p／4K 游戏布局、抓屏性能、所有 DPI／多显示器及实体设备仍未全面验收。开发环境曾出现原生异常诊断，追加复测未复现，根因仍未确认。

**部分用户报告的 1.0.5“上线后立即下线”原因尚未确诊，本版不宣称已解决所有这类报告。** 若仍失败，请保留本次日志，提供插件／DGHub 版本、实际屏幕分辨率和所选显示器编号，不要先删配置、重装或使用重置卡。

详见[本轮验收](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/resolution_acceptance_20261017.md)、[追加复测](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/postgame_retest_20261018.md)及[本机人工反馈说明](https://github.com/wanchudao/HELLDIVER_DGLAB/blob/ai-setup-test/docs/releases/user_acceptance_20261020.md)。

## 回退与下载校验

保留[旧版 1.0.5 发布页](https://github.com/wanchudao/HELLDIVER_DGLAB/releases/tag/dghub-v1.0.5)。回退时恢复完整旧包及升级前对应插件配置；1.0.5 不认识死亡模式／直播字段，1.0.6 不认识新增实验字段，不能只换旧 EXE。日志和历史会话继续保留。

`HELLDIVER_DGHub_1.0.7_MultiResolution_20261017.zip`

SHA-256：`f02e5e0c3a497f1a9d8177aca9ef3ec1ba06d731db7cf262b5cc8a7866c81ad7`

本次上传保持已测 ZIP、EXE、manifest 和运行库字节不变，包内构建日期及“本地交付”文字仍是当时记录。公开状态以本说明为准；GitHub 发布不代表 DGHub 官网市场已更新或审核通过，尚未核实官网最新下载。本轮不替用户修改安装、配置或防火墙，也不使用重置卡。
