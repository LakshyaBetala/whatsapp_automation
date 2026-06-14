import xml.etree.ElementTree as ET
import re

def sanitize_xml(raw_bytes: bytes) -> str:
    for encoding in ['utf-16', 'windows-1252', 'utf-8']:
        try:
            text = raw_bytes.decode(encoding)
            break
        except Exception:
            continue
    else:
        text = raw_bytes.decode('utf-8', errors='ignore')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = re.sub(r'&#x[0-9A-Fa-f]+;', '', text)
    text = re.sub(r'&#\d+;', '', text)
    return text

with open('raw_pl.xml', 'rb') as f:
    text = sanitize_xml(f.read())
    
root = ET.fromstring(text)
with open('pl_out.txt', 'w', encoding='utf-8') as f:
    f.write(ET.tostring(root, encoding='unicode'))
