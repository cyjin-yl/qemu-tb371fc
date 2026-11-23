import base64, hashlib, json, os, pathlib, sys, urllib.request
blob = "55d50447da56f8217231acc1aac2d9fe72647dd6"
expected_sha = "cc850b9ca0d544216dde4f47bf9dbed2ed9bfe74394182eaf71d02c138e0f0da"
url = "https://api.github.com/repos/cyjin-yl/qemu-tb371fc/git/blobs/" + blob
req = urllib.request.Request(url, headers={"Accept":"application/vnd.github+json","User-Agent":"tb371fc-fixed-public-source-reader"})
with urllib.request.urlopen(req, timeout=20) as response:
    if response.status != 200:
        raise RuntimeError("public_controller_http_status")
    body = response.read(65537)
if len(body) > 65536:
    raise RuntimeError("public_controller_response_limit")
obj = json.loads(body)
if obj.get("sha") != blob or obj.get("size") != 7587 or obj.get("encoding") != "base64":
    raise RuntimeError("public_controller_metadata")
raw = base64.b64decode("".join(obj["content"].split()), validate=True)
if len(raw) != 7587 or hashlib.sha256(raw).hexdigest() != expected_sha:
    raise RuntimeError("public_controller_byte_identity")
if hashlib.sha1(b"blob 7587\0" + raw).hexdigest() != blob:
    raise RuntimeError("public_controller_git_identity")
path = pathlib.Path("/work/android-boot-header-20261123-01-bootstrap.py")
for part in (path, *path.parents):
    if part.is_symlink():
        raise RuntimeError("controller_symlink_path")
compile(raw, str(path), "exec")
os.umask(0o077)
fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "wb") as output:
    output.write(raw)
if path.read_bytes() != raw:
    raise RuntimeError("controller_install_readback")
print(json.dumps({"controller_sha256":expected_sha,"source_git_blob":blob,"installed":True}), flush=True)
os.execv(sys.executable, [sys.executable,"-I","-B","-S",str(path)])
