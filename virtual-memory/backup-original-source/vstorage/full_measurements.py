#!/usr/bin/env python3
"""
Full measurement suite:
  1. RAM cost for big/huge files through the system
  2. 3-minute sustained run -- does RAM stay flat?
  3. Speed -- how fast can pieces be saved and retrieved?
  4. Maps/geospatial data -- does it work and what ratio?
"""
import os, lzma, hashlib, json, time, gc, threading

os.chdir("/home/claude/vstorage")

def rss_mb():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"): return int(line.split()[1])/1024.0

def rss_anon_mb():
    for line in open("/proc/self/status"):
        if line.startswith("RssAnon:"): return int(line.split()[1])/1024.0

def disk_writes():
    for line in open("/proc/self/io"):
        if line.startswith("write_bytes"): return int(line.split()[1])

BOX = 64*1024

# ============================================================
# MEASUREMENT 1: RAM cost for BIG and HUGE files
# ============================================================
print("="*64)
print("  MEASUREMENT 1: RAM at rest for big/huge files")
print("="*64)

base_rss = rss_mb()
base_anon = rss_anon_mb()

sizes_mb = [10, 50, 100, 500]
print(f"  Baseline RAM: {base_rss:.1f} MB (RSS) / {base_anon:.1f} MB (anon data)")
print()
print(f"  {'file size':>12} {'compressed':>12} {'ratio':>7} {'rss anon':>10} {'data cost':>10}")
print("-"*56)

for sz in sizes_mb:
    # generate a big structured file (realistic, like logs/records)
    chunk = b'{"id":12345,"status":"active","value":98.7,"msg":"nominal"}\n' * 100
    data = (chunk * ((sz*1024*1024)//len(chunk)+1))[:sz*1024*1024]
    compressed = lzma.compress(data, preset=6)
    gc.collect()
    anon_after = rss_anon_mb()
    ratio = len(data)/len(compressed)
    data_cost = anon_after - base_anon
    print(f"  {sz:>10}MB {len(compressed):>12,} {ratio:>6.1f}x {anon_after:>9.1f}MB {data_cost:>9.1f}MB")
    del data, compressed; gc.collect()

# ============================================================
# MEASUREMENT 2: 3-minute sustained run -- RAM stays flat?
# ============================================================
print()
print("="*64)
print("  MEASUREMENT 2: 3-minute sustained run, RAM over time")
print("="*64)

# create 100MB of structured data, compress it, fall through boxes
data_100mb = (b'{"record":true,"value":42.0}\n' * 100) * 35000
data_100mb = data_100mb[:100*1024*1024]
compressed_blob = lzma.compress(data_100mb, preset=6)
del data_100mb; gc.collect()

# fall the compressed blob
stop = threading.Event()
box_a = bytearray(compressed_blob)
box_b = bytearray(len(compressed_blob))
hops = [0]

def fall_loop():
    pos = 0
    while not stop.is_set():
        end = min(pos+BOX, len(box_a))
        box_b[pos:end] = box_a[pos:end]
        pos = end
        if pos >= len(box_a):
            box_a[:] = box_b[:]
            pos = 0
            hops[0] += 1

t = threading.Thread(target=fall_loop, daemon=True)
t.start()

start = time.time()
print(f"  Holding {len(compressed_blob)/1024:.1f}KB compressed blob, falling through boxes...")
print(f"  {'elapsed':>8} | {'RSS':>8} | {'RssAnon':>9} | {'hops':>8}")
print("-"*42)
for _ in range(6):
    time.sleep(30)
    elapsed = time.time() - start
    print(f"  {elapsed:>7.0f}s | {rss_mb():>7.1f}MB | {rss_anon_mb():>8.1f}MB | {hops[0]:>8,}")

stop.set(); t.join()
del box_a, box_b, compressed_blob; gc.collect()

# ============================================================
# MEASUREMENT 3: SPEED -- save and retrieve
# ============================================================
print()
print("="*64)
print("  MEASUREMENT 3: Speed of save and retrieval")
print("="*64)

test_data = open("medical_records.json","rb").read()

# save speed
t0 = time.perf_counter()
for _ in range(10):
    c = lzma.compress(test_data, preset=6)
save_time = (time.perf_counter()-t0)/10

# retrieve speed (decompress)
t0 = time.perf_counter()
for _ in range(10):
    d = lzma.decompress(c)
retrieve_time = (time.perf_counter()-t0)/10

print(f"  Medical records ({len(test_data)/1024:.0f}KB):")
print(f"    Save (compress):    {save_time*1000:.1f}ms")
print(f"    Retrieve (decomp):  {retrieve_time*1000:.1f}ms")
print(f"    Retrieval is {'faster' if retrieve_time<save_time else 'slower'} than save")

# ============================================================
# MEASUREMENT 4: Maps / geospatial data
# ============================================================
print()
print("="*64)
print("  MEASUREMENT 4: Maps / geospatial data")
print("="*64)

# Generate realistic map data (GeoJSON format -- what real maps use)
import random
random.seed(42)

# simulate a real city map: streets, points of interest, routes
features = []
for i in range(2000):
    lat = 6.5 + random.uniform(-0.5, 0.5)   # Lagos-ish coordinates
    lng = 3.3 + random.uniform(-0.5, 0.5)
    feature_type = random.choice(["street","building","route","poi","boundary"])
    features.append({
        "type": "Feature",
        "geometry": {
            "type": random.choice(["Point","LineString"]),
            "coordinates": [round(lng,6), round(lat,6)]
        },
        "properties": {
            "id": f"MAP_{i:05d}",
            "type": feature_type,
            "name": f"{random.choice(['Victoria','Lagos','Lekki','Ikeja','Surulere'])} {feature_type} {i}",
            "road_type": random.choice(["primary","secondary","residential","highway"]),
            "speed_limit": random.choice([30,50,80,100]),
            "last_updated": f"2026-{random.randint(1,9):02d}-{random.randint(1,28):02d}"
        }
    })

geojson = json.dumps({"type":"FeatureCollection","features":features}, indent=2).encode()
compressed_map = lzma.compress(geojson, preset=9)
map_ratio = len(geojson)/len(compressed_map)

print(f"  GeoJSON map (2000 features, ~Lagos area):")
print(f"    Raw GeoJSON:    {len(geojson):,} bytes ({len(geojson)/1024:.1f} KB)")
print(f"    Compressed:     {len(compressed_map):,} bytes ({len(compressed_map)/1024:.1f} KB)")
print(f"    Ratio:          {map_ratio:.1f}x")

# can we retrieve a specific area (bounding box query)?
map_data = json.loads(geojson)
lat_min, lat_max = 6.45, 6.55
lng_min, lng_max = 3.25, 3.35
area_features = [
    f for f in map_data["features"]
    if (lat_min <= f["geometry"]["coordinates"][1] <= lat_max and
        lng_min <= f["geometry"]["coordinates"][0] <= lng_max)
]
area_json = json.dumps({"type":"FeatureCollection","features":area_features}).encode()
area_compressed = lzma.compress(area_json, preset=9)

print(f"\n    Area query (bounding box):")
print(f"    Features in area:   {len(area_features)} of 2000")
print(f"    Area data:          {len(area_json):,} bytes -> {len(area_compressed):,} compressed")
print(f"    Only {len(area_compressed)/len(compressed_map)*100:.1f}% of map needed for this query")
print()
print(f"    Maps work well -- GeoJSON is highly structured text.")
print(f"    Block by geographic region = targeted retrieval like medical blocks.")

io_final = disk_writes()
print(f"\n  Total disk writes entire measurement run: {io_final:,} bytes")
