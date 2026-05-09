import os
import re

def refactor2():
    js_dir = 'static/js'
    for file in os.listdir(js_dir):
        if not file.endswith('.js'): continue
        filepath = os.path.join(js_dir, file)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # Remove the weird "if readyState === loading" boot wrappers entirely and just push to tasks
        content = re.sub(
            r"if\s*\(\s*document\.readyState\s*===\s*'loading'\s*\)\s*document\.addEventListener\('DOMContentLoaded',\s*([a-zA-Z0-9_]+)\s*\);\s*else\s*setTimeout\([a-zA-Z0-9_]+,\s*\d+\);",
            r"window.NX_BOOT_TASKS.push(\1);",
            content
        )
        content = re.sub(
            r"if\s*\(\s*document\.readyState\s*===\s*'loading'\s*\)\s*{\s*document\.addEventListener\('DOMContentLoaded',\s*([a-zA-Z0-9_]+)\s*\);\s*}\s*else\s*{\s*[a-zA-Z0-9_]+\(\);\s*}",
            r"window.NX_BOOT_TASKS.push(\1);",
            content
        )
        
        # Simple replacements
        content = content.replace("document.addEventListener('DOMContentLoaded',", "window.NX_BOOT_TASKS.push(")
        content = content.replace('document.addEventListener("DOMContentLoaded",', "window.NX_BOOT_TASKS.push(")
        content = content.replace("window.addEventListener('load',", "window.NX_LOAD_TASKS.push(")
        content = content.replace('window.addEventListener("load",', "window.NX_LOAD_TASKS.push(")

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
            
    print("Replaced event listeners with NX_BOOT_TASKS array.")

if __name__ == '__main__':
    refactor2()
