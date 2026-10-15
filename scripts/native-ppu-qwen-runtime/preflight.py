#!/usr/bin/env python3
"""Read-only admission guard for the sole deployment's physical PPU 0."""
import csv
import json
import socket
import subprocess
import time
from pathlib import Path

SMI='/usr/local/PPU_SDK/ppu-smi/bin/ppu-smi'
ROOT=Path('/mnt/data/workdir/yanlun/housignal/native_qwen_runtime')
MODEL=Path('/mnt/data/workdir/yanlun/experiments/minimax_h3_gwm_compare_20260922/models/text_encoder')

def assert_idle(device_csv,process_csv):
    devices=list(csv.reader(device_csv.splitlines(),skipinitialspace=True))
    try:
        rows=[r for r in devices if len(r)==5 and int(r[0])==0]
        if len(rows)!=1:raise RuntimeError('Physical PPU 0 must be uniquely observable')
        _,device_uuid,used,total,util=rows[0]
        used,total,util=int(used),int(total),int(util)
        if used>128 or total-used<90000 or util!=0:raise RuntimeError('PPU 0 lacks proven idle capacity')
        for row in csv.reader(process_csv.splitlines(),skipinitialspace=True):
            if row and row[0]==device_uuid:raise RuntimeError('A compute process already occupies PPU 0')
    except (ValueError,IndexError) as error:raise RuntimeError('Invalid PPU resource observation') from error
    return {'physical_ppu':0,'free_memory_mib':total-used,'used_memory_mib':used,'utilization_percent':util,'compute_processes':0}

def assert_loopback_port_available(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        sock.bind(("127.0.0.1",port))


def observe():
    args=[SMI,'--query-ppu=index,uuid,memory.used,memory.total,utilization.ppu','--format=csv,noheader,nounits']
    devices=subprocess.check_output(args,text=True,timeout=15)
    processes=subprocess.check_output([SMI,'--query-compute-apps=ppu_uuid,pid,used_memory','--format=csv,noheader,nounits'],text=True,timeout=15)
    return assert_idle(devices,processes),devices,processes

if __name__=='__main__':
    from native_runtime import checkpoint_manifest
    observations=[]
    for i in range(2):
        state,devices,processes=observe()
        observations.append({'observed_epoch':time.time(),'state':state,'device_csv':devices,'compute_process_csv':processes})
        if i==0:time.sleep(2)
    assert_loopback_port_available(8891)
    manifest=checkpoint_manifest(MODEL)
    (ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'resource-before.json').write_text(json.dumps(observations,indent=2)+'\n')
    print(json.dumps({'preflight':'passed','physical_ppu':0,'port':8891,'manifest_id':manifest['manifest_id']}))
