"""Opt-in localhost integration: our Node backend + a software-only fake APP."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import desktop_runtime as desktop


def run(bundle=None):
    import websocket
    from src import dglab_sender as sender
    from src.config_loader import load_config
    if bundle:
        bundle=Path(bundle).resolve()
        desktop.backend_paths=lambda: (bundle/'runtime/node.exe',bundle/'backend/src/managed.js')
    else:
        node,_=desktop.backend_paths()
        desktop.backend_paths=lambda: (node,desktop.ROOT/'build/main_backend_minimal/src/managed.js')
    # All participants are local software; no route lookup, QR window or APP.
    sender._get_lan_ip=lambda:'127.0.0.1'
    qr=threading.Event()
    sender._DglabWSClient._show_qrcode=lambda self,url:qr.set()
    records=[]
    app=None
    with tempfile.TemporaryDirectory(prefix='helldiver-loopback-') as directory:
        backend=desktop.OfficialBackend(directory,records.append)
        try:
            port=backend.start()
            config=load_config(desktop.ROOT/'config.example.ini')
            config['sender']['ws_port']=port
            config['sender']['ws_host']='127.0.0.1'
            client=sender.init_sender(config)
            if not qr.wait(5):
                raise RuntimeError('Sender registration timed out')
            app=websocket.create_connection(f'ws://127.0.0.1:{port}',timeout=3,
                                             http_proxy_host=None)
            app_id=json.loads(app.recv())['clientId']
            app.send(json.dumps({'type':'bind','clientId':client.client_id,
                                 'targetId':app_id,'message':'DGLAB'}))
            received=[]
            stop_reader=threading.Event()
            app.settimeout(.1)
            def read():
                while not stop_reader.is_set():
                    try:
                        raw=app.recv()
                        if not raw: return
                        received.append(json.loads(raw))
                    except websocket.WebSocketTimeoutException:
                        continue
                    except websocket.WebSocketConnectionClosedException:
                        return
            reader=threading.Thread(target=read,daemon=True)
            reader.start()
            if not sender.wait_for_pair(5):
                raise RuntimeError('Software APP pairing failed')
            before=len(received)
            for _ in range(5):
                sender.update_continuous(low_health=True,healing=False,hp=.5)
                time.sleep(.12)
            messages=[str(m.get('message','')) for m in received[before:]]
            if not any(m.startswith('pulse-A:') for m in messages):
                raise RuntimeError('No waveform received by software APP: '+repr(messages))
            if not any(m=='strength-1+2+10' for m in messages):
                raise RuntimeError('No expected low-health strength: '+repr(messages))
            sender.stop_output()
            time.sleep(.5)
            tail=len(received)
            time.sleep(1.2)
            late=[str(m.get('message','')) for m in received[tail:]]
            if any(m.startswith('pulse-') or (m.startswith('strength-') and not m.endswith('+0')) for m in late):
                raise RuntimeError('Output returned after stop: '+repr(late))
            all_messages=[str(m.get('message','')) for m in received]
            if 'strength-1+2+0' not in all_messages or 'clear-1' not in all_messages:
                raise RuntimeError('Stop did not clear and zero')
            sender.safe_stop()
            stop_reader.set()
            reader.join(2)
            app.close()
            app=None
            backend.close()
            if backend.process.poll() is None:
                raise RuntimeError('Owned backend did not exit')
            return {'status':'ok','transport':'real local WebSocket',
                    'peer':'software-only fake APP','pairing':True,'pulse_received':True,
                    'low_health_strength':10,'stop_zero':True,'no_late_output':True,
                    'backend_exit':backend.process.returncode,'physical_device_used':False}
        finally:
            sender.safe_stop()
            if app is not None:
                app.close()
            backend.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-local',action='store_true')
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if not args.run_local:
        parser.error('Explicit --run-local is required to start the owned test backend')
    content=json.dumps(run(args.bundle),ensure_ascii=False,indent=2)
    if args.report:
        args.report.write_text(content,encoding='utf-8')
    print(content)
