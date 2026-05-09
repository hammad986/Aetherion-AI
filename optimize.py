import os

defer_files = [
    'dashboard.js',
    'activity.js',
    'execution_graph.js',
    'evolution.js',
    'history.js',
    'agent_mem.js',
    'immersive.js',
    'feedback.js',
    'support.js'
]

js_dir = 'static/js'

for file in defer_files:
    filepath = os.path.join(js_dir, file)
    if not os.path.exists(filepath): continue
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Replace all BOOT tasks with LOAD tasks in these non-critical modules
    new_content = content.replace('window.NX_BOOT_TASKS.push', 'window.NX_LOAD_TASKS.push')
    
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Deferred tasks in {file}")

print("Optimization complete.")
