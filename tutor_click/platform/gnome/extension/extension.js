import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const IFACE = `<node><interface name="dev.handren.TutorClick">
<method name="Call"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
<signal name="Command"><arg type="s"/></signal>
</interface></node>`;
const MODS = new Set([Clutter.KEY_Control_L, Clutter.KEY_Shift_L, Clutter.KEY_Alt_L, Clutter.KEY_Super_L]);

export default class TutorClick extends Extension {
    enable() {
        this._owner = null;
        this._watch = 0;
        this._grabs = new Map();
        this._heldKeys = new Set();
        this._heldButtons = new Set();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/dev/handren/TutorClick');
        this._activated = global.display.connect('accelerator-activated', (_display, action) => {
            const bind = this._grabs.get(action);
            if (bind && this._owner) {
                this._command(bind.down);
            }
        });
        this._deactivated = global.display.connect('accelerator-deactivated', (_display, action) => {
            const bind = this._grabs.get(action);
            if (bind?.up) {
                this._command(bind.up);
            }
        });
    }

    disable() {
        this._disconnect();
        global.display.disconnect(this._activated);
        global.display.disconnect(this._deactivated);
        this._dbus.unexport();
        this._dbus = null;
    }

    _disconnect() {
        this._binds([]);
        this._release();
        this._root?.destroy();
        this._root = null;
        this._pointer?.run_dispose();
        this._keyboard?.run_dispose();
        this._pointer = null;
        this._keyboard = null;
        this._owner = null;
        if (this._watch)
            Gio.bus_unwatch_name(this._watch);
        this._watch = 0;
    }

    _release() {
        for (const b of this._heldButtons)
            this._pointer?.notify_button(GLib.get_monotonic_time(), b, Clutter.ButtonState.RELEASED);
        for (const k of this._heldKeys)
            this._keyboard?.notify_keyval(GLib.get_monotonic_time(), k, Clutter.KeyState.RELEASED);
        this._heldKeys.clear();
        this._heldButtons.clear();
    }

    async CallAsync([text], invocation) {
        try {
            const args = JSON.parse(text);
            const sender = invocation.get_sender();
            if (args.op === 'connect') {
                if (this._owner && this._owner !== sender)
                    throw new Error('Another Tutor Click process is already connected.');
                if (!this._owner) {
                    const backend = global.stage.context?.get_backend() ?? Clutter.get_default_backend();
                    const seat = backend.get_default_seat();
                    this._pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
                    this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
                    this._owner = sender;
                    this._watch = Gio.bus_watch_name_on_connection(Gio.DBus.session, sender,
                        Gio.BusNameWatcherFlags.NONE, null, () => this._disconnect());
                }
            } else if (this._owner !== sender) {
                throw new Error('Connect Tutor Click before using the helper.');
            }
            if (Main.sessionMode.isLocked || Main.sessionMode.currentMode !== 'user') {
                this._release();
                throw new Error('Unlock GNOME before using Tutor Click.');
            }
            const result = await this._call(args);
            invocation.return_value(new GLib.Variant('(s)', [JSON.stringify({result})]));
        } catch (error) {
            this._release();
            invocation.return_value(new GLib.Variant('(s)', [JSON.stringify({error: error.message})]));
        }
    }

    _windows() {
        const workspace = global.workspace_manager.get_active_workspace();
        const windows = global.get_window_actors().map(a => a.meta_window)
            .filter(w => !w.minimized && w.located_on_workspace(workspace) && w.get_frame_rect().width > 1);
        return global.display.sort_windows_by_stacking(windows).reverse();
    }

    _info(w) {
        if (!w)
            return null;
        const r = w.get_frame_rect();
        const app = Shell.WindowTracker.get_default().get_window_app(w);
        return {id: String(w.get_id()), cls: w.get_wm_class() || app?.get_id() || '',
            title: w.get_title() || '', at: [r.x, r.y], size: [r.width, r.height]};
    }

    _window(id) {
        const w = this._windows().find(w => String(w.get_id()) === String(id));
        if (!w)
            throw new Error('That app window is no longer visible.');
        return w;
    }

    _available() {
        if (Main.overview.visible)
            throw new Error('Close the GNOME overview before asking me to act.');
        if (Main.modalCount > 0)
            throw new Error('Close the GNOME system dialog before asking me to act.');
    }

    _at(x, y) {
        this._available();
        const w = this._windows().find(w => {
            const r = w.get_frame_rect();
            return x >= r.x && y >= r.y && x < r.x + r.width && y < r.y + r.height;
        });
        let actor = global.stage.get_actor_at_pos(Clutter.PickMode.REACTIVE, x, y);
        while (actor && actor !== global.window_group && !actor.get_meta_window)
            actor = actor.get_parent();
        if (!actor || (actor !== global.window_group && actor.get_meta_window?.() !== w))
            return null;
        return w;
    }

    async _call(a) {
        switch (a.op) {
        case 'connect': return {version: 1};
        case 'state': {
            const index = global.display.get_current_monitor();
            const r = global.display.get_monitor_geometry(index);
            const [x, y] = global.get_pointer();
            return {monitor: {index, x: r.x, y: r.y, width: r.width, height: r.height,
                scale: global.display.get_monitor_scale(index)}, cursor: [x, y],
            overview: Main.overview.visible, modal: Main.modalCount,
            active: this._info(global.display.focus_window), windows: this._windows().map(w => this._info(w))};
        }
        case 'window-at': return this._info(this._at(a.x, a.y));
        case 'focus': this._available(); this._window(a.id).activate(global.get_current_time()); return true;
        case 'move':
            this._available();
            if (!Number.isFinite(a.x) || !Number.isFinite(a.y))
                throw new Error('Invalid cursor coordinates.');
            this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), a.x, a.y);
            return true;
        case 'button': {
            if (a.down) {
                this._available();
                const [x, y] = global.get_pointer();
                if (Math.abs(x - a.x) > 2 || Math.abs(y - a.y) > 2 ||
                    global.display.focus_window !== this._window(a.id) || this._at(x, y) !== this._window(a.id))
                    throw new Error('The app or mouse moved just as I was about to click, so I stopped.');
            }
            const b = {left: 1, right: 3, middle: 2}[a.button];
            if (!b)
                throw new Error('Unknown mouse button.');
            if (a.down)
                this._mods(a.mods, true);
            this._pointer.notify_button(GLib.get_monotonic_time(), b,
                a.down ? Clutter.ButtonState.PRESSED : Clutter.ButtonState.RELEASED);
            if (a.down)
                this._heldButtons.add(b);
            else
                this._heldButtons.delete(b);
            if (!a.down)
                this._mods(a.mods, false);
            return true;
        }
        case 'scroll':
            this._available();
            if (this._at(...global.get_pointer().slice(0, 2)) !== this._window(a.id))
                throw new Error('The app under the mouse changed, so I stopped.');
            if (!Number.isInteger(a.notches) || Math.abs(a.notches) > 10)
                throw new Error('Too many scroll notches.');
            for (let i = 0; i < Math.abs(a.notches); i++)
                this._pointer.notify_discrete_scroll(GLib.get_monotonic_time(),
                    a.notches > 0 ? Clutter.ScrollDirection.DOWN : Clutter.ScrollDirection.UP, Clutter.ScrollSource.WHEEL);
            return true;
        case 'key':
            this._available();
            if (global.display.focus_window !== this._window(a.id))
                throw new Error('The app lost keyboard focus, so I stopped.');
            if (!Number.isInteger(a.key) || a.key <= 0 || a.key > 0x1fffffff)
                throw new Error('Invalid key.');
            try {
                this._mods(a.mods, true);
                this._key(a.key, true);
                this._key(a.key, false);
            } finally {
                this._mods(a.mods, false);
            }
            return true;
        case 'screenshot': return this._screenshot(a.monitor);
        case 'overlay': this._overlay(a); return true;
        case 'binds': this._binds(a.keys); return true;
        default: throw new Error('Unknown Tutor Click operation.');
        }
    }

    _key(key, down) {
        this._keyboard.notify_keyval(GLib.get_monotonic_time(), key,
            down ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);
        if (down)
            this._heldKeys.add(key);
        else
            this._heldKeys.delete(key);
    }

    _mods(mods, down) {
        if (!Array.isArray(mods) || mods.some(k => !MODS.has(k)))
            throw new Error('Unknown modifier.');
        for (const k of down ? mods : [...mods].reverse())
            this._key(k, down);
    }

    async _screenshot(index) {
        if (!Number.isInteger(index) || index < 0 || index >= global.display.get_n_monitors())
            throw new Error('That monitor is no longer connected.');
        const r = global.display.get_monitor_geometry(index);
        const stream = Gio.MemoryOutputStream.new_resizable();
        const shooter = new Shell.Screenshot();
        await new Promise((resolve, reject) => {
            shooter.screenshot_area(r.x, r.y, r.width, r.height, stream, (obj, result) => {
                try {
                    obj.screenshot_area_finish(result);
                    resolve();
                } catch (error) { reject(error); }
            });
        });
        stream.close(null);
        return GLib.base64_encode(stream.steal_as_bytes().get_data());
    }

    _overlay(a) {
        if (!this._root) {
            this._root = new St.Widget({reactive: false});
            Main.layoutManager.addTopChrome(this._root, {trackFullscreen: false});
            this._ring = new St.Widget({reactive: false,
                style: 'border: 3px solid #ffc828; border-radius: 22px; width: 38px; height: 38px;'});
            const style = 'background-color: rgba(18,18,23,0.94); color: #ffc828; padding: 12px; border-radius: 10px; font-size: 16px;';
            this._caption = new St.Label({reactive: false, style});
            this._subtitle = new St.Label({reactive: false, style: `${style} color: white;`});
            this._status = new St.Label({reactive: false, style});
            for (const actor of [this._ring, this._caption, this._subtitle, this._status])
                this._root.add_child(actor);
            this._overlayState = {monitor: global.display.get_current_monitor()};
        }
        if (a.clear)
            this._overlayState = {monitor: this._overlayState.monitor};
        Object.assign(this._overlayState, a);
        const s = this._overlayState;
        const r = global.display.get_monitor_geometry(s.monitor);
        this._root.set_position(r.x, r.y);
        this._root.set_size(r.width, r.height);
        this._root.visible = !s.hidden && Boolean(s.point || s.subtitle || s.status);
        this._ring.visible = Boolean(s.point);
        this._caption.visible = Boolean(s.point && s.caption);
        if (s.point) {
            const [x, y] = s.point;
            this._ring.set_position(x - 22, y - 22);
            this._caption.text = s.caption || '';
            this._caption.set_position(Math.max(16, Math.min(x + 44, r.width - 440)), Math.max(16, Math.min(y + 30, r.height - 120)));
            this._caption.set_width(Math.min(420, r.width - 32));
            this._caption.clutter_text.line_wrap = true;
        }
        this._subtitle.visible = Boolean(s.subtitle);
        this._subtitle.text = s.subtitle || '';
        this._subtitle.set_width(Math.min(1100, r.width * 0.6));
        this._subtitle.clutter_text.line_wrap = true;
        const [, height] = this._subtitle.get_preferred_height(this._subtitle.width);
        this._subtitle.set_position((r.width - this._subtitle.width) / 2, r.height - height - 48);
        const labels = {listening: 'Listening…', thinking: 'Looking at your screen…', speaking: 'Speaking…', acting: 'Doing the step…'};
        this._status.visible = Boolean(labels[s.status]);
        this._status.text = labels[s.status] || '';
        this._status.set_position((r.width - this._status.get_preferred_width(-1)[1]) / 2, 14);
    }

    _command(command) {
        if (this._owner)
            Gio.DBus.session.emit_signal(this._owner, '/dev/handren/TutorClick', 'dev.handren.TutorClick',
                'Command', new GLib.Variant('(s)', [command]));
    }

    _binds(keys) {
        for (const action of this._grabs.keys()) {
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.NONE);
            global.display.ungrab_accelerator(action);
        }
        this._grabs.clear();
        const groups = new Map();
        for (const [key, command, release] of keys) {
            const bind = groups.get(key) || {};
            bind[release ? 'up' : 'down'] = command;
            groups.set(key, bind);
        }
        for (const [key, bind] of groups) {
            const parts = key.split('+');
            const last = parts.pop();
            const accelerator = parts.map(m => `<${m === 'ctrl' ? 'Control' : m}>`).join('') + last;
            const flags = Meta.KeyBindingFlags.IGNORE_AUTOREPEAT | (bind.up ? Meta.KeyBindingFlags.TRIGGER_RELEASE : 0);
            const action = global.display.grab_accelerator(accelerator, flags);
            if (action !== Meta.KeyBindingAction.NONE) {
                this._grabs.set(action, bind);
                Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.NORMAL);
            }
        }
    }
}
