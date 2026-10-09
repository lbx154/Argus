"""Initialize private preview accounts and install bounded user services."""
from __future__ import annotations

import argparse
import json
import secrets
import shlex
import subprocess
from pathlib import Path


def initialize(args) -> dict:
    root = args.root.resolve()
    source = args.source.resolve()
    if root == source or source in root.parents:
        raise ValueError("Private trial state must be outside the source checkout")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    path = root / 'preview.json'
    if path.exists():
        return json.loads(path.read_text())
    config = {'model': 'gpt-6.1-sol', 'revision': args.revision, 'source': str(source),
              'venv': str(args.venv.resolve()), 'python_runtime': str(args.python_runtime.resolve()),
              'node_runtime': str(args.node_runtime.resolve()), 'copilot_package': str(args.copilot_package.resolve()),
              'copilot_home': str(Path.home() / '.copilot'), 'ledger': str(root / 'usage.sqlite3'),
              'meter_runtime': str(root / 'meter'), 'total_usd': 50, 'user_usd': 10,
              'session_secret': secrets.token_hex(32), 'relay_secret': secrets.token_hex(32), 'tenants': {}}
    (root / 'meter').mkdir(mode=0o700)
    for n in range(1, 6):
        name = f'trial{n}'
        directory = root / name
        directory.mkdir(mode=0o700)
        for part in ('home', 'home/.copilot', 'state', 'run', 'workspace', 'bin'):
            (directory / part).mkdir(mode=0o700)
        node = args.node_runtime.resolve()
        wrapper = directory / 'bin/copilot'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(node / 'bin/node')) + ' ' +
                           shlex.quote(str(node / 'lib/node_modules/@github/copilot/npm-loader.js')) + ' "$@"\n')
        wrapper.chmod(0o700)
        (directory / 'state/identity.md').write_text(
            '你是研究和编程助手。用简明中文说明做了什么、得到什么结果、还需要什么信息。'
            '默认工作目录为 /tenant/workspace。解释结果时少用内部代码、角色名和工具参数。\n')
        config['tenants'][name] = {'directory': str(directory), 'socket': str(directory / 'run/web.sock'),
                                   'invite': 'argus-' + secrets.token_urlsafe(24),
                                   'model_token': secrets.token_urlsafe(48), 'web_token': secrets.token_urlsafe(48)}
    path.write_text(json.dumps(config, indent=2))
    path.chmod(0o600)
    invitations = root / 'invitations.txt'
    invitations.write_text('\n'.join(f'{name}: {row["invite"]}' for name, row in config['tenants'].items()) + '\n')
    invitations.chmod(0o600)
    return config


def install_services(config: dict, root: Path) -> None:
    source = Path(config['source'])
    python = str(Path(config['venv']) / 'bin/python')
    units = Path.home() / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    commands = {
        'argus-preview-meter': [python, str(source / 'deploy/trial/native_preview.py'), 'meter', '--config', str(root / 'preview.json'), '--uds', str(root / 'meter/model.sock')],
        'argus-preview-egress': [python, str(source / 'deploy/trial/native_egress.py'), '--socket', str(root / 'meter/egress.sock')],
        'argus-preview-portal': [python, str(source / 'deploy/trial/native_preview.py'), 'portal', '--config', str(root / 'preview.json'), '--port', '18871'],
    }
    for name in config['tenants']:
        commands['argus-preview-' + name] = [python, str(source / 'deploy/trial/native_runtime.py'), 'launch', '--config', str(root / 'preview.json'), '--tenant', name]
    for name, command in commands.items():
        # systemd command lines have their own quoting and percent expansion.
        executable = ' '.join(json.dumps(arg.replace('%', '%%')) for arg in command)
        memory = '8G' if name.startswith('argus-preview-trial') else '512M'
        text = f'''[Unit]
Description=Argus invitation preview {name}
After=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=4
[Service]
Type=simple
WorkingDirectory={str(source).replace("%", "%%")}
ExecStart={executable}
Environment={json.dumps('PYTHONPATH=' + str(source))}
Environment=PYTHONUNBUFFERED=1
UMask=0077
StandardOutput=append:{root / (name + '.log')}
StandardError=append:{root / (name + '.log')}
NoNewPrivileges=yes
Restart=on-failure
RestartSec=5
MemoryMax={memory}
CPUQuota=100%
TasksMax=512
KillMode=control-group
TimeoutStopSec=15
[Install]
WantedBy=default.target
'''
        path = units / (name + '.service')
        path.write_text(text)
        path.chmod(0o600)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', *commands], check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'source', 'venv', 'python-runtime', 'node-runtime', 'copilot-package'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--install-services', action='store_true')
    args = parser.parse_args()
    config = initialize(args)
    if args.install_services:
        install_services(config, args.root.resolve())
    print('Five private invitations are available in invitations.txt; credentials were not printed.')


if __name__ == '__main__':
    main()
