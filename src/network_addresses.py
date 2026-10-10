"""Enumerate local Windows IPv4 adapters without contacting the Internet."""
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class NetworkAddress:
    name: str
    address: str
    interface_index: int
    has_gateway: bool = False

    @property
    def label(self):
        return f"{self.name} — {self.address}"


def validate_advertised_ipv4(value):
    """Only unicast interface addresses belong in a phone-facing QR code."""
    try:
        address=ipaddress.IPv4Address(str(value).strip())
    except ipaddress.AddressValueError as error:
        raise ValueError('需要有效的本机 IPv4 地址') from error
    if (address.is_loopback or address.is_unspecified or address.is_multicast
            or address.is_link_local or address.is_reserved):
        raise ValueError('该地址不能用于手机连接，请选择局域网网卡')
    return str(address)


def parse_network_addresses(content):
    """Parse the fixed local command output, excluding disconnected/APIPA adapters."""
    records=json.loads(content.lstrip('\ufeff')) if content.strip() else []
    if records is None:
        records=[]
    if isinstance(records,dict):
        records=[records]
    if not isinstance(records,list):
        raise ValueError('无法解析本机网卡列表')
    found={}
    for record in records:
        if not isinstance(record,dict) or record.get('Status') not in (None,'Up'):
            continue
        values=record.get('IPv4',[])
        if not isinstance(values,list):
            values=[values]
        gateways=record.get('Gateway',[])
        if not isinstance(gateways,list):
            gateways=[gateways]
        try:
            index=int(record.get('InterfaceIndex',0))
        except (TypeError,ValueError):
            continue
        name=str(record.get('InterfaceAlias') or f'网卡 {index}')
        for value in values:
            try:
                address=validate_advertised_ipv4(value)
            except ValueError:
                continue
            found[(index,address)]=NetworkAddress(name,address,index,any(gateways))
    return sorted(found.values(),key=lambda item:(not item.has_gateway,item.interface_index,item.address))


def discover_network_addresses():
    """Read OS adapter configuration only; do not ping or scan the LAN/Internet."""
    executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    command=("[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
             "$ErrorActionPreference = 'Stop'; "
             "Get-NetIPConfiguration | Select-Object InterfaceAlias, InterfaceIndex, "
             "@{Name='Status';Expression={$_.NetAdapter.Status}}, "
             "@{Name='IPv4';Expression={@($_.IPv4Address.IPAddress)}}, "
             "@{Name='Gateway';Expression={@($_.IPv4DefaultGateway.NextHop)}} "
             "| ConvertTo-Json -Compress -Depth 4")
    try:
        result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-Command',command],
                              stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',
                              errors='replace',timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
                              check=True)
        return parse_network_addresses(result.stdout)
    except (OSError,subprocess.SubprocessError,ValueError) as error:
        raise RuntimeError('本机网卡检测失败，请确认网络适配器已启用，再点“重新检测”') from error
