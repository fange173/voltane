# Security

- Change `SECRET_KEY` and the default `admin` password after the first start.
- Expose the app outside your LAN only through HTTPS (reverse proxy / VPN); then set `COOKIE_SECURE=true`, and `TRUST_PROXY_HEADERS=true` only behind a trusted proxy.
- Never commit `data/*.db*` or `data/backups/`.
- Report vulnerabilities privately via GitHub Security Advisories on `fange173/voltane`.
