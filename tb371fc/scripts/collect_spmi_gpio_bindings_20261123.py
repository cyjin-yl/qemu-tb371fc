from pathlib import Path
import datetime,json,hashlib
out={'kind':'tb371fc-spmi-gpio-bindings','captured_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'only driver/of-node links, PMIC attribute names, and GPIO chip label/base/count; no GPIO values/events or register accesses','spmi_devices':[],'gpio_chips':[]}
for dev in sorted(Path('/sys/bus/spmi/devices').glob('*')):
 item={'name':dev.name,'driver':(dev/'driver').resolve().name if (dev/'driver').exists() else None,'of_node':str((dev/'of_node').resolve()).replace('/sys/firmware/devicetree/base','') if (dev/'of_node').exists() else None,'attribute_names':sorted(p.name for p in dev.iterdir())}
 out['spmi_devices'].append(item)
for dev in sorted(Path('/sys/class/gpio').glob('gpiochip*')):
 item={'name':dev.name}
 for attr in ['base','ngpio','label']:
  try:item[attr]=(dev/attr).read_text().strip()
  except OSError as exc:item[attr]={'error':str(exc)}
 out['gpio_chips'].append(item)
out['socinfo_pmic_attribute_names']=sorted(p.name for p in Path('/sys/devices/soc0').glob('pmic*'))
root=Path('/data/data/com.termux/files/home/qemu-tb371fc-probe')
target=root/'android-spmi-gpio-bindings.json'
assert not target.exists()
target.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print('CAPTURE_JSON:'+json.dumps({'topology':out,'identity':json.loads((root/'android-pmic-identity.json').read_text())}))
