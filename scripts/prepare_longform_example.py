"""Clarify the example narration and save a draft through the revision API.

The product's teaching-card action handles illustration; this script never
approves content or writes directly to the database.
"""
from copy import deepcopy
import json
from pathlib import Path
import argparse

import httpx
from app.config import settings

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'reports/content-longform-20261015'
STATE=OUTPUT/'live-flow.json'
PID='63212d46-b1b9-50d0-a2c2-bcea5f4d5038'


def main():
    parser=argparse.ArgumentParser()
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--draft-only',action='store_true',help='Refine the example disclosure before drawing its source cards')
    modes.add_argument('--save-edit',action='store_true',help='Save the reviewed teaching edit through the application revision API')
    args=parser.parse_args()
    state=json.loads(STATE.read_text())
    assert state['project_id']==PID
    if state.get('production_content_id'):
        print('Illustrated revision already saved; use the existing version')
        return
    with httpx.Client(base_url='http://127.0.0.1:8017',headers={'Origin':'http://127.0.0.1:8017'},timeout=30) as client:
        r=client.post('/api/auth/login',json={'email':settings.bootstrap_email,'password':settings.bootstrap_password});r.raise_for_status()
        try:
            r=client.get('/api/projects/'+PID+'/contents');r.raise_for_status()
            original=next(c for c in r.json() if c['id']==state['content_id'])
            if args.draft_only:
                board=deepcopy(original['storyboard']);shots=board['shots']
                (OUTPUT/'raw-model-storyboard.json').write_text(json.dumps(board,ensure_ascii=False,indent=2)+'\n')
                shots[0]['voiceover']='用一组虚构住宅资料练一遍看房核对。329㎡、8.2米开间和4房同层，怎样变成你家的核对清单？'
                shots[1]['voiceover']='假设家里有孩子需要学习空间、长辈希望少绕路，你和伴侣希望安静休息。这些不同的需求，该分别核对哪些条件？'
                shots[2]['voiceover']='画面仅作空间示意，不代表实际房源。数字也来自虚构示例资料。我们用它练习区分资料依据与待核对的问题。'
                shots[4]['voiceover']='先填清单第一行：329㎡的口径写建筑面积。把“包含哪些部分、哪个文件能确认”放进待核对栏，先记录问题，保留结论。'
                shots[4]['caption']='清单第一行：口径、文件、待核对项'
                area=next(ref for ref in original['fact_refs'] if ref['id']=='e9661866-c5da-54af-aaa3-7bc121feb29c')
                if area not in shots[4]['fact_refs']:shots[4]['fact_refs'].append(deepcopy(area))
                (OUTPUT/'storyboard.json').write_text(json.dumps(board,ensure_ascii=False,indent=2)+'\n')
                (OUTPUT/'script.txt').write_text('\n'.join(s['voiceover'] for s in shots)+'\n')
                print('Example opening clarified; local storyboard ready for teaching cards')
                return
            board=json.loads((OUTPUT/'storyboard.json').read_text());shots=board['shots']
            assert [s['id'] for s in shots]==[s['id'] for s in original['storyboard']['shots']]
            if args.save_edit:
                r=client.post('/api/projects/'+PID+'/contents/'+original['id']+'/revise',json={
                    'script':'\n'.join(s['voiceover'] for s in shots),'storyboard':board,
                    'cover_title':'看房核对清单 · 虚构资料示例','fact_refs':original['fact_refs']})
                r.raise_for_status();revised=r.json()
                state.update(production_content_id=revised['id'],content_id=revised['id'],stage='teaching_edit_saved')
                STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
                print(json.dumps({'new_version':revised['version'],'approval':'pending'},ensure_ascii=False))
                return
        finally:
            client.post('/api/auth/logout')


if __name__=='__main__':main()
