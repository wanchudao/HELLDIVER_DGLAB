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

回归应覆盖门槛、11 档 HUD、双通道、F8／浮窗、完整波形周期、短租约和停止。原生 DGHub 测试需要自己的宿主安装及隔离环境，不能用软件服务代替。仓库不包含用户宿主配置、访问令牌、会话、完整原生报告或 MP4；缺失的外部测试输入需另行准备。

tools/prepare_dghub_public_release.py 为已验收 ZIP 追加公开文档和运行库许可，脱敏报告路径，复核 manifest、EXE 和每个运行文件字节一致，生成下载校验值。公开打包需要对应构建环境和有效原验收文件；它不会启动插件、连接设备或替代功能验收。
