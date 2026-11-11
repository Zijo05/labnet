# Vendored front-end libraries

These files are copied into the repository unchanged, so the web UI needs no
build step and no internet access. All are MIT licensed. They were downloaded
from `https://cdn.jsdelivr.net/npm/<package>@<version>/<path>`.

| File | Package | Version | Path in package |
|------|---------|---------|-----------------|
| `cytoscape.min.js` | cytoscape | 3.30.4 | `dist/cytoscape.min.js` |
| `xterm.js` | @xterm/xterm | 5.5.0 | `lib/xterm.js` |
| `xterm.css` | @xterm/xterm | 5.5.0 | `css/xterm.css` |
| `addon-fit.js` | @xterm/addon-fit | 0.10.0 | `lib/addon-fit.js` |
| `marked.min.js` | marked | 12.0.2 | `marked.min.js` |

To update one, download the new version to the same file name and change the
version in this table.
