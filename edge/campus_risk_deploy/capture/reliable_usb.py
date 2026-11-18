"""ACK valid frames immediately, deduplicate retransmissions, never ACK corrupt data."""
from collections import deque
import struct
import zlib
from auto_protocol import MAGIC

def frame_crc(row):
    stream,seq,kind,payload=row
    prefix=struct.pack('<8sIIHH',MAGIC,stream,seq,len(payload),kind)
    return zlib.crc32(payload,zlib.crc32(prefix))

class ReliableReader:
    def __init__(self,port,parser):
        self.port=port;self.parser=parser
        self.recent=deque();self.seen=set();self.duplicates=0
    def poll(self):
        # Waiting to fill an 8192-byte read would impose 200ms per ACK.
        # Block for one byte, then consume currently available bytes immediately.
        available=self.port.in_waiting
        raw=self.port.read(max(1,min(available,8192)))
        rows=self.parser.feed(raw);accepted=[]
        for row in rows:
            message=f'ACK {frame_crc(row):08X}\n'.encode('ascii')
            if self.port.write(message)!=len(message):raise OSError('Short ACK write')
            key=row
            if key in self.seen:
                self.duplicates+=1;continue
            self.seen.add(key);self.recent.append(key)
            if len(self.recent)>128:self.seen.remove(self.recent.popleft())
            accepted.append(row)
        return accepted
