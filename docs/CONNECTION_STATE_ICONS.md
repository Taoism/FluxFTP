# FluxFTP connection-state icons

The icon family has light and dark SVG variants for each state:

- `idle`
- `connecting`
- `connected`
- `uploading`
- `downloading`
- `fxp` (server-to-server transfer)
- `error`

The SVGs share the FluxFTP three-arm core, metal instrument bezel, readout,
reset control and stability meter. State colors and small overlays distinguish
connection and transfer activity. Both variants are packaged with desktop
builds.

Use `fluxftp.connection_state_icons.connection_state_icon(state, dark=False)`
to load an icon. The theme monitor can pass `dark=True` for dark palettes.

See the [light and dark contact sheet](connection-state-icons.svg).
