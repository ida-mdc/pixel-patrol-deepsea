"""Which dives are the deep ones. Reads each dive's own report, nothing more."""
import json, sys
from concurrent.futures import ThreadPoolExecutor
from pixel_patrol_deepsea.locations import noaa_dive_summary

CRUISES = {c: f"https://oer.hpc.msstate.edu/okeanos/{c.lower()}/" for c in sys.argv[1:]}

def one(pair):
    cruise, dive = pair
    try:
        got = noaa_dive_summary(CRUISES[cruise], dive)
    except Exception:
        return None
    return got and {**got, "cruise": cruise}

tasks = [(c, d) for c in CRUISES for d in range(1, 21)]
with ThreadPoolExecutor(12) as pool:
    rows = [r for r in pool.map(one, tasks) if r and r.get("max_depth_m")]
rows.sort(key=lambda r: -r["max_depth_m"])
for r in rows[:24]:
    print(f"{r['cruise']} dive {r['dive']:2d}  {r['max_depth_m']:7.0f} m  "
          f"{r.get('latitude', 0):9.4f} {r.get('longitude', 0):10.4f}  {r.get('on_bottom_at','')[:10]}")
json.dump(rows, open("dive_survey.json", "w"), indent=1)
print(f"\n{len(rows)} dives -> dive_survey.json")
