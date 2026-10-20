# DGHub 插件源码与构建

普通用户从 Release 下载完整插件 ZIP，由 DGHub 导入。以下步骤用于开发者；GitHub 的 Source code ZIP 不能直接导入运行。

## 本次构建环境

Windows x64、Python 3.14.7、PyInstaller 6.22.2、websockets 17.1；NumPy 2.5.2、OpenCV Python 5.0.0.93、Pillow 12.3.0、MSS 10.2.0。主入口为 tools/dghub_live_entry.py，冻结配置为 dghub_live.spec，manifest 由 dghub_plugin/sdk_config.py 生成。SDK 协议见仓库 PLUGIN_DEVELOPMENT.md。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.venv\Scripts\python.exe -m pip install websockets==17.1 pyinstaller==6.22.2 pytest
.venv\Scripts\python.exe -m PyInstaller dghub_live.spec --noconfirm --distpath release/local_dghub_build --workpath build/local_dghub_freeze
```

这会创建完整运行目录，并不代表正式发布验收通过。正常入口需要 DGHub 提供的环境变量和 SDK 会话；开发调试及离线检查参数见入口帮助。成品由宿主启动，直接双击 EXE 会因缺少宿主参数拒绝运行。

## 复核与封包

tools/dghub_package_candidate.py 从完整运行目录生成 ZIP。正式封包需要绑定同一 EXE 的冻结一致性、最终 EXE 协议和原生 SDK 策略报告；只提供别的版本报告会被拒绝。可查看该工具的 --help 和插件使用指南中的测试范围。

1.0.7 另附 --resolution-report，并用最终 EXE 执行 tools/dghub_frozen_protocol_acceptance.py --resolution-cases，覆盖 1440p／4K 的识别、输出与停止。原生导入检查需核对实验开关保存、待生效及重新启用后应用；没有高分辨率显示器时，此项原生启动仍只代表实际桌面尺寸，不代表高分辨率实机通过。最终交付用 tools/finalize_death_live_package.py 再核对高分辨率附件及运行文件，保留推算标识。

回归应覆盖门槛、11 档 HUD、双通道、F8／浮窗、完整波形周期、短租约和停止。原生 DGHub 测试需要自己的宿主安装及隔离环境，不能用软件服务代替。仓库不包含用户宿主配置、访问令牌、会话、完整原生报告或 MP4；缺失的外部测试输入需另行准备。

tools/prepare_dghub_public_release.py 是硬编码 1.0.5 的历史公开包工具，不可直接用于 1.0.7。1.0.7 使用前述 tools/finalize_death_live_package.py 流程，配合对应版本的最终验收文件，脱敏记录并核对运行文件字节不变。已完成此流程的最终 ZIP 可原样上传，公开说明和校验值作为外部附件追加，不必重新封包；任何发布工具都不替代功能验收。
