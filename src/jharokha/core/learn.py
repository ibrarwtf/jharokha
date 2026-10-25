"""Learning an app from what is on screen, so a new app (or a new view of a known one) needs
no code.

# TODO: not built. Nothing live has needed it yet; the first app learned on the live
# session shapes it. v0.24.6 in git history has the taskbar-spot version (icons by tooltip,
# save into apps.toml) and v0.24.0 the first whole-app attempt (scan, propose, verify,
# variants, save). Until then: an app is a TOML table in apps.toml (apps.example.toml),
# and a taskbar spot is one line under [calibration.session] spots.

Everything here stays pure: no capture, no OCR, no file is written."""
