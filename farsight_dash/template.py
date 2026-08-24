"""HTML template injection for sell-through dashboard."""

import os, json, re

from .core import DashEncoder, load_logo_b64, build_font_css


CHART_REGION_START = 'const CP = {'


def _rgb_triplet(hex_color):
    """'#4F7A52' -> (79,122,82). None for anything that isn't a 6-digit hex."""
    h = (hex_color or '').strip().lstrip('#')
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _rgba(hex_color, alpha):
    """'#4F7A52', .16 -> 'rgba(79,122,82,.16)'. Returns None for non-hex input."""
    h = (hex_color or '').strip().lstrip('#')
    if len(h) != 6:
        return None
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return f"rgba({r},{g},{b},{str(alpha).lstrip('0')})"


def _chart_palette_js(palette):
    """JS half of `branding.chart_palette`: replace the categorical series ramp.

    The semantic colours are handled by `_apply_chart_remap` below (a build-time rewrite);
    this covers the things that are cleaner to set from JS — the `COLORS` array used when a
    chart draws one series per retailer / category / collection, and the named lookups.

    Emitted at the very end of the template's script, so every palette object already exists.
    Returns '' when the client has no `chart_palette`, leaving those clients untouched.
    """
    if not palette:
        return ''
    out = []
    for name, key in (('TIER_COLORS', 'tiers'), ('DTC_CH_COLORS', 'dtc_channels'),
                      ('RATING_COLORS', 'ratings')):
        val = palette.get(key)
        if val:
            out.append(f'Object.assign({name},{json.dumps(dict(val))});')
    series = palette.get('series')
    if series:
        # Mutate in place — COLORS is a const, so it can't be reassigned.
        out.append(f'COLORS.length=0;COLORS.push.apply(COLORS,{json.dumps(list(series))});')
    return '\n'.join(out)


def _apply_chart_remap(html, palette):
    """Recolour the charts by remapping the engine's colour literals to the client's.

    Chart colours live as ~60 hex literals spread through the render functions (plus the `CP`
    defaults block). Each literal carries one consistent meaning — `#727F97` is always the
    primary/this-year series, `#E8DDD5` always last-year bars, `#5B9BD5` always forecast — so a
    client can rebrand every chart by declaring what those roles should look like:

        branding:
          chart_palette:
            remap:
              "#727F97": "#121212"    # this year -> ink

    Applied only from the `CP` declaration onwards, so the password gate's own inline colours
    (which run before any of this exists) are never touched. Case-insensitive on the source hex.
    No `remap` key means no substitution at all.
    """
    remap = (palette or {}).get('remap')
    if not remap:
        return html
    cut = html.find(CHART_REGION_START)
    if cut == -1:
        print("  ⚠ chart_palette.remap: couldn't locate the chart palette block — skipping.")
        return html
    head, tail = html[:cut], html[cut:]
    hits = 0
    # Exact-string rules first: they let a client pin a specific rgba() (e.g. to change the
    # alpha as well as the hue). Whatever they produce is already a brand value, so the
    # colour-aware pass below won't touch it again.
    rules = sorted(remap.items(), key=lambda kv: kv[0].startswith('#'))
    for old, new in rules:
        # Match only as a whole colour token, so a rule for '#A3E5F7' can never chew into
        # '#A3E5F7A0' (the doors chart builds 8-digit hexes by appending an alpha suffix).
        pat = re.compile(re.escape(old) + r'(?![0-9A-Fa-f])', re.IGNORECASE)
        tail, n = pat.subn(new, tail)
        hits += n

        # Several charts express the same colour as rgba() with a fill alpha instead of a
        # hex. Rewrite those too, keeping whatever alpha was there — otherwise a chart that
        # fills at 85% keeps the engine's colour while its neighbours change.
        src, dst = _rgb_triplet(old), _rgb_triplet(new)
        if not src or not dst:
            continue
        r, g_, b = src
        rgba_pat = re.compile(r'rgba\(\s*%d\s*,\s*%d\s*,\s*%d\s*,([^)]*)\)' % (r, g_, b))
        tail, n = rgba_pat.subn(lambda m: 'rgba(%d,%d,%d,%s)' % (*dst, m.group(1).strip()), tail)
        hits += n

    print(f"  Chart palette: {hits} colour literals remapped across {len(remap)} rules")
    return head + tail


def _theme_css(theme):
    """Turn a `branding.theme` config block into a CSS override sheet.

    Lets a client's dashboard match their own web identity without forking the template. Fara
    Homidi's site (farahomidi.com) is Unica-77 throughout, #121212 on white with #F3F3F3 as the
    only secondary tone, hairline rules, zero corner radius and no accent colour at all — the
    default look here is warm, rounded and teal-accented, which reads as generic next to it.

    Every key is optional; omitted keys leave the default in place.
    """
    if not theme:
        return ''
    g = lambda k, d=None: theme.get(k, d)
    parts = []

    var_map = [
        ('bg', '--bg'), ('bg_alt', '--bg-warm'), ('card', '--bg-card'),
        ('border', '--border'), ('border_light', '--border-light'),
        ('text', '--text'), ('text_muted', '--text-muted'), ('text_light', '--text-light'),
        ('accent', '--accent'), ('accent_dark', '--accent-dark'), ('accent_light', '--accent-light'),
    ]
    vars_out = [f'  {css}:{g(key)};' for key, css in var_map if g(key)]
    if g('flat'):
        vars_out += ['  --shadow:none;', '  --shadow-hover:none;']
    if vars_out:
        parts.append(':root{\n' + '\n'.join(vars_out) + '\n}')

    # Variance colours. Saturated web green/red fight a restrained editorial palette, but the
    # up/down signal still has to survive — so these are muted, not removed.
    if g('pos') or g('neg'):
        sem = []
        if g('pos'):
            sem += [f"  --green:{g('pos')};", f"  --green-bg:{g('pos_bg', 'rgba(79,122,82,.07)')};"]
        if g('neg'):
            sem += [f"  --red:{g('neg')};", f"  --red-bg:{g('neg_bg', 'rgba(164,69,62,.07)')};"]
        parts.append(':root{\n' + '\n'.join(sem) + '\n}')
        parts.append('.pos{color:var(--green);}.neg{color:var(--red);}')
        # The heat-map cells carry their own hardcoded web green/red rgba, so they don't
        # follow --green/--red and would stay saturated after everything else was muted.
        heat = []
        for key, cls in (('pos', 'green'), ('neg', 'red')):
            for alpha, suffix in ((.16, ''), (.07, 'light-')):
                bg = _rgba(g(key), alpha)
                if bg:
                    heat.append(f'.hm-{suffix}{cls}{{background:{bg};color:var(--{cls});}}')
        if heat:
            parts.append(''.join(heat))
    if g('warn'):
        parts.append(f":root{{--yellow:{g('warn')};"
                     f"--yellow-bg:{_rgba(g('warn'), .08) or 'rgba(184,137,74,.08)'};}}")

    if g('letter_spacing'):
        parts.append(f"body{{letter-spacing:{g('letter_spacing')};}}")

    radius = g('radius')
    if radius is not None:
        # radius is set inline on many elements, so this one needs !important
        parts.append(
            '.kpi-card,.chart-container,.table-container,.modal,.date-badge,.period-btn,.ms-btn,'
            '.ms-pop,.badge,.data-flag,.scope-label,.region-tile,.search-box,.door-search,'
            '.period-toggle,.tip,.ct-text,.ct-icon,.modal-count,'
            'button,select,input,textarea,table,th,td,'
            # the WIP badge and gate button are inline-styled; target them explicitly
            '#pw-gate div,#pw-gate input,#pw-gate button,.wip-badge,'
            # Catch-all for the generated markup: the status page, the banners and the status
            # chips are built as unclassed divs with an inline radius, so no class list can
            # reach them.
            '[style*="border-radius"]'
            f'{{border-radius:{radius} !important;}}')

    # A brand ground colour for the header band / splash, with the data canvas left neutral.
    # farahomidi.com uses its blue as a full-page ground on interior pages; a dashboard still
    # needs a neutral field behind dense figures, so the brand colour lives in the chrome.
    if g('header_bg'):
        parts.append(
            f".header{{background:{g('header_bg')} !important;}}"
            f"#pw-gate{{background:{g('header_bg')} !important;}}"
            ".header .subtitle{color:rgba(0,0,0,.62);}"
            ".topbar{border-bottom:1px solid rgba(0,0,0,.12);}")

    if g('flat'):
        flat = [
            '.topbar{box-shadow:none;border-bottom:1px solid var(--border);}',
            '.kpi-card,.chart-container,.table-container{box-shadow:none;border:1px solid var(--border-light);}',
            '.kpi-card:hover,.chart-container:hover{box-shadow:none;}',
            '.date-badge{background:transparent;color:var(--text);border:1px solid var(--border);font-weight:400;}',
        ]
        # Don't flatten the header/gate to the page background when a brand ground is set —
        # header_bg is emitted above and this block would otherwise override it.
        if not g('header_bg'):
            flat.insert(0, '#pw-gate{background:var(--bg) !important;}')
            flat.insert(1, '.header{background:var(--bg);}')
        parts.append(''.join(flat))

    if g('type_scale') == 'comfortable':
        # CSS sizes cluster at 9-12px (13x at 10px, 12x at 11px); the brand site is 24px with
        # a lot of air. A data grid can't be 24px, but it can stop being cramped: fewer
        # distinct sizes, a step up at the small end, and more space between blocks.
        parts.append(
            'table{font-size:12px;}'
            'th,.modal-body th{font-size:11px;padding:10px 8px;}'
            'td{padding:9px 8px;}'
            '.kpi-label{font-size:11px;margin-bottom:10px;}'
            '.kpi-value{font-size:26px;}'
            '.kpi-sub,.rt-sub,.chart-hint{font-size:11px;}'
            # Tight enough that all the tabs fit at 1440 without a horizontal scroller — the
            # bigger type would otherwise push the last two off the edge.
            '.nav-tab{font-size:12.5px;padding:15px 16px;}'
            '.chart-title,.table-title{font-size:14px;margin-bottom:16px;}'
            '.global-filters label,.filter-bar label,.door-filters label{font-size:11px;}'
            '.email-table{font-size:12.5px;}'
            '.scope-label{font-size:12px;}'
            '.page{padding:34px 40px;}'
            '.kpi-grid,.chart-row{gap:20px;margin-bottom:32px;}'
            '.chart-container,.table-container{padding:24px;}'
            '.table-container{margin-bottom:32px;}'
            # Below 1000px the stock rule centres the header and stacks it into four bands —
            # wordmark, filters, date pill, byline — which eats a third of the viewport. The
            # brand's own header stays left-aligned at every width, so keep it left and let
            # the rows sit close together.
            '@media(max-width:1000px){'
            '.header{text-align:left;align-items:flex-start;gap:14px;padding:16px 20px;}'
            '.global-filters{justify-content:flex-start;}'
            '.header>div:last-child{align-items:flex-start !important;}'
            '.date-badge,.farsight-brand{text-align:left;justify-content:flex-start;}'
            '.page{padding:22px 20px;}}')

    if g('cards') == 'rules':
        # The site has no cards, no fills and no shadows — structure comes from hairline rules
        # and space. Boxes inside boxes is the other half of the generic-dashboard look.
        parts.append(
            '.kpi-card{border:none !important;border-top:1px solid var(--text) !important;'
            'padding:14px 0 0;}'
            '.kpi-card:hover{transform:none;}'
            '.region-tile{border:none !important;border-top:1px solid var(--border) !important;'
            'padding:12px 0 0;text-align:left;}'
            '.region-tile:hover{transform:none;}'
            '.chart-container,.table-container{border:none !important;padding:0 0 12px;}'
            '.chart-title,.table-title{border-bottom:1px solid var(--border);padding-bottom:10px;}'
            # The weekly-report header row was a solid bar of ink. The brand never fills a
            # block like that — it rules and spaces instead.
            # Both the row AND the cells are filled — clearing only the cells leaves black
            # text on the row's black background.
            '.email-table thead tr{background:transparent !important;}'
            '.email-table th{background:transparent !important;color:var(--text) !important;'
            'border-bottom:1px solid var(--text);}'
            '.email-table th[colspan],.email-table th[rowspan]{'
            'border-left-color:var(--border) !important;'
            'border-bottom-color:var(--border) !important;}')

    if g('sentence_case'):
        # Ten different label styles are uppercased (column groups, KPI labels, nav tabs,
        # "AS OF WEEK", "PERIOD", "CUSTOMER"). The brand sets everything in sentence case at
        # normal weight, so this is the single biggest "reads like a SaaS dashboard" tell.
        # Several are inline-styled, hence !important. Tracking exists only to make uppercase
        # legible, so it goes with it.
        parts.append(
            '#pw-content *,#pw-gate *{text-transform:none !important;}'
            'th,.kpi-label,.nav-tab,label,.rt-label,.badge,.data-flag,.scope-label,'
            '.email-table th,.modal-body th,.period-btn,.modal-count,.risk-item .r-badge'
            '{letter-spacing:0 !important;}')

    if g('collapse_weights'):
        # Only 400 and 700 are embedded, but the CSS asks for 500 (9x) and 600 (40x), so the
        # browser synthesises those — which is why the type reads slightly heavy and muddy
        # next to the real face. The brand site is 400 almost everywhere.
        parts.append(
            '#pw-content *,#pw-gate *{font-synthesis-weight:none;}'
            '[style*="font-weight:500"],[style*="font-weight:600"]{font-weight:400 !important;}'
            '.nav-tab,.kpi-label,.chart-title,.table-title,th,label,.rt-label,.email-table th,'
            '.period-btn,.badge,.data-flag,.scope-label,.ms-compare,.modal-header h3,'
            '.risk-header,.risk-item .r-name,.risk-item .r-var,.risk-item .r-badge,'
            '.heatmap-cell,.farsight-brand,.date-badge,.kpi-delta,.modal-count,'
            '.chart-tip .ct-icon{font-weight:400;}'
            # These out-specify the list above (class+class, or element+class), so they need
            # naming in their own right rather than relying on the bare element selector.
            '.global-filters label,.filter-bar label,.door-filters label,.csv-dl-btn,'
            '.period-btn.active,.nav-tab.active{font-weight:400;}'
            # Kept bold, because these are the only two places the brand would bold: the
            # figure itself and a total. The active nav tab is marked by its rule and its ink,
            # not by weight — that is how the site's own nav works.
            '.kpi-value,.rt-value,tr.total-row{font-weight:700;}'
            # <strong> would otherwise resolve to 900 and fall back to the 700 face anyway.
            '#pw-content strong,#pw-content b{font-weight:700;}')

    if g('flag_bg'):
        # The stock "needs attention" tone is amber (#FBF0D8 / #E6C86B / #8A6D1A): the WIP
        # badge, the Open Items tab, the freshness banner and the LY-comparability banner.
        # Against brand blue and black it is the most discordant thing on the page. The client
        # explicitly wants gaps FLAGGED, so this restyles rather than removes — same
        # prominence, brand-native tone. Most of it is inline-styled, so it is reached by
        # attribute selector on the literal colour and needs !important.
        fb = g('flag_bg')
        fbd = g('flag_border', 'rgba(18,18,18,.22)')
        ft = g('flag_text', g('text', '#121212'))
        parts.append(
            f'.data-flag,[style*="#FBF0D8"],[style*="#FBE5A0"]'
            f'{{background:{fb} !important;border-color:{fbd} !important;color:{ft} !important;}}'
            f'[style*="#8A6D1A"],[style*="#7A5C00"],.nav-tab[data-page="status"]'
            f'{{color:{ft} !important;}}'
            # The badge sits ON the brand ground in the header and on the gate, so a wash of
            # that same blue would disappear. White with a hairline is how the site handles
            # anything that has to sit on top of the colour.
            f'.wip-badge{{background:#fff !important;border-color:{fbd} !important;'
            f'color:{ft} !important;}}')

    if g('gate_brand'):
        # The gate's markup is inline-styled and can't be reached by class, so it is targeted
        # on its own inline declarations. Its title is tracked 6px for the uppercase setting
        # it used to have — with uppercase gone that tracking just looks broken — and its
        # muted grey was chosen against white, not against a colour ground.
        parts.append(
            '#pw-gate [style*="letter-spacing:6px"]{letter-spacing:1.5px !important;'
            'color:var(--text) !important;}'
            '#pw-gate [style*="#A5ABAF"]{color:rgba(18,18,18,.62) !important;}'
            '#pw-gate input{border:1px solid rgba(18,18,18,.3) !important;'
            'background:#fff !important;}'
            # The brand's buttons are a 1px hairline with no fill — a solid black slab is the
            # one thing their whole visual system avoids.
            '#pw-gate button{background:transparent !important;color:var(--text) !important;'
            'border:1px solid var(--text) !important;}'
            '#pw-gate button:hover{background:var(--text) !important;color:#fff !important;}')

    if g('heading_tracking'):
        parts.append(f".header h1,.brand-title{{letter-spacing:{g('heading_tracking')};font-weight:400;}}")
    if g('table_head_tracking'):
        parts.append(f"th{{letter-spacing:{g('table_head_tracking')};}}")

    if g('ground') == 'page':
        # `header_bg` alone paints the brand colour as a stripe behind the header — an accent,
        # which is precisely the move this brand never makes. Its site uses the colour as a
        # FULL-PAGE ground with content sitting directly on it. This translates that honestly:
        # the whole page grounds in the brand colour, the header and nav sit straight on it
        # (as the site's header does), and the dense figures live on one white sheet laid over
        # the ground — a document on the brand colour, not a card grid under a coloured banner.
        # Emitted last on purpose: it overrides the header_bg/flat blocks' .topbar and nav rules.
        gb = g('header_bg') or g('bg', '#FFFFFF')
        sheet_w = g('sheet_max_width', '1280px')
        parts.append(
            f'body{{background:{gb};}}'
            # The topbar is sticky, so it needs the ground colour itself or content bleeds
            # through it on scroll. No hairline beneath — the site's header floats on the
            # ground with nothing drawn under it.
            f'.topbar{{background:{gb};border-bottom:none !important;}}'
            f'.header{{background:transparent !important;max-width:{sheet_w};margin:0 auto;width:100%;}}'
            # Nav directly on the ground: quiet ink, active marked by an ink underline. Muted
            # greys were tuned for white and go illegible on the colour.
            f'.nav-tabs{{background:transparent;border-bottom:none;max-width:{sheet_w};'
            'margin:0 auto;width:100%;}'
            '.nav-tab{color:rgba(18,18,18,.62);}'
            '.nav-tab:hover{background:transparent;color:var(--text);}'
            '.nav-tab.active{color:var(--text);border-bottom-color:var(--text);}'
            # The sheet. One white surface for all the figures, hairline edge, ground showing
            # in the gutters — proportions echo the site's wide right gutter.
            f'.page{{background:var(--bg-card);max-width:{sheet_w};margin:0 auto 56px;'
            'border:1px solid rgba(18,18,18,.18);}'
            # Sticky table headers inside the sheet keep a solid backdrop.
            'th{background:var(--bg-card);}'
            '@media(max-width:1000px){.page{margin:0 0 32px;border-left:none;border-right:none;}}')

    if g('site_mirror'):
        # Everything below is measured off farahomidi.com at 1440px, element by element, so
        # the dashboard shares the site's actual anatomy rather than a resemblance:
        #   · header: nav LEFT at the 50px page margin, wordmark RIGHT, the whole thing
        #     mix-blend-mode:difference over the ground (white ink → warm brown on the blue)
        #   · page margin: exactly 50px both sides (their shop grid: items at x=50, gutters 8)
        #   · buttons: 1px ink hairline, transparent, sentence case, 0 radius
        #   · interactions: color transitions .15s ease-in; selected/hover states tinted with
        #     the ground colour rather than grey
        ground = g('header_bg') or '#A3E5F7'
        tint = _rgba(ground, .16) or 'rgba(163,229,247,.16)'
        tint_soft = _rgba(ground, .1) or 'rgba(163,229,247,.1)'
        ink_hair = 'rgba(18,18,18,.4)'
        parts.append(
            # ── Header mirrored: controls where the site's nav sits, wordmark on the right.
            '@media(min-width:1001px){'
            '.header{flex-direction:row-reverse;justify-content:space-between;'
            'max-width:none;margin:0;padding:26px 50px 18px;}'
            '.header>div:first-child{text-align:right;}'
            '}'
            # The site's signature: white ink differenced over the ground. The logo PNG is
            # black-on-transparent, so invert() gives the white ink and the blend does the
            # rest — identical mechanism, and deterministic here because the ground is a
            # fixed hex. Applied to the image only, so the WIP badge (a sibling span inside
            # the h1) keeps its own colours.
            '#brandTitle img{filter:invert(1);mix-blend-mode:difference;height:32px !important;}'
            '.header .subtitle{font-size:12.5px;color:rgba(18,18,18,.55);}'
            # ── Nav as the site sets nav: text at the page margin, no boxes, breathing room.
            '.nav-tabs{max-width:none;margin:0;padding:0 50px;gap:0;}'
            # 26px gap: the largest that fits all 15 tabs inside 1440 − 2×50 margins.
            '.nav-tab{padding:13px 0;margin-right:26px;font-size:12px;'
            'transition:color .15s ease-in;}'
            '.nav-tab:hover{border-bottom-color:' + ink_hair + ';}'
            '.nav-tab.active{border-bottom-width:2px;}'
            # ── Sheet at the site's 50px page margins, full width between them.
            '@media(min-width:1001px){.page{max-width:none;margin:0 50px 64px;}}'
            # ── Header furniture as site furniture: hairline ink on the ground, no fills.
            f'.global-filters select,.ms-btn{{background:transparent;border:1px solid {ink_hair};'
            'color:var(--text);}'
            f'.period-toggle{{border:1px solid {ink_hair};}}'
            f'.period-btn{{background:transparent;color:rgba(18,18,18,.55);'
            f'border-right:1px solid {ink_hair};transition:color .15s ease-in;}}'
            '.period-btn:hover{background:rgba(255,255,255,.45);color:var(--text);}'
            '.period-btn.active{background:var(--bg-card);color:var(--text);}'
            '.date-badge{background:transparent;border:none;padding:0;max-width:none;'
            'font-size:12.5px;color:rgba(18,18,18,.7);font-weight:400;line-height:1.4;}'
            # ── The ground colour lives in the interactions, the way the brand would use it:
            # row hover, menu hover, text selection.
            f'tr:hover td{{background:{tint_soft};}}'
            f'.ms-pop label:hover{{background:{tint_soft};}}'
            f'::selection{{background:{ground};}}'
            f'.region-tile.active-filter{{border-top-color:var(--text) !important;'
            f'background:{tint_soft};box-shadow:none;}}'
            # ── Buttons on the sheet: the site's 1px-ink hairline button, not a filled slab.
            # The Recap button is inline-styled with the accent fill, so it's reached by its
            # onclick attribute.
            '[onclick="downloadRecap()"]{background:transparent !important;'
            'color:var(--text) !important;border:1px solid var(--text) !important;'
            'font-weight:400 !important;transition:all .15s ease-in;}'
            '[onclick="downloadRecap()"]:hover{background:var(--text) !important;'
            'color:#fff !important;}'
            '.csv-dl-btn{transition:all .15s ease-in;}'
            f'.csv-dl-btn:hover{{background:{tint} !important;}}'
            # The review-notes Save button is generated inline with the accent fill — the
            # last filled slab. Same hairline treatment, reached by its inline declaration.
            # MUST stay scoped to button: the weekly-report header <tr>s carry the same
            # inline `background:var(--accent)`, and an unscoped selector paints those rows
            # solid black on hover.
            'button[style*="background:var(--accent"]{background:transparent !important;'
            'color:var(--text) !important;border:1px solid var(--text) !important;'
            'transition:all .15s ease-in;}'
            'button[style*="background:var(--accent"]:hover{background:var(--text) !important;'
            'color:#fff !important;}'
            # And neutralise those header rows' inline fill at the source — they're the same
            # black-slab treatment the .email-table th rules already clear.
            'tr[style*="background:var(--accent"]{background:transparent !important;}'
            # ── Editorial numerals: the figure is the headline, set large and light the way
            # an editorial page would, with the weight in the datum not the chrome.
            '.kpi-value{font-size:34px;font-weight:400;letter-spacing:0;}'
            '.rt-value{font-size:20px;font-weight:400;}'
            '.chart-title,.table-title{font-size:16px;}'
            '.kpi-label{margin-bottom:12px;}')

    return '\n'.join(parts)


def _feedback_js(cfg):
    """Client-side "click anything and leave a note" layer (config: `feedback`).

    Why this exists: the team reviews the dashboard together and then has to hand-compile a
    list of fixes into an email, which is slow for them and arrives without context — we get
    "the retailer chart looks wrong" with no idea which week, filter or tab they were on.

    This lets them click the thing itself. Each note automatically captures the tab, the
    section heading it was pinned to, and the live filter state (period / retailer / as-of
    week), so a one-line note is actionable. Notes live in localStorage, so a review session
    survives refreshes and they can send the whole batch in one go.

    Deliberately backend-free: the dashboard is a static file behind a password, and asking
    this team to sign into a bug tracker would guarantee it never gets used. Sending copies a
    formatted digest to the clipboard AND opens a pre-filled email, so it works with zero
    setup; set `feedback.endpoint` to POST somewhere instead and it becomes a single click.

    Returns '' when the client has no `feedback` block, so other clients are untouched.
    """
    if not cfg or not cfg.get('enabled'):
        return ''
    conf = {
        'enabled': True,
        'slug': cfg.get('slug', ''),
        'to': cfg.get('to', ''),
        'cc': cfg.get('cc', ''),
        'subject': cfg.get('subject', 'Dashboard feedback'),
        'label': cfg.get('label', 'Add a note'),
        'endpoint': cfg.get('endpoint', ''),
        'intro': cfg.get('intro', ''),
    }
    return "window.FEEDBACK_CFG=%s;\n%s" % (json.dumps(conf), _FEEDBACK_BODY)


_FEEDBACK_BODY = r"""
(function(){
var CFG=window.FEEDBACK_CFG||{}; if(!CFG.enabled)return;
var LS='fb_notes_'+(CFG.slug||'x');
var LSU='fb_who_'+LS;
function load(){try{return JSON.parse(localStorage.getItem(LS)||'[]')}catch(e){return[]}}
function save(n){try{localStorage.setItem(LS,JSON.stringify(n))}catch(e){}}
function who(){try{return localStorage.getItem(LSU)||''}catch(e){return''}}
function setWho(v){try{localStorage.setItem(LSU,v)}catch(e){}}
var notes=load(), picking=false;

/* ---------- styles: inherit the client theme, no new colour decisions ---------- */
var css=document.createElement('style');
css.textContent=
"#fbBar{position:fixed;right:22px;bottom:22px;z-index:9000;display:flex;gap:9px;align-items:center;}"
+"#fbBtn,#fbCount{background:var(--bg-card,#fff);color:var(--text,#121212);"
+"border:1px solid var(--text,#121212);padding:10px 16px;font:inherit;font-size:13px;cursor:pointer;display:flex;"
+"align-items:center;gap:9px;transition:all .15s ease-in;}"
+"#fbBtn:hover,#fbCount:hover{background:var(--text,#121212);color:#fff;}"
+"#fbCount .n{min-width:19px;height:19px;line-height:19px;text-align:center;font-size:11px;"
+"background:var(--text,#121212);color:#fff;padding:0 5px;}"
+"#fbCount:hover .n{background:#fff;color:var(--text,#121212);}"
+"#fbBtn.on{background:var(--text,#121212);color:#fff;}"
+"#fbPanel{position:fixed;top:0;right:0;bottom:0;width:400px;max-width:92vw;background:var(--bg-card,#fff);"
+"border-left:1px solid var(--text,#121212);z-index:9100;display:none;flex-direction:column;}"
+"#fbPanel.open{display:flex;}"
+"#fbPanel header{padding:20px 22px;border-bottom:1px solid var(--border,#ddd);display:flex;justify-content:space-between;align-items:center;}"
+"#fbPanel h3{font-size:15px;font-weight:400;margin:0;}"
+"#fbList{flex:1;overflow:auto;padding:6px 22px 22px;}"
+"#fbPanel footer{padding:16px 22px;border-top:1px solid var(--border,#ddd);display:flex;gap:10px;}"
+".fb-x{background:none;border:none;font-size:20px;line-height:1;cursor:pointer;color:var(--text-muted,#777);padding:0 4px;}"
+".fb-item{border-top:1px solid var(--border,#ddd);padding:14px 0;font-size:12.5px;}"
+".fb-item .ctx{color:var(--text-muted,#777);font-size:11px;margin-bottom:5px;line-height:1.45;}"
+".fb-item .txt{white-space:pre-wrap;line-height:1.5;}"
+".fb-item .act{margin-top:7px;}"
+".fb-item .act button{background:none;border:none;color:var(--text-muted,#777);font-size:11px;cursor:pointer;"
+"padding:0;margin-right:14px;text-decoration:underline;}"
+".fb-btn{font:inherit;font-size:12.5px;padding:9px 15px;border:1px solid var(--text,#121212);"
+"background:transparent;color:var(--text,#121212);cursor:pointer;transition:all .15s ease-in;}"
+".fb-btn:hover{background:var(--text,#121212);color:#fff;}"
+".fb-btn.pri{background:var(--text,#121212);color:#fff;}"
+".fb-btn.pri:hover{opacity:.85;}"
+"body.fb-pick *{cursor:crosshair !important;}"
+"body.fb-pick #fbBtn,body.fb-pick #fbBtn *{cursor:pointer !important;}"
+".fb-hi{outline:2px solid var(--text,#121212) !important;outline-offset:2px;background:rgba(163,229,247,.22) !important;}"
+"#fbTip{position:fixed;left:50%;transform:translateX(-50%);top:0;z-index:9200;background:var(--text,#121212);"
+"color:#fff;font-size:12.5px;padding:9px 18px;display:none;}"
+"body.fb-pick #fbTip{display:block;}"
+"#fbCompose{position:absolute;z-index:9300;width:330px;max-width:92vw;background:var(--bg-card,#fff);"
+"border:1px solid var(--text,#121212);padding:14px;display:none;}"
+"#fbCompose textarea{width:100%;min-height:88px;font:inherit;font-size:12.5px;padding:9px;box-sizing:border-box;"
+"border:1px solid var(--border,#ccc);resize:vertical;}"
+"#fbCompose .ctx{font-size:11px;color:var(--text-muted,#777);margin-bottom:8px;line-height:1.45;}"
+"#fbCompose .row{display:flex;gap:8px;justify-content:flex-end;margin-top:9px;}"
+".fb-pin{position:absolute;z-index:8000;width:19px;height:19px;background:var(--text,#121212);color:#fff;"
+"font-size:10px;line-height:19px;text-align:center;cursor:pointer;}"
+"@media print{#fbBtn,#fbPanel,#fbCompose,.fb-pin,#fbTip{display:none !important;}}";
document.head.appendChild(css);

/* ---------- chrome ---------- */
var bar=document.createElement('div'); bar.id='fbBar';
bar.innerHTML='<button id="fbBtn"><span>'+(CFG.label||'Add a note')+'</span></button>'
 +'<button id="fbCount" title="Review and send your notes"><span class="n" id="fbN">0</span> <span id="fbNW">notes</span></button>';
document.body.appendChild(bar);
var btn=document.getElementById('fbBtn'), cnt=document.getElementById('fbCount');
var tip=document.createElement('div'); tip.id='fbTip';
tip.textContent='Click anything on the page to attach a note — press Esc to cancel';
document.body.appendChild(tip);
var panel=document.createElement('div'); panel.id='fbPanel';
panel.innerHTML='<header><h3>Notes for Farsight</h3><button class="fb-x" id="fbClose">&times;</button></header>'
 +'<div id="fbList"></div>'
 +'<footer><button class="fb-btn pri" id="fbSend" style="flex:1;">Send to Farsight</button>'
 +'<button class="fb-btn" id="fbClear">Clear all</button></footer>';
document.body.appendChild(panel);
var comp=document.createElement('div'); comp.id='fbCompose';
comp.innerHTML='<div class="ctx" id="fbCtx"></div><textarea id="fbTxt" placeholder="What is wrong, or what would you like instead?"></textarea>'
 +'<input id="fbWho" placeholder="Your name" style="width:100%;font:inherit;font-size:12px;padding:7px 9px;margin-top:7px;box-sizing:border-box;border:1px solid var(--border,#ccc);">'
 +'<div class="row"><button class="fb-btn" id="fbCancel">Cancel</button><button class="fb-btn pri" id="fbSave">Save note</button></div>';
document.body.appendChild(comp);

/* ---------- context capture: the whole point ---------- */
function tabName(){var a=document.querySelector('.nav-tab.active');return a?a.textContent.trim():'';}
function filters(){
  var out=[];
  var p=document.querySelector('.period-btn.active'); if(p)out.push('Period '+p.textContent.trim());
  var r=document.getElementById('msRetLabel'); if(r&&r.textContent.trim())out.push(r.textContent.trim());
  var w=document.getElementById('fAsOfWeek'); if(w&&w.selectedIndex>=0&&w.options[w.selectedIndex])out.push(w.options[w.selectedIndex].text.trim());
  return out.join(' · ');
}
function titleText(t){
  var c=t.cloneNode(true);
  // Titles carry more than the title: a hint span, a tooltip, and in the weekly report the
  // Recap/CSV buttons and a 'Week 34 · Aug · FY2026' stamp all live inside the same heading.
  Array.prototype.forEach.call(c.querySelectorAll(
      '.chart-hint,.chart-tip,.has-tooltip .tip,.data-flag,button,[onclick],.csv-dl-btn'),
    function(x){x.parentNode.removeChild(x);});
  return (c.textContent||'').trim().replace(/\s+/g,' ').slice(0,80);
}
function regionOf(el){
  if(!el.closest)return '';
  if(el.closest('.modal'))return 'Pop-up';
  if(el.closest('.nav-tabs'))return 'Tab bar';
  if(el.closest('.global-filters,.period-toggle,.ms-wrap,.filter-bar,.door-filters'))return 'Filters';
  if(el.closest('.header,.topbar'))return 'Header';
  return '';
}
function describe(el){
  // Name the exact thing clicked, in the order a person would recognise it.
  var a=el.getAttribute?(el.getAttribute('aria-label')||el.getAttribute('title')||el.getAttribute('alt')||''):'';
  var tag=(el.tagName||'').toLowerCase();
  if(tag==='img')  return 'image'+(a?' "'+a+'"':'');
  if(tag==='canvas')return 'the chart';
  var own=(el.textContent||'').trim().replace(/\s+/g,' ');
  if(tag==='select'){var o=el.options&&el.options[el.selectedIndex];return 'dropdown'+(o?' ("'+o.text.trim()+'")':'');}
  if(tag==='input') return 'input'+(el.value?' ("'+String(el.value).slice(0,24)+'")':'');
  if(a) return '"'+a.slice(0,50)+'"';
  if(own) return '"'+own.slice(0,50)+'"';
  return tag||'element';
}
function nearestHeading(el){
  // Walk backwards through the document for the last heading-ish thing above the click,
  // so notes on plain text or a status row still land under something nameable.
  var all=Array.prototype.slice.call(document.querySelectorAll(
    '.page.active .chart-title,.page.active .table-title,.page.active h1,.page.active h2,.page.active h3,.page.active .kpi-label'));
  if(!all.length)return '';
  var y=0; try{y=el.getBoundingClientRect().top;}catch(e){}
  var best='';
  all.forEach(function(h){ try{ if(h.getBoundingClientRect().top<=y+2) best=titleText(h); }catch(e){} });
  return best;
}
function sectionOf(el){
  var n=el, hop=0;
  while(n&&n!==document.body&&hop<7){
    if(n.classList&&(n.classList.contains('chart-container')||n.classList.contains('table-container')||n.classList.contains('kpi-card'))){
      var t=n.querySelector('.chart-title,.table-title,.kpi-label');
      if(t)return titleText(t)+' — '+describe(el);
    }
    n=n.parentElement; hop++;
  }
  // Not inside a chart/table/KPI. Say where on the page it was and what it was, rather than
  // falling through to a useless placeholder — a note reading "(page)" cannot be acted on and
  // would mean going back to ask, which is the whole thing this is meant to avoid.
  var reg=regionOf(el);
  if(reg)return reg+' — '+describe(el);
  var head=nearestHeading(el);
  return (head?head+' — ':'')+describe(el);
}
function anchorFor(el){
  var n=el.closest?el.closest('.chart-container,.table-container,.kpi-card'):null;
  return n||el;
}

/* ---------- picking ---------- */
var lastHi=null, pending=null;
function clearHi(){if(lastHi){lastHi.classList.remove('fb-hi');lastHi=null;}}
document.addEventListener('mouseover',function(e){
  if(!picking)return; if(bar.contains(e.target)||comp.contains(e.target))return;
  clearHi(); var a=anchorFor(e.target); if(a&&a!==document.body){a.classList.add('fb-hi');lastHi=a;}
});
document.addEventListener('click',function(e){
  if(!picking)return;
  if(bar.contains(e.target)||comp.contains(e.target))return;
  e.preventDefault(); e.stopPropagation();
  var a=anchorFor(e.target);
  pending={tab:tabName(),section:sectionOf(e.target),filters:filters(),
           where:regionOf(e.target)||'Page body',
           at:Math.round(window.scrollY)+'px down · '+window.innerWidth+'x'+window.innerHeight};
  var r=(a&&a.getBoundingClientRect)?a.getBoundingClientRect():{left:80,bottom:120};
  comp.style.left=Math.max(12,Math.min(window.innerWidth-345,r.left+window.scrollX))+'px';
  comp.style.top=(r.bottom+window.scrollY+9)+'px';
  // Show them what we captured. If it reads thin they can say so in the note itself,
  // rather than us discovering later that we cannot place it.
  document.getElementById('fbCtx').innerHTML='<b>'+esc(pending.tab)+'</b>'
    +(pending.section?' &mdash; '+esc(pending.section):'')
    +(pending.filters?'<br>'+esc(pending.filters):'');
  document.getElementById('fbTxt').value='';
  var wf=document.getElementById('fbWho'); wf.value=who(); wf.style.display=who()?'none':'block';
  comp.style.display='block'; document.getElementById('fbTxt').focus();
  setPick(false);
},true);
document.addEventListener('keydown',function(e){if(e.key==='Escape'){setPick(false);comp.style.display='none';}});
function setPick(on){picking=on;document.body.classList.toggle('fb-pick',on);btn.classList.toggle('on',on);if(!on)clearHi();}

/* ---------- save / render ---------- */
document.getElementById('fbSave').onclick=function(){
  var t=document.getElementById('fbTxt').value.trim(); if(!t){comp.style.display='none';return;}
  // Asked inline rather than via prompt(): a modal on first save is jarring, and prompt is
  // blocked outright in some embedded browsers, which silently lost the note.
  var w=(document.getElementById('fbWho').value||'').trim(); if(w)setWho(w); else w=who();
  notes.push({t:t,who:w,tab:pending.tab,section:pending.section,filters:pending.filters,
              where:pending.where,at:pending.at,when:new Date().toLocaleString()});
  save(notes); comp.style.display='none'; render();
};
document.getElementById('fbCancel').onclick=function(){comp.style.display='none';};
btn.onclick=function(e){ e.stopPropagation(); setPick(!picking); panel.classList.remove('open'); comp.style.display='none'; };
cnt.onclick=function(e){ e.stopPropagation(); setPick(false); panel.classList.add('open'); render(); };
document.getElementById('fbClose').onclick=function(){panel.classList.remove('open');};
document.getElementById('fbClear').onclick=function(){
  if(!notes.length)return;
  if(confirm('Delete all '+notes.length+' notes? This cannot be undone.')){notes=[];save(notes);render();}
};
function render(){
  document.getElementById('fbN').textContent=notes.length;
  document.getElementById('fbNW').textContent=(notes.length===1?'note':'notes');
  cnt.style.display=notes.length?'flex':'none';
  var L=document.getElementById('fbList');
  if(!notes.length){
    L.innerHTML='<p style="font-size:12.5px;color:var(--text-muted,#777);line-height:1.6;padding-top:16px;">'
      +'No notes yet.<br><br>Click <b>'+(CFG.label||'Add a note')+'</b>, then click whatever looks wrong — a chart, a number, a table. '
      +'We\'ll capture which tab and which filters you were on, so you only have to type the point itself.</p>';
    return;
  }
  var h='';
  notes.forEach(function(n,i){
    h+='<div class="fb-item"><div class="ctx">'+esc(n.tab)+(n.section?' — '+esc(n.section):'')
      +(n.filters?'<br>'+esc(n.filters):'')+'<br>'+esc(n.who||'')+(n.who?' · ':'')+esc(n.when)+'</div>'
      +'<div class="txt">'+esc(n.t)+'</div>'
      +'<div class="act"><button data-i="'+i+'" class="fbDel">Delete</button></div></div>';
  });
  L.innerHTML=h;
  Array.prototype.forEach.call(L.querySelectorAll('.fbDel'),function(b){
    b.onclick=function(){notes.splice(+b.getAttribute('data-i'),1);save(notes);render();};
  });
}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

/* ---------- digest + send ---------- */
function digest(){
  var by={};
  notes.forEach(function(n){(by[n.tab]=by[n.tab]||[]).push(n);});
  var out=[(CFG.intro||'Dashboard feedback')+' — '+notes.length+' note'+(notes.length===1?'':'s')+'\n'];
  Object.keys(by).forEach(function(tab){
    out.push('\n'+(tab||'(page)').toUpperCase());
    by[tab].forEach(function(n,i){
      out.push('  '+(i+1)+'. '+n.t);
      var meta=[]; if(n.section)meta.push(n.section); if(n.filters)meta.push(n.filters);
      if(meta.length)out.push('     ['+meta.join('  ·  ')+']');
      if(n.at)out.push('     (' + (n.where?n.where+' · ':'') + n.at + ')');
      if(n.who)out.push('     — '+n.who+', '+n.when);
    });
  });
  return out.join('\n');
}
document.getElementById('fbSend').onclick=function(){
  if(!notes.length){alert('No notes to send yet.');return;}
  var body=digest();
  if(CFG.endpoint){
    fetch(CFG.endpoint,{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain'},body:body})
      .then(function(){alert('Sent — thank you. '+notes.length+' note'+(notes.length===1?'':'s')+' delivered.');})
      .catch(function(){fallback(body);});
    return;
  }
  fallback(body);
};
function fallback(body){
  var done=function(){
    var sub=encodeURIComponent(CFG.subject||'Dashboard feedback');
    var href='mailto:'+encodeURIComponent(CFG.to||'')+'?subject='+sub
             +(CFG.cc?'&cc='+encodeURIComponent(CFG.cc):'')
             +'&body='+encodeURIComponent(body.length>1500?body.slice(0,1500)+'\n\n[…full list copied to your clipboard — paste here]':body);
    window.location.href=href;
  };
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(body).then(function(){
      alert('Copied all '+notes.length+' notes to your clipboard, and opening an email now.\n\nIf the email does not open, just paste into a new one.');
      done();
    },done);
  } else { done(); }
}
render();
})();
"""


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
    wip_gate = ('<div class="wip-badge" style="display:inline-block;background:#FBE5A0;color:#7A5C00;border:1px solid #E6C200;'
                'border-radius:8px;font-size:11px;font-weight:700;letter-spacing:1px;padding:4px 14px;'
                'margin-bottom:20px;text-transform:uppercase;">&#9888;&#65039; Work in Progress &middot; Draft</div>'
                ) if wip else ''
    wip_header = ('<span class="wip-badge" style="display:inline-block;background:#FBE5A0;color:#7A5C00;border:1px solid #E6C200;'
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

    html = html.replace('{{THEME_CSS}}', _theme_css(config['branding'].get('theme')))

    chart_palette = config['branding'].get('chart_palette')
    html = _apply_chart_remap(html, chart_palette)
    fb_cfg = dict(config.get('feedback') or {})
    if fb_cfg:
        fb_cfg.setdefault('slug', config.get('client_slug', ''))
    html = html.replace('{{FEEDBACK_JS}}', _feedback_js(fb_cfg))
    html = html.replace('{{CHART_PALETTE_JS}}', _chart_palette_js(chart_palette))

    # Write output
    output_path = os.path.join(output_dir, 'index.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"  Output: {output_path}")
    print(f"  Size: {os.path.getsize(output_path):,} bytes")
    return output_path
