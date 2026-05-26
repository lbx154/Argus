#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  git \
  openssh-server \
  python3

if ! id -u user >/dev/null 2>&1; then
  useradd -m -s /bin/bash user
fi

mkdir -p /run/sshd /git /app/www /home/user/.ssh
chmod 700 /home/user/.ssh
touch /home/user/.ssh/authorized_keys
chmod 600 /home/user/.ssh/authorized_keys
chown -R user:user /home/user/.ssh

if [ ! -d /git/server ]; then
  git init --bare /git/server
fi

git --git-dir=/git/server symbolic-ref HEAD refs/heads/master

cat > /git/server/hooks/post-receive <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

WEB_ROOT=/app/www
mkdir -p "$WEB_ROOT"

git --git-dir=/git/server --work-tree="$WEB_ROOT" checkout -f master
git --git-dir=/git/server --work-tree="$WEB_ROOT" clean -fd
EOF

chmod 755 /git/server/hooks/post-receive
chown -R user:user /git /app/www

ssh-keygen -A

cat > /app/start-services.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

mkdir -p /run/sshd

if pgrep -x sshd >/dev/null 2>&1; then
  pkill -x sshd
fi

if pgrep -f 'python3 -m http.server 8080 --directory /app/www' >/dev/null 2>&1; then
  pkill -f 'python3 -m http.server 8080 --directory /app/www'
fi

/usr/sbin/sshd
nohup python3 -m http.server 8080 --directory /app/www >/app/http-server.log 2>&1 &
EOF

chmod 755 /app/start-services.sh
/app/start-services.sh
