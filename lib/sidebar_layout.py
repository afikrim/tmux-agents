"""Sidebar sizing and private, per-server preferences.

Call mutations while holding the existing ensure lock. Geometry observations
distinguish an intentional pane resize from a terminal or topology change.
"""
import json
import os

from . import store

DEFAULTS = {'width': 32, 'min_width': 24}
MIN_SETTING = 16
MAX_SETTING = 160
MIN_MAIN = 39
MIN_WINDOW = 72


def _read(path):
    try:
        with open(path) as f: raw = f.read(1024*1024+1)
        data = json.loads(raw) if len(raw) <= 1024*1024 else None
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def preferences(paths):
    raw = _read(os.path.join(paths['dir'], 'sidebar-layout.json'))
    try: return validate(raw)
    except ValueError: return dict(DEFAULTS)


def validate(raw):
    result = {k: raw.get(k, v) for k, v in DEFAULTS.items()}
    if any(type(v) is not int or not MIN_SETTING <= v <= MAX_SETTING for v in result.values()):
        raise ValueError('Sidebar widths must be between 16 and 160 columns')
    if result['width'] < result['min_width']:
        raise ValueError('Preferred width must be at least the sidebar minimum')
    return result


def save(paths, settings):
    store.atomic_write(os.path.join(paths['dir'], 'sidebar-layout.json'), validate(settings))


def target(settings, width, diff_space=0):
    available = width - MIN_MAIN - 1 - diff_space
    if width < MIN_WINDOW or available < settings['min_width']:
        return None
    return min(settings['width'], available)


def group(panes):
    windows = {}
    for p in panes:
        info = windows.setdefault(p['window_id'], dict(spec=p['window_id'], width=p['win_width'],
                        height=p.get('win_height', 0), path=p['path'], panes=[]))
        # Linked windows share physical panes across sessions.
        if not any(old['pane_id'] == p['pane_id'] for old in info['panes']):
            info['panes'].append(p)
    return windows


def observation(info, settings):
    side = next((p for p in info['panes'] if p.get('sidebar')), None)
    if side is None or any(p.get('zoomed') for p in info['panes']): return None
    return dict(pane=side['pane_id'], width=side.get('width', 0),
                window_width=info['width'], window_height=info['height'],
                structure=sorted(p['pane_id'] for p in info['panes']),
                diff=[[p['pane_id'], p.get('width', 0)] for p in info['panes'] if p.get('diff')],
                preference=[settings['width'], settings['min_width']])


def full_left(info, pane):
    return (pane.get('left') == 0 and pane.get('top') == 0
            and pane.get('height', 0) == info['height'] and info['height'] > 0)


def prepare(paths, windows, server, attached=()):
    settings = preferences(paths)
    cached = _read(os.path.join(paths['dir'], 'sidebar-geometry.json'))
    previous = cached.get('windows', {}) if cached.get('server') == server else {}
    if not isinstance(previous, dict): previous = {}
    candidates = []
    migrating = not cached and not os.path.exists(os.path.join(paths['dir'], 'sidebar-layout.json'))
    for wid, info in windows.items():
        side = next((p for p in info['panes'] if p.get('sidebar')), None)
        current = observation(info, settings)
        if not side or not current or not full_left(info, side): continue
        old = previous.get(wid)
        manual = (isinstance(old, dict) and current['width'] != old.get('width')
                  and all(current[k] == old.get(k) for k in
                          ('pane','window_width','structure','diff','preference')))
        if manual or migrating:
            candidates.append((wid in attached, side.get('win_active', False), current['width']))
    if candidates:
        chosen = max(candidates)[2]
        requested = max(settings['min_width'], min(MAX_SETTING, chosen))
        if requested != settings['width']:
            settings = dict(settings, width=requested)
            save(paths, settings)
    return settings


def remember(paths, panes, server, settings, observed=None):
    windows = {}
    for wid, info in group(panes).items():
        value = observation(info, settings)
        original = (observed or {}).get(wid)
        if value and original:
            before=observation(original,settings)
            if before and before['pane']==value['pane']:
                # Record what we actually reconciled. A mouse drag or client
                # resize can happen after that snapshot despite our lock.
                value=before
            elif not before:
                value['width']=available_target(original,settings) or value['width']
                value['window_width']=original['width']
        if value: windows[wid] = value
    path = os.path.join(paths['dir'], 'sidebar-geometry.json')
    data = dict(server=server, windows=windows)
    if _read(path) != data: store.atomic_write(path, data)


def available_target(info, settings):
    diff = sum(p.get('width', 0)+1 for p in info['panes'] if p.get('diff'))
    return target(settings, info['width'], diff)
