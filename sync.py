#!/usr/bin/env python3
"""
可靠的同步脚本 - 同步本地 -> 服务器
每次上传前删除远程文件，确保真正写入。
"""

import sys
import hashlib
import os
import paramiko
from pathlib import Path

# ========== 配置 ==========
HOST = os.environ.get('DEPLOY_HOST', '47.93.237.75')
PORT = int(os.environ.get('DEPLOY_PORT', '22'))
USER = os.environ.get('DEPLOY_USER', 'root')
PASSWORD = os.environ.get('DEPLOY_PASSWORD')
REMOTE_BASE = os.environ.get('DEPLOY_REMOTE_BASE', '/var/www/photo_gal')

SYNC_FILES = [
    'server.py',
    'requirements.txt',
    'public/index.html',
    'public/growth.html',
    'public/map.html',
    'public/poems.html',
    'public/vocabulary.html',
]

IGNORE = {'__pycache__', '.pyc', '.DS_Store', 'Thumbs.db'}

# ========== 工具 ==========

def md5(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def connect():
    if not PASSWORD:
        print('[!] Error: DEPLOY_PASSWORD not set')
        sys.exit(1)
    t = paramiko.Transport((HOST, PORT))
    t.connect(username=USER, password=PASSWORD)
    return paramiko.SFTPClient.from_transport(t), t

def sync_file(sftp, local_path, remote_path):
    """删除远程文件 -> 上传 -> 验证 MD5"""
    # 删除旧文件
    try:
        sftp.remove(remote_path)
        print(f'  [X] deleted {local_path.name}')
    except FileNotFoundError:
        pass

    # 上传
    sftp.put(str(local_path), str(remote_path))

    # 验证 MD5
    local_md5 = md5(local_path)
    with sftp.file(remote_path, 'rb') as f:
        remote_md5 = hashlib.md5(f.read()).hexdigest()

    if local_md5 != remote_md5:
        print(f'  [!] MD5 mismatch: local={local_md5[:8]} remote={remote_md5[:8]}')
        return False

    print(f'  [>] {local_path.name} -> OK ({local_md5[:8]})')
    return True

# ========== 主程序 ==========

def main():
    print(f'[*] Connecting to {USER}@{HOST}:{PORT} ...')
    sftp, transport = connect()
    print('[+] Connected\n')

    base = Path(__file__).parent.resolve()
    all_ok = True

    for item in SYNC_FILES:
        local_path = base / item
        remote_path = f'{REMOTE_BASE}/{item}'

        if not local_path.exists():
            continue

        print(f'[F] {item}')
        if not sync_file(sftp, local_path, remote_path):
            all_ok = False

    transport.close()

    if all_ok:
        print('\n[*] All synced, restarting service ...')
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD or '')
        _, stdout, stderr = ssh.exec_command('systemctl restart photo_gal')
        err = stderr.read().decode().strip()
        print('  [+] Service restarted' if not err else f'  [!] {err}')
        ssh.close()
    else:
        print('\n[!] Some files failed to sync')

    return all_ok

if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
