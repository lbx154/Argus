# Mobile access

Argus runs on a workstation or a cluster node; the work it does is long-horizon,
so most of it happens while you are somewhere else. There are three ways to
drive it from a phone, and they all talk to the same daemon.

| Surface | Reach the daemon by | Needs a public URL |
| --- | --- | --- |
| Telegram bot | outbound long polling | no |
| Feishu / Lark bot | outbound WebSocket | no |
| Web UI (installable) | direct HTTP on your network | no |

The two chat bridges dial **out**, so a daemon behind NAT needs no tunnel, no
port forward, and no callback address. The web UI needs your phone to be able
to reach the machine — the same network, a VPN, or a tunnel you already run.

## Telegram

```bash
export ARGUS_SKILL_ENABLE_TELEGRAM=1
export ARGUS_SKILL_TELEGRAM_BOT_TOKEN=123456:AA...   # from @BotFather
export ARGUS_SKILL_TELEGRAM_CHAT_ID=-1001234567890
# optional: only accept commands from one account
export ARGUS_SKILL_TELEGRAM_USER_ID=987654321
```

The daemon starts the bridge on boot. On first connect it publishes the command
list, so your Telegram client shows the whole surface in its `/` menu — you do
not have to remember the commands. `/status` replies carry quick-action buttons
for the common follow-ups.

Replies longer than Telegram's 4096-character limit are split across messages
rather than truncated, so a long `/journal` or `/backlog` arrives whole.

## Feishu / Lark

The bridge uses the open platform's **long connection** mode: the daemon opens
a WebSocket outward and events arrive on it. There is no request URL to
configure and no inbound port to open.

1. Create an app at <https://open.feishu.cn/> (or
   <https://open.larksuite.com/> for international Lark) and add the **bot**
   capability.
2. Grant `im:message`, `im:message.p2p_msg:readonly` and `im:message:send_as_bot`. Progress reactions also
   need `im:message.reaction`.
3. Under *Event Subscription*, choose **长连接 / long connection** — not a
   request URL — and subscribe to `im.message.receive_v1`.
4. Install the SDK and start the daemon:

```bash
pip install 'argus[feishu]'

export ARGUS_SKILL_ENABLE_FEISHU=1
export ARGUS_SKILL_FEISHU_APP_ID=cli_xxx
export ARGUS_SKILL_FEISHU_APP_SECRET=xxx
# optional but recommended: restrict who may drive the daemon
export ARGUS_SKILL_FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
# optional: 'lark' for the international host, or a full URL
export ARGUS_SKILL_FEISHU_DOMAIN=feishu
```

Message the bot and it answers with the same commands Telegram serves, rendered
as cards. While a command runs the bot reacts 🤔 to your message and clears the
reaction when it finishes (❌ if it failed).

If `lark-oapi` is not installed the bridge logs one line and stays dormant;
nothing else about the daemon changes.

### Both bridges share one command surface

`/add`, `/status`, `/nudge`, `/backlog`, `/journal`, `/continuous`, `/config`,
`/backend`, `/identity`, `/skills`, `/run`, `/note`, `/done`, `/skip`, `/rm`,
`/stop`, `/reset`, `/help`. Plain text is routed the same way on both: injected
into the running task if one is active, queued as a new task if the daemon is
idle.

They live in `argus/life/chat/`, so a third channel only has to implement
`ChatTransport` — it inherits every command.

## Web UI on a phone

```bash
argus --web --web-host 0.0.0.0
```

This prints the reachable URL and a QR code. Scan it and the phone opens the
workbench already authenticated; the token is stored, so the page keeps working
after a reload.

**A non-loopback bind is always authenticated.** If `ARGUS_SKILL_WEB_TOKEN` is
set it is used; if not, a token is minted for that run and printed. Set
`ARGUS_SKILL_WEB_TOKEN` yourself if you want one that survives restarts. To
serve without a token — only sensible behind your own authenticating proxy —
set `ARGUS_SKILL_WEB_ALLOW_INSECURE=1`, and the banner will say so.

The token gates every command (task dispatch, daemon control, config changes)
and the live event WebSocket. Read endpoints — the project list, snapshots, and
event history — answer without it, so treat a LAN bind as publishing your
project state to that network. Bind to `127.0.0.1` and forward the port over
SSH if that is not acceptable:

```bash
# on the server
argus --web --no-open
# on your machine
ssh -L 8799:127.0.0.1:8799 user@server
```

`pip install 'argus[qr]'` renders the QR code. Without it the URL is still
printed in full.

The default `--web-host 127.0.0.1` is unchanged and still needs no token.

### Install it to the home screen

The web UI ships a manifest and icons, so **Add to Home Screen** (iOS Safari) or
**Install app** (Android Chrome) gives you a standalone launcher with no browser
chrome. Because the token was stored on the paired visit, launching from the
home screen stays signed in.

On a phone the workbench uses a bottom tab bar — Sessions, Mission, Activity,
Preview — instead of the desktop three-pane layout. Controls clear the notch and
the home indicator, and the composer lifts above the software keyboard.
