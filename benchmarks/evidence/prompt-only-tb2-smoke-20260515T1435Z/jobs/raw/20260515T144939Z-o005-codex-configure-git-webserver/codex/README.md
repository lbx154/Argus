# configure-git-webserver

Container: `tb2-codex-configure-git-webserver-144939`

What was configured inside the container:

- SSH server running for git-over-SSH access.
- Bare repository at `/git/server` owned by `user`.
- `post-receive` hook that deploys branch `master` into `/app/www`.
- HTTP server on port `8080` serving `/app/www`.

Useful files exported from `/app`:

- `/app/provision_container.sh`
- `/app/start-services.sh`
- `/app/www/hello.html`

Verification performed:

- Cloned with `git clone user@localhost:/git/server /tmp/client`
- Created and committed `hello.html`
- Pushed with `git push origin master`
- Confirmed `curl http://localhost:8080/hello.html` returns `hello world`
