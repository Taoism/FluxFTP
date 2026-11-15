"""Generate the light and dark FluxFTP connection-state SVG icon family."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'fluxftp' / 'assets' / 'connection-states'

STATES = {
    'idle': ('IDLE', '#ffbf55', '#d35043', ''),
    'connecting': ('CONNECTING', '#56cfff', '#e0a63e', 'orbit'),
    'connected': ('CONNECTED', '#5beba0', '#48c875', 'check'),
    'uploading': ('UPLOADING', '#ffc252', '#e16b42', 'up'),
    'downloading': ('DOWNLOADING', '#ffc252', '#e16b42', 'down'),
    'fxp': ('FXP TRANSFER', '#53e3f4', '#db7648', 'fxp'),
    'error': ('ERROR', '#ff5e58', '#e74742', 'warning'),
}

LIGHT = {
    'shell': ['#f3f2eb', '#899398', '#d8d8d0', '#737d82', '#efeee6'],
    'edge': '#43515a', 'glass': ['#3b4967', '#111a2a', '#293b5a'],
    'panel': '#252c30', 'panel_edge': '#58646b', 'text': '#e0e3db',
}
DARK = {
    'shell': ['#8b969b', '#303a42', '#535e65', '#252e35', '#7b878d'],
    'edge': '#a8b5ba', 'glass': ['#303d59', '#0c1421', '#202f49'],
    'panel': '#10171d', 'panel_edge': '#79858b', 'text': '#e1e7e7',
}


def icon_svg(name, dark):
    label, glow, lamp, effect = STATES[name]
    palette = DARK if dark else LIGHT
    shell = ''.join(f'<stop offset="{i / (len(palette["shell"])-1):.2f}" stop-color="{color}"/>'
                    for i, color in enumerate(palette['shell']))
    glass = ''.join(f'<stop offset="{i / (len(palette["glass"])-1):.2f}" stop-color="{color}"/>'
                    for i, color in enumerate(palette['glass']))
    overlay = {
        'orbit': f'<circle cx="128" cy="123" r="49" fill="none" stroke="{glow}" stroke-width="5" stroke-dasharray="17 8"/><path d="m162 91 15 1-5 14" fill="none" stroke="{glow}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>',
        'check': f'<path d="m108 123 13 13 28-30" fill="none" stroke="{glow}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>',
        'up': f'<path d="M128 177v-36m0 0-16 16m16-16 16 16" fill="none" stroke="{glow}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>',
        'down': f'<path d="M128 137v36m0 0-16-16m16 16 16-16" fill="none" stroke="{glow}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>',
        'fxp': f'<path d="M81 150h94m0 0-14-13m14 13-14 13M175 164H81m0 0 14-13m-14 13 14 13" fill="none" stroke="{glow}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>',
        'warning': f'<path d="m128 140 23 39h-46z" fill="#291519" stroke="{glow}" stroke-width="5" stroke-linejoin="round"/><path d="M128 151v13m0 7v1" stroke="#fff2df" stroke-width="4" stroke-linecap="round"/>',
        '': '',
    }[effect]
    center = '' if effect in ('check', 'warning') else f'<circle cx="128" cy="123" r="22" fill="url(#flare)"/>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="FluxFTP {label.lower()} state icon">
<defs>
 <linearGradient id="shell" x1="22" y1="8" x2="229" y2="248" gradientUnits="userSpaceOnUse">{shell}</linearGradient>
 <linearGradient id="glass" x1="43" y1="42" x2="216" y2="197" gradientUnits="userSpaceOnUse">{glass}</linearGradient>
 <linearGradient id="beam" x1="72" y1="75" x2="184" y2="177" gradientUnits="userSpaceOnUse"><stop stop-color="#f4ffff"/><stop offset=".52" stop-color="{glow}"/><stop offset="1" stop-color="#c2d0ff"/></linearGradient>
 <radialGradient id="flare"><stop stop-color="#fff"/><stop offset=".24" stop-color="{glow}"/><stop offset="1" stop-color="{glow}" stop-opacity="0"/></radialGradient>
 <linearGradient id="amber" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#ffd778"/><stop offset="1" stop-color="#ed702d"/></linearGradient>
 <filter id="bloom" x="51" y="55" width="154" height="144" color-interpolation-filters="sRGB"><feGaussianBlur stdDeviation="7"/></filter>
</defs>
<rect x="5" y="5" width="246" height="246" rx="37" fill="#10171c"/>
<rect x="9" y="8" width="238" height="240" rx="35" fill="url(#shell)" stroke="{palette['edge']}" stroke-width="2"/>
<circle cx="20" cy="19" r="3" fill="#535e64"/><circle cx="236" cy="19" r="3" fill="#535e64"/><circle cx="20" cy="237" r="3" fill="#535e64"/><circle cx="236" cy="237" r="3" fill="#535e64"/>
<rect x="73" y="12" width="110" height="20" rx="3" fill="#9f3831" stroke="#57231f"/>
<text x="128" y="25" text-anchor="middle" fill="#fff0d2" font-family="monospace" font-size="10" font-weight="700" letter-spacing="1.2">FLUXFTP CORE</text>
<rect x="27" y="37" width="202" height="156" rx="47" fill="#080b10" stroke="#4b5055" stroke-width="4"/>
<rect x="36" y="43" width="184" height="144" rx="39" fill="url(#glass)" stroke="#aab5bb" stroke-opacity=".55" stroke-width="2"/>
<path d="M47 59Q128 29 209 59" fill="none" stroke="#fff" stroke-opacity=".16" stroke-width="4"/>
<path d="M75 76 128 123 181 76M128 123v42" fill="none" stroke="{glow}" stroke-width="24" stroke-linecap="round" stroke-linejoin="round" opacity=".62" filter="url(#bloom)"/>
<path d="M75 76 128 123 181 76M128 123v42" fill="none" stroke="url(#beam)" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="75" cy="76" r="11" fill="{lamp}" stroke="#ffd2ab" stroke-width="3"/><circle cx="181" cy="76" r="11" fill="{lamp}" stroke="#ffd2ab" stroke-width="3"/><circle cx="128" cy="165" r="9" fill="{lamp}" stroke="#ffd2ab" stroke-width="3"/>
{center}{overlay}
<path d="M64 88v34q0 10 10 10h5M192 88v34q0 10-10 10h-5M128 175v9" fill="none" stroke="#e6bd59" stroke-width="4" stroke-linecap="round"/>
<rect x="22" y="198" width="212" height="46" rx="6" fill="{palette['panel']}" stroke="{palette['panel_edge']}" stroke-width="2"/>
<rect x="29" y="204" width="54" height="26" rx="3" fill="#080c0e" stroke="#4c565a"/>
<path d="M35 208h13v3H38v5h8v3h-8v5h10v3H35zM51 208h13v3H54v5h8v3h-8v5h10v3H51zM67 208h10v3h-7v5h6v3h-6v5h7v3H67z" fill="url(#amber)"/>
<text x="56" y="237" text-anchor="middle" fill="#c1c9c3" font-family="monospace" font-size="4.5" letter-spacing=".5">CORE LOAD</text>
<rect x="91" y="203" width="49" height="31" rx="4" fill="#aeb5ad" stroke="#e8e5d7"/>
<circle cx="115" cy="215" r="9" fill="#252d30" stroke="#626e6e" stroke-width="1.5"/><path d="M115 209v12m-5-9 10 6m0-6-10 6" stroke="{glow}" stroke-width="1.8" stroke-linecap="round"/><circle cx="115" cy="215" r="2" fill="#f2d572"/>
<text x="115" y="230" text-anchor="middle" fill="#283033" font-family="monospace" font-size="4.5" font-weight="700">RESET</text>
<rect x="148" y="203" width="78" height="31" rx="4" fill="#070b0d" stroke="#586168"/>
<path d="M155 221a30 19 0 0 1 64 0" fill="#263032" stroke="#aeb6a9" stroke-width="1.4"/><path d="m187 219 13-11" stroke="{glow}" stroke-width="2" stroke-linecap="round"/><circle cx="187" cy="219" r="2.5" fill="#e2cf9c"/>
<path d="M160 218v-3m10-8 2 3m15-5v4m14-3-1 3m10 4-2 2" stroke="#f0d59b" stroke-width="1"/>
<text x="187" y="231" text-anchor="middle" fill="#c6c9b9" font-family="monospace" font-size="4.4" letter-spacing=".4">{label}</text>
</svg>'''


def main():
    preview = [
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1680 620">',
        '<rect width="1680" height="620" fill="#15202b"/>',
        '<rect y="310" width="1680" height="310" fill="#e8edf0"/>',
        '<text x="32" y="34" fill="#dce8ef" font-family="Segoe UI,Arial,sans-serif" font-size="18" font-weight="700">DARK MODE</text>',
        '<text x="32" y="344" fill="#263746" font-family="Segoe UI,Arial,sans-serif" font-size="18" font-weight="700">LIGHT MODE</text>',
    ]
    for theme in ('light', 'dark'):
        folder = OUTPUT / theme
        folder.mkdir(parents=True, exist_ok=True)
        row = 0 if theme == 'dark' else 1
        for index, state in enumerate(STATES):
            (folder / f'{state}.svg').write_text(icon_svg(state, theme == 'dark'), encoding='utf-8')
            x, y = 22 + index * 238, 42 + row * 310
            preview.append(f'<image x="{x}" y="{y}" width="212" height="212" href="../fluxftp/assets/connection-states/{theme}/{state}.svg"/>')
            preview.append(f'<text x="{x + 106}" y="{y + 238}" fill="{"#e4eaf0" if theme == "dark" else "#263746"}" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-size="13" font-weight="700">{STATES[state][0]}</text>')
    preview.append('</svg>')
    (ROOT / 'docs' / 'connection-state-icons.svg').write_text('\n'.join(preview) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
