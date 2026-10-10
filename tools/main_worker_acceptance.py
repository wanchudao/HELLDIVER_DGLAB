"""Opt-in frozen worker test: localhost software APP, capture, stop and orphan exit."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import desktop_runtime as desktop


def run(executable,qr_host=None):
    import mss
    import websocket
    executable=Path(executable).resolve()
    bundle=executable.parent/'_internal'
    desktop.backend_paths=lambda:(bundle/'runtime/node.exe',bundle/'backend/src/managed.js')
    with mss.MSS() as capture:
        monitor=capture.monitors[1]
        size=(monitor['width'],monitor['height'])
    checks=[]
    for scenario in ('paired_stop','parent_heartbeat_expiry'):
        with tempfile.TemporaryDirectory(prefix='helldiver-worker-check-') as directory:
            folder=Path(directory)
            backend=desktop.OfficialBackend(folder)
            process=app=None
            heartbeat_done=threading.Event()
            heartbeat_thread=None
            try:
                port=backend.start()
                config=desktop.make_config(folder/'config.ini',1,size,'dglab',port,qr_host=qr_host)
                (folder/'heartbeat').touch()
                if scenario=='paired_stop':
                    def heartbeat():
                        while not heartbeat_done.wait(.1):
                            (folder/'heartbeat').touch()
                    heartbeat_thread=threading.Thread(target=heartbeat,daemon=True)
                    heartbeat_thread.start()
                process=subprocess.Popen([str(executable),'--worker',str(config),'--session',str(folder)],
                                         cwd=folder,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,creationflags=desktop.NO_WINDOW)
                log_path=folder/'runner.log'
                deadline=time.monotonic()+10
                log=''
                while time.monotonic()<deadline:
                    log=log_path.read_text(encoding='utf-8') if log_path.exists() else ''
                    if 'HD_QR=' in log or process.poll() is not None:
                        break
                    time.sleep(.1)
                assert 'HD_QR=' in log,log
                if qr_host: assert f'#ws://{qr_host}:{port}/' in log,log
                if scenario=='paired_stop':
                    client_id=next(line.rsplit('/',1)[-1] for line in log.splitlines() if line.startswith('HD_QR='))
                    app=websocket.create_connection(f'ws://127.0.0.1:{port}',timeout=.25,http_proxy_host=None)
                    app_id=json.loads(app.recv())['clientId']
                    app.send(json.dumps({'type':'bind','clientId':client_id,'targetId':app_id,'message':'DGLAB'}))
                    deadline=time.monotonic()+3
                    messages=[]
                    while time.monotonic()<deadline:
                        try:
                            raw=app.recv()
                            if raw: messages.append(json.loads(raw))
                        except websocket.WebSocketTimeoutException:
                            pass
                    assert process.poll() is None,'Worker exited before stop'
                    assert not any(str(m.get('message','')).startswith('pulse-') for m in messages)
                    (folder/'STOP').touch()
                    process.wait(timeout=8)
                else:
                    process.wait(timeout=10)
                log=log_path.read_text(encoding='utf-8')
                assert process.returncode==0,log
                assert 'Traceback' not in log,log
                frames=re.search(r'总帧数:\s*(\d+)',log)
                count=int(frames.group(1)) if frames else 0
                if scenario=='paired_stop':
                    assert 'OK 配对成功' in log and count>0,log
                    assert '触发事件: 0' in log,log
                    status=json.loads((folder/'ui_status.json').read_text(encoding='utf-8'))
                    assert status['session']==folder.name and status['sequence']>1
                    assert status['paused'] and not status['valid'] and status['hp'] is None
                    assert status['mode']=='dglab'
                backend.close()
                assert backend.process.returncode==0
                checks.append({'scenario':scenario,'worker_exit':process.returncode,
                               'backend_exit':backend.process.returncode,'captured_frames':count,
                               'ui_status_written':scenario=='paired_stop'})
            finally:
                if process and process.poll() is None:
                    desktop.stop_worker(process,folder)
                heartbeat_done.set()
                if heartbeat_thread: heartbeat_thread.join(2)
                if app: app.close()
                backend.close()
    return {'status':'ok','frozen_worker':True,'physical_device_used':False,
            'software_app_only':True,'initially_paused':True,'selected_qr_host':qr_host,'checks':checks}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-local',action='store_true')
    parser.add_argument('--exe',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path)
    parser.add_argument('--qr-host')
    args=parser.parse_args()
    if not args.run_local: parser.error('Explicit --run-local required')
    result=run(args.exe,args.qr_host)
    content=json.dumps(result,ensure_ascii=False,indent=2)
    args.report.write_text(content,encoding='utf-8')
    print(content)
