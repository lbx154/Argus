"""One bounded JSON transformation. No workspace, provider, or process APIs.

Executed with Python -I -S in a disposable working directory. The language is
deliberately smaller than Python: JSON values, ordinary control flow, selected
builtins, and explicit imports of pure math/re/json functions. This is not a
general script runner or a replacement for the caller's filesystem sandbox.
"""
from __future__ import annotations

import ast
import builtins
import json
import math
import re
import sys
from types import SimpleNamespace

MAX_BYTES = 65536
IMPORTS = {
    'math': {name: getattr(math, name) for name in (
        'ceil', 'floor', 'sqrt', 'log', 'log10', 'exp', 'sin', 'cos', 'tan',
        'isfinite', 'isclose', 'fsum', 'gcd', 'pi', 'e',
    )},
    're': {name: getattr(re, name) for name in (
        'sub', 'split', 'findall', 'finditer', 'match', 'fullmatch', 'search', 'escape',
        'IGNORECASE', 'MULTILINE', 'DOTALL',
    )},
    'json': {'loads': json.loads, 'dumps': json.dumps},
}
BUILTINS = {name: getattr(builtins, name) for name in (
    'abs', 'all', 'any', 'bool', 'dict', 'enumerate', 'filter', 'float', 'int',
    'isinstance', 'len', 'list', 'map', 'max', 'min', 'next', 'range', 'reversed',
    'round', 'set', 'sorted', 'str', 'sum', 'tuple', 'zip',
    'Exception', 'ValueError', 'TypeError', 'KeyError', 'IndexError',
)}


def validate_source(source: str) -> None:
    if not isinstance(source, str) or not 0 < len(source.encode()) <= 16384:
        raise ValueError('source must contain at most 16 KiB')
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as error:
        raise ValueError(f'invalid Python source: {str(error)[:300]}') from None
    if not any(isinstance(node, ast.FunctionDef) and node.name == 'run' for node in tree.body):
        raise ValueError('define run(value) returning a JSON value')
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ClassDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal)):
            raise ValueError('only pure functions and explicit from math/re/json imports are supported')
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module not in IMPORTS or any(alias.name not in IMPORTS[node.module] for alias in node.names):
                raise ValueError('import is outside the JSON transformation API')
        if isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            raise ValueError('private attribute access is unavailable')
        if isinstance(node, ast.Name) and node.id.startswith('__'):
            raise ValueError('interpreter internals are unavailable')
        if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.name.startswith('__')):
            raise ValueError('decorators and interpreter hooks are unavailable')


def _import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in IMPORTS or not fromlist or any(item not in IMPORTS[name] for item in fromlist):
        raise ValueError('import is outside the JSON transformation API')
    return SimpleNamespace(**IMPORTS[name])


def _deny_io(event, _args):
    if event == 'open' or event.startswith(('socket.', 'subprocess.', 'ctypes.', 'os.', 'shutil.', 'winreg.')):
        raise ValueError('runtime tools cannot access files, network, or processes')


def main() -> None:
    # Limits apply inside the worker, never through preexec_fn in the threaded host.
    if sys.platform != 'win32':
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    try:
        if len(raw) > MAX_BYTES:
            raise ValueError('tool input exceeds 64 KiB')
        request = json.loads(raw)
        source = request['source']
        validate_source(source)
        compiled = compile(source, '<learned-tool>', 'exec')
        namespace = {'__builtins__': {**BUILTINS, '__import__': _import}}
        sys.addaudithook(_deny_io)
        exec(compiled, namespace)
        output = namespace['run'](request['input'])
        encoded = json.dumps({'output': output}, ensure_ascii=True, allow_nan=False)
        if len(encoded.encode()) > MAX_BYTES:
            raise ValueError('tool output exceeds 64 KiB')
        sys.stdout.write(encoded)
    except BaseException as error:
        sys.stdout.write(json.dumps({'error': f'{type(error).__name__}: {str(error)[:400]}'}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
