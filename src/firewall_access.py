"""Opt-in, path-scoped Windows Firewall management for the frozen backend.

Ordinary startup never calls a mutating command. The elevated helper derives
its own backend path; it accepts neither a program path nor an output file.
"""
import base64
import ctypes
from ctypes import wintypes as wt
import hashlib
import json
import ntpath
import os
import re
from pathlib import Path
import subprocess
import sys

GROUP = "HELLDIVER Main - managed LAN access"
MARKER = "HELLDIVER_Main_LAN_v1; Private TCP LocalSubnet; app-owned rule"
OPERATIONS = ("configure", "remove")


class FirewallError(RuntimeError):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class AuthorizationCancelled(FirewallError):
    pass


def bundled_node():
    from src.desktop_runtime import backend_paths
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise FirewallError("一键授权仅用于 Windows 完整 EXE 包；源码环境不修改系统 Node 的权限。", 50)
    base = Path(sys.executable).resolve().parent
    expected = base / "_internal" / "runtime" / "node.exe"
    node, _ = backend_paths()
    if (not expected.is_file() or node.resolve() != expected.resolve()
            or expected.resolve().parent != base / "_internal" / "runtime"):
        raise FirewallError("随包后端路径无效，请完整解压软件；不会放行其他 Node。", 50)
    return expected.resolve()


def policy(node):
    program = str(Path(node).resolve())
    if any(char in program for char in ("\0", "\r", "\n")):
        raise ValueError("Invalid backend path")
    digest = hashlib.sha256(ntpath.normcase(program).encode("utf-8")).hexdigest()[:20]
    return {"name": "HELLDIVER_Main_LAN_" + digest, "program": program,
            "group": GROUP, "description": MARKER,
            "profile": "Private", "protocol": "TCP", "remote_address": "LocalSubnet"}


def _quote(value):
    return "'" + value.replace("'", "''") + "'"


def query_script(node):
    p = policy(node)
    template = """$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Import-Module NetSecurity -ErrorAction Stop
$name=NAME
$program=PROGRAM
$group=GROUP
$marker=MARKER
function Read-MainRule($store) {
    try { Get-NetFirewallRule -Name $name -PolicyStore $store -ErrorAction Stop }
    catch { if ($_.CategoryInfo.Category -ne 'ObjectNotFound') { throw } }
}
function Test-MainScope($r) {
    $a=@($r | Get-NetFirewallApplicationFilter)
    $port=@($r | Get-NetFirewallPortFilter)
    $address=@($r | Get-NetFirewallAddressFilter)
    return ($r.Group -ceq $group -and $r.Description -ceq $marker -and
        $a.Count -eq 1 -and $a[0].Program -ieq $program -and [string]$r.Enabled -eq 'True' -and
        [string]$r.Profile -eq 'Private' -and [string]$r.Direction -eq 'Inbound' -and
        [string]$r.Action -eq 'Allow' -and [string]$r.EdgeTraversalPolicy -eq 'Block' -and
        $port.Count -eq 1 -and [string]$port[0].Protocol -in @('TCP','6') -and
        @($port[0].LocalPort).Count -eq 1 -and [string]$port[0].LocalPort -eq 'Any' -and
        $address.Count -eq 1 -and @($address[0].RemoteAddress).Count -eq 1 -and
        [string]$address[0].RemoteAddress -eq 'LocalSubnet')
}
$rules=@(Read-MainRule 'PersistentStore')
$present=$rules.Count -gt 0
$owned=$false
$matches=$false
$active=$false
if ($rules.Count -eq 1) {
    $r=$rules[0]
    $a=@($r | Get-NetFirewallApplicationFilter)
    $owned=($r.Group -ceq $group -and $r.Description -ceq $marker -and
            $a.Count -eq 1 -and $a[0].Program -ieq $program)
    $matches=Test-MainScope $r
    if ($matches) {
        $effective=@(Read-MainRule 'ActiveStore')
        if ($effective.Count -eq 1) { $active=Test-MainScope $effective[0] }
    }
}
[pscustomobject]@{present=[bool]$present;owned=[bool]$owned;matches=[bool]$matches;
    active=[bool]$active;program=$program;name=$name} | ConvertTo-Json -Compress
"""
    literals = {"NAME": p["name"], "PROGRAM": p["program"], "GROUP": GROUP, "MARKER": MARKER}
    return re.sub(r"\b(NAME|PROGRAM|GROUP|MARKER)\b", lambda m: _quote(literals[m[0]]), template)


def mutation_script(node, operation):
    if operation not in OPERATIONS:
        raise ValueError("Unsupported firewall operation")
    # Ownership is checked before either update or removal. We never touch
    # Windows' user-created allow/block rules or another installation's rule.
    script = query_script(node).split("$present=$rules.Count", 1)[0] + """
if ($rules.Count -gt 1) { exit 183 }
if ($rules.Count -eq 1) {
    $r=$rules[0]
    $a=@($r | Get-NetFirewallApplicationFilter)
    if ($r.Group -cne $group -or $r.Description -cne $marker -or
        $a.Count -ne 1 -or $a[0].Program -ine $program) { exit 183 }
}
"""
    if operation == "remove":
        return script + """
if ($rules.Count -eq 1) {
    Remove-NetFirewallRule -Name $name -PolicyStore PersistentStore -Confirm:$false -ErrorAction Stop
}
[pscustomobject]@{status='ok';operation='remove'} | ConvertTo-Json -Compress
"""
    return script + """
$scope=@{Enabled='True';Profile='Private';Direction='Inbound';Action='Allow';
    Program=$program;Protocol='TCP';LocalPort='Any';RemotePort='Any';
    LocalAddress='Any';RemoteAddress='LocalSubnet';EdgeTraversalPolicy='Block';
    Authentication='NotRequired';Encryption='NotRequired';OverrideBlockRules=$false;
    PolicyStore='PersistentStore';ErrorAction='Stop'}
if ($rules.Count -eq 0) {
    New-NetFirewallRule -Name $name -DisplayName 'HELLDIVER Main - Private LAN TCP' -Group $group -Description $marker @scope | Out-Null
} else {
    Set-NetFirewallRule -Name $name @scope | Out-Null
}
[pscustomobject]@{status='ok';operation='configure'} | ConvertTo-Json -Compress
"""


def _powershell_path():
    if os.name != "nt":
        raise FirewallError("仅支持 Windows", 50)
    buffer = ctypes.create_unicode_buffer(32768)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = (wt.LPWSTR, wt.UINT)
    kernel.GetSystemDirectoryW.restype = wt.UINT
    count = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < count < len(buffer):
        raise FirewallError("无法定位 Windows 系统 PowerShell")
    return Path(buffer.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def run_powershell(script):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run([str(_powershell_path()), "-NoLogo", "-NoProfile", "-NonInteractive",
                                 "-EncodedCommand", encoded], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30)
    except subprocess.TimeoutExpired as error:
        raise FirewallError("查询／操作超时；请刷新状态，不把超时当作成功。", 1460) from error
    if result.returncode:
        raise FirewallError("防火墙查询／操作失败：" + result.stderr.strip()[-1200:], result.returncode)
    try:
        value = json.loads(result.stdout.lstrip("\ufeff").strip())
        if not isinstance(value, dict):
            raise ValueError("Expected object")
        return value
    except ValueError as error:
        raise FirewallError("系统返回了无法确认的规则状态") from error


def query_status():
    node = bundled_node()
    value = run_powershell(query_script(node))
    expected = policy(node)
    if (not isinstance(value, dict)
            or any(type(value.get(key)) is not bool for key in ("present", "owned", "matches", "active"))
            or value.get('program') != expected['program'] or value.get('name') != expected['name']):
        raise FirewallError("系统返回了不完整的规则状态")
    return value


def run_helper(operation):
    """Only the explicitly elevated, frozen helper may mutate a rule."""
    if operation not in OPERATIONS:
        return 50
    try:
        node = bundled_node()
        if not ctypes.windll.shell32.IsUserAnAdmin():
            return 5
        result = run_powershell(mutation_script(node, operation))
        return 0 if result.get("status") == "ok" and result.get("operation") == operation else 1
    except FirewallError as error:
        return error.code if 0 < error.code < 65536 else 1
    except (OSError, ValueError):
        return 1


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("fMask", wt.ULONG), ("hwnd", wt.HWND),
                ("lpVerb", wt.LPCWSTR), ("lpFile", wt.LPCWSTR), ("lpParameters", wt.LPCWSTR),
                ("lpDirectory", wt.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wt.HINSTANCE),
                ("lpIDList", wt.LPVOID), ("lpClass", wt.LPCWSTR), ("hkeyClass", wt.HKEY),
                ("dwHotKey", wt.DWORD), ("hIcon", wt.HANDLE), ("hProcess", wt.HANDLE)]


def authorize(operation, parent_hwnd=0):
    if operation not in OPERATIONS:
        raise ValueError("Unsupported firewall operation")
    bundled_node()  # No elevation when the bundle is missing or points elsewhere.
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = (ctypes.POINTER(_ShellExecuteInfo),)
    shell.ShellExecuteExW.restype = wt.BOOL
    kernel.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)
    kernel.WaitForSingleObject.restype = wt.DWORD
    kernel.GetExitCodeProcess.argtypes = (wt.HANDLE, ctypes.POINTER(wt.DWORD))
    kernel.GetExitCodeProcess.restype = wt.BOOL
    kernel.CloseHandle.argtypes = (wt.HANDLE,)
    kernel.CloseHandle.restype = wt.BOOL
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100 | 0x400  # Own process handle; synchronous launch; no shell error UI.
    info.hwnd = parent_hwnd
    info.lpVerb = "runas"
    info.lpFile = str(Path(sys.executable).resolve())
    info.lpParameters = "--firewall-helper " + operation
    info.lpDirectory = str(Path(sys.executable).resolve().parent)
    info.nShow = 0
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == 1223:
            raise AuthorizationCancelled("已取消 Windows 授权，未执行本次规则修改。", code)
        raise FirewallError("无法请求 Windows 授权（错误码 %s）" % code, code)
    if not info.hProcess:
        raise FirewallError("未获得授权进程句柄，无法确认结果。")
    try:
        if kernel.WaitForSingleObject(info.hProcess, 60000) != 0:
            raise FirewallError("授权进程尚未确认完成，请稍后刷新状态。", 1460)
        code = wt.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise FirewallError("无法确认授权进程结果。")
        if code.value:
            reasons = {5: "管理员权限未取得", 50: "完整包后端路径校验失败",
                       183: "同名规则不属于本程序，已拒绝覆盖或删除",
                       1460: "系统操作超时，状态未确认"}
            raise FirewallError(reasons.get(code.value, "系统操作失败，可能受策略或安全软件限制")
                                + "（错误码 %s）" % code.value, code.value)
    finally:
        kernel.CloseHandle(info.hProcess)


def status_text(value):
    if value["matches"] and value["active"]:
        return "本程序规则已配置：专用网络 · 本地子网 · TCP。此状态不证明手机一定可达。"
    if value["matches"]:
        return "规则已保存，但未确认在有效策略中启用；请检查管理员／组织策略。"
    if value["present"] and not value["owned"]:
        return "同名规则不属于本程序：不会覆盖或删除，请使用手动排查。"
    if value["present"]:
        return "本程序规则已存在，但已禁用或范围与当前方案不符；可点击配置修正。"
    return "尚未添加本按钮管理的规则；已有手动放行规则不在这里修改。"
