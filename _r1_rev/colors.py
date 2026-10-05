import numpy as np
from PIL import Image
from collections import Counter

for name in ["flow_visual_flat/period-5.0-time-0.50.png",
             "flow_visual_flat/period-5.0-time-2.55.png",
             "flow_visual_flat/period-5.0-time-4.60.png"]:
    im = Image.open("../benchmark_logs/"+name).convert("RGB")
    a = np.array(im)
    print(name, a.shape)
    c = Counter(map(tuple, a.reshape(-1,3)))
    for col, n in c.most_common(8):
        print("   ", col, n, "#%02x%02x%02x"%col)
    print()
