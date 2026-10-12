"""Hot-read only dashboard settings, without changing the trading process env."""
from pathlib import Path
import re
import threading

KEYS = frozenset({'DASHBOARD_REFRESH_SECONDS','NEWS_REFRESH_SECONDS','DASHBOARD_CHAT_MODEL'})
_lock=threading.RLock()
_cache={}

def values(root=None):
    root=Path(root) if root is not None else Path(__file__).resolve().parent
    path=root/'.env'
    try:
        st=path.stat()
        if st.st_size>262144:return {}
        identity=(st.st_mtime_ns,st.st_size,getattr(st,'st_ino',None))
        with _lock:
            old=_cache.get(str(path))
            if old and old[0]==identity:return dict(old[1])
            text=path.read_text(encoding='utf-8-sig')
            out={};duplicate=set()
            for line in text.splitlines():
                m=re.fullmatch(r'\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.*?)\s*',line)
                if not m or m[1] not in KEYS:continue
                key,raw=m.groups()
                if key in out:duplicate.add(key)
                if len(raw)>=2 and raw[0]==raw[-1] and raw[0] in '\"\'':raw=raw[1:-1]
                else:raw=re.split(r'\s+#',raw,maxsplit=1)[0].strip()
                out[key]=raw
            for key in duplicate:out.pop(key,None)
            _cache[str(path)]=(identity,out)
            return dict(out)
    except (OSError,UnicodeError):return {}

def get_int(name,default,minimum,maximum,root=None):
    if name not in KEYS:raise ValueError('Unsupported dashboard setting')
    raw=values(root).get(name)
    if raw is None:return default
    if not re.fullmatch(r'[1-9][0-9]{0,8}',raw):return default
    n=int(raw)
    return n if minimum<=n<=maximum else default

def chat_model(default,root=None):
    raw=values(root).get('DASHBOARD_CHAT_MODEL')
    if raw and re.fullmatch(r'[A-Za-z][A-Za-z0-9._/-]{0,99}',raw) and '..' not in raw and '//' not in raw:return raw
    return default
