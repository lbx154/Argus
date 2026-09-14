# Windows desktop icon

The active desktop, taskbar and tray icon is `icon-light-rounded.ico`, selected
by `tauri.conf.json`. It has a white (`#ffffff`) rounded-square background and
a dark (`#202326`) Argus mark. The eye white and highlight remain white.

`icon-light-rounded.svg` preserves the existing dark rounded-square mark's
geometry, including the 112-unit corner radius. `icon-light-rounded.png` is the
256-pixel rendering approved by the user. The ICO contains 16, 24, 32, 48, 64,
128 and 256-pixel RGBA images generated from that rendering; its 256-pixel frame
is pixel-identical to the PNG.

The older ICO variants remain available, but are not the active icon. In
particular, `icon-light.ico` has a circular background and must not be substituted
for the approved rounded-square icon. The dark-brand generator does not own or
overwrite the newly named active icon.
