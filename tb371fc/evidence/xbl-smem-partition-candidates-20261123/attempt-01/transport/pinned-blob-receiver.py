import base64, hashlib, json, subprocess, sys, urllib.request
GIT_BLOB = "cb814500fb1d4dceb8096ade0a93554388a1e9df"
SIZE = 99954
SHA256 = "d5827a3fb01f80b256b7f5dad1bd5d11f8da63b2c44e93db7e202f72e471e59b"
INSTALLER_SHA256 = "a0edf87af518a3a857a8086669513c79be11f362ed883197412b21def5e2e5ca"
class TransportError(Exception):
    pass
def check(ok, code):
    if not ok:
        raise TransportError(code)
try:
    check(len(sys.argv) == 2, "fixed_installer_argument_required")
    installer = sys.argv[1]
    check(hashlib.sha256(installer.encode("utf-8")).hexdigest() == INSTALLER_SHA256,
          "fixed_installer_identity")
    request = urllib.request.Request(
        "https://api.github.com/repos/cyjin-yl/qemu-tb371fc/git/blobs/" + GIT_BLOB,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "tb371fc-pinned-data-transport"})
    with urllib.request.urlopen(request, timeout=8) as response:
        http_status = response.status
        raw_response = response.read(262145)
    check(http_status == 200 and len(raw_response) <= 262144, "github_blob_response_limit_or_status")
    data = json.loads(raw_response)
    check(type(data) is dict and data.get("sha") == GIT_BLOB
          and type(data.get("size")) is int and data["size"] == SIZE
          and data.get("encoding") == "base64"
          and type(data.get("content")) is str, "github_blob_metadata")
    payload = base64.b64decode("".join(data["content"].split()), validate=True)
    check(len(payload) == SIZE and hashlib.sha256(payload).hexdigest() == SHA256,
          "package_byte_identity")
    check(hashlib.sha1(b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload).hexdigest()
          == GIT_BLOB, "git_object_identity")
    # The downloaded JSON is data only. Execute the fixed, separately supplied
    # and hashed receiver source; its original compile/O_EXCL guards still run.
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", installer, SHA256],
        input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    print(json.dumps({
        "transport_verified": True, "http_status": http_status,
        "http_response_bytes": len(raw_response),
        "http_response_sha256": hashlib.sha256(raw_response).hexdigest(),
        "package_git_blob_sha": GIT_BLOB,
        "package_bytes": SIZE, "package_sha256": SHA256,
        "fixed_installer_sha256": INSTALLER_SHA256,
        "installer_exit_code": result.returncode,
        "installer_stdout": result.stdout.decode("utf-8", errors="strict"),
        "installer_stderr": result.stderr.decode("utf-8", errors="strict"),
        "scanner_launched": False,
    }, sort_keys=True))
    sys.exit(result.returncode)
except Exception as error:
    print(json.dumps({"transport_verified": False, "scanner_launched": False,
                      "error_type": type(error).__name__,
                      "error_code": str(error) if isinstance(error, TransportError)
                      else "network_or_transport_error"}, sort_keys=True))
    sys.exit(1)
