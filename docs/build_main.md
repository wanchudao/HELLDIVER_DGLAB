# 独立版源码与构建

普通用户下载 Releases 中的完整软件包，无需执行以下步骤。源码构建需要自己的 Python、Node 和开发依赖；“运行库随包”针对 Release，不针对 Source code 压缩包。

本次实际构建使用 Windows x64、Python 3.14.7、Node 24.19.0、PyInstaller 6.22.2；主要 Python 包版本在 requirements-desktop.txt，生产后端依赖锁在 packaging/backend/package-lock.json。其他版本没有在本次构建矩阵中验证。

## 开发环境

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install pytest pyinstaller==6.22.2
.venv\Scripts\python.exe tools/desktop_app.py
```

requirements.txt 包含 DGHub 共享回归需要的依赖；独立版打包会排除 dxcam 和 websockets，不把 DGHub 插件放入最终包。代码启动仍默认暂停，硬件连接需要用户明确选择。

## 生产后端准备及构建

```powershell
npm ci --omit=dev --prefix packaging/backend
New-Item -ItemType Directory -Force build/main_backend_minimal
Copy-Item official_v2/socket/v2/backend/src build/main_backend_minimal/src -Recurse
Copy-Item packaging/backend/node_modules build/main_backend_minimal/node_modules -Recurse
.venv\Scripts\python.exe -m PyInstaller main_desktop.spec --noconfirm --distpath release/local_build --workpath build/local_freeze
```

以上准备面向空的构建目录，不要反复合并不同版本的 src 或 node_modules。生产程序使用最小后端依赖清单，不要求用户电脑已有 Node；spec 将构建机的 node.exe 一并复制入软件包。

## 回归

```powershell
.venv\Scripts\python.exe -m pytest -q
npm ci --prefix official_v2/socket/v2/backend
npm test --prefix official_v2/socket/v2/backend -- --runInBand
```

测试默认阻断真实设备连接。仓库包含 HUD 照片和已有原始图片，MP4 文件、私人配置及原生 DGHub 安装不上传；缺少原 MP4 时对应测试会明确跳过，不代表视频通过。2023 项全部通过是发布机具有原始素材时的记录，不承诺干净克隆每台机器具有相同测试数。

tools/package_main_release.py 需要对应最终 EXE 的完成验收报告，并校验冻结代码与源码，不能用别的版本报告代替。tools/prepare_public_release.py 在不改变已测 EXE／Node 的前提下补充许可证与脱敏公开报告。仓库不会附带用户的本地发布报告；要重新发布应自行重跑最终 EXE 验收，不能只调用打包命令冒充验证。
