# "It won't load on my phone"

Work down this list in order. Ninety percent of the time it is #1 or #2.

1. **Same Wi-Fi?** The phone and the laptop must be on the same network.
   Hotspots from the laptop itself work too. Corporate/college Wi-Fi often
   isolates clients ("AP isolation") — if two laptops can't ping each other,
   phones can't reach your server either; use a personal hotspot instead.

2. **Right URL?** Run `python3 serve.py` and use the LAN URL it prints
   (`http://192.168.x.x:8080`), **not** `localhost:8080` — localhost on the
   phone is the phone.

3. **Type `http://` explicitly.** Phone browsers silently upgrade to
   `https://`, and this dev server has no TLS. If the page "spins forever",
   retype the URL with `http://` at the front.

4. **macOS firewall.** System Settings → Network → Firewall: allow incoming
   connections for Python, or turn the firewall off for the demo.

5. **Backend port too.** The app calls the API on port **8000** of the same
   host. Start it listening on all interfaces:
   `uvicorn app.main:app --host 0.0.0.0` — the default binds localhost only,
   which a phone cannot reach. Quick check from the phone browser:
   `http://192.168.x.x:8000/health` should show `{"status":"ok"}`.

6. **Stale service worker.** If you see an old version of the app: close all
   tabs of the site, then in the phone browser clear site data (or bump
   `VERSION` in `sw.js` and reload twice).

7. **Still stuck?** Set `MOCK = true` at the top of `index.html` — the whole
   app then runs from canned data with no backend at all. That is the
   presentation fallback.
