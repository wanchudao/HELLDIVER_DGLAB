"""Automatic local adapter selection must never guess a phone-facing address."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from src import network_addresses as network
from src import desktop_runtime as desktop
from src import dglab_sender as sender
from src.config_loader import load_config


@pytest.mark.parametrize('value',['127.0.0.1','0.0.0.0','169.254.2.3','224.0.0.1',
                                  '255.255.255.255','localhost','::1','192.168.1.5:9999',
                                  'ws://192.168.1.5','bad','',None])
def test_invalid_phone_addresses_rejected(value):
    with pytest.raises(ValueError): network.validate_advertised_ipv4(value)


@pytest.mark.parametrize('address',['192.168.2.100','10.0.0.2','172.20.0.1','192.0.2.5'])
def test_valid_ipv4_normalized(address):
    assert network.validate_advertised_ipv4(' '+address+' ')==address


def test_single_adapter_json_and_unicode_name():
    result=network.parse_network_addresses(json.dumps({'InterfaceAlias':'以太网','InterfaceIndex':13,
            'IPv4':['192.168.2.100'],'Gateway':['192.168.2.1'],'Status':'Up'}))
    assert result==[network.NetworkAddress('以太网','192.168.2.100',13,True)]
    assert result[0].label=='以太网 — 192.168.2.100'


def test_all_usable_addresses_are_listed_and_stable_deduped():
    rows=[{'InterfaceAlias':'VPN','InterfaceIndex':2,'IPv4':['10.1.0.1'],'Status':'Up'},
          {'InterfaceAlias':'无线网络','InterfaceIndex':8,'IPv4':['192.168.3.2','192.168.3.3','192.168.3.2'],
           'Gateway':['192.168.3.1'],'Status':'Up'},
          {'InterfaceAlias':'已断开','InterfaceIndex':9,'IPv4':['192.168.5.2'],'Status':'Disconnected'},
          {'InterfaceAlias':'蓝牙','InterfaceIndex':10,'IPv4':['169.254.3.4','127.0.0.1']},
          {'InterfaceIndex':11,'IPv4':None}, {'InterfaceIndex':None,'IPv4':'10.0.0.3'}]
    result=network.parse_network_addresses(json.dumps(rows))
    assert [item.address for item in result]==['192.168.3.2','192.168.3.3','10.1.0.1']


@pytest.mark.parametrize('content',['','null','[]','  '])
def test_no_network_does_not_create_localhost_fallback(content):
    assert network.parse_network_addresses(content)==[]


def test_discovery_only_reads_local_os_configuration(monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(stdout='{"InterfaceAlias":"网卡","InterfaceIndex":1,"IPv4":"10.0.0.5","Status":"Up"}')
    monkeypatch.setattr(network.subprocess,'run',run)
    assert network.discover_network_addresses()[0].address=='10.0.0.5'
    args,kwargs=calls[0]
    assert 'Get-NetIPConfiguration' in args[-1]
    assert '8.8.8.8' not in args[-1] and 'ping' not in args[-1].lower()
    assert kwargs['timeout']==10 and kwargs['check'] and kwargs['encoding']=='utf-8'


@pytest.mark.parametrize('error',[OSError('missing'),subprocess.TimeoutExpired('powershell',10),
                                 subprocess.CalledProcessError(1,'powershell')])
def test_detection_failure_is_explicit_not_guessed(monkeypatch,error):
    def fail(*a,**kw): raise error
    monkeypatch.setattr(network.subprocess,'run',fail)
    with pytest.raises(RuntimeError,match='检测失败'): network.discover_network_addresses()


def test_selected_address_is_separate_from_backend_loopback(tmp_path):
    path=desktop.make_config(tmp_path/'selected.ini',1,(1920,1080),'dglab',43123,qr_host='192.168.3.9')
    config=load_config(path)
    assert config['sender']['qr_host']=='192.168.3.9'
    assert config['sender']['ws_host']=='127.0.0.1'
    assert config['sender']['ws_port']==43123
    with pytest.raises(ValueError):
        desktop.make_config(tmp_path/'bad.ini',1,(1920,1080),'dglab',qr_host='127.0.0.1')
    assert not (tmp_path/'bad.ini').exists()


def test_selected_ip_is_used_for_every_qr_without_internet_guess(monkeypatch):
    def forbidden(): raise AssertionError('Selected IP must not use Internet routing')
    monkeypatch.setattr(sender,'_get_lan_ip',forbidden)
    client=sender._DglabWSClient('ws://127.0.0.1:43210',qr_host='192.168.3.9')
    urls=[]
    monkeypatch.setattr(client,'_show_qrcode',urls.append)
    for client_id in ('first-client','reconnected-client'):
        client._handle_bind({'clientId':client_id,'targetId':'','message':'targetId'})
    assert urls==[
        'https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#ws://192.168.3.9:43210/'+client_id
        for client_id in ('first-client','reconnected-client')]


def test_invalid_qr_address_cannot_replace_live_sender(monkeypatch):
    calls=[]
    monkeypatch.setattr(sender,'safe_stop',lambda:calls.append('stop'))
    with pytest.raises(ValueError): sender.init_sender({'sender':{'qr_host':'127.0.0.1'}})
    assert not calls
