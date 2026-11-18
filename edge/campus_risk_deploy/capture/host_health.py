"""Low-rate host measurements; no audio/model state changes."""
from pathlib import Path
import shutil
import time

class HostHealth:
    def __init__(self):
        self.last_wall=None
        self.last_cpu=None
    def sample(self,output_dir):
        wall=time.monotonic();cpu=time.process_time()
        percent=None
        if self.last_wall is not None and wall>self.last_wall:
            percent=round(100*(cpu-self.last_cpu)/(wall-self.last_wall),1)
        self.last_wall=wall;self.last_cpu=cpu
        fields={}
        try:
            for line in Path('/proc/self/status').read_text().splitlines():
                key=line.split(':',1)[0]
                if key in ('VmRSS','VmHWM'):fields[key]=round(int(line.split()[1])/1024,1)
        except OSError:pass
        temps={}
        for path in Path('/sys/class/thermal').glob('thermal_zone*/temp'):
            try:temps[path.parent.name]=round(float(path.read_text().strip())/1000,1)
            except (OSError,ValueError):pass
        target=Path(output_dir).resolve()
        while not target.exists() and target!=target.parent:target=target.parent
        return dict(rss_mib=fields.get('VmRSS'),peak_rss_mib=fields.get('VmHWM'),
                    process_cpu_percent=percent,thermal_c=temps,
                    disk_free_gib=round(shutil.disk_usage(target).free/1024**3,2))
