#!/usr/bin/env python3
"""skypeui.py  –  Skype 2.x / XP-era UI in Python/tkinter
Windows XP Luna theme applied from B00merang-Project/Windows-XP assets.

Callbacks wired by the orchestrator:
    ui.on_call_start   = lambda name: ...
    ui.on_call_answer  = lambda name: ...
    ui.on_call_end     = lambda name, secs: ...
    ui.on_contact_sel  = lambda name: ...
    ui.on_state_change = lambda state: ...
    ui.on_status_change = lambda status_str: ...   # user changed own status
    ui.on_config_save  = lambda cfg_dict: ...      # user saved settings

Public API (called by backend via UIBridge):
    ui.start_call(name)
    ui.incoming_call(name)
    ui.answer_call()
    ui.end_call()
    ui.update_contacts(list_of_dicts)
    ui.set_status(status_str, platform_label='')
"""

import datetime
import json
import math
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, simpledialog
from typing import Any, Callable, Dict, List, Optional

if sys.platform.startswith('win'):
    import ctypes

try:
    from PIL import Image, ImageTk
except Exception:  # pragma: no cover
    Image = None
    ImageTk = None

# ── config path ───────────────────────────────────────────────────────────────
_CFG_PATH = Path(__file__).parent / "config.json"
_XP_KIT_ICON = Path(__file__).parent / "assets" / "xp_ui_kit" / "thumbnail.png"
_XP_KIT_DIR = Path(__file__).parent / "assets" / "xp_ui_kit"
_XP_THEME_UNITY_DIR = Path(__file__).parent / "assets" / "xp_theme_unity"

# Default icon picks extracted from Windows-XP-UI-Kit fig assets.
# You can override by creating assets/xp_ui_kit/icons.json with:
# {"phone": "<file>.png", "person": "<file>.png"}
_XP_ICON_FILES = {
    "phone": "28537e2d313b3eda168e8bf2ea93305e3bbfeab7.png",
    "person": "19fb335d5835351ec91a89ec2bd6b563a481ec67.png",
    # Caption glyphs from Windows-XP-UI-Kit extracted assets
    "caption_min": "96d81ebfecd94f2b6f0e1431b05552fe88c6a06f.png",
    "caption_max": "58547231f258e6dc90a91d71a633560479e774a9.png",
    "caption_close": "ae06e6ddbba80571e18688a63df12d37a5fbff13.png",
}
_XP_ICON_CACHE: Dict[tuple, Any] = {}
_CALL_BTN_CACHE: Dict[tuple, Dict[str, Any]] = {}
_WINDOWS_DPI_SCALE: float = 1.0

# ══════════════════════════════════════════════════════════════════════════════
# XP Luna Palette  (sourced from B00merang Windows-XP Luna gtk-2.0/color
#                   and metacity-1 titlebar pixel samples)
# ══════════════════════════════════════════════════════════════════════════════

# Window / widget background — exact from gtk-color-scheme bg_color
BG        = '#ECE9D8'
PANEL     = '#ECE9D8'

# Active tab / pane face — slightly lighter
TAB_ACT   = '#FFFFFF'
TAB_INACT = '#D4D0C8'
TAB_ROW   = '#ECE9D8'

# Selection — exact from selected_bg_color
SEL_BG    = '#1466C9'
SEL_FG    = '#FFFFFF'

# Dividers, borders
DIV       = '#8E8D84'

# Status-bar background  (XP taskbar is #1F3A74 deep-blue strip)
SB_BG     = '#245EDC'
SB_FG     = '#FFFFFF'

# Titlebar gradient stops (from metacity-1 titlebar-focused-middle.png)
TB_TOP    = '#005CE9'   # y=0
TB_MID    = '#0058E6'   # y=10
TB_BOT    = '#0143CF'   # y=28
TB_LIGHT  = '#3B97FF'   # bright highlight row y=1
TB_SHINE  = '#6EC0FF'   # very bright cap

# Titlebar unfocused
TB_UNF_MID = '#7A99E0'

# Titlebar text
TB_TEXT   = '#FFFFFF'
TB_SHADOW = '#00338B'

# XP button face (raised 3-D):  from gtk-2.0 and system colors
BTN_FACE   = '#ECE9D8'
BTN_HILITE = '#FFFFFF'
BTN_DARK   = '#ACA899'
BTN_DDARK  = '#716F64'
BTN_SHADOW = '#C0BDB4'

# XP "Luna blue" accent used in tab active top-border
LUNA_BLUE  = '#003399'

# Text
GREY_TXT  = '#716F64'
LINK      = '#0000CC'

# Presence dots
GREEN_DOT = '#1DB954'
RED_DOT   = '#CC2222'
AWAY_DOT  = '#F0A500'

# Skype brand colour for S logo
SKYPE_BLUE = '#00AFF0'

_STATUS_OPTIONS = ['Online', 'Away', 'Do Not Disturb', 'Invisible', 'Offline']
_STATUS_COLORS  = {
    'Online':         '#1DB954',
    'Away':           '#F0A500',
    'Do Not Disturb': '#CC0000',
    'Invisible':      '#AAAAAA',
    'Offline':        '#888888',
}

# Font — Tahoma is the XP system font
_F  = ('Tahoma', 9)
_FB = ('Tahoma', 9, 'bold')
_FS = ('Tahoma', 8)
_FL = ('Tahoma', 13, 'bold')


def prepare_windows_dpi_awareness() -> float:
    """Enable Windows DPI awareness early and return UI scale factor."""
    global _WINDOWS_DPI_SCALE
    if not sys.platform.startswith('win'):
        _WINDOWS_DPI_SCALE = 1.0
        return _WINDOWS_DPI_SCALE

    try:
        import ctypes as _ctypes

        user32 = _ctypes.windll.user32
        shcore = getattr(_ctypes.windll, 'shcore', None)

        # Best quality first: Per-monitor v2 awareness.
        try:
            if hasattr(user32, 'SetProcessDpiAwarenessContext'):
                DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = _ctypes.c_void_p(-4)
                user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        except Exception:
            pass

        # Fallback for older versions.
        try:
            if shcore is not None and hasattr(shcore, 'SetProcessDpiAwareness'):
                PROCESS_PER_MONITOR_DPI_AWARE = 2
                shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
            elif hasattr(user32, 'SetProcessDPIAware'):
                user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass

    _WINDOWS_DPI_SCALE = _query_windows_dpi_scale()
    return _WINDOWS_DPI_SCALE


def _query_windows_dpi_scale() -> float:
    if not sys.platform.startswith('win'):
        return 1.0
    try:
        import ctypes as _ctypes

        user32 = _ctypes.windll.user32
        if hasattr(user32, 'GetDpiForSystem'):
            dpi = int(user32.GetDpiForSystem())
            if dpi > 0:
                return max(1.0, min(4.0, dpi / 96.0))
    except Exception:
        pass
    try:
        import ctypes as _ctypes

        gdi32 = _ctypes.windll.gdi32
        hdc = _ctypes.windll.user32.GetDC(0)
        LOGPIXELSX = 88
        dpi = int(gdi32.GetDeviceCaps(hdc, LOGPIXELSX))
        _ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return max(1.0, min(4.0, dpi / 96.0))
    except Exception:
        pass
    return 1.0


def apply_tk_rendering_profile(root: tk.Misc) -> float:
    """Configure tk scaling and font defaults for crisp Windows rendering."""
    scale = _WINDOWS_DPI_SCALE if sys.platform.startswith('win') else 1.0
    if scale <= 0:
        scale = 1.0
    try:
        # Tk scaling unit = pixels per point; 96 DPI => 96 / 72.
        tk_scale = (96.0 * scale) / 72.0
        root.tk.call('tk', 'scaling', tk_scale)
    except Exception:
        pass

    try:
        root.option_add('*Font', 'Tahoma 9')
    except Exception:
        pass

    named_fonts = {
        'TkDefaultFont': ('Tahoma', 9, 'normal'),
        'TkTextFont': ('Tahoma', 9, 'normal'),
        'TkMenuFont': ('Tahoma', 9, 'normal'),
        'TkHeadingFont': ('Tahoma', 9, 'bold'),
        'TkCaptionFont': ('Tahoma', 9, 'bold'),
        'TkSmallCaptionFont': ('Tahoma', 8, 'normal'),
        'TkTooltipFont': ('Tahoma', 8, 'normal'),
        'TkIconFont': ('Tahoma', 8, 'normal'),
        'TkFixedFont': ('Consolas', 9, 'normal'),
    }
    for name, (family, size, weight) in named_fonts.items():
        try:
            f = tkfont.nametofont(name)
            if weight == 'bold':
                f.configure(family=family, size=size, weight='bold')
            else:
                f.configure(family=family, size=size, weight='normal')
        except Exception:
            continue
    return scale


# ══════════════════════════════════════════════════════════════════════════════
# XP-style widget helpers
# ══════════════════════════════════════════════════════════════════════════════

def _xp_button(parent, text, command, width=None, fg='black',
               bg=BTN_FACE, font: tuple = (), padx=10, pady=3) -> tk.Button:
    """
    Raised 3-D XP-style button.  Uses tk.Button with XP-accurate colors.
    The 3-D border is simulated via relief='raised' + matching highlight colors.
    """
    kw = dict(
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        font=font if font else _F,
        relief='raised',
        bd=2,
        padx=padx,
        pady=pady,
        activebackground=BTN_SHADOW,
        activeforeground=fg,
        highlightbackground=BTN_DDARK,
        highlightcolor=BTN_HILITE,
        highlightthickness=1,
        cursor='hand2',
        takefocus=False,
    )
    if width is not None:
        kw['width'] = width
    return tk.Button(parent, **kw)  # type: ignore[arg-type]


def _xp_label(parent, text, bg=PANEL, fg='black', font=None, **kw) -> tk.Label:
    return tk.Label(parent, text=text, bg=bg, fg=fg,  # type: ignore[arg-type]
                    font=font or _F, **kw)


def _gradient_canvas(parent, w, h, stops, horizontal=False) -> tk.Canvas:
    """
    Draw a vertical (or horizontal) linear gradient on a Canvas.
    stops = [(ratio, '#RRGGBB'), ...]  ratio in [0..1] sorted ascending.
    """
    c = tk.Canvas(parent, width=w, height=h, highlightthickness=0, bd=0)
    steps = h if not horizontal else w

    def _hex(color):
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return r, g, b

    # build per-pixel color
    def _interp(t):
        for i in range(len(stops) - 1):
            r0, c0 = stops[i]
            r1, c1 = stops[i + 1]
            if r0 <= t <= r1:
                f = (t - r0) / max(r1 - r0, 1e-9)
                rgb0 = _hex(c0)
                rgb1 = _hex(c1)
                r = int(rgb0[0] + (rgb1[0] - rgb0[0]) * f)
                g = int(rgb0[1] + (rgb1[1] - rgb0[1]) * f)
                b = int(rgb0[2] + (rgb1[2] - rgb0[2]) * f)
                return f'#{r:02x}{g:02x}{b:02x}'
        return stops[-1][1]

    for i in range(steps):
        t = i / max(steps - 1, 1)
        col = _interp(t)
        if not horizontal:
            c.create_line(0, i, w, i, fill=col)
        else:
            c.create_line(i, 0, i, h, fill=col)
    return c


def _draw_gradient_into_canvas(
    canvas: tk.Canvas,
    w: int,
    h: int,
    stops,
    tag: str = 'grad',
    clear_all: bool = False,
) -> None:
    """Redraw a vertical gradient into an existing canvas."""
    def _hex(color: str):
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        return r, g, b

    def _interp(t: float) -> str:
        for i in range(len(stops) - 1):
            r0, c0 = stops[i]
            r1, c1 = stops[i + 1]
            if r0 <= t <= r1:
                f = (t - r0) / max(r1 - r0, 1e-9)
                rgb0 = _hex(c0)
                rgb1 = _hex(c1)
                r = int(rgb0[0] + (rgb1[0] - rgb0[0]) * f)
                g = int(rgb0[1] + (rgb1[1] - rgb0[1]) * f)
                b = int(rgb0[2] + (rgb1[2] - rgb0[2]) * f)
                return f'#{r:02x}{g:02x}{b:02x}'
        return stops[-1][1]

    if clear_all:
        canvas.delete('all')
    else:
        canvas.delete(tag)
    for y in range(max(1, h)):
        t = y / max(h - 1, 1)
        col = _interp(t)
        canvas.create_line(0, y, w, y, fill=col, tags=tag)


def _xp_titlebar(parent, title: str, width: int, height: int = 29) -> tk.Canvas:
    """
    XP Luna blue titlebar gradient canvas with white title text + drop shadow.
    Gradient matches metacity-1 titlebar-focused-middle.png pixel samples.
    """
    stops = [
        (0.00, '#3B97FF'),   # bright highlight at very top
        (0.05, '#005CE9'),   # solid blue
        (0.35, '#0058E6'),   # mid blue
        (0.85, '#0067FF'),   # slight lighter before bottom
        (0.93, '#0143CF'),   # darker at bottom edge
        (1.00, '#0133B0'),   # bottom trim
    ]
    c = _gradient_canvas(parent, width, height, stops)
    # Drop shadow for text
    c.create_text(10, height // 2 + 1, text=title, anchor='w',
                  fill=TB_SHADOW, font=('Tahoma', 9, 'bold'),
                  tags='title_shadow')
    # Title text
    c.create_text(10, height // 2, text=title, anchor='w',
                  fill=TB_TEXT, font=('Tahoma', 9, 'bold'),
                  tags='title')
    return c


# ══════════════════════════════════════════════════════════════════════════════
# Handset / icon canvas helpers
# ══════════════════════════════════════════════════════════════════════════════

def _handset_pts(cx, cy, r, flip=False):
    start_deg, end_deg = 150.0, 30.0
    n, pts = 24, []
    for i in range(n + 1):
        t = i / n
        a = math.radians(start_deg + (end_deg - start_deg) * t)
        x = cx + r * math.cos(a)
        y = cy - r * math.sin(a)
        if flip:
            x = 2 * cx - x
        pts.append((x, y))
    return pts


def _draw_handset_on(canvas, cx, cy, r, thick, color, flip=False):
    pts = _handset_pts(cx, cy, r, flip=flip)
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]; x1, y1 = pts[i + 1]
        canvas.create_line(x0, y0, x1, y1, width=thick,
                           fill=color, capstyle='round', joinstyle='round')
    pad_r = thick * 0.85
    for x, y in (pts[0], pts[-1]):
        canvas.create_oval(x - pad_r, y - pad_r, x + pad_r, y + pad_r,
                           fill=color, outline='')


def _make_handset_canvas(parent, size, color, flip=False, bg=BG):
    c = tk.Canvas(parent, width=size, height=size,
                  bg=bg, highlightthickness=0)
    _draw_handset_on(c, size / 2, size / 2, size * 0.30,
                     max(3, round(size * 0.14)), color, flip=flip)
    return c


def _make_xp_call_btn(parent, color, flip=False, size=34, bg=BG) -> tk.Canvas:
    """
    XP-style call button: gradient raised pill with handset icon.
    """
    c = tk.Canvas(parent, width=size, height=size,
                  bg=bg, highlightthickness=0)

    # Outer shadow/border
    c.create_oval(1, 1, size - 2, size - 2,
                  fill=_darken(color, 0.55), outline='')

    # Main face gradient (bright top → color → dark bottom)
    bright = _lighten(color, 1.5)
    mid    = color
    dark   = _darken(color, 0.65)
    for y in range(3, size - 3):
        t  = (y - 3) / max(size - 7, 1)
        if t < 0.15:
            col = _lerp_color(bright, mid, t / 0.15)
        else:
            col = _lerp_color(mid, dark, (t - 0.15) / 0.85)
        r = size // 2 - 2
        cx = size // 2
        half_w = int(math.sqrt(max(r * r - (y - cx) ** 2, 0)))
        c.create_line(cx - half_w, y, cx + half_w, y, fill=col)

    # Highlight arc at top
    c.create_arc(4, 3, size - 4, size // 2,
                 start=20, extent=140,
                 outline=_lighten(color, 1.8), width=1, style='arc')

    # Handset icon
    _draw_handset_on(c, size / 2, size / 2, size * 0.26,
                     max(2, round(size * 0.11)), '#FFFFFF', flip=flip)
    return c


def _lighten(hex_color: str, factor: float) -> str:
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    r = min(255, int(r * factor))
    g = min(255, int(g * factor))
    b = min(255, int(b * factor))
    return f'#{r:02x}{g:02x}{b:02x}'


def _darken(hex_color: str, factor: float) -> str:
    return _lighten(hex_color, factor)


def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f'#{r:02x}{g:02x}{b:02x}'


def _make_silhouette_canvas(parent, size=120, bg='#D4D0C8'):
    c = tk.Canvas(parent, width=size, height=size,
                  bg=bg, highlightthickness=1, highlightbackground=BTN_DDARK)
    cx = size / 2
    hr = size * 0.155; hy = size * 0.30
    c.create_oval(cx - hr, hy - hr, cx + hr, hy + hr, fill='#333333', outline='')
    sw = size * 0.46; st = hy + hr * 0.6; sh = size * 0.76
    c.create_arc(cx - sw, st, cx + sw, st + sh * 2, start=0, extent=180,
                 fill='#333333', outline='', style='chord')
    br = max(8, round(size * 0.115))
    bx = cx + hr * 0.75; by = hy + hr * 0.75
    c.create_oval(bx - br, by - br, bx + br, by + br,
                  fill=GREEN_DOT, outline='white', width=2)
    aw = max(3, round(br * 0.52))
    c.create_polygon(bx - aw, by - aw, bx + aw + 1, by, bx - aw, by + aw,
                     fill='white', outline='')
    return c


def _load_xp_icon_name(kind: str) -> str:
    name = _XP_ICON_FILES.get(kind, "")
    cfg_path = _XP_KIT_DIR / "icons.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                raw = str(data.get(kind, "") or "").strip()
                if raw:
                    name = raw
        except Exception:
            pass
    return name


def _ui_scale_for(widget: tk.Misc) -> float:
    try:
        px_per_in = float(widget.winfo_fpixels('1i'))
        if px_per_in > 0:
            return max(1.0, min(4.0, px_per_in / 96.0))
    except Exception:
        pass
    return max(1.0, min(4.0, _WINDOWS_DPI_SCALE))


def _load_scaled_photo(parent: tk.Misc, path: Path, target_size: int) -> Optional[Any]:
    """Load image with high-quality scaling for icon rendering."""
    target_size = max(1, int(target_size))
    if Image is not None and ImageTk is not None:
        try:
            resampling = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS')
            source = Image.open(path).convert('RGBA')
            src_w, src_h = source.size
            if src_w <= 0 or src_h <= 0:
                return None
            fit = min(target_size / src_w, target_size / src_h)
            fit = max(fit, 0.01)
            new_w = max(1, int(round(src_w * fit)))
            new_h = max(1, int(round(src_h * fit)))
            source = source.resize((new_w, new_h), resampling)
            if new_w != target_size or new_h != target_size:
                canvas = Image.new('RGBA', (target_size, target_size), (0, 0, 0, 0))
                ox = (target_size - new_w) // 2
                oy = (target_size - new_h) // 2
                canvas.paste(source, (ox, oy), source)
                source = canvas
            return ImageTk.PhotoImage(source, master=parent)
        except Exception:
            pass
    try:
        img = tk.PhotoImage(master=parent, file=str(path))
        w = max(1, int(img.width()))
        h = max(1, int(img.height()))
        scale = max(1, int(max(w, h) / max(1, target_size)))
        if scale > 1:
            img = img.subsample(scale, scale)
        return img
    except Exception:
        return None


def _xp_icon_image(parent, kind: str, target_size: int) -> Optional[Any]:
    file_name = _load_xp_icon_name(kind)
    if not file_name:
        return None
    path = _XP_KIT_DIR / file_name
    if not path.exists():
        return None

    scale_bucket = int(round(_ui_scale_for(parent) * 100))
    key = (kind, target_size, scale_bucket, str(path))
    cached = _XP_ICON_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        render_size = max(1, int(round(target_size)))
        img = _load_scaled_photo(parent, path, render_size)
        if img is None:
            return None
        _XP_ICON_CACHE[key] = img
        return img
    except Exception:
        return None


def _xp_asset_image(parent, file_name: str, target_size: int) -> Optional[Any]:
    path = _XP_KIT_DIR / str(file_name or "")
    if not path.exists():
        return None
    scale_bucket = int(round(_ui_scale_for(parent) * 100))
    key = ("asset", target_size, scale_bucket, str(path))
    cached = _XP_ICON_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        render_size = max(1, int(round(target_size)))
        img = _load_scaled_photo(parent, path, render_size)
        if img is None:
            return None
        _XP_ICON_CACHE[key] = img
        return img
    except Exception:
        return None


def _xp_caption_image(parent, kind: str, pressed: bool, target_size: Optional[int] = None) -> Optional[Any]:
    """Load real XP caption button bitmap from the theme resource pack."""
    name_map = {
        ('min', False): 'minimize_focused_normal.png',
        ('min', True): 'minimize_focused_pressed.png',
        ('max', False): 'maximize_focused_normal.png',
        ('max', True): 'maximize_focused_pressed.png',
        ('restore', False): 'unmaximize_focused_normal.png',
        ('restore', True): 'unmaximize_focused_pressed.png',
        ('close', False): 'close_focused_normal.png',
        ('close', True): 'close_focused_pressed.png',
    }
    file_name = name_map.get((kind, bool(pressed)))
    if not file_name:
        return None
    path = _XP_THEME_UNITY_DIR / file_name
    if not path.exists():
        return None
    render_size = max(1, int(target_size or 0)) if target_size else 0
    key = ('caption', kind, bool(pressed), render_size, str(path))
    cached = _XP_ICON_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        if render_size > 0:
            img = _load_scaled_photo(parent, path, render_size)
        else:
            img = tk.PhotoImage(master=parent, file=str(path))
        if img is None:
            return None
        _XP_ICON_CACHE[key] = img
        return img
    except Exception:
        return None


def _windows_work_area() -> Optional[tuple]:
    """Return (left, top, width, height) excluding taskbar on Windows."""
    if not sys.platform.startswith('win'):
        return None
    try:
        import ctypes as _ctypes

        class RECT(_ctypes.Structure):
            _fields_ = [
                ('left', _ctypes.c_long),
                ('top', _ctypes.c_long),
                ('right', _ctypes.c_long),
                ('bottom', _ctypes.c_long),
            ]

        SPI_GETWORKAREA = 0x0030
        rect = RECT()
        ok = _ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, _ctypes.byref(rect), 0
        )
        if not ok:
            return None
        w = int(rect.right - rect.left)
        h = int(rect.bottom - rect.top)
        if w <= 0 or h <= 0:
            return None
        return int(rect.left), int(rect.top), w, h
    except Exception:
        return None


def _load_call_button_images(parent, size: int) -> Dict[str, Any]:
    """Load call button sprite (green/red, up/down)."""
    key = (str(parent), int(size))
    cached = _CALL_BTN_CACHE.get(key)
    if cached is not None:
        return cached

    out: Dict[str, Any] = {}
    if Image is None or ImageTk is None:
        _CALL_BTN_CACHE[key] = out
        return out

    candidates = [
        _XP_KIT_DIR / 'call_buttons_sprite.png',
        _XP_KIT_DIR / 'call_buttons.png',
        Path(__file__).parent / 'assets' / 'call_buttons_sprite.png',
    ]
    sprite_path = next((p for p in candidates if p.exists()), None)
    if sprite_path is None:
        _CALL_BTN_CACHE[key] = out
        return out

    try:
        sprite = Image.open(sprite_path).convert('RGBA')
        w, h = sprite.size
        seg = w // 4
        if seg <= 0:
            _CALL_BTN_CACHE[key] = out
            return out

        # Expected order from user-provided strip:
        # [green_up, red_up, green_down, red_down]
        slots = {
            'green_up': 0,
            'red_up': 1,
            'green_down': 2,
            'red_down': 3,
        }
        resampling = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS')
        for name, idx in slots.items():
            left = idx * seg
            right = (idx + 1) * seg if idx < 3 else w
            tile = sprite.crop((left, 0, right, h)).resize((size, size), resampling)
            out[name] = ImageTk.PhotoImage(tile, master=parent)
    except Exception:
        out = {}

    _CALL_BTN_CACHE[key] = out
    return out


def _apply_rounded_window_region(win: tk.Misc, radius: int = 8, top_only: bool = True) -> None:
    """Apply rounded-corner region to a toplevel on Windows.

    XP windows look best with subtle top rounding; bottom corners stay square.
    """
    if not sys.platform.startswith('win'):
        return
    try:
        import ctypes as _ctypes
        win.update_idletasks()
        w = int(win.winfo_width())
        h = int(win.winfo_height())
        if w <= 0 or h <= 0:
            return
        hwnd = int(win.winfo_id())
        if top_only:
            # Build top-rounded region, then union with lower rectangle to keep
            # bottom corners square (more XP-authentic).
            rgn_round = _ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
            rgn_rect = _ctypes.windll.gdi32.CreateRectRgn(0, radius, w + 1, h + 1)
            rgn_final = _ctypes.windll.gdi32.CreateRectRgn(0, 0, 1, 1)
            RGN_OR = 2
            _ctypes.windll.gdi32.CombineRgn(rgn_final, rgn_round, rgn_rect, RGN_OR)
            _ctypes.windll.gdi32.DeleteObject(rgn_round)
            _ctypes.windll.gdi32.DeleteObject(rgn_rect)
            _ctypes.windll.user32.SetWindowRgn(hwnd, rgn_final, True)
        else:
            hrgn = _ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius, radius)
            _ctypes.windll.user32.SetWindowRgn(hwnd, hrgn, True)
    except Exception:
        pass


def _clear_window_region(win: tk.Misc) -> None:
    """Clear custom region to avoid clipping when maximized."""
    if not sys.platform.startswith('win'):
        return
    try:
        import ctypes as _ctypes

        hwnd = int(win.winfo_id())
        _ctypes.windll.user32.SetWindowRgn(hwnd, 0, True)
    except Exception:
        pass


def _make_icon_canvas(parent, size, kind, bg=PANEL):
    c = tk.Canvas(parent, width=size, height=size,
                  bg=bg, highlightthickness=0)
    # XP-style raised icon box
    c.create_rectangle(0, 0, size - 1, size - 1,
                       fill=BTN_SHADOW, outline=BTN_DARK)
    c.create_rectangle(1, 1, size - 2, size - 2,
                       fill=BTN_FACE, outline='')
    img = _xp_icon_image(parent, kind, target_size=max(8, size - 6))
    if img is not None:
        c.create_image(size // 2, size // 2, image=img)
    elif kind == 'phone':
        _draw_handset_on(c, size / 2 + 1, size / 2, size * 0.26,
                         max(2, size // 8), '#333333')
    else:
        cx, cy = size / 2, size / 2
        hr = size * 0.14
        hy = cy - size * 0.04
        c.create_oval(cx - hr, hy - hr, cx + hr, hy + hr,
                      fill='#333333', outline='')
        sw = size * 0.30
        c.create_arc(cx - sw, hy + hr - 1, cx + sw, hy + hr + sw * 1.6,
                     start=0, extent=180, fill='#333333', outline='', style='chord')
    return c


def _clear(frame):
    for w in frame.winfo_children():
        w.destroy()


def _load_cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cfg(cfg: dict) -> None:
    with open(_CFG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# XP Dialog helper
# ══════════════════════════════════════════════════════════════════════════════

def _dialog(root, title: str, fields: List[tuple],
            width: int = 340) -> Optional[Dict[str, str]]:
    """
    XP-styled modal form dialog.
    fields = [('Label', 'key', 'default', show_char_or_''), ...]
    """
    dlg = tk.Toplevel(root)
    dlg.withdraw()
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.grab_set()
    dlg.configure(bg=BG)

    h = len(fields) * 44 + 100
    dlg.geometry(f'{width}x{h}')

    use_custom_chrome = sys.platform.startswith('win')

    result: Dict[str, str] = {}

    def _finish() -> None:
        if use_custom_chrome:
            try:
                dlg.wm_attributes('-topmost', False)
            except Exception:
                pass
        try:
            dlg.withdraw()
        except Exception:
            pass
        dlg.after_idle(dlg.destroy)

    if use_custom_chrome:
        dlg.overrideredirect(True)
    else:
        try:
            dlg.transient(root)
        except Exception:
            pass

    # XP titlebar stripe
    tb = _xp_titlebar(dlg, title, width, 26)
    tb.place(x=0, y=0)

    if use_custom_chrome:
        btn_w, btn_h = 21, 21
        top_pad, right_pad, gap = 3, 5, 0

        btn_min = tk.Canvas(dlg, width=btn_w, height=btn_h,
                            highlightthickness=0, bd=0, cursor='hand2')
        btn_max = tk.Canvas(dlg, width=btn_w, height=btn_h,
                            highlightthickness=0, bd=0, cursor='hand2')
        btn_close = tk.Canvas(dlg, width=btn_w, height=btn_h,
                              highlightthickness=0, bd=0, cursor='hand2')

        x_close = width - right_pad - btn_w
        x_max = x_close - gap - btn_w
        x_min = x_max - gap - btn_w
        btn_min.place(x=x_min, y=top_pad, width=btn_w, height=btn_h)
        btn_max.place(x=x_max, y=top_pad, width=btn_w, height=btn_h)
        btn_close.place(x=x_close, y=top_pad, width=btn_w, height=btn_h)

        cap_state = {'maximized': False, 'restore': f'{width}x{h}'}

        def _draw_btn(cv: tk.Canvas, kind: str, pressed: bool) -> None:
            cv.delete('all')
            draw_kind = 'restore' if (kind == 'max' and cap_state['maximized']) else kind
            img = _xp_caption_image(cv, draw_kind, pressed)
            if img is not None:
                cv.create_image(btn_w // 2, btn_h // 2, image=img)

        def _on_minimize() -> None:
            dlg.overrideredirect(False)
            dlg.update_idletasks()
            dlg.iconify()

        def _on_max_toggle() -> None:
            if not cap_state['maximized']:
                cap_state['restore'] = dlg.geometry()
                wa = _windows_work_area()
                if wa:
                    x, y, w, h2 = wa
                    dlg.geometry(f'{w}x{h2}+{x}+{y}')
                else:
                    sw = dlg.winfo_screenwidth()
                    sh = dlg.winfo_screenheight()
                    dlg.geometry(f'{sw}x{sh}+0+0')
                cap_state['maximized'] = True
            else:
                dlg.geometry(cap_state['restore'])
                cap_state['maximized'] = False
                _apply_rounded_window_region(dlg, radius=8)

        def _bind_btn(cv: tk.Canvas, kind: str, command: Callable[[], None]) -> None:
            def _press(_e):
                _draw_btn(cv, kind, True)

            def _release(_e):
                command()
                _draw_btn(cv, kind, False)

            def _leave(_e):
                _draw_btn(cv, kind, False)

            cv.bind('<ButtonPress-1>', _press)
            cv.bind('<ButtonRelease-1>', _release)
            cv.bind('<Leave>', _leave)

        _draw_btn(btn_min, 'min', False)
        _draw_btn(btn_max, 'max', False)
        _draw_btn(btn_close, 'close', False)
        _bind_btn(btn_min, 'min', _on_minimize)
        _bind_btn(btn_max, 'max', _on_max_toggle)
        _bind_btn(btn_close, 'close', _finish)

        drag = {'x': 0, 'y': 0}

        def _drag_start(event):
            drag['x'] = int(event.x_root)
            drag['y'] = int(event.y_root)

        def _drag_move(event):
            dx = int(event.x_root) - drag['x']
            dy = int(event.y_root) - drag['y']
            x = dlg.winfo_x() + dx
            y = dlg.winfo_y() + dy
            dlg.geometry(f'+{x}+{y}')
            drag['x'] = int(event.x_root)
            drag['y'] = int(event.y_root)

        tb.bind('<ButtonPress-1>', _drag_start)
        tb.bind('<B1-Motion>', _drag_move)
        tb.bind('<Double-Button-1>', lambda _e: _on_max_toggle())

        def _on_map(_e):
            if dlg.state() != 'iconic':
                def _restore():
                    dlg.overrideredirect(True)
                    if not cap_state['maximized']:
                        _apply_rounded_window_region(dlg, radius=8)
                dlg.after(0, _restore)

        dlg.bind('<Map>', _on_map)
        dlg.bind('<Configure>', lambda _e: (not cap_state['maximized']) and _apply_rounded_window_region(dlg, radius=8))

    body = tk.Frame(dlg, bg=BG, bd=2, relief='sunken')
    body.place(x=6, y=32, width=width - 12, height=h - 80)

    entries: Dict[str, tk.Entry] = {}
    for i, field in enumerate(fields):
        label, key, default = field[0], field[1], field[2]
        show  = field[3] if len(field) > 3 else ''
        tk.Label(body, text=label, bg=BG, font=_F,
                 anchor='w').grid(row=i, column=0, padx=10, pady=6, sticky='w')
        e = tk.Entry(body, font=_F, width=22, show=show,
                     bg='white', fg='black', relief='sunken',
                     insertbackground='black', bd=2)
        e.insert(0, str(default))
        e.grid(row=i, column=1, padx=8, pady=6, sticky='ew')
        entries[key] = e

    body.columnconfigure(1, weight=1)

    def _ok():
        for key, e in entries.items():
            result[key] = e.get().strip()
        _finish()

    def _cancel():
        _finish()

    btn_row = tk.Frame(dlg, bg=BG)
    btn_row.place(x=0, y=h - 44, width=width, height=40)

    _xp_button(btn_row, 'OK',     _ok,     width=8).pack(side='left',  padx=(width // 2 - 80, 4))
    _xp_button(btn_row, 'Cancel', _cancel, width=8).pack(side='left')

    dlg.bind('<Return>', lambda e: _ok())
    dlg.bind('<Escape>', lambda e: _cancel())

    root.update_idletasks()
    dlg.update_idletasks()

    # Robust centering: prefer parent-window center, fallback to screen center.
    try:
        rw = int(root.winfo_width())
        rh = int(root.winfo_height())
        rx = int(root.winfo_rootx())
        ry = int(root.winfo_rooty())
    except Exception:
        rw = rh = rx = ry = 0

    sw = int(dlg.winfo_screenwidth())
    sh = int(dlg.winfo_screenheight())

    x = rx + rw // 2 - width // 2
    y = ry + rh // 2 - h // 2

    # If parent coordinates are not reliable (common with custom chrome),
    # fall back to true screen center.
    if rw <= 1 or rh <= 1 or x <= 5 or y <= 5:
        x = (sw - width) // 2
        y = (sh - h) // 2

    x = max(0, x)
    y = max(0, y)
    dlg.geometry(f'+{x}+{y}')

    # Ensure the dialog appears above the custom-chrome main window.
    dlg.lift()
    try:
        dlg.focus_force()
    except Exception:
        pass

    if use_custom_chrome:
        try:
            # Keep auth/input dialog above the custom overrideredirect main
            # window for the full modal lifetime.
            dlg.wm_attributes('-topmost', True)
        except Exception:
            pass

    # Some Windows + overrideredirect combinations snap new dialogs to (0,0)
    # after initial map/topmost changes. Re-apply desired geometry eagerly and
    # once more in the next event tick.
    target_geometry = f'{width}x{h}+{x}+{y}'
    try:
        dlg.geometry(target_geometry)
        dlg.after(0, lambda g=target_geometry: dlg.geometry(g))
    except Exception:
        pass

    # Focus first input field for immediate typing.
    if entries:
        first = next(iter(entries.values()))
        first.focus_set()
        first.select_range(0, 'end')

    dlg.protocol('WM_DELETE_WINDOW', _finish)
    dlg.deiconify()
    if use_custom_chrome:
        _apply_rounded_window_region(dlg, radius=8)
    dlg.wait_window()
    return result if result else None


# ══════════════════════════════════════════════════════════════════════════════
# SkypeUI
# ══════════════════════════════════════════════════════════════════════════════

class SkypeUI:
    W, H = 265, 454

    def __init__(self, root: tk.Tk, username: str = 'john_smith'):
        self.root     = root
        self.username = username
        self._dpi_scale = apply_tk_rendering_profile(self.root)
        self._ui_host: tk.Misc = self.root
        self.root.title(f'Not Skype - {username}')
        self.root.geometry(f'{self.W}x{self.H}')
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        # Custom XP-Luna window chrome (titlebar + X [] _) on Windows.
        # This is required because native titlebar styling is OS-controlled.
        self._use_custom_window_chrome = sys.platform.startswith('win')
        self._window_is_maximized = False
        self._window_restore_geometry = self.root.geometry()
        self._did_initial_center = False
        self._did_map_center = False
        self._drag_start_x = 0
        self._drag_start_y = 0
        if self._use_custom_window_chrome:
            self.root.overrideredirect(True)
            self.root.bind('<Map>', self._on_root_map)
            # Explicit outer border for custom-chrome windows.
            self.root.configure(bg=BTN_DDARK)
            self._ui_host = tk.Frame(self.root, bg=BG, bd=0, highlightthickness=0)
            self._ui_host.pack(fill='both', expand=True, padx=1, pady=1)

        # XP window border color
        self.root.tk_setPalette(
            background=BG,
            foreground='black',
            activeBackground=SEL_BG,
            activeForeground=SEL_FG,
            highlightBackground=BTN_DARK,
            highlightColor=BTN_HILITE,
        )

        self.state:          str           = 'log'
        self.active_contact: Optional[str] = None
        self._call_mode:     str           = 'idle'
        self._call_secs:     int           = 0
        self._timer_id:      Optional[str] = None
        self._dur_var:       Optional[tk.StringVar] = None
        self._recording_active: bool       = False
        self._recording_last_path: str     = ''

        self.contacts:     List[dict] = []
        self.missed_calls: List[str]  = []
        self.call_history: List[dict] = []  # [{name, type, timestamp, duration_secs}]

        # Names that are redacted in the Friends list (stored in config.json)
        cfg = _load_cfg()
        self._hidden_contacts: set = set(cfg.get('hidden_contacts', []))

        # Contacts selected to be sent to the CIT200 handset phone book
        # Empty set = send all contacts (no filter)
        _sel = cfg.get('contacts', {}).get('selected_contacts', [])
        self._phone_contacts: set = set(_sel) if _sel else set()

        self._status_text:    str = 'Online'
        self._platform_label: str = ''

        # Recording toggle (persisted in config.json -> recording.auto_record_calls)
        rec_cfg = cfg.get('recording', {})
        self._record_calls_var = tk.BooleanVar(
            value=bool(rec_cfg.get('auto_record_calls', True))
        )

        # Mic mode (handset as plain mic/speaker — no platform)
        self._mic_mode: bool = False

        # Orchestrator callbacks
        self.on_call_start:    Optional[Callable[[str], None]]      = None
        self.on_call_answer:   Optional[Callable[[str], None]]      = None
        self.on_call_end:      Optional[Callable[[str, int], None]] = None
        self.on_contact_sel:   Optional[Callable[[str], None]]      = None
        self.on_state_change:  Optional[Callable[[str], None]]      = None
        self.on_status_change: Optional[Callable[[str], None]]      = None
        self.on_config_save:   Optional[Callable[[dict], None]]     = None
        self.on_mic_mode:      Optional[Callable[[bool], None]]     = None

        self._build_chrome()
        self._apply_window_icon()
        self._center_main_window()
        self._apply_rounded_main_window()
        self._set_window_title()
        self._render()

    def _dpi_px(self, value: int, min_px: int = 1, max_px: Optional[int] = None) -> int:
        # Keep chrome scaling modest so XP proportions stay intact.
        scale = max(1.0, min(1.35, float(getattr(self, '_dpi_scale', 1.0))))
        out = int(round(float(value) * scale))
        out = max(int(min_px), out)
        if max_px is not None:
            out = min(out, int(max_px))
        return out

    def _center_main_window(self) -> None:
        """Center the main Skype window on the current screen."""
        self.root.update_idletasks()
        w = int(self.W)
        h = int(self.H)
        sw = int(self.root.winfo_screenwidth())
        sh = int(self.root.winfo_screenheight())
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.root.geometry(f'{w}x{h}+{x}+{y}')
        self._window_restore_geometry = self.root.geometry()
        self._did_initial_center = True

    def _apply_rounded_main_window(self) -> None:
        """Apply XP-like rounded corners to main window on Windows."""
        if not self._use_custom_window_chrome:
            return
        if not sys.platform.startswith('win'):
            return
        if self._window_is_maximized:
            return
        _apply_rounded_window_region(self.root, radius=8)

    def _apply_window_icon(self) -> None:
        """Apply icon from Windows-XP-UI-Kit assets if available."""
        try:
            if _XP_KIT_ICON.exists():
                self._win_icon = tk.PhotoImage(file=str(_XP_KIT_ICON))
                self.root.iconphoto(True, self._win_icon)
        except Exception:
            pass

    def _set_window_title(self) -> None:
        title = f'Not Skype - {self.username}'
        self.root.title(title)
        if hasattr(self, '_title_canvas'):
            self._redraw_titlebar_canvas()

    def _redraw_titlebar_canvas(self) -> None:
        if not hasattr(self, '_title_canvas'):
            return
        title = f'Not Skype - {self.username}'
        w = max(int(self._chrome.winfo_width() or 0), self.W)
        h = max(1, int(self._title_canvas.winfo_height() or 28))
        self._title_canvas.configure(width=w, height=h)
        stops = [
            (0.00, '#3B97FF'),
            (0.05, '#005CE9'),
            (0.35, '#0058E6'),
            (0.85, '#0067FF'),
            (0.93, '#0143CF'),
            (1.00, '#0133B0'),
        ]
        _draw_gradient_into_canvas(self._title_canvas, w, h, stops, tag='tb_grad', clear_all=True)
        self._title_canvas.create_text(
            10, 15,
            text=title,
            fill='#0B2E6A',
            font=('Tahoma', 9, 'bold'),
            anchor='w',
            tags='title_shadow'
        )
        self._title_canvas.create_text(
            10, 14,
            text=title,
            fill=TB_TEXT,
            font=('Tahoma', 9, 'bold'),
            anchor='w',
            tags='title'
        )

    def _redraw_call_bar_canvas(self) -> None:
        if not hasattr(self, '_call_bar_bg'):
            return
        w = max(int(self._call_bar_bg.master.winfo_width() or 0), self.W)
        h = 48
        self._call_bar_bg.configure(width=w, height=h)
        stops = [(0.0, '#F0EFE8'), (0.5, '#E8E6DC'), (1.0, '#D8D6CA')]
        _draw_gradient_into_canvas(self._call_bar_bg, w, h, stops, tag='call_grad', clear_all=True)

    # ══════════════════════════════════════════════════════════════════════════
    # Public API

    # ══════════════════════════════════════════════════════════════════════════

    def set_status(self, status: str, platform_label: str = '') -> None:
        self._status_text    = status
        self._platform_label = platform_label
        self._update_status_bar()

    def start_call(self, contact_name: str) -> None:
        self.active_contact = contact_name
        self._call_mode     = 'outgoing'
        self._call_secs     = 0
        self._switch('calling')

    def incoming_call(self, contact_name: str) -> None:
        self.active_contact = contact_name
        self._call_mode     = 'incoming'
        self._call_secs     = 0
        self._switch('calling')

    def answer_call(self) -> None:
        if self.state == 'calling':
            self._call_mode = 'in_call'
            self._switch('in_call')

    def end_call(self) -> None:
        if self.state in ('calling', 'in_call'):
            self._do_end_call_cleanup()

    def set_call_recording(self, active: bool, saved_path: str = '') -> None:
        """Called by backend to update recording indicator state."""
        self._recording_active = bool(active)
        if saved_path:
            self._recording_last_path = saved_path
        if self.state in ('calling', 'in_call'):
            self._render()

    def prompt_input(self, title: str, prompt: str, secret: bool = False) -> str:
        """XP-styled modal text prompt used by backend auth flows."""
        show_char = '*' if secret else ''
        res = _dialog(
            self.root,
            title,
            [(prompt, 'value', '', show_char)],
            width=340,
        )
        if not res:
            return ''
        return str(res.get('value', '') or '').strip()

    def update_contacts(self, contacts: List[dict]) -> None:
        self.contacts = contacts
        if self.state in ('log', 'friends'):
            self._render()

    # ══════════════════════════════════════════════════════════════════════════
    # Chrome
    # ══════════════════════════════════════════════════════════════════════════

    def _build_chrome(self) -> None:
        host = self._ui_host
        if self._use_custom_window_chrome:
            self._build_window_chrome()
        self._build_menubar()

        # ── Tab strip ────────────────────────────────────────────────────────
        self._tab_strip = tk.Frame(host, bg=TAB_ROW, bd=0)
        self._tab_strip.pack(fill='x', side='top')

        # 1-px Luna blue tab underline
        tk.Frame(host, bg=LUNA_BLUE, height=2).pack(fill='x', side='top')

        # ── Main pane ────────────────────────────────────────────────────────
        self._pane = tk.Frame(host, bg=PANEL, bd=0)
        self._pane.pack(fill='both', expand=True, side='top')

        # ── Divider ──────────────────────────────────────────────────────────
        tk.Frame(host, bg=BTN_DARK, height=1).pack(fill='x', side='top')

        # ── Call button bar ──────────────────────────────────────────────────
        ctrl = tk.Frame(host, bg=BG, height=48)
        ctrl.pack(fill='x', side='top')
        ctrl.pack_propagate(False)

        # Gradient background for call bar
        self._call_bar_bg = _gradient_canvas(
            ctrl, self.W, 48,
            [(0.0, '#F0EFE8'), (0.5, '#E8E6DC'), (1.0, '#D8D6CA')]
        )
        self._call_bar_bg.place(x=0, y=0, relwidth=1, height=48)

        self._call_btn_frame = tk.Frame(ctrl, bg='')
        self._call_btn_frame.place(x=14, y=7)
        self._rebuild_call_btn()

        # Mic mode toggle button (centered)
        self._mic_btn_frame = tk.Frame(ctrl, bg='')
        self._mic_btn_frame.place(x=self.W // 2 - 17, y=7)
        self._rebuild_mic_btn()

        self._hang_btn_frame = tk.Frame(ctrl, bg='')
        self._hang_btn_frame.place(x=self.W - 50, y=7)
        self._rebuild_hang_btn(active=False)

        # ── Divider + status bar ─────────────────────────────────────────────
        tk.Frame(host, bg=BTN_DARK, height=1).pack(fill='x', side='top')
        self._build_status_bar()
        self._redraw_call_bar_canvas()

    def _build_window_chrome(self) -> None:
        """Draw XP-style custom titlebar with X, [], and _ buttons."""
        bar_h = self._dpi_px(28, min_px=28, max_px=38)
        chrome = tk.Frame(self._ui_host, bg=BTN_DARK, height=bar_h + 1, bd=0)
        chrome.pack(fill='x', side='top')
        chrome.pack_propagate(False)
        self._chrome = chrome

        self._title_canvas = _xp_titlebar(chrome, f'Not Skype - {self.username}', self.W, bar_h)
        self._title_canvas.place(x=0, y=0, relwidth=1, height=bar_h)

        # Drag window by titlebar background.
        self._title_canvas.bind('<ButtonPress-1>', self._on_window_drag_start)
        self._title_canvas.bind('<B1-Motion>', self._on_window_drag_move)

        # XP caption buttons: _, [], X
        self._caption_btn_w = self._dpi_px(21, min_px=21, max_px=28)
        self._caption_btn_h = self._dpi_px(21, min_px=21, max_px=28)
        self._caption_top_pad = self._dpi_px(3, min_px=3, max_px=5)
        self._caption_right_pad = self._dpi_px(5, min_px=5, max_px=7)
        self._caption_gap = self._dpi_px(0, min_px=0, max_px=1)

        self._btn_min = tk.Canvas(
            chrome,
            width=self._caption_btn_w,
            height=self._caption_btn_h,
            highlightthickness=0,
            bd=0,
            cursor='hand2',
        )
        self._btn_max = tk.Canvas(
            chrome,
            width=self._caption_btn_w,
            height=self._caption_btn_h,
            highlightthickness=0,
            bd=0,
            cursor='hand2',
        )
        self._btn_close = tk.Canvas(
            chrome,
            width=self._caption_btn_w,
            height=self._caption_btn_h,
            highlightthickness=0,
            bd=0,
            cursor='hand2',
        )

        self._draw_caption_button(self._btn_min, 'min', pressed=False)
        self._draw_caption_button(self._btn_max, 'max', pressed=False)
        self._draw_caption_button(self._btn_close, 'close', pressed=False)

        self._bind_caption_button(self._btn_min, 'min', self._on_window_minimize)
        self._bind_caption_button(self._btn_max, 'max', self._on_window_maximize_toggle)
        self._bind_caption_button(self._btn_close, 'close', self._on_window_close)

        self._layout_caption_buttons()

        # Double-click titlebar toggles maximize/restore.
        self._title_canvas.bind('<Double-Button-1>', lambda _e: self._on_window_maximize_toggle())
        self.root.bind('<Configure>', self._on_root_configure)

    def _layout_caption_buttons(self) -> None:
        w = max(int(self._chrome.winfo_width() or 0), self.W)
        x_close = w - self._caption_right_pad - self._caption_btn_w
        x_max = x_close - self._caption_gap - self._caption_btn_w
        x_min = x_max - self._caption_gap - self._caption_btn_w
        y = self._caption_top_pad
        self._btn_min.place(x=x_min, y=y, width=self._caption_btn_w, height=self._caption_btn_h)
        self._btn_max.place(x=x_max, y=y, width=self._caption_btn_w, height=self._caption_btn_h)
        self._btn_close.place(x=x_close, y=y, width=self._caption_btn_w, height=self._caption_btn_h)

    def _decorate_luna_toplevel(self, win: tk.Toplevel, title: str, width: int, height: int) -> None:
        """Apply Luna custom titlebar with _, [], X to secondary windows."""
        if not self._use_custom_window_chrome:
            return

        win.overrideredirect(True)
        btn_w = self._dpi_px(21, min_px=21, max_px=28)
        btn_h = self._dpi_px(21, min_px=21, max_px=28)
        top_pad = self._dpi_px(3, min_px=3, max_px=5)
        right_pad = self._dpi_px(5, min_px=5, max_px=7)
        gap = self._dpi_px(0, min_px=0, max_px=1)

        tb = _xp_titlebar(win, title, width, 26)
        tb.place(x=0, y=0)

        bmin = tk.Canvas(win, width=btn_w, height=btn_h, highlightthickness=0, bd=0, cursor='hand2')
        bmax = tk.Canvas(win, width=btn_w, height=btn_h, highlightthickness=0, bd=0, cursor='hand2')
        bclose = tk.Canvas(win, width=btn_w, height=btn_h, highlightthickness=0, bd=0, cursor='hand2')

        x_close = width - right_pad - btn_w
        x_max = x_close - gap - btn_w
        x_min = x_max - gap - btn_w
        bmin.place(x=x_min, y=top_pad, width=btn_w, height=btn_h)
        bmax.place(x=x_max, y=top_pad, width=btn_w, height=btn_h)
        bclose.place(x=x_close, y=top_pad, width=btn_w, height=btn_h)

        state = {'maximized': False, 'restore': f'{width}x{height}'}

        def _close() -> None:
            try:
                win.withdraw()
            except Exception:
                pass
            win.after_idle(win.destroy)

        def _draw_btn(cv: tk.Canvas, kind: str, pressed: bool) -> None:
            cv.delete('all')
            draw_kind = 'restore' if (kind == 'max' and state['maximized']) else kind
            img = _xp_caption_image(cv, draw_kind, pressed, target_size=min(btn_w, btn_h))
            if img is not None:
                cv.create_image(btn_w // 2, btn_h // 2, image=img)

        def _minimize() -> None:
            win.overrideredirect(False)
            win.update_idletasks()
            win.iconify()

        def _max_toggle() -> None:
            if not state['maximized']:
                state['restore'] = win.geometry()
                _clear_window_region(win)
                wa = _windows_work_area()
                if wa:
                    x, y, w, h2 = wa
                    win.geometry(f'{w}x{h2}+{x}+{y}')
                else:
                    sw = win.winfo_screenwidth()
                    sh = win.winfo_screenheight()
                    win.geometry(f'{sw}x{sh}+0+0')
                state['maximized'] = True
            else:
                win.geometry(state['restore'])
                state['maximized'] = False
                _apply_rounded_window_region(win, radius=8)

        def _bind_btn(cv: tk.Canvas, kind: str, command: Callable[[], None]) -> None:
            def _press(_e):
                _draw_btn(cv, kind, True)

            def _release(_e):
                command()
                _draw_btn(cv, kind, False)

            def _leave(_e):
                _draw_btn(cv, kind, False)

            cv.bind('<ButtonPress-1>', _press)
            cv.bind('<ButtonRelease-1>', _release)
            cv.bind('<Leave>', _leave)

        _draw_btn(bmin, 'min', False)
        _draw_btn(bmax, 'max', False)
        _draw_btn(bclose, 'close', False)
        _bind_btn(bmin, 'min', _minimize)
        _bind_btn(bmax, 'max', _max_toggle)
        _bind_btn(bclose, 'close', _close)

        drag = {'x': 0, 'y': 0}

        def _drag_start(event):
            drag['x'] = int(event.x_root)
            drag['y'] = int(event.y_root)

        def _drag_move(event):
            if state['maximized']:
                return
            dx = int(event.x_root) - drag['x']
            dy = int(event.y_root) - drag['y']
            x = win.winfo_x() + dx
            y = win.winfo_y() + dy
            win.geometry(f'+{x}+{y}')
            drag['x'] = int(event.x_root)
            drag['y'] = int(event.y_root)

        tb.bind('<ButtonPress-1>', _drag_start)
        tb.bind('<B1-Motion>', _drag_move)
        tb.bind('<Double-Button-1>', lambda _e: _max_toggle())

        def _on_map(_e):
            if win.state() != 'iconic':
                def _restore():
                    win.overrideredirect(True)
                    if not state['maximized']:
                        _apply_rounded_window_region(win, radius=8)
                win.after(0, _restore)

        win.bind('<Map>', _on_map)
        win.bind('<Configure>', lambda _e: (not state['maximized']) and _apply_rounded_window_region(win, radius=8))
        win.protocol('WM_DELETE_WINDOW', _close)
        _apply_rounded_window_region(win, radius=8)

    def _draw_caption_button(self, canvas: tk.Canvas, kind: str, pressed: bool) -> None:
        """Paint XP Luna caption button (_ [] X)."""
        canvas.delete('all')
        w = self._caption_btn_w
        h = self._caption_btn_h

        # First choice: real XP button bitmap from resource pack.
        draw_kind = 'restore' if (kind == 'max' and self._window_is_maximized) else kind
        xp_img = _xp_caption_image(canvas, draw_kind, pressed, target_size=min(w, h))
        if xp_img is not None:
            canvas.create_image(w // 2, h // 2, image=xp_img)
            return

        if kind == 'close':
            top, mid, bot = ('#F8D3D3', '#E27E7E', '#C04B4B') if not pressed else ('#EAA6A6', '#CD5C5C', '#A13A3A')
            glyph = '#1D1D1D'
        else:
            top, mid, bot = ('#D8E6FB', '#8BB2EA', '#5E8FD8') if not pressed else ('#B8D0F5', '#6E9EE2', '#4C79C3')
            glyph = '#15386B'

        # 3-stop vertical gradient
        for y in range(h):
            t = y / max(h - 1, 1)
            if t < 0.55:
                c = _lerp_color(top, mid, t / 0.55)
            else:
                c = _lerp_color(mid, bot, (t - 0.55) / 0.45)
            canvas.create_line(0, y, w, y, fill=c)

        # Border highlights/shadows
        canvas.create_rectangle(0, 0, w - 1, h - 1, outline='#3E65A6' if kind != 'close' else '#8E3838')
        canvas.create_line(1, 1, w - 2, 1, fill='#F3F8FF' if kind != 'close' else '#FFECEC')
        canvas.create_line(1, 1, 1, h - 2, fill='#F3F8FF' if kind != 'close' else '#FFECEC')
        canvas.create_line(1, h - 2, w - 2, h - 2, fill='#2E548F' if kind != 'close' else '#7C2A2A')
        canvas.create_line(w - 2, 1, w - 2, h - 2, fill='#2E548F' if kind != 'close' else '#7C2A2A')

        cx = w // 2 + (1 if pressed else 0)
        cy = h // 2 + (1 if pressed else 0)
        icon_key = {
            'min': 'caption_min',
            'max': 'caption_max',
            'close': 'caption_close',
        }.get(kind, '')
        icon_name = _load_xp_icon_name(icon_key)
        icon_sz = max(10, min(w - 6, h - 6))
        icon_img = _xp_asset_image(canvas, icon_name, target_size=icon_sz) if icon_name else None
        if icon_img is not None:
            canvas.create_image(cx, cy, image=icon_img)
        elif kind == 'min':
            half = max(3, min(w, h) // 5)
            y = cy + max(1, half // 2)
            canvas.create_line(cx - half, y, cx + half, y, fill=glyph, width=1)
        elif kind == 'max':
            half_w = max(3, min(w, h) // 5)
            half_h = max(2, half_w - 1)
            canvas.create_rectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h, outline=glyph, width=1)
        else:
            half = max(2, min(w, h) // 6)
            canvas.create_line(cx - half, cy - half, cx + half, cy + half, fill=glyph, width=1)
            canvas.create_line(cx + half, cy - half, cx - half, cy + half, fill=glyph, width=1)

    def _bind_caption_button(self, canvas: tk.Canvas, kind: str, command: Callable[[], None]) -> None:
        def _press(_e):
            self._draw_caption_button(canvas, kind, pressed=True)

        def _release(_e):
            command()
            self._draw_caption_button(canvas, kind, pressed=False)

        def _leave(_e):
            self._draw_caption_button(canvas, kind, pressed=False)

        canvas.bind('<ButtonPress-1>', _press)
        canvas.bind('<ButtonRelease-1>', _release)
        canvas.bind('<Leave>', _leave)

    def _on_window_drag_start(self, event) -> None:
        self._drag_start_x = int(event.x_root)
        self._drag_start_y = int(event.y_root)

    def _on_window_drag_move(self, event) -> None:
        if self._window_is_maximized:
            return
        dx = int(event.x_root) - self._drag_start_x
        dy = int(event.y_root) - self._drag_start_y
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f'+{x}+{y}')
        self._drag_start_x = int(event.x_root)
        self._drag_start_y = int(event.y_root)

    def _on_window_minimize(self) -> None:
        # On Windows, iconify can require native decorations for a moment.
        if self._use_custom_window_chrome:
            self.root.overrideredirect(False)
        self.root.update_idletasks()
        self.root.iconify()

    def _on_window_maximize_toggle(self) -> None:
        if not self._window_is_maximized:
            self._window_restore_geometry = self.root.geometry()
            _clear_window_region(self.root)
            wa = _windows_work_area()
            if wa:
                x, y, w, h = wa
                self.root.geometry(f'{w}x{h}+{x}+{y}')
            else:
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                self.root.geometry(f'{sw}x{sh}+0+0')
            self._window_is_maximized = True
        else:
            self.root.geometry(self._window_restore_geometry)
            self._window_is_maximized = False
        self._draw_caption_button(self._btn_max, 'max', pressed=False)
        self.root.after(0, self._apply_rounded_main_window)

    def _on_window_close(self) -> None:
        self.root.destroy()

    def _on_root_map(self, _event) -> None:
        # Restore custom chrome after minimizing.
        if self._use_custom_window_chrome and self.root.state() != 'iconic':
            def _restore_chrome():
                self.root.overrideredirect(True)
                # Some Windows setups reset custom-chrome windows to top-left
                # on first map; force-center once after mapping.
                if not self._did_map_center:
                    self._center_main_window()
                    self._did_map_center = True
                self._apply_rounded_main_window()
            self.root.after(0, _restore_chrome)

    def _on_root_configure(self, _event) -> None:
        if self._use_custom_window_chrome and hasattr(self, '_btn_close'):
            self._layout_caption_buttons()
            self._redraw_titlebar_canvas()
            self._redraw_call_bar_canvas()
            w = max(self.root.winfo_width(), self.W)
            if hasattr(self, '_hang_btn_frame'):
                self._hang_btn_frame.place_configure(x=w - 50)
            if hasattr(self, '_mic_btn_frame'):
                self._mic_btn_frame.place_configure(x=w // 2 - 17)
            self.root.after(0, self._apply_rounded_main_window)

    def _rebuild_call_btn(self) -> None:
        _clear(self._call_btn_frame)
        sprite = _load_call_button_images(self._call_btn_frame, size=34)
        if sprite.get('green_up') and sprite.get('green_down'):
            c = tk.Canvas(self._call_btn_frame, width=34, height=34,
                          bg=BG, highlightthickness=0, bd=0, cursor='hand2')
            c.pack()
            c.create_image(17, 17, image=sprite['green_up'], tags='img')

            def _press(_e):
                c.itemconfigure('img', image=sprite['green_down'])

            def _release(_e):
                c.itemconfigure('img', image=sprite['green_up'])
                self._on_green_btn()

            def _leave(_e):
                c.itemconfigure('img', image=sprite['green_up'])

            c.bind('<ButtonPress-1>', _press)
            c.bind('<ButtonRelease-1>', _release)
            c.bind('<Leave>', _leave)
            self._call_canvas = c
        else:
            c = _make_xp_call_btn(self._call_btn_frame, '#1A7A1A', size=34)
            c.pack()
            c.bind('<Button-1>', lambda e: self._on_green_btn())
            c.config(cursor='hand2')
            self._call_canvas = c

    def _rebuild_hang_btn(self, active: bool) -> None:
        _clear(self._hang_btn_frame)
        sprite = _load_call_button_images(self._hang_btn_frame, size=34)
        if sprite.get('red_up') and sprite.get('red_down'):
            hc = tk.Canvas(self._hang_btn_frame, width=34, height=34,
                           bg=BG, highlightthickness=0, bd=0, cursor='hand2')
            hc.pack()
            hc.create_image(17, 17, image=sprite['red_up'], tags='img')

            def _press(_e):
                hc.itemconfigure('img', image=sprite['red_down'])

            def _release(_e):
                hc.itemconfigure('img', image=sprite['red_up'])
                self._on_red_btn()

            def _leave(_e):
                hc.itemconfigure('img', image=sprite['red_up'])

            hc.bind('<ButtonPress-1>', _press)
            hc.bind('<ButtonRelease-1>', _release)
            hc.bind('<Leave>', _leave)
            self._hang_canvas = hc
        else:
            color = '#CC2222' if active else '#888888'
            hc = _make_xp_call_btn(self._hang_btn_frame, color, flip=True, size=34)
            hc.pack()
            hc.bind('<Button-1>', lambda e: self._on_red_btn())
            hc.config(cursor='hand2')
            self._hang_canvas = hc

    def _rebuild_mic_btn(self) -> None:
        """Build the mic-mode toggle button in the call bar center."""
        _clear(self._mic_btn_frame)
        active = self._mic_mode
        bg_color = '#316AC5' if active else '#A0A0A0'
        fg_color = 'white'
        text = '\U0001f3a4' if active else '\U0001f3a4'

        btn = tk.Canvas(self._mic_btn_frame, width=34, height=34,
                        highlightthickness=0, bd=0, cursor='hand2')
        btn.pack()

        # Draw a rounded rect background
        btn.create_oval(2, 2, 32, 32, fill=bg_color, outline='#555555', width=1)
        btn.create_text(17, 17, text='\U0001f399', font=('Segoe UI Emoji', 12),
                        fill=fg_color)

        def _press(_e):
            btn.itemconfigure(1, fill='#1E4F8F' if active else '#808080')

        def _release(_e):
            btn.itemconfigure(1, fill=bg_color)
            self._on_mic_btn()

        def _leave(_e):
            btn.itemconfigure(1, fill=bg_color)

        btn.bind('<ButtonPress-1>', _press)
        btn.bind('<ButtonRelease-1>', _release)
        btn.bind('<Leave>', _leave)
        self._mic_canvas = btn

    def _on_mic_btn(self) -> None:
        """Toggle mic mode on/off."""
        self._mic_mode = not self._mic_mode
        self._rebuild_mic_btn()
        if self._mic_mode:
            # Entering mic mode — disable normal call state
            if self.state in ('calling', 'in_call'):
                self._do_end_call_cleanup()
        self._render()
        if self.on_mic_mode:
            self.on_mic_mode(self._mic_mode)

    def _build_menubar(self) -> None:
        mf = ('Tahoma', 8)
        mb = tk.Menu(self.root,
                     bg=BTN_FACE, fg='black',
                     activebackground=SEL_BG, activeforeground=SEL_FG,
                     bd=0, relief='flat', font=mf)

        def _submenu():
            return tk.Menu(mb, tearoff=0,
                           bg='white', fg='black',
                           activebackground=SEL_BG, activeforeground=SEL_FG,
                           bd=1, relief='solid', font=mf)

        # ── File ──────────────────────────────────────────────────────────
        fm = _submenu()
        fm.add_command(label='Change username…',  command=self._menu_change_username)
        fm.add_command(label='Change password…',  command=self._menu_change_password)
        fm.add_separator()
        fm.add_command(label='Sign out',          command=self._menu_sign_out)
        fm.add_separator()
        fm.add_command(label='Exit',              command=self.root.destroy)
        mb.add_cascade(label='File', menu=fm)

        # ── View ──────────────────────────────────────────────────────────
        vm = _submenu()
        vm.add_command(label='Log',      command=lambda: self._switch('log'))
        vm.add_command(label='Friends',  command=lambda: self._switch('friends'))
        vm.add_separator()
        vm.add_command(label='Refresh contacts', command=self._menu_refresh_contacts)
        mb.add_cascade(label='View', menu=vm)

        # ── Tools ─────────────────────────────────────────────────────────
        tm = _submenu()
        tm.add_command(label='Telegram settings…', command=self._menu_telegram_settings)
        tm.add_command(label='Audio settings…',    command=self._menu_audio_settings)
        tm.add_command(label='HID / handset…',     command=self._menu_hid_settings)
        tm.add_separator()
        tm.add_checkbutton(
            label='Record calls',
            variable=self._record_calls_var,
            command=self._toggle_record_calls,
        )
        tm.add_separator()
        tm.add_command(label='Manage contacts…',   command=self._menu_manage_contacts)
        tm.add_command(label='Phone contacts…',    command=self._menu_phone_contacts)
        tm.add_separator()
        tm.add_command(label='Call someone…',      command=self._menu_call_someone)
        tm.add_separator()
        tm.add_command(label='Edit config.json…',  command=self._menu_edit_config)
        mb.add_cascade(label='Tools', menu=tm)

        # ── Help ──────────────────────────────────────────────────────────
        hm = _submenu()
        hm.add_command(label='About…', command=self._menu_about)
        mb.add_cascade(label='Help', menu=hm)

        if self._use_custom_window_chrome:
            # With overrideredirect windows, native root menus render above the
            # custom titlebar and look broken. Draw an in-client menu row.
            row = tk.Frame(self._ui_host, bg=BTN_FACE, height=22, bd=1, relief='flat')
            row.pack(fill='x', side='top')
            row.pack_propagate(False)

            def _menu_btn(label: str, menu_obj: tk.Menu) -> None:
                btn = tk.Label(
                    row,
                    text=label,
                    font=mf,
                    bg=BTN_FACE,
                    fg='black',
                    padx=8,
                    pady=2,
                    cursor='hand2',
                    bd=0,
                )
                btn.pack(side='left')

                def _open(_e=None):
                    try:
                        menu_obj.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
                    finally:
                        menu_obj.grab_release()

                def _enter(_e=None):
                    btn.configure(bg=SEL_BG, fg=SEL_FG)

                def _leave(_e=None):
                    btn.configure(bg=BTN_FACE, fg='black')

                btn.bind('<Button-1>', _open)
                btn.bind('<Enter>', _enter)
                btn.bind('<Leave>', _leave)

            _menu_btn('File', fm)
            _menu_btn('View', vm)
            _menu_btn('Tools', tm)
            _menu_btn('Help', hm)
        else:
            self.root.config(menu=mb)

    def _build_status_bar(self) -> None:
        # XP-style deep-blue taskbar strip
        sb = tk.Frame(self._ui_host, bg=SB_BG, height=22, bd=0)
        sb.pack(fill='x', side='top')
        sb.pack_propagate(False)

        # Skype S logo — clickable
        logo_c = tk.Canvas(sb, width=18, height=18, bg=SB_BG,
                           highlightthickness=0, cursor='hand2')
        logo_c.pack(side='left', padx=(4, 0), pady=2)
        logo_c.create_oval(1, 1, 17, 17, fill=SKYPE_BLUE, outline='#007EBE', width=1)
        logo_c.create_text(9, 10, text='S', fill='white', font=('Arial', 7, 'bold'))
        logo_c.bind('<Button-1>', lambda e: self._open_status_menu(logo_c))

        arr = tk.Label(sb, text='▾', bg=SB_BG, fg='#AACCFF',
                       font=('Tahoma', 7), cursor='hand2')
        arr.pack(side='left', padx=(1, 3))
        arr.bind('<Button-1>', lambda e: self._open_status_menu(arr))

        tk.Frame(sb, bg='#5580CC', width=1).pack(side='left', fill='y', pady=3)

        self._sb_var = tk.StringVar(value='Online')
        self._sb_lbl = tk.Label(sb, textvariable=self._sb_var,
                                bg=SB_BG, fg=SB_FG, font=_FS, anchor='w')
        self._sb_lbl.pack(side='left', padx=6)

    # ══════════════════════════════════════════════════════════════════════════
    # Menu actions
    # ══════════════════════════════════════════════════════════════════════════

    def _menu_change_username(self) -> None:
        cfg = _load_cfg()
        res = _dialog(self.root, 'Change username', [
            ('New username:', 'username', cfg.get('username', ''))
        ])
        if res and res.get('username'):
            cfg['username'] = res['username']
            _save_cfg(cfg)
            self.username = res['username']
            self._set_window_title()
            messagebox.showinfo('Username',
                f'Username changed to {self.username!r}.\nRestart to reconnect.',
                parent=self.root)
            if self.on_config_save:
                self.on_config_save(cfg)

    def _menu_change_password(self) -> None:
        messagebox.showinfo('Password',
            'Passwords are managed by your backend service (Telegram/Discord).\n'
            'Use Tools → Telegram settings to update your API credentials.',
            parent=self.root)

    def _menu_sign_out(self) -> None:
        if messagebox.askyesno('Sign out', 'Sign out and close Not Skype?',
                               parent=self.root):
            self.root.destroy()

    def _menu_refresh_contacts(self) -> None:
        if self.on_config_save:
            self.on_config_save({'__action__': 'refresh_contacts'})
        else:
            messagebox.showinfo('Contacts', 'Contact refresh scheduled.',
                                parent=self.root)

    def _menu_manage_contacts(self) -> None:
        """
        XP-style dialog: checklist of all contacts.
        Checked  = visible (normal name).
        Unchecked = hidden (redacted in friends list).
        """
        if not self.contacts:
            messagebox.showinfo('Manage contacts',
                                'No contacts loaded yet.',
                                parent=self.root)
            return

        WIN_W, WIN_H = 300, 420
        dlg = tk.Toplevel(self.root)
        dlg.withdraw()
        dlg.title('Manage contacts')
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.configure(bg=BG)
        dlg.geometry(f'{WIN_W}x{WIN_H}')
        self._decorate_luna_toplevel(dlg, 'Manage contacts', WIN_W, WIN_H)

        # Instructions label
        tk.Label(dlg, text='Uncheck contacts to redact their name in Friends.',
                 bg=BG, font=_FS, fg=GREY_TXT, wraplength=WIN_W - 16,
                 justify='left').place(x=8, y=30)

        # Scrollable multi-select list (selected = visible in Friends)
        list_h = WIN_H - 110
        outer = tk.Frame(dlg, bg=BG, bd=2, relief='sunken')
        outer.place(x=8, y=52, width=WIN_W - 16, height=list_h)

        sb = tk.Scrollbar(outer, orient='vertical')
        sb.pack(side='right', fill='y')

        names = [ct['name'] for ct in sorted(self.contacts, key=lambda c: c['name'].lower())]
        lst = tk.Listbox(
            outer,
            selectmode='browse',
            exportselection=False,
            activestyle='none',
            bg='white',
            fg='black',
            font=_FS,
            selectbackground=SEL_BG,
            selectforeground=SEL_FG,
            highlightthickness=0,
            bd=0,
            yscrollcommand=sb.set,
        )
        lst.pack(side='left', fill='both', expand=True)

        checked: set = {name for name in names if name not in self._hidden_contacts}

        def _display_name(name: str) -> str:
            return f"[x] {name}" if name in checked else f"[ ] {name}"

        def _render_list(keep_view: bool = True) -> None:
            top = lst.yview()[0] if keep_view else 0.0
            active = int(lst.index('active')) if names else 0
            lst.delete(0, 'end')
            for nm in names:
                lst.insert('end', _display_name(nm))
            if names:
                active = max(0, min(active, len(names) - 1))
                lst.activate(active)
                lst.selection_clear(0, 'end')
                lst.selection_set(active)
            lst.yview_moveto(top)

        def _toggle_index(idx: int) -> None:
            if idx < 0 or idx >= len(names):
                return
            nm = names[idx]
            if nm in checked:
                checked.remove(nm)
            else:
                checked.add(nm)
            _render_list(keep_view=True)

        _render_list(keep_view=False)

        def _on_click(event):
            try:
                idx = int(lst.nearest(event.y))
            except Exception:
                idx = -1
            _toggle_index(idx)
            return 'break'

        def _on_space(_e=None):
            try:
                idx = int(lst.index('active'))
            except Exception:
                idx = -1
            _toggle_index(idx)
            return 'break'

        lst.bind('<ButtonRelease-1>', _on_click)
        lst.bind('<space>', _on_space)

        def _wheel(event):
            step = int(-1 * (event.delta // 120)) if getattr(event, 'delta', 0) else 0
            if step:
                lst.yview_scroll(step, 'units')
            return 'break'

        lst.bind('<MouseWheel>', _wheel)

        # Select-all / Select-none helpers
        def _all():
            checked.clear()
            checked.update(names)
            _render_list(keep_view=True)

        def _none():
            checked.clear()
            _render_list(keep_view=True)

        btn_y = WIN_H - 54
        sel_row = tk.Frame(dlg, bg=BG)
        sel_row.place(x=8, y=btn_y - 22, width=WIN_W - 16, height=20)
        tk.Label(sel_row, text='Select:', bg=BG, font=_FS).pack(side='left')
        lnk_all = tk.Label(sel_row, text='All', bg=BG, font=_FS, fg=LINK,
                           cursor='hand2')
        lnk_all.pack(side='left', padx=(4, 0))
        lnk_all.bind('<Button-1>', lambda _e: _all())
        tk.Label(sel_row, text=' / ', bg=BG, font=_FS).pack(side='left')
        lnk_none = tk.Label(sel_row, text='None', bg=BG, font=_FS, fg=LINK,
                            cursor='hand2')
        lnk_none.pack(side='left')
        lnk_none.bind('<Button-1>', lambda _e: _none())

        def _save():
            new_hidden = {name for name in names if name not in checked}
            self._hidden_contacts = new_hidden
            cfg = _load_cfg()
            cfg['hidden_contacts'] = sorted(new_hidden)
            _save_cfg(cfg)
            # Re-render friends list if currently visible
            if self.state == 'friends':
                self._render()
            dlg.destroy()

        def _cancel():
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.place(x=0, y=btn_y, width=WIN_W, height=40)
        _xp_button(btn_row, 'OK',     _save,   width=8).pack(side='left', padx=(WIN_W // 2 - 80, 4))
        _xp_button(btn_row, 'Cancel', _cancel, width=8).pack(side='left')

        dlg.bind('<Return>', lambda _e: _save())
        dlg.bind('<Escape>', lambda _e: _cancel())

        # Centre over main window
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + self.root.winfo_width()  // 2 - WIN_W // 2
        y = self.root.winfo_rooty() + self.root.winfo_height() // 2 - WIN_H // 2
        dlg.geometry(f'+{x}+{y}')
        dlg.deiconify()
        dlg.wait_window()

    def _menu_phone_contacts(self) -> None:
        """XP-style dialog to choose contacts sent to handset phonebook."""
        if not self.contacts:
            messagebox.showinfo('Phone contacts',
                                'No contacts loaded yet.',
                                parent=self.root)
            return

        WIN_W, WIN_H = 300, 450
        dlg = tk.Toplevel(self.root)
        dlg.withdraw()
        dlg.title('Phone contacts')
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.configure(bg=BG)
        dlg.geometry(f'{WIN_W}x{WIN_H}')
        self._decorate_luna_toplevel(dlg, 'Phone contacts', WIN_W, WIN_H)

        # Instructions
        tk.Label(dlg,
                 text='Check contacts to send to the Linksys handset.\n'
                      'If none are checked, all contacts are sent.',
                 bg=BG, font=_FS, fg=GREY_TXT, wraplength=WIN_W - 16,
                 justify='left').place(x=8, y=30)

        # Count label (updated live)
        count_var = tk.StringVar(value='')
        tk.Label(dlg, textvariable=count_var,
                 bg=BG, font=_FS, fg=GREY_TXT).place(x=8, y=58)

        # Scrollable multi-select list (selected = sent to handset)
        list_y = 76
        list_h = WIN_H - list_y - 80
        outer = tk.Frame(dlg, bg=BG, bd=2, relief='sunken')
        outer.place(x=8, y=list_y, width=WIN_W - 16, height=list_h)

        sb = tk.Scrollbar(outer, orient='vertical')
        sb.pack(side='right', fill='y')

        dot_gutter = tk.Canvas(outer, width=14, bg='white', highlightthickness=0, bd=0)
        dot_gutter.pack(side='left', fill='y')

        lst = tk.Listbox(
            outer,
            selectmode='browse',
            exportselection=False,
            activestyle='none',
            bg='white',
            fg='black',
            font=_FS,
            selectbackground=SEL_BG,
            selectforeground=SEL_FG,
            highlightthickness=0,
            bd=0,
            yscrollcommand=sb.set,
        )
        lst.pack(side='left', fill='both', expand=True)

        def _wheel(event):
            step = int(-1 * (event.delta // 120)) if getattr(event, 'delta', 0) else 0
            if step:
                lst.yview_scroll(step, 'units')
                _draw_dots()
            return 'break'

        lst.bind('<MouseWheel>', _wheel)
        dot_gutter.bind('<MouseWheel>', _wheel)

        # Build row model (selected = in phone_contacts; empty set means all)
        send_all = len(self._phone_contacts) == 0
        items: List[tuple] = []   # (name, handle, online)
        for ct in sorted(self.contacts, key=lambda c: c['name'].lower()):
            name = ct['name']
            handle = ct.get('handle', name)
            online = bool(ct.get('online', False))
            items.append((name, handle, online))

        checked_idx: set = set()
        for i, (name, handle, _online) in enumerate(items):
            chosen = send_all or name in self._phone_contacts or handle in self._phone_contacts
            if chosen:
                checked_idx.add(i)

        def _draw_dots() -> None:
            dot_gutter.delete('all')
            h = max(1, int(dot_gutter.winfo_height()))
            dot_gutter.configure(scrollregion=(0, 0, 14, h))
            for i, (_name, _handle, online) in enumerate(items):
                box = lst.bbox(i)
                if not box:
                    continue
                y = int(box[1] + box[3] / 2)
                color = GREEN_DOT if online else '#AAAAAA'
                dot_gutter.create_oval(4, y - 3, 10, y + 3, fill=color, outline='')

        def _on_scrollbar(*args):
            lst.yview(*args)
            _draw_dots()

        def _on_list_scroll(first, last):
            sb.set(first, last)
            _draw_dots()

        lst.configure(yscrollcommand=_on_list_scroll)
        sb.config(command=_on_scrollbar)

        def _display_phone_row(idx: int) -> str:
            name, _handle, _online = items[idx]
            mark = '[x]' if idx in checked_idx else '[ ]'
            return f"{mark} {name}"

        def _render_phone_list(keep_view: bool = True) -> None:
            top = lst.yview()[0] if keep_view else 0.0
            active = int(lst.index('active')) if items else 0
            lst.delete(0, 'end')
            for i in range(len(items)):
                lst.insert('end', _display_phone_row(i))
            if items:
                active = max(0, min(active, len(items) - 1))
                lst.activate(active)
                lst.selection_clear(0, 'end')
                lst.selection_set(active)
            lst.yview_moveto(top)
            lst.after_idle(_draw_dots)

        def _toggle_phone_index(idx: int) -> None:
            if idx < 0 or idx >= len(items):
                return
            if idx in checked_idx:
                checked_idx.remove(idx)
            else:
                checked_idx.add(idx)
            _render_phone_list(keep_view=True)
            _update_count()

        _render_phone_list(keep_view=False)

        def _on_click_phone(event):
            idx = int(lst.nearest(event.y))
            _toggle_phone_index(idx)
            return 'break'

        def _on_click_gutter(event):
            idx = int(lst.nearest(event.y))
            _toggle_phone_index(idx)
            return 'break'

        def _on_space_phone(_e=None):
            try:
                idx = int(lst.index('active'))
            except Exception:
                idx = -1
            _toggle_phone_index(idx)
            return 'break'

        lst.bind('<ButtonRelease-1>', _on_click_phone)
        lst.bind('<space>', _on_space_phone)
        dot_gutter.bind('<ButtonRelease-1>', _on_click_gutter)
        lst.bind('<Configure>', lambda _e: _draw_dots())

        def _update_count():
            n = len(checked_idx)
            total = len(items)
            if n == 0:
                count_var.set(f'0 selected (all {total} will be sent)')
            else:
                count_var.set(f'{n} of {total} selected')

        _update_count()

        # Select-all / Select-none helpers
        def _all():
            checked_idx.clear()
            checked_idx.update(range(len(items)))
            _render_phone_list(keep_view=True)
            _update_count()

        def _none():
            checked_idx.clear()
            _render_phone_list(keep_view=True)
            _update_count()

        btn_y = WIN_H - 54
        sel_row = tk.Frame(dlg, bg=BG)
        sel_row.place(x=8, y=btn_y - 22, width=WIN_W - 16, height=20)
        tk.Label(sel_row, text='Select:', bg=BG, font=_FS).pack(side='left')
        lnk_all = tk.Label(sel_row, text='All', bg=BG, font=_FS, fg=LINK,
                           cursor='hand2')
        lnk_all.pack(side='left', padx=(4, 0))
        lnk_all.bind('<Button-1>', lambda _e: _all())
        tk.Label(sel_row, text=' / ', bg=BG, font=_FS).pack(side='left')
        lnk_none = tk.Label(sel_row, text='None', bg=BG, font=_FS, fg=LINK,
                            cursor='hand2')
        lnk_none.pack(side='left')
        lnk_none.bind('<Button-1>', lambda _e: _none())

        def _save():
            checked = {items[i][0] for i in checked_idx if 0 <= i < len(items)}
            # If all are checked, store empty set (means "send all")
            if len(checked) == 0 or len(checked) == len(items):
                selected_list: list = []
                self._phone_contacts = set()
            else:
                selected_list = sorted(checked)
                self._phone_contacts = checked

            # Persist to config.json under contacts.selected_contacts
            cfg = _load_cfg()
            contacts_cfg = cfg.setdefault('contacts', {})
            contacts_cfg['selected_contacts'] = selected_list
            # Keep UI list unfiltered; apply subset to handset only.
            contacts_cfg['selected_only'] = False
            contacts_cfg['force_selected_only'] = bool(selected_list)
            contacts_cfg['selected_prioritize'] = False
            _save_cfg(cfg)

            # Notify orchestrator to refresh contacts on the phone
            if self.on_config_save:
                self.on_config_save({
                    '__action__': 'refresh_contacts',
                    'selected_contacts': selected_list,
                })

            dlg.destroy()

        def _cancel():
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.place(x=0, y=btn_y, width=WIN_W, height=40)
        _xp_button(btn_row, 'OK',     _save,   width=8).pack(
            side='left', padx=(WIN_W // 2 - 80, 4))
        _xp_button(btn_row, 'Cancel', _cancel, width=8).pack(side='left')

        dlg.bind('<Return>', lambda _e: _save())
        dlg.bind('<Escape>', lambda _e: _cancel())

        # Centre over main window
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + self.root.winfo_width()  // 2 - WIN_W // 2
        y = self.root.winfo_rooty() + self.root.winfo_height() // 2 - WIN_H // 2
        dlg.geometry(f'+{x}+{y}')
        dlg.deiconify()
        dlg.after(0, _draw_dots)
        dlg.wait_window()

    def _menu_telegram_settings(self) -> None:
        cfg = _load_cfg()
        tg  = cfg.get('telegram', {})
        res = _dialog(self.root, 'Telegram settings', [
            ('API ID:',       'api_id',       tg.get('api_id',       '')),
            ('API Hash:',     'api_hash',     tg.get('api_hash',     ''), '*'),
            ('Phone number:', 'phone',        tg.get('phone',        '')),
            ('Session name:', 'session_name', tg.get('session_name', 'skype_session')),
        ], width=380)
        if not res:
            return
        try:
            api_id = int(res['api_id']) if res['api_id'] else 0
        except ValueError:
            messagebox.showerror('Error', 'API ID must be a number.', parent=self.root)
            return
        tg['api_id']       = api_id
        tg['api_hash']     = res['api_hash']
        tg['phone']        = res['phone']
        tg['session_name'] = res['session_name'] or 'skype_session'
        cfg['telegram']    = tg
        _save_cfg(cfg)
        messagebox.showinfo('Telegram',
            'Settings saved.\nRestart with --platform telegram_private to connect.',
            parent=self.root)
        if self.on_config_save:
            self.on_config_save(cfg)

    def _menu_audio_settings(self) -> None:
        cfg   = _load_cfg()
        audio = cfg.get('audio', {})
        res   = _dialog(self.root, 'Audio settings', [
            ('Sample rate (Hz):', 'sample_rate', audio.get('sample_rate', 16000)),
            ('Channels:',         'channels',    audio.get('channels',    1)),
            ('Chunk size:',       'chunk_size',  audio.get('chunk_size',  960)),
        ])
        if not res:
            return
        try:
            cfg['audio'] = {
                'sample_rate': int(res['sample_rate']),
                'channels':    int(res['channels']),
                'chunk_size':  int(res['chunk_size']),
            }
        except ValueError:
            messagebox.showerror('Error', 'All audio values must be integers.',
                                 parent=self.root)
            return
        _save_cfg(cfg)
        messagebox.showinfo('Audio', 'Audio settings saved.\nRestart to apply.',
                            parent=self.root)
        if self.on_config_save:
            self.on_config_save(cfg)

    def _menu_hid_settings(self) -> None:
        cfg = _load_cfg()
        hid = cfg.get('hid', {})
        res = _dialog(self.root, 'HID / Handset settings', [
            ('Transport mode\n(dual/feature_only/output_only):',
             'transport_mode',     hid.get('transport_mode',     'dual')),
            ('Keepalive interval (s):',
             'keepalive_interval', hid.get('keepalive_interval', 1.6)),
            ('Q9→Q10 delay (s):',
             'q9_q10_delay',       hid.get('q9_q10_delay',       0.2)),
        ], width=400)
        if not res:
            return
        try:
            cfg['hid'] = {
                'transport_mode':     res['transport_mode'] or 'dual',
                'keepalive_interval': float(res['keepalive_interval']),
                'q9_q10_delay':       float(res['q9_q10_delay']),
            }
        except ValueError:
            messagebox.showerror('Error', 'Numeric fields must be numbers.',
                                 parent=self.root)
            return
        _save_cfg(cfg)
        messagebox.showinfo('HID', 'Handset settings saved.\nRestart to apply.',
                            parent=self.root)
        if self.on_config_save:
            self.on_config_save(cfg)

    def _toggle_record_calls(self) -> None:
        """Toggle call recording on/off and persist to config."""
        enabled = self._record_calls_var.get()
        cfg = _load_cfg()
        rec = cfg.setdefault('recording', {})
        rec['auto_record_calls'] = enabled
        _save_cfg(cfg)
        if self.on_config_save:
            self.on_config_save(cfg)

    def _menu_call_someone(self) -> None:
        name = simpledialog.askstring(
            'Call someone',
            'Enter username or phone number:',
            parent=self.root)
        if name and name.strip():
            self._dial(name.strip())

    def _menu_edit_config(self) -> None:
        try:
            with open(_CFG_PATH, encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror('Error', str(e), parent=self.root)
            return

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title('config.json')
        win.geometry('490x440')
        win.grab_set()
        win.configure(bg=BG)
        self._decorate_luna_toplevel(win, 'config.json', 490, 440)

        from tkinter import scrolledtext
        txt = scrolledtext.ScrolledText(win, font=('Courier New', 9), wrap='none',
                                        bg='white', fg='black', bd=2, relief='sunken')
        txt.place(x=6, y=32, width=478, height=360)
        txt.insert('1.0', content)

        def _save():
            try:
                new_cfg = json.loads(txt.get('1.0', 'end'))
            except json.JSONDecodeError as e:
                messagebox.showerror('JSON error', str(e), parent=win)
                return
            _save_cfg(new_cfg)
            if self.on_config_save:
                self.on_config_save(new_cfg)
            messagebox.showinfo('Saved', 'config.json saved.\nRestart to apply.',
                                parent=win)
            win.destroy()

        btn_row = tk.Frame(win, bg=BG)
        btn_row.place(x=6, y=398, height=34)
        _xp_button(btn_row, 'Save',   _save,       width=8).pack(side='left', padx=(0, 6))
        _xp_button(btn_row, 'Cancel', win.destroy, width=8).pack(side='left')
        win.deiconify()

    def _menu_about(self) -> None:
        cfg = _load_cfg()
        platform = cfg.get('platform', 'local')
        messagebox.showinfo('About Not Skype',
            f'Not Skype™ 2.x UI clone\n'
            f'XP Luna theme by B00merang-Project\n\n'
            f'Platform: {platform}\n'
            f'Username: {self.username}\n\n'
            f'CIT200 handset support via hidapi.\n'
            f'Telegram via Telethon + NTgCalls.',
            parent=self.root)

    # ══════════════════════════════════════════════════════════════════════════
    # Status picker
    # ══════════════════════════════════════════════════════════════════════════

    def _open_status_menu(self, anchor_widget) -> None:
        m = tk.Menu(self.root, tearoff=0,
                    bg='white', fg='black',
                    activebackground=SEL_BG, activeforeground=SEL_FG,
                    bd=1, relief='solid', font=_F)
        for s in _STATUS_OPTIONS:
            col = _STATUS_COLORS.get(s, '#888888')
            m.add_command(
                label=f'●  {s}',
                foreground=col,
                command=lambda st=s: self._set_own_status(st),
            )
        try:
            x = anchor_widget.winfo_rootx()
            y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _set_own_status(self, status: str) -> None:
        self._status_text = status
        self._update_status_bar()
        if self.on_status_change:
            self.on_status_change(status)

    # ══════════════════════════════════════════════════════════════════════════
    # Button handlers
    # ══════════════════════════════════════════════════════════════════════════

    def _on_green_btn(self) -> None:
        if self.state == 'calling' and self._call_mode == 'incoming':
            name: str = self.active_contact or ''
            self._call_mode = 'in_call'
            self._switch('in_call')
            if self.on_call_answer:
                self.on_call_answer(name)
        elif self.state in ('log', 'friends') and self.active_contact:
            self._dial(self.active_contact)

    def _on_red_btn(self) -> None:
        # Always attempt to end call regardless of UI state — the backend
        # hangup is idempotent and harmless when not in a call.
        contact = self.active_contact
        secs    = self._call_secs
        was_in_call = self.state in ('calling', 'in_call')
        if was_in_call:
            self._do_end_call_cleanup()
        if self.on_call_end and contact is not None:
            self.on_call_end(contact, secs)

    def _dial(self, name: str) -> None:
        self.active_contact = name
        self._call_mode     = 'outgoing'
        self._switch('calling')
        if self.on_call_start:
            self.on_call_start(name)

    # ══════════════════════════════════════════════════════════════════════════
    # State machine
    # ══════════════════════════════════════════════════════════════════════════

    def _switch(self, state: str) -> None:
        prev = self.state
        self.state = state
        if state == 'in_call' and prev != 'in_call':
            self._start_timer()
        elif state not in ('calling', 'in_call'):
            self._stop_timer()
        self._render()
        if self.on_state_change:
            self.on_state_change(state)

    def _do_end_call_cleanup(self) -> None:
        self._stop_timer()
        self._call_mode     = 'idle'
        self.active_contact = None
        self._recording_active = False
        self._switch('log')

    def _start_timer(self) -> None:
        self._call_secs = 0
        self._tick()

    def _stop_timer(self) -> None:
        if self._timer_id:
            self.root.after_cancel(self._timer_id)
            self._timer_id = None

    def _tick(self) -> None:
        self._call_secs += 1
        if self._dur_var:
            m, s = divmod(self._call_secs, 60)
            self._dur_var.set(f'Call duration: {m:02d}:{s:02d}')
        self._timer_id = self.root.after(1000, self._tick)

    # ══════════════════════════════════════════════════════════════════════════
    # Render
    # ══════════════════════════════════════════════════════════════════════════

    def _render(self) -> None:
        _clear(self._tab_strip)
        _clear(self._pane)

        # ── Mic mode: minimal UI ──────────────────────────────────────
        if self._mic_mode:
            self._draw_mic_mode()
            self._update_status_bar(call_contact='')
            self._rebuild_hang_btn(active=False)
            return

        s = self.state
        c = self.active_contact or ''

        # Single tab row (all tabs side-by-side).
        row = tk.Frame(self._tab_strip, bg=TAB_ROW)
        row.pack(fill='x', side='top')

        self._tab(row, '\u26a1 Start', False, self._menu_call_someone)
        self._tab(row, '\U0001f4cb Log', s == 'log', lambda: self._switch('log'))
        self._tab(row, '\U0001f464 Friends', s == 'friends', lambda: self._switch('friends'))

        if c and self._call_mode != 'idle':
            # Truncate call tab label so it doesn't overflow the tab strip.
            max_label = 12
            clabel = c if len(c) <= max_label else c[:max_label - 1] + '\u2026'
            self._tab(
                row,
                f'\u260e {clabel}',
                s in ('calling', 'in_call'),
                lambda: self._switch(
                    'calling' if self._call_mode in ('incoming', 'outgoing')
                    else 'in_call'
                ),
            )

        if s == 'log':
            self._draw_log()
        elif s == 'friends':
            self._draw_friends()
        elif s == 'calling':
            self._draw_calling()
        else:
            self._draw_in_call()

        self._update_status_bar(
            call_contact=c if s in ('calling', 'in_call') else '')

        # Rebuild hang-up button color
        in_call = s in ('calling', 'in_call')
        self._rebuild_hang_btn(active=in_call)

    # ══════════════════════════════════════════════════════════════════════════
    # Views
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_mic_mode(self) -> None:
        """Render the minimal mic-mode pane — handset as mic/speaker."""
        p = self._pane

        # Blue header banner
        banner = _gradient_canvas(
            p, self.W, 28,
            [(0.0, '#1E55B0'), (1.0, '#316AC5')]
        )
        banner.pack(fill='x')
        banner.create_text(self.W // 2, 14, anchor='center',
                           text='Handset Microphone Mode',
                           fill='white', font=_FB)

        # Spacer
        tk.Frame(p, bg=PANEL, height=20).pack(fill='x')

        # Mic icon
        mic_cv = tk.Canvas(p, width=64, height=64, bg=PANEL,
                           highlightthickness=0, bd=0)
        mic_cv.pack(pady=(10, 6))
        mic_cv.create_oval(8, 8, 56, 56, fill='#316AC5', outline='#1E4F8F', width=2)
        mic_cv.create_text(32, 32, text='\U0001f399', font=('Segoe UI Emoji', 22),
                           fill='white')

        # Status text
        tk.Label(p, text='Audio passthrough active',
                 bg=PANEL, fg='#006600', font=_FB).pack(pady=(4, 2))
        tk.Label(p, text='Handset mic and speaker are live.\n'
                         'Click the mic button again to stop.',
                 bg=PANEL, fg=GREY_TXT, font=_FS, justify='center').pack(pady=(0, 10))

        # Spacer fill
        tk.Frame(p, bg=PANEL).pack(fill='both', expand=True)

    def _draw_log(self) -> None:
        p = self._pane

        if self._call_mode != 'idle' and self.active_contact:
            # XP Luna blue active-call banner
            banner = _gradient_canvas(
                p, self.W, 24,
                [(0.0, '#1E55B0'), (1.0, '#316AC5')]
            )
            banner.pack(fill='x')
            verb = ('Incoming' if self._call_mode == 'incoming' else
                    'Calling'  if self._call_mode == 'outgoing' else 'In call with')
            banner.create_text(8, 12, anchor='w',
                               text=f'  {verb}: {self.active_contact}',
                               fill='white', font=_FB)
            target = ('calling' if self._call_mode in ('incoming', 'outgoing')
                      else 'in_call')
            tk.Button(p, text='Resume', bg='#4A80D0', fg='white', bd=0,
                      font=_FS, cursor='hand2', relief='flat',
                      command=lambda t=target: self._switch(t)).place(
                x=self.W - 62, y=3, width=54, height=18)

        tk.Label(p, text='You have', bg=PANEL,
                 font=_FB).pack(anchor='w', padx=10, pady=(10, 6))

        row1 = tk.Frame(p, bg=PANEL)
        row1.pack(fill='x', padx=8, pady=(0, 2))
        _make_icon_canvas(row1, 32, 'phone', bg=PANEL).pack(
            side='left', padx=(0, 8), pady=2)
        info1 = tk.Frame(row1, bg=PANEL)
        info1.pack(side='left', fill='x', expand=True, anchor='n')
        tk.Label(info1, text=f'{len(self.missed_calls)} missed calls',
                 bg=PANEL, font=_FB).pack(anchor='w')
        from_f = tk.Frame(info1, bg=PANEL)
        from_f.pack(anchor='w', fill='x')
        tk.Label(from_f, text='From: ', bg=PANEL, font=_FS,
                 fg=GREY_TXT).pack(side='left')
        self._pack_missed_links(from_f)

        # XP-style sunken divider
        tk.Frame(p, bg=BTN_DARK,   height=1).pack(fill='x', padx=6, pady=(6, 0))
        tk.Frame(p, bg=BTN_HILITE, height=1).pack(fill='x', padx=6, pady=(0, 6))

        row2 = tk.Frame(p, bg=PANEL)
        row2.pack(fill='x', padx=8, pady=(0, 2))
        _make_icon_canvas(row2, 32, 'friends', bg=PANEL).pack(
            side='left', padx=(0, 8), pady=2)
        online = sum(1 for ct in self.contacts if ct.get('online'))
        total  = len(self.contacts)
        lbl2 = tk.Label(row2, text=f'{online} friends online',
                        bg=PANEL, font=_FB, cursor='hand2', fg=LINK)
        lbl2.pack(side='left', anchor='w')
        lbl2.bind('<Button-1>', lambda e: self._switch('friends'))

        # ── Previous calls section ────────────────────────────────────
        if self.call_history:
            # XP-style sunken divider
            tk.Frame(p, bg=BTN_DARK,   height=1).pack(fill='x', padx=6, pady=(6, 0))
            tk.Frame(p, bg=BTN_HILITE, height=1).pack(fill='x', padx=6, pady=(0, 4))

            tk.Label(p, text='Previous calls', bg=PANEL,
                     font=_FB).pack(anchor='w', padx=10, pady=(0, 2))

            # Scrollable frame for call history entries
            hist_outer = tk.Frame(p, bg=PANEL)
            hist_outer.pack(fill='both', expand=True, padx=6)
            hist_canvas = tk.Canvas(hist_outer, bg='white', bd=1, relief='sunken',
                                    highlightthickness=0)
            hist_sb = tk.Scrollbar(hist_outer, orient='vertical',
                                   command=hist_canvas.yview)
            hist_inner = tk.Frame(hist_canvas, bg='white')

            hist_inner.bind(
                '<Configure>',
                lambda e: hist_canvas.configure(scrollregion=hist_canvas.bbox('all'))
            )
            hist_canvas.create_window((0, 0), window=hist_inner, anchor='nw')
            hist_canvas.configure(yscrollcommand=hist_sb.set)

            hist_canvas.pack(side='left', fill='both', expand=True)
            # Only show scrollbar if there are enough entries
            if len(self.call_history) > 6:
                hist_sb.pack(side='right', fill='y')

            # Render entries (most recent first)
            for entry in reversed(self.call_history):
                ctype = entry.get('type', 'outgoing')
                name  = entry.get('name', '?')
                ts    = entry.get('timestamp', 0.0)
                dur   = entry.get('duration_secs', 0)

                row_bg = 'white'
                row = tk.Frame(hist_inner, bg=row_bg)
                row.pack(fill='x', padx=2, pady=1)

                # Type indicator
                if ctype == 'missed':
                    arrow = '\u2199'   # down-left arrow (missed)
                    arrow_fg = '#CC0000'
                elif ctype == 'incoming':
                    arrow = '\u2199'   # down-left arrow (incoming)
                    arrow_fg = '#006600'
                else:
                    arrow = '\u2197'   # up-right arrow (outgoing)
                    arrow_fg = GREY_TXT
                tk.Label(row, text=arrow, fg=arrow_fg, bg=row_bg,
                         font=_F, width=2).pack(side='left')

                # Contact name (clickable)
                name_lbl = tk.Label(row, text=name, fg=LINK, bg=row_bg,
                                    font=_FS, cursor='hand2', anchor='w')
                name_lbl.pack(side='left', padx=(0, 4))
                name_lbl.bind('<Button-1>',
                              lambda e, n=name: self._select_contact(n))

                # Time + duration on the right
                if ts > 0:
                    dt = datetime.datetime.fromtimestamp(ts)
                    time_str = dt.strftime('%H:%M')
                else:
                    time_str = ''
                if dur > 0:
                    dm, ds = divmod(dur, 60)
                    dur_str = f'{dm}:{ds:02d}'
                    detail = f'{time_str}  {dur_str}'
                elif ctype == 'missed':
                    detail = f'{time_str}  missed'
                else:
                    detail = time_str
                tk.Label(row, text=detail, fg=GREY_TXT, bg=row_bg,
                         font=_FS, anchor='e').pack(side='right', padx=(0, 2))
        else:
            tk.Frame(p, bg=PANEL).pack(fill='both', expand=True)

        tk.Label(p, text=f'{total} users online', bg=PANEL,
                 font=_FS, fg=GREY_TXT).pack(anchor='w', padx=10, pady=(0, 2))

        # XP-style search / quick-dial entry
        placeholder = 'Type a name or number…'
        ent = tk.Entry(p, fg='#999999', font=_FS, bd=2, relief='sunken',
                       bg='white', insertbackground='black')
        ent.insert(0, placeholder)

        def _fi(_e):
            if ent.get() == placeholder:
                ent.delete(0, 'end'); ent.config(fg='black')

        def _fo(_e):
            if not ent.get():
                ent.insert(0, placeholder); ent.config(fg='#999999')

        def _search_or_dial(_e=None):
            val = ent.get().strip()
            if not val or val == placeholder:
                return
            matches = [c['name'] for c in self.contacts
                       if val.lower() in c['name'].lower()]
            if matches:
                self._select_contact(matches[0])
                self._render()
            else:
                self._dial(val)

        def _key(_e):
            val = ent.get().strip()
            if val and val != placeholder:
                matches = [c['name'] for c in self.contacts
                           if val.lower() in c['name'].lower()]
                if matches:
                    self._select_contact(matches[0])

        ent.bind('<FocusIn>',   _fi)
        ent.bind('<FocusOut>',  _fo)
        ent.bind('<Return>',    _search_or_dial)
        ent.bind('<KeyRelease>', _key)
        ent.pack(fill='x', padx=6, pady=(0, 6))

    def _pack_missed_links(self, parent: tk.Frame) -> None:
        names = self.missed_calls
        line1 = names[:3]; line2 = names[3:]
        for i, name in enumerate(line1):
            lnk = tk.Label(parent, text=name, fg=LINK, bg=PANEL,
                           font=_FS, cursor='hand2')
            lnk.pack(side='left')
            lnk.bind('<Button-1>', lambda e, n=name: self._select_contact(n))
            if i < len(line1) - 1 or line2:
                tk.Label(parent, text=',', bg=PANEL,
                         font=_FS, fg='#333333').pack(side='left')
        if line2:
            row2 = tk.Frame(parent.master, bg=PANEL)
            row2.pack(anchor='w')
            tk.Label(row2, text='and ', bg=PANEL,
                     font=_FS, fg='#333333').pack(side='left')
            for i, name in enumerate(line2):
                lnk = tk.Label(row2, text=name, fg=LINK, bg=PANEL,
                               font=_FS, cursor='hand2')
                lnk.pack(side='left')
                lnk.bind('<Button-1>',
                         lambda e, n=name: self._select_contact(n))
                if i < len(line2) - 1:
                    tk.Label(row2, text=',', bg=PANEL, font=_FS).pack(side='left')
            tk.Label(row2, text='.', bg=PANEL, font=_FS).pack(side='left')

    def _draw_friends(self) -> None:
        if not self.contacts:
            tk.Label(self._pane, text='Loading contacts…',
                     bg=PANEL, fg=GREY_TXT, font=_F).pack(pady=20)
            return

        # Scrollable container: Canvas + Scrollbar + inner Frame
        outer = tk.Frame(self._pane, bg=PANEL, bd=0)
        outer.pack(fill='both', expand=True)

        sb = tk.Scrollbar(outer, orient='vertical')
        sb.pack(side='right', fill='y')

        canvas = tk.Canvas(outer, bg=PANEL, yscrollcommand=sb.set,
                           highlightthickness=0, bd=0)
        canvas.pack(side='left', fill='both', expand=True)
        sb.config(command=canvas.yview)

        inner = tk.Frame(canvas, bg=PANEL)
        win_id = canvas.create_window((0, 0), window=inner, anchor='nw')

        # Stretch inner frame to canvas width
        def _on_canvas_resize(event, cid=win_id):
            canvas.itemconfig(cid, width=event.width)
        canvas.bind('<Configure>', _on_canvas_resize)

        # Update scroll region when inner frame changes size
        def _on_frame_configure(_e):
            canvas.configure(scrollregion=canvas.bbox('all'))
        inner.bind('<Configure>', _on_frame_configure)

        # Mouse-wheel scrolling (Windows delta is ±120 per notch)
        def _on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), 'units')
        canvas.bind('<MouseWheel>', _on_mousewheel)
        inner.bind('<MouseWheel>', _on_mousewheel)

        def _bind_wheel(widget):
            widget.bind('<MouseWheel>', _on_mousewheel)
            for child in widget.winfo_children():
                _bind_wheel(child)

        for contact in self.contacts:
            self._contact_row(inner, contact)
            _bind_wheel(inner.winfo_children()[-1])

    # Redaction block character and fixed display width (chars)
    _REDACT_CHAR  = '\u2588'   # █
    _REDACT_WIDTH = 12         # always show 12 block chars

    def _contact_row(self, parent: tk.Frame, contact: dict) -> None:
        name    = contact['name']
        online  = contact.get('online', False)
        hidden  = name in self._hidden_contacts
        sel     = (name == self.active_contact)
        bg_n    = SEL_BG if sel else PANEL
        fg_n    = SEL_FG if sel else 'black'

        row = tk.Frame(parent, bg=bg_n)
        row.pack(fill='x')

        # Presence dot — always shown (grey when hidden so status is also masked)
        dot_color  = ('#AAAAAA' if hidden else (GREEN_DOT if online else '#AAAAAA'))
        dot_border = 'white' if sel else ''
        dot = tk.Canvas(row, width=12, height=12,
                        bg=bg_n, highlightthickness=0)
        dot.pack(side='left', padx=(10, 4), pady=6)
        dot.create_oval(1, 1, 11, 11, fill=dot_color,
                        outline=dot_border, width=1 if sel else 0)
        if online and not hidden and not sel:
            dot.create_oval(2, 2, 6, 6, fill='#7FE87F', outline='')

        # Display text: real name or fixed-width block string
        display = (self._REDACT_CHAR * self._REDACT_WIDTH) if hidden else name
        lbl_fg  = (GREY_TXT if (hidden and not sel) else fg_n)
        lbl = tk.Label(row, text=display, bg=bg_n, fg=lbl_fg,
                       font=_F, anchor='w')
        lbl.pack(side='left', fill='x', expand=True, pady=6)

        def _redraw_dot(d=dot, color=dot_color, outlined=False):
            d.delete('all')
            d.create_oval(1, 1, 11, 11, fill=color,
                          outline='white' if outlined else '',
                          width=1 if outlined else 0)

        if hidden:
            # Hidden rows: hover highlight still works but no click/dial action
            def _enter_h(_, r=row, d=dot, l=lbl):
                for w in (r, d, l):
                    w.config(bg=SEL_BG)
                l.config(fg=SEL_FG)
                _redraw_dot(d, dot_color, outlined=True)

            def _leave_h(_, r=row, d=dot, l=lbl):
                for w in (r, d, l):
                    w.config(bg=bg_n)
                l.config(fg=lbl_fg)
                _redraw_dot(d, dot_color, outlined=sel)

            for w in (row, dot, lbl):
                w.bind('<Enter>', _enter_h)
                w.bind('<Leave>', _leave_h)
        else:
            def _enter(_, r=row, d=dot, l=lbl):
                for w in (r, d, l):
                    w.config(bg=SEL_BG)
                l.config(fg=SEL_FG)
                _redraw_dot(d, dot_color, outlined=True)

            def _leave(_, r=row, d=dot, l=lbl, b=bg_n, f=fg_n):
                for w in (r, d, l):
                    w.config(bg=b)
                l.config(fg=f)
                _redraw_dot(d, dot_color, outlined=sel)

            def _click(_, n=name):
                self._select_contact(n)
                self._render()

            def _dbl(_, n=name):
                self._select_contact(n)
                self._dial(n)

            for w in (row, dot, lbl):
                w.bind('<Enter>',           _enter)
                w.bind('<Leave>',           _leave)
                w.bind('<Button-1>',        _click)
                w.bind('<Double-Button-1>', _dbl)

    def _draw_calling(self) -> None:
        f = tk.Frame(self._pane, bg=PANEL)
        f.pack(expand=True, fill='both')

        # Gradient header strip
        hdr = _gradient_canvas(f, self.W, 46,
                               [(0.0, TB_TOP), (0.5, TB_MID), (1.0, TB_BOT)])
        hdr.pack(fill='x')
        incoming = self._call_mode == 'incoming'
        hdr_text = 'Incoming call' if incoming else 'Calling…'
        hdr.create_text(self.W // 2, 23, text=hdr_text, fill='white', font=_FB)

        _make_silhouette_canvas(f, 110).pack(pady=(14, 6))

        tk.Label(f, text=self.active_contact or '',
                 bg=PANEL, font=_FL).pack()
        sub = 'Press Answer or the green button' if incoming else 'Ringing…'
        tk.Label(f, text=sub, bg=PANEL, font=_FS, fg=GREY_TXT).pack(pady=2)

        if self._recording_active:
            tk.Label(
                f,
                text='● Recording',
                bg=PANEL,
                fg='#CC0000',
                font=('Tahoma', 8, 'bold')
            ).pack(pady=(2, 0))

        if incoming:
            btn_row = tk.Frame(f, bg=PANEL)
            btn_row.pack(pady=(14, 0))
            _xp_button(btn_row, 'Answer', self._on_green_btn,
                       bg='#1A7A1A', fg='white', padx=14, pady=5).pack(
                side='left', padx=8)
            _xp_button(btn_row, 'Ignore', self._on_red_btn,
                       bg='#AA1111', fg='white', padx=14, pady=5).pack(
                side='left', padx=8)

    def _draw_in_call(self) -> None:
        self._dur_var = tk.StringVar(value='Call duration: 00:00')
        f = tk.Frame(self._pane, bg=PANEL)
        f.pack(expand=True, fill='both')

        # Gradient header
        hdr = _gradient_canvas(f, self.W, 46,
                               [(0.0, '#1A7A1A'), (0.5, '#158015'), (1.0, '#0E5C0E')])
        hdr.pack(fill='x')
        hdr.create_text(self.W // 2, 23, text='Call connected', fill='white', font=_FB)

        _make_silhouette_canvas(f, 110).pack(pady=(14, 6))

        tk.Label(f, text=self.active_contact or '', bg=PANEL, font=_FL).pack()
        tk.Label(f, textvariable=self._dur_var, bg=PANEL,
                 font=_FS, fg=GREY_TXT).pack(pady=2)
        if self._recording_active:
            tk.Label(
                f,
                text='● Recording call',
                bg=PANEL,
                fg='#CC0000',
                font=('Tahoma', 8, 'bold')
            ).pack(pady=(2, 0))

    # ══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _tab(self, parent, text, active, cmd) -> None:
        """XP-style notebook tab — canvas-drawn for pixel-perfect Luna look.

        Active tab: white face, 3-D raised edges, Luna-blue 2px top accent,
        bottom edge open (connects flush to content pane).
        Inactive tab: grey D4D0C8 face, 3-D raised but shorter, bottom closed.
        """
        # Measure text to size the canvas
        _measure = tk.Label(parent, text=text, font=_FS)
        _measure.update_idletasks()
        tw = _measure.winfo_reqwidth()
        th = _measure.winfo_reqheight()
        _measure.destroy()

        pad_x = 8       # horizontal padding inside tab
        w = tw + pad_x * 2
        h_active = th + 8    # taller for active tab
        h_inactive = th + 6
        h = h_active if active else h_inactive

        c = tk.Canvas(parent, width=w, height=h, bg=TAB_ROW,
                      highlightthickness=0, bd=0)
        c.pack(side='left', padx=(1, 0), pady=(1, 0))

        face = TAB_ACT if active else TAB_INACT

        if active:
            # ── Active tab shape ──────────────────────────────────────────
            # 2px Luna-blue accent across the very top
            c.create_rectangle(1, 0, w - 1, 2, fill=LUNA_BLUE,
                               outline=LUNA_BLUE)
            # Left highlight edge (white)
            c.create_line(0, 2, 0, h, fill=BTN_HILITE, width=1)
            # Right dark edge (outer)
            c.create_line(w - 1, 2, w - 1, h, fill=BTN_DDARK, width=1)
            # Right shadow edge (inner)
            c.create_line(w - 2, 3, w - 2, h, fill=BTN_DARK, width=1)
            # Face fill (no bottom line — tab merges into pane)
            c.create_rectangle(1, 2, w - 2, h, fill=face, outline=face)
            # Re-draw left highlight on top of fill
            c.create_line(0, 2, 0, h, fill=BTN_HILITE, width=1)
            # Re-draw right edges on top of fill
            c.create_line(w - 1, 2, w - 1, h, fill=BTN_DDARK, width=1)
            c.create_line(w - 2, 3, w - 2, h, fill=BTN_DARK, width=1)
            # Top accent again (on top of fill)
            c.create_rectangle(1, 0, w - 1, 2, fill=LUNA_BLUE,
                               outline=LUNA_BLUE)
            # Text
            c.create_text(w // 2, (h + 2) // 2, text=text, font=_FS,
                          fill='black', anchor='center')
        else:
            # ── Inactive tab shape ────────────────────────────────────────
            y0 = 2   # offset down so inactive is shorter than active
            # Left highlight
            c.create_line(0, y0, 0, h, fill=BTN_HILITE, width=1)
            # Top highlight
            c.create_line(0, y0, w - 1, y0, fill=BTN_HILITE, width=1)
            # Right dark edge (outer)
            c.create_line(w - 1, y0, w - 1, h, fill=BTN_DDARK, width=1)
            # Right shadow (inner)
            c.create_line(w - 2, y0 + 1, w - 2, h, fill=BTN_DARK, width=1)
            # Bottom dark edge (closed — not connected to pane)
            c.create_line(0, h - 1, w, h - 1, fill=BTN_DARK, width=1)
            # Face fill
            c.create_rectangle(1, y0 + 1, w - 2, h - 1, fill=face,
                               outline=face)
            # Re-draw edges on top of fill
            c.create_line(0, y0, 0, h, fill=BTN_HILITE, width=1)
            c.create_line(0, y0, w - 1, y0, fill=BTN_HILITE, width=1)
            c.create_line(w - 1, y0, w - 1, h, fill=BTN_DDARK, width=1)
            c.create_line(w - 2, y0 + 1, w - 2, h, fill=BTN_DARK, width=1)
            c.create_line(0, h - 1, w, h - 1, fill=BTN_DARK, width=1)
            # Text
            c.create_text(w // 2, (y0 + h) // 2, text=text, font=_FS,
                          fill=GREY_TXT, anchor='center')

        # Click binding on the whole canvas
        c.bind('<Button-1>', lambda e: cmd())
        c.configure(cursor='hand2')

    def _select_contact(self, name: str) -> None:
        self.active_contact = name
        if self.on_contact_sel:
            self.on_contact_sel(name)

    def _update_status_bar(self, call_contact: str = '') -> None:
        if call_contact:
            self._sb_var.set(f'\u260e  Call with {call_contact}')
            return
        label = self._status_text
        if self._platform_label and self._platform_label != 'local':
            pretty = {
                'telegram_private': 'Telegram',
                'telegram_ntg':     'Telegram',
                'telegram':         'Telegram',
                'discord':          'Discord',
            }.get(self._platform_label, self._platform_label)
            label = f'{label}  ·  {pretty}'
        self._sb_var.set(label)


# ══════════════════════════════════════════════════════════════════════════════
# Demo entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    prepare_windows_dpi_awareness()
    root = tk.Tk()
    app  = SkypeUI(root)
    app.update_contacts([
        {'name': 'pamela',      'online': True},
        {'name': 'andrew',      'online': True},
        {'name': 'skype_lover', 'online': True},
        {'name': 'skype_rocks', 'online': False},
        {'name': 'catherine',   'online': True},
        {'name': 'hilary',      'online': False},
    ])
    app.on_call_start    = lambda n:    print(f'[call_start]  {n}')
    app.on_call_answer   = lambda n:    print(f'[call_answer] {n}')
    app.on_call_end      = lambda n, s: print(f'[call_end]    {n}  {s}s')
    app.on_contact_sel   = lambda n:    print(f'[contact_sel] {n}')
    app.on_state_change  = lambda st:   print(f'[state]       {st}')
    app.on_status_change = lambda st:   print(f'[status]      {st}')
    app.on_config_save   = lambda cfg:  print(f'[config_save] {list(cfg.keys())}')
    root.mainloop()


if __name__ == '__main__':
    main()
