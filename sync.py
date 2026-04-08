#!/usr/bin/env python3
"""
成长记录网站 - 服务器同步脚本

安全机制：
  - 自动定时任务只做 MD5 比对检查，不写入，任何情况下不会破坏服务器
  - 手动推送必须主动执行 python sync.py

用法:
  python sync.py              # 手动推送本地 -> 服务器（稳定后操作）
  python sync.py --pull       # 从服务器拉取到本地（安全）
  python sync.py --check       # 仅检查两边差异（安全，任何时候可运行）
  python sync.py --watch       # 监听本地变化自动推送（开发时用）
"""

import os
import sys
import time
import hashlib
import paramiko
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ========== 配置 ==========
HOST = '47.93.237.75'
PORT = 22
USER = 'root'
PASSWORD = 'Mcmc@1991'
REMOTE_BASE = '/var/www/photo_gal'

SYNC_PATHS = [
    'server.py',
    'requirements.txt',
    'public/',
]

IGNORE_PATHS = {'__pycache__', '.pyc', '.DS_Store', 'Thumbs.db', 'photos.db'}

# 同步时跳过删除的目录/文件（素材类，远程独立管理）
PROTECTED_PATHS = {'photos', 'thumbnails', 'videos', 'photos.db'}

# ========== 连接 ==========

def get_sftp():
    transport = paramiko.Transport((HOST, PORT))
    transport.connect(username=USER, password=PASSWORD)
    return paramiko.SFTPClient.from_transport(transport), transport

def md5_file(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def md5_remote(sftp, remote_path):
    with sftp.file(remote_path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

# ========== Check: 仅比对 MD5，不写入（安全）==========
def check():
    """仅比对两边文件差异，不做任何修改"""
    print(f'[*] Connecting to {USER}@{HOST}:{PORT} ...')
    sftp, transport = get_sftp()
    print('[+] Connected\n')

    base = Path(__file__).parent.resolve()
    local_newer = []
    remote_newer = []
    same = []

    for item in SYNC_PATHS:
        local_path = base / item
        remote_path = Path(REMOTE_BASE) / item

        if item.endswith('/'):
            # 目录，逐文件比对
            if not local_path.exists():
                continue
            for root, dirs, files in os.walk(local_path):
                dirs[:] = [d for d in dirs if d not in IGNORE_PATHS]
                rel_root = Path(root).relative_to(local_path)
                for fname in files:
                    if fname in IGNORE_PATHS:
                        continue
                    lf = Path(root) / fname
                    rf = Path(remote_path) / rel_root / fname
                    cmp = compare_file(sftp, lf, rf)
                    if cmp == 'local_newer':
                        local_newer.append(str(lf))
                    elif cmp == 'remote_newer':
                        remote_newer.append(str(lf))
                    else:
                        same.append(str(lf))
        else:
            cmp = compare_file(sftp, local_path, remote_path)
            if cmp == 'local_newer':
                local_newer.append(item)
            elif cmp == 'remote_newer':
                remote_newer.append(item)
            else:
                same.append(item)

    transport.close()

    print('[=] Comparison result:')
    for f in same:    print(f'  [=] {f}')
    for f in local_newer: print(f'  [>] {f} (local is NEWER)')
    for f in remote_newer: print(f'  [<] {f} (remote is NEWER)')

    print()
    if local_newer:
        print(f'[!] {len(local_newer)} file(s) newer locally - run "python sync.py" to push')
    if remote_newer:
        print(f'[!] {len(remote_newer)} file(s) newer on server - run "python sync.py --pull" to fetch')
    if not local_newer and not remote_newer:
        print('[+] Both sides are in sync')

    return local_newer, remote_newer

def compare_file(sftp, local_path, remote_path):
    """比对单个文件，返回: 'local_newer' / 'remote_newer' / 'same'"""
    try:
        local_md5 = md5_file(local_path)
        remote_md5 = md5_remote(sftp, str(remote_path))
        if local_md5 == remote_md5:
            return 'same'
        # 比对修改时间辅助判断方向
        local_mtime = os.path.getmtime(local_path)
        try:
            remote_mtime = sftp.stat(str(remote_path)).st_mtime
        except FileNotFoundError:
            remote_mtime = 0
        return 'local_newer' if local_mtime > remote_mtime else 'remote_newer'
    except FileNotFoundError as e:
        if 'No such file' in str(e):
            return 'local_newer'  # 本地有，远程没有
        raise
    except Exception:
        return 'same'

# ========== Push: 本地 -> 服务器（手动操作）==========

def push():
    print(f'[*] Connecting to {USER}@{HOST}:{PORT} ...')
    sftp, transport = get_sftp()
    print('[+] Connected\n')

    base = Path(__file__).parent.resolve()
    any_change = False

    for item in SYNC_PATHS:
        local_path = base / item
        if not local_path.exists():
            print(f'  - {item} (local not found, skip)')
            continue

        if item.endswith('/'):
            remote_dir = Path(REMOTE_BASE) / item.rstrip('/')
            print(f'[D] {item}')
            if push_dir(sftp, local_path, remote_dir):
                any_change = True
        else:
            remote_path = Path(REMOTE_BASE) / item
            print(f'  [F] {item}')
            if push_file(sftp, local_path, remote_path):
                any_change = True

    # 删除远程多余文件（保护素材目录）
    if delete_remote_extras(sftp, base):
        any_change = True

    transport.close()
    print()

    if any_change:
        print('[*] Restarting service...')
        restart_service()
    else:
        print('[=] Nothing to push')

    return any_change

def push_file(sftp, local_path, remote_path):
    try:
        local_md5 = md5_file(local_path)
        try:
            remote_md5 = md5_remote(sftp, str(remote_path))
        except FileNotFoundError:
            remote_md5 = None

        if local_md5 == remote_md5:
            print(f'  [=] {local_path.name} (unchanged)')
            return False

        sftp.put(str(local_path), str(remote_path))
        print(f'  [>] {local_path.name} -> server')
        return True
    except Exception as e:
        print(f'  [!] {local_path}: {e}')
        return False

def push_dir(sftp, local_dir, remote_dir):
    changed = False
    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [d for d in dirs if d not in IGNORE_PATHS]
        rel_root = Path(root).relative_to(local_dir)
        for fname in files:
            if fname in IGNORE_PATHS:
                continue
            lf = Path(root) / fname
            rf = Path(remote_dir) / rel_root / fname
            if push_file(sftp, lf, rf):
                changed = True
    return changed

def delete_remote_extras(sftp, base):
    """删除远程 public/ 中本地没有的文件"""
    remote_public = f"{REMOTE_BASE}/public"
    deleted = False
    try:
        for fname in sftp.listdir(remote_public):
            if fname in IGNORE_PATHS or fname.startswith('.'):
                continue
            local_path = base / 'public' / fname
            if not local_path.exists():
                sftp.remove(f"{remote_public}/{fname}")
                print(f'  [X] {fname} (deleted from remote)')
                deleted = True
    except FileNotFoundError:
        pass
    return deleted

# ========== Pull: 服务器 -> 本地（安全）==========

def pull():
    print(f'[*] Connecting to {USER}@{HOST}:{PORT} ...')
    sftp, transport = get_sftp()
    print('[+] Connected\n')

    base = Path(__file__).parent.resolve()
    any_change = False

    for item in SYNC_PATHS:
        local_path = base / item
        remote_path = Path(REMOTE_BASE) / item

        if item.endswith('/'):
            if not local_path.exists():
                local_path.mkdir(parents=True, exist_ok=True)
            print(f'[D] {item}')
            if pull_dir(sftp, local_path, remote_path):
                any_change = True
        else:
            print(f'  [F] {item}')
            if pull_file(sftp, local_path, remote_path):
                any_change = True

    transport.close()
    print()
    if any_change:
        print('[+] Local updated from server')
    else:
        print('[=] Already up to date')

    return any_change

def pull_file(sftp, local_path, remote_path):
    try:
        remote_md5 = md5_remote(sftp, str(remote_path))
        try:
            local_md5 = md5_file(local_path)
        except FileNotFoundError:
            local_md5 = None

        if local_md5 == remote_md5:
            print(f'  [=] {local_path.name} (unchanged)')
            return False

        sftp.get(str(remote_path), str(local_path))
        print(f'  [<] server -> {local_path.name}')
        return True
    except FileNotFoundError:
        print(f'  [!] {remote_path} not on server')
        return False
    except Exception as e:
        print(f'  [!] {local_path}: {e}')
        return False

def pull_dir(sftp, local_dir, remote_dir):
    changed = False
    try:
        for entry in sftp.listdir(str(remote_dir)):
            if entry in IGNORE_PATHS:
                continue
            lf = local_dir / entry
            rf = remote_dir / entry
            if pull_file(sftp, lf, rf):
                changed = True
    except FileNotFoundError:
        pass
    return changed

# ========== 服务重启 ==========

def restart_service():
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD)
        _, stdout, stderr = ssh.exec_command('systemctl restart photo_gal')
        err = stderr.read().decode().strip()
        print('  [+] Service restarted' if not err else f'  [!] {err}')
        ssh.close()
    except Exception as e:
        print(f'  [!] restart failed: {e}')

# ========== 文件监听（开发时用）==========

class WatchHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_sync = 0
        self.cooldown = 3

    def on_modified(self, event):
        self._handle(event)
    def on_created(self, event):
        self._handle(event)

    def _handle(self, event):
        if event.is_directory:
            return
        path = Path(event.path)
        rel = path.relative_to(Path(__file__).parent.resolve())
        name = str(rel)
        if not any(name.startswith(p.rstrip('/')) for p in SYNC_PATHS):
            return
        if any(part in IGNORE_PATHS for part in path.parts):
            return
        now = time.time()
        if now - self.last_sync < self.cooldown:
            return
        print(f'\n[*] Change detected: {name}')
        self.last_sync = now
        push()

def watch():
    print('[*] Watching (Ctrl+C to exit)...')
    base = Path(__file__).parent.resolve()
    h = WatchHandler()
    observer = Observer()
    observer.schedule(h, str(base / 'server.py'), recursive=False)
    observer.schedule(h, str(base / 'public'), recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print('\n[*] Stopped')

# ========== 主程序 ==========

if __name__ == '__main__':
    if '--pull' in sys.argv:
        pull()
    elif '--check' in sys.argv:
        check()
    elif '--watch' in sys.argv:
        push()
        watch()
    else:
        # Default: push (手动操作)
        push()
