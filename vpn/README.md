# rezka VPN fallback

Put your VPN provider's OpenVPN config file here as `vpn.ovpn` — it's what
the `vpn` docker-compose service (gluetun) uses to open the tunnel.
Never commit it: `vpn/*.ovpn` is already in `.gitignore`.

Setup:

1. Get an `.ovpn` file from your VPN provider (custom/self-hosted OpenVPN
   server, or any provider gluetun supports as `VPN_SERVICE_PROVIDER` —
   see https://github.com/qdm12/gluetun/wiki for the full list and their
   provider-specific env vars, which replace `OPENVPN_CUSTOM_CONFIG` below).
2. Save it as `vpn/vpn.ovpn`.
3. In `.env`, set:
   - `VPN_OPENVPN_USER` / `VPN_OPENVPN_PASSWORD` — your VPN account credentials.
   - `REZKA_VPN_PROXY_URL=http://vpn:8888` — tells the worker to use it.
4. Start it alongside the rest:
   ```
   docker compose --profile rezka-vpn up -d
   ```

If `REZKA_VPN_PROXY_URL` is left empty, nothing changes — the worker never
tries it and the `vpn` container doesn't need to be running at all.

This proxy is only ever tried for rezka downloads, and only as the very
last resort — after a direct connection and every proxy in the regular
pool (`/addproxy`) have already failed. See
`app.worker.tasks._resolve_proxies`.
