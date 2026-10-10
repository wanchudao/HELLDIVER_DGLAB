"""Verify the final frozen main edition against source, then package exact files."""
import argparse
import hashlib
import importlib.metadata
import json
import marshal
from pathlib import Path
import shutil
import subprocess
import sys
from types import CodeType
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.inspect_frozen_archive import read_archive,read_pyz

ROOT=Path(__file__).resolve().parents[1]


def normalized(code):
    return code.replace(co_filename='<verified-source>',co_consts=tuple(
        normalized(value) if isinstance(value,CodeType) else value for value in code.co_consts))


def package(folder,destination,report_prefix='main_final',date='2026-09-13',python_tests=498):
    folder=folder.resolve()
    destination=destination.resolve()
    exe=folder/'HELLDIVER_Main.exe'
    _,entries,extract=read_archive(exe)
    _,modules,extract_module=read_pyz(extract('PYZ.pyz'))
    checked=[]
    sources={name: ROOT.joinpath(*name.split('.')).with_suffix('.py')
             for name,location in modules.items()
             if (name=='src' or name.startswith('src.')) and location[0] in (0,1)}
    sources['desktop_app']=ROOT/'tools/desktop_app.py'
    if 'src.feedback_settings' in modules:
        sources['tools.main_feedback_acceptance']=ROOT/'tools/main_feedback_acceptance.py'
    if 'tools.main_threshold_acceptance' in modules:
        sources['tools.main_threshold_acceptance']=ROOT/'tools/main_threshold_acceptance.py'
    if 'src.panel_window' in modules:
        sources['tools.panel_acceptance']=ROOT/'tools/panel_acceptance.py'
    if 'src.hud_profiles' in modules:
        sources['tools.main_hud_acceptance']=ROOT/'tools/main_hud_acceptance.py'
        sources['tools.main_hud_ui_acceptance']=ROOT/'tools/main_hud_ui_acceptance.py'
    if 'src.firewall_access' in modules:
        sources['tools.main_firewall_acceptance']=ROOT/'tools/main_firewall_acceptance.py'
    if 'src.desktop_view' in modules:
        sources['tools.main_theme_acceptance']=ROOT/'tools/main_theme_acceptance.py'
    for name,path in sources.items():
        if not path.is_file(): path=path.with_suffix('')/'__init__.py'
        raw=extract('desktop_app') if name=='desktop_app' else extract_module(name)
        frozen=marshal.loads(raw)
        source=compile(path.read_bytes(),str(path),'exec',dont_inherit=True,optimize=0)
        if normalized(frozen)!=normalized(source):
            raise RuntimeError('Frozen source mismatch: '+name)
        checked.append(name)
    if any(name.startswith('dghub_plugin') for name in modules):
        raise RuntimeError('Wrong edition bundled')
    internal=folder/'_internal'
    for source in (ROOT/'official_v2/socket/v2/backend/src').glob('*.js'):
        if source.read_bytes()!=(internal/'backend/src'/source.name).read_bytes():
            raise RuntimeError('Backend source mismatch: '+source.name)
    assert (ROOT/'config.example.ini').read_bytes()==(internal/'config.example.ini').read_bytes()
    if 'src.feedback_settings' in modules:
        for name in ('main_official_guide.md','waveform_sources.md'):
            if (ROOT/'docs'/name).read_bytes()!=(internal/'docs'/name).read_bytes():
                raise RuntimeError('Bundled guide mismatch: '+name)
    stamp=date.replace('-','')
    reports={name:json.loads((ROOT/'build'/f'{report_prefix}_{name}_{stamp}.json').read_text(encoding='utf-8'))
             for name in ('selfcheck','backend_acceptance','worker_acceptance')}
    image_report=ROOT/'build'/f'{report_prefix}_images_{stamp}.json'
    if image_report.is_file(): reports['images']=json.loads(image_report.read_text(encoding='utf-8'))
    if 'src.panel_window' in modules:
        reports['panel']=json.loads((ROOT/'build'/f'{report_prefix}_panel_{stamp}.json').read_text(encoding='utf-8'))
        assert reports['panel']['native_windows'] and reports['panel']['no_device']
        assert reports['panel']['native_button_mapping'] and reports['panel']['close_detected']
        assert all(check['foreground_unchanged'] for check in reports['panel']['checks'])
        user_report=ROOT/'build'/f'{report_prefix}_user_feedback_{stamp}.json'
        if user_report.is_file():
            reports['user_island']=json.loads(user_report.read_text(encoding='utf-8'))
            assert reports['user_island']['exe_sha256']==hashlib.sha256(exe.read_bytes()).hexdigest()
            assert reports['user_island']['source']=='user_report'
    if 'src.feedback_settings' in modules:
        reports['protocol']=json.loads((ROOT/'build'/f'{report_prefix}_protocol_{stamp}.json').read_text(encoding='utf-8'))
        assert reports['selfcheck']['feedback_settings']
        assert reports['selfcheck']['waveform_count']==18
        assert reports['protocol']['channel_modes']==['a','b','both']
        assert len(reports['protocol']['checks'])==15
        assert reports['protocol']['stop_both'] and reports['protocol']['no_late_output']
        assert 'images' in reports and len(reports['images']['images'])==3
        if 'src.desktop_view' in modules:
            images=reports['images']['images']
            assert all(item['hud_valid'] and not item['first_frame_valid'] for item in images)
            assert [round(item['health_raw'],3) for item in images]==[1.0,0.328,0.0]
    if 'tools.main_threshold_acceptance' in modules:
        reports['threshold']=json.loads((ROOT/'build'/f'{report_prefix}_threshold_{stamp}.json').read_text(encoding='utf-8'))
        assert reports['threshold']['full_preference_trigger_protocol_chain']
        assert reports['threshold']['final_both_zero'] and reports['threshold']['no_late_output']
        assert len(reports['threshold']['checks'])==8
    if 'src.hud_profiles' in modules:
        reports['hud']=json.loads((ROOT/'build'/f'{report_prefix}_hud_{stamp}.json').read_text(encoding='utf-8'))
        assert reports['selfcheck']['hud_settings'] is True
        assert reports['selfcheck']['hud_scales']==list(range(75,126,5))
        assert reports['hud']['status']=='ok'
        assert reports['hud']['scales']==list(range(75,126,5))
        assert reports['hud']['photo_profiles']==[75,80,85,90,95,100,125]
        assert reports['hud']['interpolated_profiles']==[105,110,115,120]
        assert reports['hud']['wrong_profile_rejected'] is True
        assert reports['hud']['no_app'] is True and reports['hud']['no_capture'] is True
        reports['hud_ui']=json.loads((ROOT/'build'/f'{report_prefix}_hud_ui_{stamp}.json').read_text(encoding='utf-8'))
        assert reports['hud_ui']['no_app'] and not reports['hud_ui']['game_capture_started']
        assert reports['hud_ui']['persisted'] and reports['hud_ui']['running_readonly']
        expected_ui = (10 if 'src.firewall_access' in modules else 6) + (2 if 'tools.main_threshold_acceptance' in modules else 0)
        assert len(reports['hud_ui']['checks'])==expected_ui
        reports['video']=json.loads((ROOT/'build'/f'{report_prefix}_video_{stamp}.json').read_text(encoding='utf-8'))
        assert reports['video']['status']=='ok' and reports['video']['sender_mode']=='mock'
        assert reports['video']['offline_only'] and len(reports['video']['videos'])==2
        assert all(item['regression_passed'] and item['worker_exit_code']==0
                   and item['summary']['final_strength']==0 for item in reports['video']['videos'])
        junit_path=ROOT/'build'/f'{report_prefix}_pytest_{stamp}.xml'
        suite=ET.parse(junit_path).getroot().find('testsuite')
        if suite is None:
            raise RuntimeError('Missing completed Python regression suite')
        assert int(suite.attrib['tests'])==python_tests
        assert all(int(suite.attrib[key])==0 for key in ('errors','failures','skipped'))
        reports['python_regression']={'status':'ok','tests':python_tests,'failures':0,'skipped':0,
                                     'report_sha256':hashlib.sha256(junit_path.read_bytes()).hexdigest()}
        node_path=ROOT/'build'/f'{report_prefix}_node_{stamp}.json'
        node=json.loads(node_path.read_text(encoding='utf-8'))
        assert node['success'] and node['numPassedTests']==node['numTotalTests']==16
        assert node['numFailedTests']==node['numPendingTests']==0
        reports['node_regression']={'status':'ok','tests':16,'failures':0,'skipped':0,
                                   'report_sha256':hashlib.sha256(node_path.read_bytes()).hexdigest()}
    if 'src.firewall_access' in modules:
        reports['firewall']=json.loads((ROOT/'build'/f'{report_prefix}_firewall_{stamp}.json').read_text(encoding='utf-8'))
        assert reports['firewall']['frozen'] and reports['firewall']['script_syntax']
        assert reports['firewall']['netsecurity_parameters']
        assert reports['firewall']['firewall_modified'] is False and reports['firewall']['real_uac_tested'] is False
        assert reports['firewall']['policy']['program']==str((internal/'runtime/node.exe').resolve())
        assert reports['firewall']['policy']['profile']=='Private'
        assert reports['firewall']['policy']['protocol']=='TCP'
        assert reports['firewall']['policy']['remote_address']=='LocalSubnet'
        assert reports['selfcheck']['firewall_opt_in'] and reports['selfcheck']['firewall_modified'] is False
        assert reports['hud_ui']['firewall_modified'] is False and reports['hud_ui']['real_uac_tested'] is False
        assert reports['hud_ui']['permission_mock_operations']==['configure','configure','remove']
    if 'src.desktop_view' in modules:
        reports['theme']=json.loads((ROOT/'build'/f'{report_prefix}_theme_{stamp}.json').read_text(encoding='utf-8'))
        assert reports['theme']['frozen'] and reports['theme']['native_windows']
        assert not reports['theme']['backend_started'] and not reports['theme']['firewall_modified']
        assert any(item['mode']=='session_active' and item['decoration_ticks']==0 for item in reports['theme']['performance'])
        assert reports['theme']['software_telemetry_injected'] is True
    assert all(report['status']=='ok' for report in reports.values())
    dependencies={name:importlib.metadata.version(name) for name in
                  ('numpy','opencv-python','mss','websocket-client','qrcode','Pillow')}
    dependencies['node']=subprocess.check_output([str(internal/'runtime/node.exe'),'--version'],text=True).strip()
    for name in ('ws','uuid','winston','dotenv'):
        dependencies['node:'+name]=json.loads((internal/'backend/node_modules'/name/'package.json').read_text())['version']
    shutil.copyfile(ROOT/'docs/main_official_guide.md',folder/'使用说明.md')
    acceptance_files=[]
    if 'src.desktop_view' in modules:
        upgrade = folder/'升级与测试.md'
        shutil.copyfile(ROOT/'docs/main_ui_upgrade_20261010.md', upgrade)
        acceptance_files.append(upgrade)
    if 'src.hud_profiles' in modules:
        acceptance=folder/'验收记录.md'
        source_name=('main_ui_acceptance_20261010.md' if 'src.desktop_view' in modules else
                     'threshold_acceptance_20261010.md' if 'tools.main_threshold_acceptance' in modules else
                     'main_firewall_acceptance_20261007.md' if 'src.firewall_access' in modules else 'main_hud_acceptance_20261002.md')
        shutil.copyfile(ROOT/'docs'/source_name,acceptance)
        acceptance_files.append(acceptance)
    info={'edition':'main-official-backend','date':date,'entry':'HELLDIVER_Main.exe',
          'exe_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'frozen_source_verified':checked,
          'python_tests_passed':python_tests,'node_tests_passed':16,'dependencies':dependencies,
          'checks':reports,'physical_device_tested':False,'user_phone_app_tested':False}
    (folder/'BUILD_INFO.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    files=[exe,folder/'使用说明.md',folder/'BUILD_INFO.json',*acceptance_files,*sorted(
        p for p in internal.rglob('*') if p.is_file()
        and p.name not in ('feedback_settings.ini','hud_settings.ini','ui_settings.ini'))]
    if 'src.desktop_view' in modules:
        preview_dir=folder/'界面截图'
        preview_dir.mkdir(exist_ok=True)
        for name in ('main_window.png','hud_saved.png','guide_page.png','control_panel.png','island.png'):
            source=ROOT/'build/ui_cuttingedge_20261010/final_theme'/name
            target=preview_dir/name
            shutil.copyfile(source,target)
            files.append(target)
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in files: archive.write(path,'HELLDIVER_Main/'+path.relative_to(folder).as_posix())
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert archive.read('HELLDIVER_Main/HELLDIVER_Main.exe')==exe.read_bytes()
        assert not any('/sessions/' in name or name.startswith('sessions/') for name in archive.namelist())
        assert not any(Path(name).name in ('feedback_settings.ini','hud_settings.ini','ui_settings.ini')
                       for name in archive.namelist())
    result={'status':'ok','zip':str(destination),'zip_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
            'exe_sha256':info['exe_sha256'],'modules_verified':len(checked),'files':len(files),
            'zip_bytes':destination.stat().st_size}
    (ROOT/'build'/f'{report_prefix}_package_{stamp}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',required=True,type=Path)
    parser.add_argument('--zip',required=True,type=Path)
    parser.add_argument('--report-prefix',default='main_final')
    parser.add_argument('--date',default='2026-09-13')
    parser.add_argument('--python-tests',type=int,default=498)
    args=parser.parse_args()
    print(json.dumps(package(args.folder,args.zip,args.report_prefix,args.date,args.python_tests),indent=2))
