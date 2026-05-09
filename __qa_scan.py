import re, os, sys, ast
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

files = [f for f in os.listdir('.') if f.endswith('.py') and not f.startswith('__qa')]
security_issues = []
import_issues = []

for fn in files:
    with open(fn,'r',encoding='utf-8',errors='replace') as f:
        lines = f.readlines()
    for i,line in enumerate(lines,1):
        stripped = line.strip()
        # Hard-coded secrets
        if re.search(r'(api_key|secret|password|token)\s*=\s*["\'][a-zA-Z0-9_\-]{20,}["\']', stripped, re.IGNORECASE):
            security_issues.append(f'HARDCODED_SECRET {fn}:{i}: {stripped[:80]}')
        # eval() usage
        if re.search(r'\beval\s*\(', stripped):
            security_issues.append(f'EVAL_USAGE {fn}:{i}: {stripped[:80]}')
        # exec() usage  
        if re.search(r'\bexec\s*\(', stripped):
            security_issues.append(f'EXEC_USAGE {fn}:{i}: {stripped[:80]}')
        # shell=True
        if 'shell=True' in stripped:
            security_issues.append(f'SHELL_TRUE {fn}:{i}: {stripped[:80]}')
        # print to find debug prints
        if stripped.startswith('print(') and 'password' in stripped.lower():
            security_issues.append(f'PRINT_PASSWORD {fn}:{i}: {stripped[:80]}')

# Check imports that might be missing
standard_libs = {'os','sys','re','json','time','datetime','threading','subprocess','pathlib',
                 'hashlib','base64','hmac','uuid','logging','traceback','copy','math','random',
                 'collections','functools','itertools','io','struct','socket','ssl','http',
                 'urllib','email','csv','sqlite3','tempfile','shutil','glob','ast','abc',
                 'contextlib','dataclasses','enum','typing','warnings','inspect','importlib',
                 'queue','multiprocessing','concurrent','asyncio','weakref','gc','signal'}

for fn in files:
    try:
        with open(fn,'r',encoding='utf-8',errors='replace') as f:
            src = f.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ''
                else:
                    mod = node.names[0].name if node.names else ''
                top = mod.split('.')[0].lower() if mod else ''
                # flag non-standard, non-local imports that might be missing
                if top and top not in standard_libs and not os.path.exists(top+'.py') and not os.path.exists(top):
                    pass  # would need pip check
    except:
        pass

print('=== SECURITY AUDIT ===')
for s in security_issues[:40]:
    print(s)
if not security_issues:
    print('No hardcoded secrets or dangerous eval/exec found (good)')
print(f'Total security flags: {len(security_issues)}')
print()

# Check for dangerous shell commands
print('=== SHELL=TRUE USAGES (potential injection risk) ===')
shell_uses = [s for s in security_issues if 'SHELL_TRUE' in s]
for s in shell_uses:
    print(s)
print(f'Total shell=True: {len(shell_uses)}')
