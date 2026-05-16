import os
import re
from bs4 import BeautifulSoup

def sanitize_file(filepath):
    print(f"Sanitizing {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        html = f.read()
    
    soup = BeautifulSoup(html, 'html.parser')
    
    # Remove script tags with telemetry/analytics or external sources
    for script in soup.find_all('script'):
        src = script.get('src', '')
        text = script.string or ''
        if ('replit' in src.lower() or 'segment.com' in src.lower() or 
            'google' in src.lower() or 'amplitude' in src.lower() or 
            'fbevents.js' in src.lower() or 'analytics' in text.lower() or 
            'replit' in text.lower() or '__PUBLIC_ENV__' in text):
            script.decompose()

    # Remove tracking / meta tags
    for meta in soup.find_all('meta'):
        if meta.get('property', '').startswith('og:') or meta.get('property', '').startswith('fb:'):
            meta.decompose()
        elif meta.get('name', '').startswith('twitter:'):
            meta.decompose()
        elif 'replit' in str(meta).lower():
            meta.decompose()

    # Remove external links / prefetch / preload
    for link in soup.find_all('link'):
        href = link.get('href', '')
        rel = link.get('rel', [])
        if not isinstance(rel, list):
            rel = [rel]
        
        if 'replit' in href.lower() or 'icon' in rel or 'manifest' in rel or 'preload' in rel or 'dns-prefetch' in rel:
            link.decompose()

    # Remove external images / replace logos
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if 'replit' in src.lower() or src.startswith('http'):
            img['src'] = '/static/img/placeholder.svg'
            
    # Clean up classes and IDs with replit
    for tag in soup.find_all(True):
        # Clean classes
        if tag.has_attr('class'):
            new_classes = []
            for c in tag['class']:
                if 'replit' in c.lower():
                    new_classes.append(c.lower().replace('replit', 'aetherion'))
                else:
                    new_classes.append(c)
            tag['class'] = new_classes
            
        # Clean IDs
        if tag.has_attr('id') and 'replit' in tag['id'].lower():
            tag['id'] = tag['id'].lower().replace('replit', 'aetherion')
            
        # Clean inline styles
        if tag.has_attr('style') and 'replit' in tag['style'].lower():
            tag['style'] = tag['style'].lower().replace('replit', 'aetherion')
            
        # Clean text
        if tag.string:
            if 'replit' in tag.string.lower():
                # naive replace for exact case is tricky, let's do simple re
                new_text = re.sub(r'replit', 'Aetherion', tag.string, flags=re.IGNORECASE)
                tag.string.replace_with(new_text)

    # Convert back to string and do some raw regex for remaining 'replit' strings (like in hrefs)
    clean_html = str(soup)
    clean_html = re.sub(r'replit\.com', 'aetherion.local', clean_html, flags=re.IGNORECASE)
    clean_html = re.sub(r'replit', 'aetherion', clean_html, flags=re.IGNORECASE)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(clean_html)

def main():
    base_dir = 'c:\\Users\\mdham\\Downloads\\openhand_ai-main\\openhand_ai-main-2\\Replit_UIUX_Reference'
    
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith('.html'):
                sanitize_file(os.path.join(root, file))
                
if __name__ == '__main__':
    main()
