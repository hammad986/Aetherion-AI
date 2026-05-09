import os

def refactor():
    with open('templates/index.html', 'r', encoding='utf-8') as f:
        lines = f.readlines()

    blocks = []
    current_block = []
    in_tag = False
    tag_type = None

    for i, line in enumerate(lines):
        if '<style>' in line or ('<style' in line and '>' in line):
            if not in_tag:
                in_tag = True
                tag_type = 'style'
                current_block = [(i, line)]
                continue
        elif '<script>' in line or ('<script' in line and '>' in line and 'src=' not in line and 'application/json' not in line):
            if not in_tag:
                in_tag = True
                tag_type = 'script'
                current_block = [(i, line)]
                continue
        
        if in_tag:
            current_block.append((i, line))
            if tag_type == 'style' and '</style>' in line:
                in_tag = False
                blocks.append({'type': 'style', 'lines': current_block})
            elif tag_type == 'script' and '</script>' in line:
                in_tag = False
                blocks.append({'type': 'script', 'lines': current_block})

    # Names mapping based on index
    names = {
        0: 'static/css/base.css',
        1: 'static/css/components.css',
        2: 'static/css/layout.css',
        3: 'static/js/ui.js',
        4: 'static/js/runtime.js',
        5: 'static/js/activity.js',
        6: 'static/css/forms.css',
        7: 'static/js/feedback.js',
        8: 'static/js/dashboard.js',
        9: 'static/js/evolution.js',
        10: 'static/js/agent_mem.js',
        11: 'static/js/history.js',
        12: 'static/css/graphs.css',
        13: 'static/js/execution_graph.js',
        14: 'static/js/immersive.js',
        15: 'static/js/session.js',
        16: 'static/js/support.js',
        17: 'static/css/support.css',
        18: 'static/js/ux_trust.js',
        19: 'static/css/stability.css',
        20: 'static/js/stability.js'
    }

    # Extract contents
    for i, b in enumerate(blocks):
        name = names.get(i, f"static/{b['type']}/block_{i}.{'css' if b['type'] == 'style' else 'js'}")
        
        # Don't write the actual <script> or <style> tag lines to the external file
        # Check if the first line is just the tag and the last is just the closing tag
        # We'll just strip them.
        content_lines = [l[1] for l in b['lines']]
        if '<style' in content_lines[0]: content_lines[0] = content_lines[0].split('>', 1)[1]
        if '</style>' in content_lines[-1]: content_lines[-1] = content_lines[-1].rsplit('</style>', 1)[0]
        if '<script' in content_lines[0]: content_lines[0] = content_lines[0].split('>', 1)[1]
        if '</script>' in content_lines[-1]: content_lines[-1] = content_lines[-1].rsplit('</script>', 1)[0]
        
        with open(name, 'w', encoding='utf-8') as f:
            f.write(''.join(content_lines).strip() + '\n')
        
        b['filename'] = name

    # Build new index.html
    new_lines = []
    skip_until = -1
    for i, line in enumerate(lines):
        if i <= skip_until:
            continue
            
        # Check if this line is the start of a block
        block_start = next((b for b in blocks if b['lines'][0][0] == i), None)
        if block_start:
            if block_start['type'] == 'style':
                new_lines.append(f'  <link rel="stylesheet" href="/{block_start["filename"]}">\n')
            else:
                # Use defer to prevent blocking and keep order
                new_lines.append(f'  <script src="/{block_start["filename"]}" defer></script>\n')
            skip_until = block_start['lines'][-1][0]
        else:
            new_lines.append(line)

    with open('templates/index_clean.html', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    print("Refactor script complete. Check index_clean.html")

if __name__ == '__main__':
    refactor()
