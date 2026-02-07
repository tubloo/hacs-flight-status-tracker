# HACS Readiness Notes (no PRs yet)

## Local files added
- `hacs.json`
- `.github/workflows/hacs.yml`
- `.github/workflows/hassfest.yml`
- `assets/brands/flight_status_tracker/README.md`

## Still needed before public HACS inclusion
1) **Brands PR**
   - Add `icon.png` (and optional `logo.png`) to:
     `home-assistant/brands/custom_integrations/flight_status_tracker/`

2) **GitHub repo metadata**
   - Topics: `home-assistant`, `hacs`, `custom-component`, `integration`
   - Enable Issues, add description

3) **Release**
   - Create a GitHub Release after actions pass

## Notes
- This repo should pass HACS + Hassfest once those workflows are enabled.
- `hacs.json` intentionally omits `domains` (not allowed for integrations).
