"""Preserve unittest status; annotate only public-source test method names."""
import ast
from pathlib import Path
import re
import subprocess
import sys

# Only source-defined identifiers may leave the existing log via annotations.
public_tests = set()
for source in Path('tests').glob('test_*.py'):
    public_tests.update(node.name for node in ast.walk(ast.parse(source.read_text()))
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and re.fullmatch(r'test_[A-Za-z0-9_]{1,160}', node.name))
result = subprocess.run([
    sys.executable, '-B', '-W', 'error::ResourceWarning', '-m', 'unittest',
    'discover', '-s', 'tests', '-p', 'test_*.py',
], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
print(result.stdout, end='', flush=True)
if result.returncode:
    names = set(re.findall(r'^(?:FAIL|ERROR): (test_[A-Za-z0-9_]+)\b', result.stdout, re.M))
    for name in sorted(names & public_tests):
        print('::error title=Python fixture failed::' + name, flush=True)
raise SystemExit(result.returncode)
