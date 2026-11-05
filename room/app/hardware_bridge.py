"""Optional USB adapter. Configure action/event names AFTER confirming hardware.
Run: python -m app.hardware_bridge --help
No firmware or port assumptions are made by this adapter.
"""
import argparse
import time
import httpx
from atech import Board

COLORS={'ready':{'r':30,'g':150,'b':30},'clarify':{'r':150,'g':80,'b':0},'working':{'r':0,'g':60,'b':150},'idle':{'r':0,'g':0,'b':0}}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',required=True)
    p.add_argument('--led-action',required=True,help='Exact fill action from atech describe')
    p.add_argument('--button-key',required=True,help='Exact button event key from atech describe')
    p.add_argument('--url',default='http://127.0.0.1:8765')
    args=p.parse_args()
    last_color=None;last_button=None
    with Board.connect(port=args.port) as board, httpx.Client(base_url=args.url,timeout=5) as client:
        while True:
            response=client.get('/api/state');response.raise_for_status()
            tasks=response.json()['tasks']
            status=next((s for s in ['clarify','working','ready'] if any(t['status']==s and (s!='ready' or not t['shown']) for t in tasks)),'idle')
            if status!=last_color:
                board.call(args.led_action,COLORS[status]);last_color=status
            event=board.latest(args.button_key)
            if event is not None and event is not last_button:
                last_button=event
                if event.value is True or event.value in ('pressed','tap'):
                    task=next((t for t in reversed(tasks) if t['status']=='ready' and not t['shown']),None)
                    if task:
                        result=client.post('/api/action',json={'command':'show','id':task['id']});result.raise_for_status()
            time.sleep(.25)

if __name__=='__main__': main()
