"""Password login for people; device bearer authentication stays separate."""
from collections import deque
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import secrets
import threading
import time

COOKIE = 'campus_review_session'

def password_hash(password):
    if not isinstance(password,str) or not 8 <= len(password) <= 128:
        raise ValueError('Password must contain 8 to 128 characters')
    salt=secrets.token_hex(16); rounds=600000
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),rounds).hex()
    return f'pbkdf2_sha256${rounds}${salt}${digest}'

def verify_password(password,encoded):
    if not isinstance(password,str) or not 1 <= len(password) <= 128:return False
    try:
        algorithm,rounds,salt,digest=encoded.split('$')
        if algorithm!='pbkdf2_sha256' or not 600000 <= int(rounds) <= 2000000:return False
        actual=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),int(rounds)).hex()
        return hmac.compare_digest(actual,digest)
    except (ValueError,TypeError,AttributeError):return False

class RateLimited(ValueError):pass

class WebAuth:
    def __init__(self,encoded,lifetime=28800):
        self.encoded=encoded
        self.lifetime=lifetime
        self.sessions={};self.failures={};self.lock=threading.Lock()

    def login(self,password,ip):
        with self.lock:
            now=time.time()
            self.failures={k:deque(t for t in v if now-t<60) for k,v in self.failures.items() if v and now-v[-1]<60}
            attempts=self.failures.setdefault(ip,deque())
            if len(attempts)>=5:raise RateLimited('尝试次数过多，请一分钟后重试')
            if not self.encoded or not verify_password(password,self.encoded):
                attempts.append(now);return None
            self.failures.pop(ip,None)
            self.sessions={k:v for k,v in self.sessions.items() if v['expires']>now}
            sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
            record=dict(csrf=csrf,expires=now+self.lifetime)
            self.sessions[hashlib.sha256(sid.encode()).hexdigest()]=record
            return sid,record

    def get(self,cookie):
        try:
            c=SimpleCookie();c.load(cookie or '')
            value=c[COOKIE].value
        except (CookieError,KeyError):return None
        key=hashlib.sha256(value.encode()).hexdigest()
        with self.lock:
            record=self.sessions.get(key)
            if not record or record['expires']<=time.time():
                self.sessions.pop(key,None);return None
            return dict(record)

    def logout(self,cookie):
        try:
            c=SimpleCookie();c.load(cookie or '');key=hashlib.sha256(c[COOKIE].value.encode()).hexdigest()
        except (CookieError,KeyError):return
        with self.lock:self.sessions.pop(key,None)

def session_cookie(value,secure=False,max_age=28800):
    return f'{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}'+('; Secure' if secure else '')
