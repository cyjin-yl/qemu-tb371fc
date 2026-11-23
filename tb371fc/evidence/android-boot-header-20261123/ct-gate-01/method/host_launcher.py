import datetime,hashlib,json,os,pathlib,shlex,subprocess
os.umask(0o077)
spool=pathlib.Path("/tank/shared/projects/qemu-tb371fc/.git/tb371fc-private-input-transfer-20261123/android-boot-header-ct-gate-01")
spool.mkdir(mode=0o700,exist_ok=False)
ct="import base64, hashlib, json, os, pathlib, sys, urllib.request\nblob = \"55d50447da56f8217231acc1aac2d9fe72647dd6\"\nexpected_sha = \"cc850b9ca0d544216dde4f47bf9dbed2ed9bfe74394182eaf71d02c138e0f0da\"\nurl = \"https://api.github.com/repos/cyjin-yl/qemu-tb371fc/git/blobs/\" + blob\nreq = urllib.request.Request(url, headers={\"Accept\":\"application/vnd.github+json\",\"User-Agent\":\"tb371fc-fixed-public-source-reader\"})\nwith urllib.request.urlopen(req, timeout=20) as response:\n    if response.status != 200:\n        raise RuntimeError(\"public_controller_http_status\")\n    body = response.read(65537)\nif len(body) > 65536:\n    raise RuntimeError(\"public_controller_response_limit\")\nobj = json.loads(body)\nif obj.get(\"sha\") != blob or obj.get(\"size\") != 7587 or obj.get(\"encoding\") != \"base64\":\n    raise RuntimeError(\"public_controller_metadata\")\nraw = base64.b64decode(\"\".join(obj[\"content\"].split()), validate=True)\nif len(raw) != 7587 or hashlib.sha256(raw).hexdigest() != expected_sha:\n    raise RuntimeError(\"public_controller_byte_identity\")\nif hashlib.sha1(b\"blob 7587\\0\" + raw).hexdigest() != blob:\n    raise RuntimeError(\"public_controller_git_identity\")\npath = pathlib.Path(\"/work/android-boot-header-20261123-01-bootstrap.py\")\nfor part in (path, *path.parents):\n    if part.is_symlink():\n        raise RuntimeError(\"controller_symlink_path\")\ncompile(raw, str(path), \"exec\")\nos.umask(0o077)\nfd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)\nwith os.fdopen(fd, \"wb\") as output:\n    output.write(raw)\nif path.read_bytes() != raw:\n    raise RuntimeError(\"controller_install_readback\")\nprint(json.dumps({\"controller_sha256\":expected_sha,\"source_git_blob\":blob,\"installed\":True}), flush=True)\nos.execv(sys.executable, [sys.executable,\"-I\",\"-B\",\"-S\",str(path)])\n"
with (spool/"ct-bootstrap.py").open("x") as stream:
    stream.write(ct)
subprocess.run(["tmux","has-session","-t","qemu-tb371fc"],check=True,timeout=5)
names=subprocess.run(["tmux","list-windows","-t","qemu-tb371fc","-F","#{window_name}"],check=True,capture_output=True,text=True,timeout=5).stdout.splitlines()
if "android-boot-header-gate-01" in names:
    raise RuntimeError("named_window_exists")
argv=["sudo","-n","/usr/sbin/pct","exec","102","--","docker","exec","qemu-tb371fc-dev","python3","-I","-B","-S","-c",ct]
command=shlex.join(argv)+" > "+shlex.quote(str(spool/"console.log"))+" 2>&1"
r=subprocess.run(["tmux","new-window","-d","-P","-F","#{window_id}:#{pane_id}","-t","qemu-tb371fc:","-n","android-boot-header-gate-01",command],capture_output=True,text=True,timeout=5)
receipt={"schema":"tb371fc-android-boot-header-launch-v1","started_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"exit_code":r.returncode,"tmux":r.stdout.strip(),"stderr":r.stderr,"ct_bootstrap_sha256":hashlib.sha256(ct.encode()).hexdigest(),"controller_sha256":"cc850b9ca0d544216dde4f47bf9dbed2ed9bfe74394182eaf71d02c138e0f0da","public_sources_only":True,"tests_completed":False}
with (spool/"launch.json").open("x") as stream:
    json.dump(receipt,stream,indent=2)
    stream.write("\n")
print(json.dumps(receipt))
