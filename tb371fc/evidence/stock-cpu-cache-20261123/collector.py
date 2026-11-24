import json,pathlib,datetime,platform,subprocess
root=pathlib.Path('/sys/devices/system/cpu')
rows=[]
for cpu in sorted(root.glob('cpu[0-9]*'),key=lambda p:int(p.name[3:])):
 r={'cpu':cpu.name,'cache':[]}
 for field in ['regs/identification/midr_el1','topology/core_id','topology/cluster_id','topology/physical_package_id','cpu_capacity']:
  p=cpu/field
  try:r[field]=p.read_text().strip()
  except OSError as e:r[field]={'unavailable':e.__class__.__name__}
 for index in sorted((cpu/'cache').glob('index[0-9]*')):
  c={'index':index.name}
  for field in ['level','type','size','number_of_sets','ways_of_associativity','coherency_line_size','shared_cpu_list']:
   try:c[field]=(index/field).read_text().strip()
   except OSError as e:c[field]={'unavailable':e.__class__.__name__}
  r['cache'].append(c)
 rows.append(r)
print(json.dumps({'schema_version':1,'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'evidence_class':'device_android','model':subprocess.check_output(['/system/bin/getprop','ro.product.model'],text=True).strip(),'kernel_release':platform.release(),'cpus':rows,'device_registers_written':False,'microbenchmark_run':False,'device_linux_tested':False},indent=2))
