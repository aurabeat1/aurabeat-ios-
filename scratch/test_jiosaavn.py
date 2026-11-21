import urllib.request
import json

url = "https://www.jiosaavn.com/api.php?__call=search.getResults&_format=json&_marker=0&api_version=4&ctx=android&q=punjabi+top+10&p=1&n=10"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req) as r:
    data = json.loads(r.read())
    results = data.get("results", [])

for s in results:
    sid = s.get("id")
    title = s.get("title")
    d_url = f"https://www.jiosaavn.com/api.php?__call=song.getDetails&cc=in&_marker=0%3F_marker%3D0&_format=json&pids={sid}"
    d_req = urllib.request.Request(d_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(d_req) as dr:
            d_data = json.loads(dr.read())
            info = d_data.get(sid)
            has_media = bool(info and info.get("encrypted_media_url"))
            print(f"{sid}: \"{title}\" -> has_media={has_media}")
    except Exception as e:
        print(f"{sid}: ERROR: {e}")
