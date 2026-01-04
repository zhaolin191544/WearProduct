import re
from typing import List
from PIL import Image
import pytesseract

FLOAT_RE = re.compile(r"0\.\d{3,6}")  # 粗略：匹配 0.xxx

def extract_floats(text: str) -> List[float]:
    # 只抓 0.xxx 形式
    vals = []
    for m in FLOAT_RE.findall(text):
        try:
            vals.append(float(m))
        except:
            pass
    # 去重 + 排序
    vals = sorted(set(vals))
    return vals
