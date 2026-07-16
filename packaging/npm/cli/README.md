# Argus CLI

Binary-only preview distribution for Linux x64.

```bash
npm install -g @argusbot/cli
argus-skill --setup
argus
```

The npm tarball contains a small JavaScript launcher and a platform executable;
it does not contain the Argus Python source tree. The executable is not a
security boundary and may still be reverse engineered.

The first binary preview does not bundle optional local quant stacks such as
Qlib, LightGBM, PyTorch, or private market-data integrations. Those workloads
must continue to use a separately managed project environment.
