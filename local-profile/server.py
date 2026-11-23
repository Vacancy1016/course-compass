"""Loopback-only identity service. Student profiles stay in browser storage."""
from contextlib import closing
import argparse
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
TTL = 8 * 3600
ASSETS = {'/': 'index.html', '/index.html': 'index.html', '/vault.js': 'vault.js'}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def derive(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64*1024*1024)


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Handler(BaseHTTPRequestHandler):
    def send_data(self, status, data, mime='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self, db):
        host = self.headers.get('Host', '')
        port = self.server.server_port
        if host not in (f'localhost:{port}', f'127.0.0.1:{port}'):
            raise Problem(403, '请使用本机地址。')
        path = urlsplit(self.path).path
        if self.command == 'GET' and path in ASSETS:
            name = ASSETS[path]
            return self.send_data(200, (ROOT/name).read_bytes(), mimetypes.guess_type(name)[0] or 'text/javascript')
        if self.headers.get('Origin') not in (None, 'http://'+host):
            raise Problem(403, '请求来源不匹配。')
        now = int(time.time())
        if self.command == 'POST':
            length = int(self.headers.get('Content-Length', 0))
            if self.headers.get_content_type() != 'application/json' or not 0 < length <= 4096:
                raise Problem(400, '请求格式无效。')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise Problem(400, '请求格式无效。')
        if self.command == 'POST' and path in ('/api/register', '/api/login'):
            name, password = payload.get('name'), payload.get('password')
            if not isinstance(name,str) or not isinstance(password,str) or not 1 <= len(name.strip()) <= 60 or not 8 <= len(password) <= 256:
                raise Problem(400, '账号需为 1–60 个字符，密码需为 8–256 个字符。')
            name = name.strip().casefold()
            account = db.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
            if path == '/api/register':
                if account:
                    raise Problem(409, '该账号已存在，请登录或换一个名称。')
                salt = secrets.token_bytes(16)
                uid = secrets.token_hex(16)
                db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)', (uid,name,salt,derive(password,salt),secrets.token_hex(32),secrets.token_hex(16)))
            else:
                count, until = self.server.failures.get(name,(0,0))
                if until <= now: count = 0
                if count >= 8: raise Problem(429,'尝试过于频繁，请稍后再试。')
                check = derive(password, account['salt'] if account else bytes(16))
                if not account or not hmac.compare_digest(check, account['verifier']):
                    self.server.failures[name]=(count+1,now+60)
                    raise Problem(401, '账号或密码错误。')
                self.server.failures.pop(name,None)
                uid = account['id']
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expiry<=?',(now,))
            db.execute('INSERT INTO sessions VALUES (?,?,?)',(digest(token),uid,now+TTL))
            return {'token':token}
        token = self.headers.get('Authorization','').removeprefix('Bearer ')
        account = db.execute('SELECT u.*, s.expiry FROM users u JOIN sessions s ON s.uid=u.id WHERE s.digest=? AND s.expiry>?',(digest(token),now)).fetchone()
        if not account: raise Problem(401,'请登录本人账号。')
        if self.command == 'GET' and path == '/api/session':
            return {k:account[k] for k in ('id','name','key','generation','expiry')}
        if self.command == 'POST' and path == '/api/logout':
            db.execute('DELETE FROM sessions WHERE digest=?',(digest(token),))
            return {'ok':True}
        if self.command == 'POST' and path == '/api/profile/delete':
            if payload.get('generation') != account['generation']:
                raise Problem(409,'档案已变更，请刷新页面。')
            changed = db.execute('UPDATE users SET key=?,generation=? WHERE id=? AND generation=?',
                                 (secrets.token_hex(32),secrets.token_hex(16),account['id'],account['generation']))
            if changed.rowcount != 1: raise Problem(409,'档案已变更，请刷新页面。')
            db.execute('DELETE FROM sessions WHERE uid=? AND digest<>?',(account['id'],digest(token)))
            return {'ok':True}
        raise Problem(404,'未找到该操作。')

    def handle_request(self):
        try:
            with closing(sqlite3.connect(self.server.db_path)) as db, db:
                db.row_factory=sqlite3.Row
                result = self.dispatch(db)
            if result is not None: self.send_data(200,result)
        except Problem as e: self.send_data(e.status,{'error':e.message})
        except (UnicodeError,ValueError): self.send_data(400,{'error':'请求格式无效。'})
        except sqlite3.IntegrityError: self.send_data(409,{'error':'账号已存在，请重新登录。'})
        except sqlite3.Error: self.send_data(503,{'error':'身份服务暂时不可用。'})

    do_GET = do_POST = handle_request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',type=Path,default=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'CourseCompass')
    args=parser.parse_args()
    args.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.failures={}
    server.db_path=args.data_dir/'identity.sqlite3'
    with closing(sqlite3.connect(server.db_path)) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT UNIQUE,salt BLOB,verifier BLOB,key TEXT,generation TEXT);
            CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,uid TEXT,expiry INTEGER);''')
    print(f'Course Compass: http://127.0.0.1:{server.server_port}/',flush=True)
    server.serve_forever()

if __name__=='__main__': main()
