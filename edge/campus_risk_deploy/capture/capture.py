#!/usr/bin/env python3
"""Monitor XMOS VAD, or explicitly arm automatic USB segment collection."""
import argparse
import datetime
import json
import os
from pathlib import Path
import time
import wave
from auto_protocol import Parser,Segment,validate_overlap
from contextlib import nullcontext
from host_health import HostHealth
from reliable_usb import ReliableReader

def main():
    import serial
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_68:EE:8F:4F:CF:30-if00')
    p.add_argument('--seconds',type=float,default=30)
    p.add_argument('--arm',action='store_true',help='Explicitly enable automatic audio capture')
    p.add_argument('--out-dir',type=Path,default=Path('recordings_arq'))
    p.add_argument('--clock-sync',default='unknown')
    p.add_argument('--metrics-every',type=float,default=10,help='Host measurement interval, 0 disables')
    args=p.parse_args()
    if args.seconds<0:p.error('seconds must be zero (continuous) or positive')
    if args.metrics_every<0:p.error('metrics-every must not be negative')
    parser=Parser();segment=None;previous=None;saved=0;requested=False;stopped=False;active=False
    sync_count=0;sync_sample=None;crc_baseline=None;startup_crc=0;sync_crc_seen=0;last_reported_crc=0
    health=HostHealth();last_health=None;clock_anchor=None;segment_received=None
    kwargs=dict(port=None,baudrate=921600,timeout=0.2,write_timeout=2)
    if os.name=='posix':kwargs['exclusive']=True
    port=serial.Serial(**kwargs);port.dtr=False;port.rts=False;port.port=args.port
    session=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    reader=ReliableReader(port,parser)
    with nullcontext(None) as worker, port:
        port.dtr=True
        port.write(b'STOP\n');port.flush()
        # Drain the old USB FIFO after STOP. A reconnect can begin mid-frame.
        print('SYNCING: stopping device and draining old USB data...',flush=True)
        drain_deadline=time.monotonic()+1.5
        while time.monotonic()<drain_deadline:
            reader.poll()
        startup_crc=parser.crc_errors
        parser=Parser();reader.parser=parser
        started=time.monotonic();last_status=started;ready=False
        sync_deadline=started+8;arm_sent_at=None
        try:
            while args.seconds==0 or time.monotonic()-started<args.seconds:
                rows=reader.poll()
                if crc_baseline is None and parser.crc_errors!=sync_crc_seen:
                    sync_count=0;sync_sample=None;sync_crc_seen=parser.crc_errors
                if crc_baseline is not None and parser.crc_errors!=last_reported_crc:
                    print('CRC_DISCARDED_AWAITING_RETRY',parser.crc_errors-crc_baseline,
                          json.dumps(parser.last_error),flush=True)
                    last_reported_crc=parser.crc_errors
                for stream,seq,kind,payload in rows:
                    if kind==4:
                        status=json.loads(payload)
                        if status.get('firmware')!='usb_auto_v2_arq':raise ValueError('Wrong firmware')
                        print('VAD_STATUS',json.dumps(status),flush=True)
                        last_status=time.monotonic();ready=True
                        if clock_anchor is None and isinstance(status.get('samples'),int):
                            clock_anchor=(status['samples'],datetime.datetime.now(datetime.timezone.utc))
                        if not stopped:
                            sample=status.get('samples')
                            if not status['armed'] and not status['recording'] and isinstance(sample,int):
                                sync_count=sync_count+1 if sync_sample is not None and sample>sync_sample else 1
                                sync_sample=sample
                            else:
                                sync_count=0;sync_sample=None
                            if sync_count<2:continue
                            stopped=True
                            crc_baseline=parser.crc_errors;last_reported_crc=crc_baseline
                            print('SYNC_OK startup_crc_errors',startup_crc+crc_baseline,flush=True)
                            started=time.monotonic()
                            if args.arm:
                                if not status['vad_ok']:raise ValueError('XMOS VAD read failed; refusing to arm')
                                port.write(b'ARM\n');port.flush();requested=True;arm_sent_at=time.monotonic()
                        if requested and status['armed'] and not active:
                            active=True;started=time.monotonic()
                            print('ARMED_READY: start quiet/speech/quiet test now',flush=True)
                        if active and not status['armed']:raise ValueError('Device disarmed unexpectedly')
                        continue
                    if not active:continue
                    if kind==1:
                        if segment is not None:raise ValueError('Previous segment unfinished')
                        segment=Segment(stream,seq,payload)
                        segment_received=datetime.datetime.now(datetime.timezone.utc).isoformat()
                        print('SEGMENT_START',stream,'prefix',segment.meta['prefix_frames']/16000,flush=True)
                    elif segment is not None:
                        segment.accept(stream,seq,kind,payload)
                        if segment.done:
                            linked=validate_overlap(previous,segment) if previous else False
                            args.out_dir.mkdir(parents=True,exist_ok=True)
                            out=args.out_dir/f'usb_auto_{session}_{saved+1:04d}_{stream:08x}.wav'
                            with wave.open(str(out.with_suffix('.partial')),'wb') as w:
                                w.setnchannels(2);w.setsampwidth(2);w.setframerate(16000);w.writeframes(segment.pcm)
                            os.replace(out.with_suffix('.partial'),out)
                            report=dict(segment.meta,transport='USB_CDC',seconds=15,packets=segment.seq,
                                        crc_errors=0,discarded_crc_frames=parser.crc_errors-crc_baseline,
                                        duplicate_frames=reader.duplicates,startup_crc_errors=startup_crc+crc_baseline,
                                        overlap_with_previous_verified=linked,listening_verified=False)
                            anchor_sample,anchor_time=clock_anchor
                            capture_start=anchor_time+datetime.timedelta(seconds=(segment.meta['start_sample']-anchor_sample)/16000)
                            capture_end=capture_start+datetime.timedelta(seconds=15)
                            report.update(session=session,firmware='usb_auto_v2_arq',protocol='RUSBAUT2',
                                          usb_port=args.port,capture_start_utc=capture_start.isoformat(),
                                          capture_end_utc=capture_end.isoformat(),
                                          capture_start_local=capture_start.astimezone().isoformat(),
                                          capture_end_local=capture_end.astimezone().isoformat(),
                                          segment_start_received_at=segment_received,
                                          audio_saved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                          timestamp_quality='estimated_from_ESP_sample_counter_and_host_USB_status_receipt; USB latency and oscillator drift not calibrated',
                                          time_sync=args.clock_sync,
                                          timestamp_anchor=dict(sample=anchor_sample,host_utc=anchor_time.isoformat()))
                            meta_path=out.with_suffix('.json');temp=meta_path.with_suffix('.json.tmp')
                            temp.write_text(json.dumps(report,indent=2),encoding='utf-8')
                            os.replace(temp,meta_path)
                            saved+=1;print('AUTO_WAV_OK',out,'overlap_verified',linked,flush=True)
                            if worker:
                                queued=worker.submit(out)
                                print('INFERENCE_QUEUED' if queued else 'INFERENCE_BACKLOG',out,flush=True)
                            previous=segment;segment=None
                if time.monotonic()-last_status>8:raise ValueError('No fresh firmware/VAD status for 8 seconds')
                if not stopped and time.monotonic()>sync_deadline:raise ValueError('STOP synchronization timed out')
                if requested and not active and time.monotonic()-arm_sent_at>8:raise ValueError('ARM acknowledgement timed out')
                if args.metrics_every and (last_health is None or time.monotonic()-last_health>=args.metrics_every):
                    last_health=time.monotonic()
                    measurement=health.sample(args.out_dir)
                    measurement.update(session=session,elapsed_seconds=round(last_health-started,1),
                                       discarded_crc_frames=parser.crc_errors-(crc_baseline or 0),duplicate_frames=reader.duplicates)
                    if worker:measurement['inference_queue']=worker.stats()
                    print('HOST_HEALTH',json.dumps(measurement),flush=True)
            if not ready or not stopped:raise ValueError('Could not synchronize with stopped automatic firmware')
            if args.arm and not active:raise ValueError('Device did not acknowledge ARM')
        finally:
            try:port.write(b'STOP\n');port.flush()
            except OSError as e:print('STOP could not be delivered:',e,flush=True)
    print('AUTO_SESSION_END',saved,'complete segments;', 'partial discarded' if segment else 'no partial segment',flush=True)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('Stopped by user; incomplete segment not saved')
    except (OSError,ValueError) as e:raise SystemExit(f'AUTO_CAPTURE_FAILED: {e}')

