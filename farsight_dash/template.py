"""HTML template injection for sell-through dashboard."""

import os, json

from .core import DashEncoder, load_logo_b64, build_font_css


def build_html(config, data, output_dir, shared_dir):
    """Inject data into HTML template and write output file.

    Args:
        config: Client config dict
        data: The DATA dict from aggregate.build_all()
        output_dir: Directory to write index.html
        shared_dir: Path to shared_data/ for branding assets
    """
    print("Building HTML output...")

    # Find the template
    template_path = os.path.join(os.path.dirname(__file__), 'html', 'sellthrough_template.html')
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"HTML template not found: {template_path}")

    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # Serialize data to JSON
    data_json = json.dumps(data, cls=DashEncoder, separators=(',', ':'))
    print(f"  JSON size: {len(data_json):,} chars")

    # ── Replace template markers ──

    # Client branding
    client_name = config['client_name']
    password = config['password']
    primary_color = config['branding'].get('primary_color', '#4A90D9')
    dashboard_url = config.get('dashboard_url', '')

    html = html.replace('{{CLIENT_NAME}}', client_name)
    html = html.replace('{{CLIENT_SLUG}}', config['client_slug'])
    html = html.replace('{{PASSWORD}}', password)
    html = html.replace('{{PRIMARY_COLOR}}', primary_color)
    html = html.replace('{{DASHBOARD_URL}}', dashboard_url)

    # Optional "Work in Progress" draft badge (config: wip: true) — client-specific.
    wip = config.get('wip', False)
    wip_gate = ('<div style="display:inline-block;background:#FBE5A0;color:#7A5C00;border:1px solid #E6C200;'
                'border-radius:8px;font-size:11px;font-weight:700;letter-spacing:1px;padding:4px 14px;'
                'margin-bottom:20px;text-transform:uppercase;">&#9888;&#65039; Work in Progress &middot; Draft</div>'
                ) if wip else ''
    wip_header = ('<span style="display:inline-block;background:#FBE5A0;color:#7A5C00;border:1px solid #E6C200;'
                  'border-radius:6px;font-size:10px;font-weight:700;letter-spacing:.8px;padding:2px 9px;'
                  'margin-left:14px;vertical-align:middle;text-transform:uppercase;">&#9888;&#65039; WIP &middot; Draft</span>'
                  ) if wip else ''
    html = html.replace('{{WIP_BADGE}}', wip_gate)
    html = html.replace('{{WIP_BADGE_HEADER}}', wip_header)

    # Inject DATA JSON
    html = html.replace('{{DATA_JSON}}', data_json)

    # Font CSS
    font_config = None
    if config['branding'].get('font_family') and config['branding'].get('font_files'):
        font_config = {
            'family': config['branding']['font_family'],
            'files': config['branding']['font_files'],
        }
    # Also embed header font if different
    header_font_config = None
    branding = config['branding']
    if branding.get('header_font_family') and branding.get('header_font_files'):
        header_font_config = {
            'family': branding['header_font_family'],
            'files': branding['header_font_files'],
        }
    font_css = build_font_css(shared_dir, font_config)
    if header_font_config:
        font_css += '\n' + build_font_css(shared_dir, header_font_config)
    html = html.replace('{{FONT_CSS}}', font_css)

    # Font family for CSS
    font_family = config['branding'].get('font_family')
    if font_family:
        html = html.replace('{{FONT_FAMILY}}', f"'{font_family}',")
    else:
        html = html.replace('{{FONT_FAMILY}}', '')

    # Header font (optional — for brand title, defaults to body font)
    header_font = config['branding'].get('header_font_family')
    if header_font:
        html = html.replace('{{HEADER_FONT_FAMILY}}', f"'{header_font}',")
    else:
        html = html.replace('{{HEADER_FONT_FAMILY}}', '')

    # Logos
    # Farsight logo (always included — it's the "built by" branding)
    farsight_logo_b64 = load_logo_b64(shared_dir, 'Farsight Logo.png')
    if not farsight_logo_b64:
        # Fallback: bundled logo in farsight_dash/html/
        bundled = os.path.join(os.path.dirname(__file__), 'html', 'farsight_logo.png')
        if os.path.exists(bundled):
            import base64
            with open(bundled, 'rb') as bf:
                farsight_logo_b64 = base64.b64encode(bf.read()).decode()
    html = html.replace('{{FARSIGHT_LOGO_B64}}', farsight_logo_b64 or '')

    # Client logo in the header, replacing the client name set in type. OPT-IN via
    # branding.logo_in_header — several configs already set logo_file for other purposes
    # (the public DEMO points at "Farsight Logo.png"), and turning this on by default would
    # silently restyle their headers on the next rebuild.
    client_logo_b64 = ''
    if config['branding'].get('logo_in_header'):
        client_logo_b64 = load_logo_b64(shared_dir, config['branding'].get('logo_file'))
        if not client_logo_b64:
            print("  ⚠ logo_in_header is set but branding.logo_file was not found in "
                  "shared_data/Branding — falling back to the text title.")
    html = html.replace('{{CLIENT_LOGO_B64}}', client_logo_b64)

    # Write output
    output_path = os.path.join(output_dir, 'index.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"  Output: {output_path}")
    print(f"  Size: {os.path.getsize(output_path):,} bytes")
    return output_path
