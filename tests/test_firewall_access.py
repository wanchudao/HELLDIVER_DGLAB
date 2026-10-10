import base64
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from src import firewall_access as fw
from tools import desktop_app


def test_policy_is_stable_per_path_and_limited(tmp_path):
    node = tmp_path / "中文 GROUP MARKER" / "node.exe"
    p = fw.policy(node)
    assert p == fw.policy(node)
    assert p["profile"] == "Private" and p["remote_address"] == "LocalSubnet"
    assert p["protocol"] == "TCP"
    assert p["name"] != fw.policy(tmp_path / "other" / "node.exe")["name"]
    assert fw.policy(str(node).upper())["name"] == p["name"]


def test_program_literal_is_escaped_once_without_interpreting_commands(tmp_path):
    node = tmp_path / "GROUP MARKER '$HOME $(echo bad)" / "node.exe"
    script = fw.query_script(node)
    assert "$program=" + fw._quote(str(node.resolve())) in script
    assert "New-NetFirewallRule" not in script and "Remove-NetFirewallRule" not in script
    assert "Set-NetFirewallRule" not in script


@pytest.mark.parametrize("operation",fw.OPERATIONS)
def test_mutations_are_exact_owned_rules_not_global_settings(tmp_path,operation):
    script = fw.mutation_script(tmp_path / "node.exe",operation)
    assert "-Name $name -PolicyStore $store" in script
    assert "$r.Group -cne $group" in script and "$a[0].Program -ine $program" in script
    for forbidden in ("Set-NetFirewallProfile","-DisplayGroup","netsh", "Public", "Domain", "-ExecutionPolicy"):
        assert forbidden not in script
    if operation == "configure":
        assert "Profile='Private'" in script and "RemoteAddress='LocalSubnet'" in script
        assert "OverrideBlockRules=$false" in script and "EdgeTraversalPolicy='Block'" in script
        assert "Remove-NetFirewallRule" not in script
    else:
        assert "New-NetFirewallRule" not in script and "Set-NetFirewallRule" not in script


@pytest.mark.parametrize("operation",["all","disable","",None])
def test_invalid_actions_do_not_run_or_elevate(monkeypatch,tmp_path,operation):
    monkeypatch.setattr(fw,"bundled_node",lambda:pytest.fail("Path lookup reached"))
    with pytest.raises(ValueError): fw.mutation_script(tmp_path / "node.exe",operation)
    with pytest.raises(ValueError): fw.authorize(operation)
    assert fw.run_helper(operation)==50


def test_source_environment_never_modifies_system_node(monkeypatch):
    monkeypatch.delattr(fw.sys,"frozen",raising=False)
    monkeypatch.setattr(fw,"run_powershell",lambda script:pytest.fail("Unexpected PowerShell"))
    with pytest.raises(fw.FirewallError,match="源码"): fw.bundled_node()
    assert fw.run_helper("configure")==50


def test_frozen_path_must_be_this_packages_node(monkeypatch,tmp_path):
    from src import desktop_runtime
    root=tmp_path / "app"
    target=root / "_internal/runtime/node.exe"
    target.parent.mkdir(parents=True)
    target.touch()
    monkeypatch.setattr(fw.sys,"frozen",True,raising=False)
    monkeypatch.setattr(fw.sys,"executable",str(root / "HELLDIVER_Main.exe"))
    monkeypatch.setattr(desktop_runtime,"backend_paths",lambda:(target,None))
    assert fw.bundled_node()==target.resolve()
    monkeypatch.setattr(desktop_runtime,"backend_paths",lambda:(Path("C:/other/node.exe"),None))
    with pytest.raises(fw.FirewallError): fw.bundled_node()


def test_non_admin_helper_does_not_spawn_mutation(monkeypatch,tmp_path):
    monkeypatch.setattr(fw,"bundled_node",lambda:tmp_path / "node.exe")
    monkeypatch.setattr(fw.ctypes.windll.shell32,"IsUserAnAdmin",lambda:False)
    monkeypatch.setattr(fw,"run_powershell",lambda script:pytest.fail("Not elevated"))
    assert fw.run_helper("configure")==5


@pytest.mark.parametrize("operation",fw.OPERATIONS)
def test_helper_uses_derived_path_and_no_user_output_file(monkeypatch,tmp_path,operation):
    monkeypatch.setattr(fw,"bundled_node",lambda:tmp_path / "node.exe")
    monkeypatch.setattr(fw.ctypes.windll.shell32,"IsUserAnAdmin",lambda:True)
    scripts=[]
    def run(script):
        scripts.append(script)
        return {"status":"ok","operation":operation}
    monkeypatch.setattr(fw,"run_powershell",run)
    assert fw.run_helper(operation)==0
    assert scripts==[fw.mutation_script(tmp_path / "node.exe",operation)]


@pytest.mark.parametrize("code",[1,183,1460])
def test_helper_failures_preserve_error_code(monkeypatch,tmp_path,code):
    monkeypatch.setattr(fw,"bundled_node",lambda:tmp_path / "node.exe")
    monkeypatch.setattr(fw.ctypes.windll.shell32,"IsUserAnAdmin",lambda:True)
    def fail(script): raise fw.FirewallError("failure",code)
    monkeypatch.setattr(fw,"run_powershell",fail)
    assert fw.run_helper("configure")==code


def test_cli_helper_is_dispatched_without_starting_app(monkeypatch):
    calls=[]
    monkeypatch.setattr(fw,"run_helper",lambda operation:calls.append(operation) or 5)
    monkeypatch.setattr(desktop_app,"launch",lambda:pytest.fail("Main window launched"))
    assert desktop_app.main(["--firewall-helper","configure"])==5
    assert calls==["configure"]


def test_powershell_uses_system_binary_encoded_command_and_timeout(monkeypatch):
    monkeypatch.setattr(fw,"_powershell_path",lambda:Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs))
        return SimpleNamespace(returncode=0,stdout='{"present":false}',stderr="")
    monkeypatch.setattr(fw.subprocess,"run",run)
    assert fw.run_powershell("安全测试")=={"present":False}
    args,kwargs=calls[0]
    assert base64.b64decode(args[-1]).decode("utf-16-le")=="安全测试"
    assert kwargs["timeout"]==30 and kwargs["stdin"]==subprocess.DEVNULL
    assert "shell" not in kwargs and "-ExecutionPolicy" not in args


@pytest.mark.parametrize("value",["invalid","[]","null",'{"present":"false"}'])
def test_invalid_query_output_cannot_be_treated_as_ready(monkeypatch,value):
    monkeypatch.setattr(fw,"bundled_node",lambda:Path("C:/test/node.exe"))
    def run(script):
        try: return json.loads(value)
        except ValueError: raise fw.FirewallError("bad json")
    monkeypatch.setattr(fw,"run_powershell",run)
    with pytest.raises(fw.FirewallError): fw.query_status()


def test_powershell_timeout_never_reports_success(monkeypatch):
    monkeypatch.setattr(fw,"_powershell_path",lambda:Path("powershell.exe"))
    def run(*a,**kw): raise subprocess.TimeoutExpired("owned PS",30)
    monkeypatch.setattr(fw.subprocess,"run",run)
    with pytest.raises(fw.FirewallError) as caught: fw.run_powershell("readonly")
    assert caught.value.code==1460


@pytest.mark.parametrize("present,owned,matches,active,expected",[
    (False,False,False,False,"尚未添加"),(True,False,False,False,"不属于"),
    (True,True,False,False,"已禁用"),(True,True,True,False,"有效策略"),
    (True,True,True,True,"不证明手机一定可达")])
def test_status_is_rule_evidence_not_a_phone_connectivity_claim(present,owned,matches,active,expected):
    assert expected in fw.status_text(dict(present=present,owned=owned,matches=matches,active=active))


# Execute the production PowerShell in an isolated runspace with cmdlets
# shadowed by functions. No UAC and no real firewall write are permitted.
def fake_cmdlets(node,existing="none"):
    p=fw.policy(node)
    existing_rule="" if existing=="none" else """
$script:rule=[pscustomobject]@{Group=GROUP;Description=MARKER;Program=PROGRAM;
    Enabled='True';Profile='Private';Direction='Inbound';Action='Allow';EdgeTraversalPolicy='Block'}
""".replace("GROUP",fw._quote(fw.GROUP)).replace("MARKER",fw._quote(fw.MARKER)).replace("PROGRAM",fw._quote(p["program"]))
    if existing=="foreign": existing_rule+="$script:rule.Group='Not ours'\n"
    return """
$script:rule=$null
$script:events=@()
function Import-Module { param($Name,$ErrorAction) }
function Get-NetFirewallRule { param($Name,$PolicyStore,$ErrorAction) if($script:rule){$script:rule} }
function Get-NetFirewallApplicationFilter { param([Parameter(ValueFromPipeline)]$InputObject) process { [pscustomobject]@{Program=$InputObject.Program} } }
function Get-NetFirewallPortFilter { param([Parameter(ValueFromPipeline)]$InputObject) process { [pscustomobject]@{Protocol='6';LocalPort=@('Any')} } }
function Get-NetFirewallAddressFilter { param([Parameter(ValueFromPipeline)]$InputObject) process { [pscustomobject]@{RemoteAddress=@('LocalSubnet')} } }
function New-NetFirewallRule {
    param($Name,$DisplayName,$Group,$Description,$Enabled,$Profile,$Direction,$Action,$Program,$Protocol,$LocalPort,$RemotePort,$LocalAddress,$RemoteAddress,$EdgeTraversalPolicy,$Authentication,$Encryption,$OverrideBlockRules,$PolicyStore,$ErrorAction)
    $script:events+=@{operation='new';name=$Name;program=$Program;profile=$Profile;remote=$RemoteAddress;protocol=$Protocol;override=$OverrideBlockRules}
    $script:rule=[pscustomobject]@{Group=$Group;Description=$Description;Program=$Program;Enabled=$Enabled;Profile=$Profile;Direction=$Direction;Action=$Action;EdgeTraversalPolicy=$EdgeTraversalPolicy}
}
function Set-NetFirewallRule {
    param($Name,$Enabled,$Profile,$Direction,$Action,$Program,$Protocol,$LocalPort,$RemotePort,$LocalAddress,$RemoteAddress,$EdgeTraversalPolicy,$Authentication,$Encryption,$OverrideBlockRules,$PolicyStore,$ErrorAction)
    $script:events+=@{operation='set';name=$Name;program=$Program;profile=$Profile;remote=$RemoteAddress;protocol=$Protocol;override=$OverrideBlockRules}
}
function Remove-NetFirewallRule { param($Name,$PolicyStore,$Confirm,$ErrorAction) $script:events+=@{operation='remove';name=$Name};$script:rule=$null }
"""+existing_rule


@pytest.mark.parametrize("operation,existing,expected",[
    ("configure","none","new"),("configure","owned","set"),
    ("remove","owned","remove"),("remove","none",None)])
def test_production_powershell_with_mocked_cmdlets_never_touches_real_rules(tmp_path,operation,existing,expected):
    node=tmp_path / "中文 '测试" / "node.exe"
    command=fake_cmdlets(node,existing)+"\n$unused=& {\n"+fw.mutation_script(node,operation)+"\n}\n"
    command+="ConvertTo-Json -InputObject @{events=@($script:events)} -Depth 6 -Compress"
    result=fw.run_powershell(command)
    if expected is None:
        assert result["events"]==[]
    else:
        event=result["events"][0]
        assert event["operation"]==expected and event["name"]==fw.policy(node)["name"]
        if operation=="configure":
            assert event["program"]==str(node.resolve())
            assert event["profile"]=="Private" and event["remote"]=="LocalSubnet"
            assert event["protocol"]=="TCP" and event["override"] is False


@pytest.mark.parametrize("operation",fw.OPERATIONS)
def test_foreign_rule_collision_refuses_mutation_in_real_ps(tmp_path,operation):
    command=fake_cmdlets(tmp_path / "node.exe","foreign")+fw.mutation_script(tmp_path / "node.exe",operation)
    with pytest.raises(fw.FirewallError) as caught: fw.run_powershell(command)
    assert caught.value.code==183


def test_read_query_parses_rule_fields_in_real_ps(tmp_path):
    node=tmp_path / "node.exe"
    result=fw.run_powershell(fake_cmdlets(node,"owned")+fw.query_script(node))
    assert all(result[k] is True for k in ("present","owned","matches","active"))


def test_read_access_denied_is_not_mistaken_for_missing_rule(tmp_path):
    node=tmp_path / 'node.exe'
    script=fake_cmdlets(node)+"""
function Get-NetFirewallRule { param($Name,$PolicyStore,$ErrorAction)
    Write-Error 'Synthetic access denied' -Category PermissionDenied -ErrorAction Stop
}
"""+fw.query_script(node)
    with pytest.raises(fw.FirewallError): fw.run_powershell(script)


def test_effective_policy_with_changed_profile_is_not_treated_as_configured(tmp_path):
    node=tmp_path / 'node.exe'
    script=fake_cmdlets(node,'owned')+"""
function Get-NetFirewallRule { param($Name,$PolicyStore,$ErrorAction)
    $r=$script:rule.PSObject.Copy()
    if ($PolicyStore -eq 'ActiveStore') { $r.Profile='Public' }
    $r
}
"""+fw.query_script(node)
    result=fw.run_powershell(script)
    assert result['matches'] is True and result['active'] is False


class NativeFunction:
    def __init__(self, call): self.call=call
    def __call__(self,*args): return self.call(*args)


@pytest.mark.parametrize("launched,wait,exit_code,error",[
    (True,0,0,None),(False,0,0,1223),(True,0,5,5),
    (True,0,183,183),(True,0x102,0,1460),(False,0,0,5)])
def test_native_elevation_is_runas_fixed_helper_and_closes_owned_handle(monkeypatch,tmp_path,launched,wait,exit_code,error):
    monkeypatch.setattr(fw,"bundled_node",lambda:tmp_path / "node.exe")
    captured=[]
    closed=[]
    def launch(ref):
        info=ref._obj
        captured.append((info.lpVerb,info.lpFile,info.lpParameters,info.nShow,info.hwnd))
        if launched: info.hProcess=123
        return launched
    def get_exit(handle,ref):
        ref._obj.value=exit_code
        return True
    shell=SimpleNamespace(ShellExecuteExW=NativeFunction(launch))
    kernel=SimpleNamespace(WaitForSingleObject=NativeFunction(lambda handle,timeout:wait),
                           GetExitCodeProcess=NativeFunction(get_exit),
                           CloseHandle=NativeFunction(lambda handle:closed.append(handle) or True))
    monkeypatch.setattr(fw.ctypes,"WinDLL",lambda name,**kw:shell if name=="shell32" else kernel)
    monkeypatch.setattr(fw.ctypes,"get_last_error",lambda:1223 if error==1223 else 5)
    if error is None:
        fw.authorize("configure",42)
    else:
        with pytest.raises(fw.FirewallError) as caught: fw.authorize("configure",42)
        assert caught.value.code==error
        if error==1223: assert isinstance(caught.value,fw.AuthorizationCancelled)
    assert captured==[("runas",str(Path(fw.sys.executable).resolve()),"--firewall-helper configure",0,42)]
    assert closed==([123] if launched else [])
