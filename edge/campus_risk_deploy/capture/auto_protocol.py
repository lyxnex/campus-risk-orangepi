"""Framing and strict segment validation for USB automatic capture v1."""
import struct
import zlib
MAGIC=b'RUSBAUT2'
HEADER=struct.Struct('<8sIIHHI')

class Parser:
    def __init__(self):
        self.buffer=bytearray()
        self.crc_errors=0
        self.last_error=None
    def feed(self,data):
        self.buffer.extend(data)
        rows=[]
        while True:
            at=self.buffer.find(MAGIC)
            if at<0:
                self.buffer[:]=self.buffer[-7:]
                return rows
            del self.buffer[:at]
            if len(self.buffer)<24:return rows
            _,stream,seq,n,kind,crc=HEADER.unpack_from(self.buffer)
            if n>1000 or kind not in (1,2,3,4):
                del self.buffer[0]
                continue
            if len(self.buffer)<24+n:return rows
            payload=bytes(self.buffer[24:24+n])
            if zlib.crc32(payload,zlib.crc32(self.buffer[:20]))!=crc:
                self.crc_errors+=1
                self.last_error=dict(stream=stream,sequence=seq,kind=kind,payload_bytes=n,
                                     expected_crc=crc,actual_crc=zlib.crc32(payload,zlib.crc32(self.buffer[:20])))
                del self.buffer[0]
                continue
            del self.buffer[:24+n]
            rows.append((stream,seq,kind,payload))

class Segment:
    def __init__(self,stream,seq,payload):
        if len(payload)!=32 or seq!=0:raise ValueError('Invalid START')
        rate,channels,bits,frames,tx,prefix,drops,start=struct.unpack('<IHHIIIIQ',payload)
        if (rate,channels,bits,frames)!=(16000,2,16,240000):raise ValueError('Wrong PCM format')
        if prefix not in (0,32000):raise ValueError('Wrong prefix size')
        self.meta=dict(stream=stream,rate=rate,channels=channels,bits=bits,frames=frames,
                       prefix_frames=prefix,start_sample=start,tx_before=tx,drops_before=drops)
        self.pcm=bytearray()
        self.seq=0
        self.done=False
    def accept(self,stream,seq,kind,payload):
        if stream!=self.meta['stream'] or seq!=self.seq or self.done:
            raise ValueError('Stream/sequence mismatch')
        if kind==2:
            if len(payload)!=1000 or len(self.pcm)+1000>960000:raise ValueError('Wrong audio size')
            self.pcm.extend(payload)
            self.seq+=1
        elif kind==3:
            if len(payload)!=12:raise ValueError('Wrong END size')
            frames,drops,tx=struct.unpack('<III',payload)
            if frames!=240000 or len(self.pcm)!=960000:raise ValueError('Incomplete segment')
            if drops or tx!=self.meta['tx_before']:raise ValueError('Device reported data loss')
            self.done=True
        else:raise ValueError('Unexpected frame kind')

def validate_overlap(previous,current):
    linked=current.meta['start_sample']==previous.meta['start_sample']+208000
    if linked and current.pcm[:128000]!=previous.pcm[-128000:]:
        raise ValueError('Adjacent 2-second overlap bytes do not match')
    return linked
