"""One-off: copy every still-available produce artifact video into the private data repo's 'videos' release."""
import io, os, sys, zipfile, requests
sys.path.insert(0, "pipeline")
from common import ROOT, store_video
repo = os.environ["GITHUB_REPOSITORY"]; h = {"Authorization": f"Bearer {os.environ['GH_TOKEN']}"}
arts = requests.get(f"https://api.github.com/repos/{repo}/actions/artifacts", params={"per_page": 100}, headers=h).json()["artifacts"]
done = 0
for a in arts:
    if a["expired"] or not a["name"].startswith("reel-"):
        continue
    z = requests.get(a["archive_download_url"], headers=h, timeout=600)
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        for n in zf.namelist():
            if n.endswith(".mp4"):
                p = ROOT / "build" / os.path.basename(n); p.parent.mkdir(exist_ok=True); p.write_bytes(zf.read(n))
                print(n, store_video(p)); done += 1
print("copied", done)
