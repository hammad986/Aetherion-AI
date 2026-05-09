import os
import re

def extract():
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
        elif '<script>' in line or ('<script' in line and '>' in line and 'src=' not in line):
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

    print(f"Found {len(blocks)} blocks to extract")
    for i, b in enumerate(blocks):
        start = b['lines'][0][0] + 1
        end = b['lines'][-1][0] + 1
        print(f"Block {i}: {b['type']} from {start} to {end} ({end-start+1} lines)")

if __name__ == '__main__':
    extract()
