import datetime,hashlib,json,os,stat,struct,urllib.request,zipfile
from pathlib import Path,PurePosixPath
c=json.loads(Path('/dev/shm/ddrstats-recovery-6a5/private-artifact-download-config-001.json').read_text())
p=Path(c['zip']); assert not p.exists()
h=hashlib.sha256(); n=0
with urllib.request.urlopen(c['url'],timeout=45) as response,p.open('xb') as f:
    while True:
        b=response.read(1024*1024)
        if not b: break
        n+=len(b); assert n<=c['zip_bytes']; h.update(b); f.write(b)
    f.flush(); os.fsync(f.fileno())
assert n==c['zip_bytes'] and h.hexdigest()==c['zip_sha256']
with zipfile.ZipFile(p) as z:
    names=[]
    for info in z.infolist():
        q=PurePosixPath(info.filename)
        assert not q.is_absolute() and '\\' not in info.filename and '..' not in q.parts and '.' not in q.parts
        assert str(q)==info.filename.rstrip('/') and not stat.S_ISLNK(info.external_attr>>16)
        names.append(info.filename)
    assert len(names)==len(set(names))
    sources=[x for x in names if x.endswith('/source-commit.txt') or x=='source-commit.txt']
    assert sources and all(z.read(x)==(c['head']+'\n').encode() for x in sources)
    members=[x for x in names if x.endswith('/qemu-system-aarch64') or x=='qemu-system-aarch64']
    assert members==['qemu-build/qemu-system-aarch64']
    qh=hashlib.sha256(); qn=0; header=b''
    with z.open(members[0]) as f:
        while True:
            b=f.read(1024*1024)
            if not b: break
            if not header: header=b[:64]
            qn+=len(b); qh.update(b)
    assert qn==c['qemu_bytes'] and qh.hexdigest()==c['qemu_sha256']
    assert header[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',header,18)[0]==62
r={'schema':'tb371fc-ddrstats-exact-artifact-download-recovery-v1','recovered_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':c['head'],'run_id':45544186469,'artifact_id':14991304675,'zip_bytes':n,'zip_sha256':h.hexdigest(),'qemu_bytes':qn,'qemu_sha256':qh.hexdigest(),'qemu_member':members[0],'complete_hashes_verified':True,'structural_preflight_passed':True,'actual_audits_completed':False,'qemu_executed':False,'native_executed':False,'original_receipt_bytes_recovered':False,'recovery_of_same_immutable_artifact':True}
out=Path('/dev/shm/ddrstats-recovery-6a5/artifact-download-recovery-001.json')
with out.open('x') as f: json.dump(r,f,indent=2); f.write('\n')
print(json.dumps(r))
