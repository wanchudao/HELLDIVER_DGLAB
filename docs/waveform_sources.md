# 内置波形来源

官方 16 组 V3 示例来自 dungeonlab-open/dglab-websocket-simple：
https://github.com/dungeonlab-open/dglab-websocket-simple/blob/main/socket/DG_WAVES_V2_V3_simple.js

取用字段为 name 与 expectedV3，不执行 raw 分享字符串，也不修改数据包内容。
2026-09-14 获取的原文件 UTF-8 文本 SHA256：BFD15D647E32C63864E0053995C8DFEB925D3A63CB4FDE77BC2944A1ABF350ED。
本程序中官方呼吸沿用 weak_pulse 标识，与旧版字节相同；其余 15 组新增选择。
另两组 heal_pulse / death_pulse 保留本项目既有治疗、死亡模板。

原仓库：https://github.com/dungeonlab-open/dglab-websocket-simple
原作者为 DG-LAB / dungeonlab-open。原仓库声明未经授权不得商业使用其内容；本项目保留此来源与限制，不赋予额外许可。
模板名称只是原作者命名，不代表对实际体感或特定强度的承诺。

所有模板在导入模块时检查 HEX、幅度、长度及带 UUID 的协议消息长度；软件运行时不会从互联网获取或执行波形代码。
