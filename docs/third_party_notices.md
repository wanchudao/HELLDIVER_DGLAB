# 第三方来源与运行库说明

本次发布为 HELLDIVER DGLAB 独立主版本。各组件分别保留自身许可；本说明不为第三方内容增加授权，不将整个软件包统一许可为 MIT。

## DG-LAB 后端及波形

来源：[dungeonlab-open/dglab-websocket-simple](https://github.com/dungeonlab-open/dglab-websocket-simple)。内置 V3 波形来源、文件指纹及迁移方式见 [waveform_sources.md](waveform_sources.md)。后端适配位于 official_v2/socket/v2/backend/src；本项目保留来源及上游的“未经授权，请勿将本内容用于任何商业用途”声明。无需运行时联网下载模板。

## 软件包运行库

| 组件 | 本次版本 | 说明 |
|---|---|---|
| Python | 3.14.7 | 许可文本包含在 LICENSES/Python |
| Node.js | 24.19.0 | 使用对应版本上游完整 LICENSE，含内嵌第三方声明；LICENSES/Node |
| NumPy | 2.5.2 | 包内 numpy dist-info 的 licenses 保留 |
| OpenCV Python | 5.0.0.93 | 自身及第三方许可文本在 LICENSES/opencv-python |
| Pillow | 12.3.0 | LICENSES/Pillow |
| MSS | 10.2.0 | LICENSES/mss |
| websocket-client | 1.9.1 | LICENSES/websocket-client |
| qrcode | 8.2 | LICENSES/qrcode |
| Tcl／Tk | 随 Python 构建 | LICENSES/tcl、LICENSES/tk 保留许可条款 |
| PyInstaller | 6.22.2 | 构建工具与引导程序相关说明，含分发例外；LICENSES/PyInstaller |

Node 后端依赖 ws 8.21.0、uuid 11.1.1、winston 3.17.0、dotenv 16.6.1 及其传递依赖；许可文本保留在 _internal/backend/node_modules 的对应组件目录。微软运行库和 Windows 系统组件依各自许可使用，不受本项目另行授权。

Node 许可采用 https://raw.githubusercontent.com/nodejs/node/v24.19.0/LICENSE。Tcl／Tk 许可采用各自上游仓库的 license.terms。其他文本取自本轮实际构建环境的发行包，而不是重新给组件选择许可证。

## 游戏相关内容

游戏名称、商标和测试截图的相关权利归各自权利人。本项目不宣称与游戏官方有合作或授权。新版界面装饰由 Tk／OpenCV 代码绘制，不包含直接复制的游戏徽章或债券封面。

如需再分发或商业使用，应分别核对相应组件和内容的许可。公开源码不能替代授权。
