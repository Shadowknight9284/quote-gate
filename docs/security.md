# Security notes

- Synthetic data only; no credentials or real market/brokerage access in the app.
- Services bind 127.0.0.1 by default. Cross-VM runs will bind a LAN address only for the
  venue port; no public ports, no Tailscale Funnel, no firewall changes.
- No authentication on the venue/client protocol: trusted-network prototype only.
- Frame size, connection count, and in-flight requests are bounded (see protocol.md).
- Delegated coding agent runs as a general user account on a separate VM in a per-task
  directory; this is not a sandbox. Patches are reviewed and re-tested locally before use.
- Not verified: VLAN segmentation, restrictive tailnet policy, independent physical hosts.
