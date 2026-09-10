"""A contact sheet of the boxes being counted as mistakes, so they can be judged."""
import json, os, sys
import cv2, numpy as np

S = os.environ["S"]
label = sys.argv[1]
rows = json.load(open(f"{S}/errors_{label}.json"))[:int(sys.argv[2]) if len(sys.argv) > 2 else 12]
tiles, PAD, SIDE = [], 0.5, 190
for row in rows:
    frame = cv2.imread(f"{S}/frames/{row['sequence']}/{row['frame']}")
    x1, y1, x2, y2 = row["box"]
    px, py = int((x2 - x1) * PAD) + 10, int((y2 - y1) * PAD) + 10
    patch = frame[max(0, y1 - py):y2 + py, max(0, x1 - px):x2 + px]
    if min(patch.shape[:2]) < 4:
        continue
    cv2.rectangle(patch, (px, py), (px + (x2 - x1), py + (y2 - y1)), (0, 235, 255), 1)
    tile = cv2.resize(patch, (SIDE, SIDE), interpolation=cv2.INTER_NEAREST)
    cv2.putText(tile, f"{row['conf']:.2f} {row['sequence']}", (4, 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
    cv2.putText(tile, row["class"][:24], (4, SIDE - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (120, 255, 160), 1)
    tiles.append(tile)
per = 4
grid = [np.hstack(tiles[i:i + per] + [np.zeros_like(tiles[0])] * (per - len(tiles[i:i + per])))
        for i in range(0, len(tiles), per)]
cv2.imwrite(f"{S}/sheet_{label}.png", np.vstack(grid))
print(f"{S}/sheet_{label}.png  ({len(tiles)} boxes)")
