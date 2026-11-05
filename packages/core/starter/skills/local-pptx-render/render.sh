#!/usr/bin/env bash
set -euo pipefail
input=$(realpath "$1")
mkdir -p "$2"
out=$(realpath "$2")
taskroot=$(mktemp -d /tmp/local-pptx-render.XXXXXX)
mkdir -p "$taskroot/lists/partial" "$taskroot/cache/archives/partial" "$taskroot/root"
opts=(-o "Dir::State::lists=$taskroot/lists" -o "Dir::Cache=$taskroot/cache" -o Debug::NoLocking=1)
apt-get "${opts[@]}" update > "$taskroot/update.log" 2>&1
apt-get "${opts[@]}" --download-only -y install libreoffice-impress --no-install-recommends > "$taskroot/download.log" 2>&1
for f in "$taskroot"/cache/archives/*.deb; do dpkg-deb -x "$f" "$taskroot/root"; done
python3 - "$taskroot/root" <<'PY'
import sys,os,shutil
root=sys.argv[1]
for d,ds,fs in os.walk(root):
 for n in ds+fs:
  f=os.path.join(d,n)
  if os.path.islink(f):
   t=os.readlink(f)
   if t.startswith('/') and os.path.exists(root+t):
    os.unlink(f);os.symlink(root+t,f)
prog=root+'/usr/lib/libreoffice/program'
for n in ['fundamentalrc','bootstraprc','lounorc','redirectrc']:
 f=prog+'/'+n
 if os.path.exists(f):
  t=open(f).read().replace('file:///usr/','file://'+root+'/usr/').replace('file:///etc/','file://'+root+'/etc/')
  open(f,'w').write(t)
shutil.copytree(root+'/usr/lib/libreoffice/share/.registry',root+'/etc/libreoffice/registry',dirs_exist_ok=True)
PY
prog="$taskroot/root/usr/lib/libreoffice/program"
URE_BOOTSTRAP="vnd.sun.star.pathname:$prog/fundamentalrc" LD_LIBRARY_PATH="$prog:$taskroot/root/usr/lib/x86_64-linux-gnu" "$prog/soffice" --headless "-env:UserInstallation=file://$taskroot/profile" --convert-to pdf --outdir "$out" "$input"
name=$(basename "$input" .pptx)
pdftoppm -scale-to 1600 -png "$out/$name.pdf" "$out/slide"
printf 'Rendered to %s; inspect all slide PNGs. Runtime: %s\n' "$out" "$taskroot"
