import os
import sys
from collections import defaultdict
from types import SimpleNamespace
import subprocess
import time
import threading

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('GnomeDesktop', '3.0')
from gi.repository import Gtk, Gio, GLib, GnomeDesktop
from gi.repository.GdkPixbuf import Pixbuf

GUI_GLADE_FILENAME = sys.path[0] + '/gui.glade'
AUTOSTART_DESKTOP_PATH = os.environ['HOME'] + '/.config/autostart/hidamari.desktop'
AUTOSTART_DESKTOP_CONTENT = \
    '''[Desktop Entry]
Type=Application
Name=Hidamari
Exec=hidamari -p 1
StartupNotify=false
Terminal=false
Icon=hidamari
Categories=System;Monitor;
    '''

from utils import ConfigHandler, create_dir, scan_dir


def setup_autostart(autostart):
    if autostart:
        with open(AUTOSTART_DESKTOP_PATH, mode='w') as f:
            f.write(AUTOSTART_DESKTOP_CONTENT)
    else:
        try:
            os.remove(AUTOSTART_DESKTOP_PATH)
        except OSError:
            pass


def get_length(filename):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "format=duration", "-of",
                             "default=noprint_wrappers=1:nokey=1", filename],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    return float(result.stdout)


def generate_thumbnail_gnome(filename):
    factory = GnomeDesktop.DesktopThumbnailFactory()
    mtime = os.path.getmtime(filename)
    # Use Gio to determine the URI and mime type
    f = Gio.file_new_for_path(filename)
    uri = f.get_uri()
    info = f.query_info(
        'standard::content-type', Gio.FileQueryInfoFlags.NONE, None)
    mime_type = info.get_content_type()

    if factory.lookup(uri, mtime) is not None:
        return False

    if not factory.can_thumbnail(uri, mime_type, mtime):
        return False

    thumbnail = factory.generate_thumbnail(uri, mime_type)
    if thumbnail is None:
        return False

    factory.save_thumbnail(thumbnail, uri, mtime)
    return True


def get_thumbnail_gnome(video_path, list_store, idx):
    file = Gio.File.new_for_path(video_path)
    info = file.query_info('*', 0, None)
    thumbnail = info.get_attribute_byte_string('thumbnail::path')
    if thumbnail is not None:
        new_pixbuf = Pixbuf.new_from_file_at_size(thumbnail, -1, 96)
        list_store[idx][0] = new_pixbuf
    else:
        generate_thumbnail_gnome(video_path)


def get_thumbnail(video_path, list_store, idx):
    # TODO too resource intensive, looking for a better approach
    root_path = os.path.dirname(video_path) + '/.thumbnails'
    file_path = '{}/{}.png'.format(root_path, os.path.basename(video_path))
    if not os.path.exists(file_path):
        create_dir(root_path)
        sec = get_length(video_path) / 3
        subprocess.call(
            'ffmpeg -y -i "{}" -ss {}.000 -vf scale=96:-1 -vframes 1 "{}" -loglevel quiet > /dev/null 2>&1 < /dev/null'.format(
                video_path, time.strftime('%H:%M:%S', time.gmtime(sec)), file_path), shell=True)
    new_pixbuf = Pixbuf.new_from_file_at_size(file_path, -1, 96)
    list_store[idx][0] = new_pixbuf


class ControlPanel(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.github.jeffshee.hidamari.gui')

        self.config_handler = ConfigHandler(self._on_config_modified)
        self.config = self.config_handler.config

        # Builder Initialization
        self.builder = Gtk.Builder()
        self.builder.add_from_file(GUI_GLADE_FILENAME)
        object_list = ['window', 'icon_view', 'volume', 'volume_adjustment', 'autostart', 'mute_audio',
                       'detect_maximized', 'static_wallpaper', 'blur_adjustment', 'blur_radius', 'apply']
        object_dict = defaultdict()
        for obj in object_list:
            object_dict[obj] = self.builder.get_object(obj)
        self.object = SimpleNamespace(**object_dict)

    def do_startup(self):
        Gtk.Application.do_startup(self)

        # Object Initialization
        self.object.window.connect('destroy', Gtk.main_quit)
        self.object.window.show_all()
        self._reload_icon_view()
        self._reload_widget()

        self.builder.connect_signals(
            {'on_apply_clicked': self._on_apply_clicked, 'on_cancel_clicked': self._on_cancel_clicked,
             'on_value_changed': self._on_value_changed, 'on_refresh_clicked': self._on_refresh_clicked})

    def do_activate(self):
        Gtk.main()

    def _on_config_modified(self):
        def _run():
            self.config = self.config_handler.config
            self._reload_widget()

        # To ensure thread safe
        GLib.idle_add(_run)

    def _on_apply_clicked(self, *args):
        selected = self.object.icon_view.get_selected_items()
        if len(selected) != 0:
            icon_view_selection = selected[0].get_indices()[0]
            self.config.video_path = self.file_list[icon_view_selection]
        #
        setup_autostart(self.object.autostart.get_active())
        self.config.static_wallpaper = self.object.static_wallpaper.get_active()
        self.config.detect_maximized = self.object.detect_maximized.get_active()
        self.config.mute_audio = self.object.mute_audio.get_active()
        self.config.static_wallpaper_blur_radius = self.object.blur_adjustment.get_value()
        self.config.audio_volume = self.object.volume_adjustment.get_value() / 100
        self.config_handler.save()
        self.object.apply.set_sensitive(False)

    def _on_cancel_clicked(self, *args):
        self._reload_widget()

    def _on_refresh_clicked(self, *args):
        self._reload_icon_view()

    def _on_value_changed(self, *args):
        self.object.volume.set_sensitive(not self.object.mute_audio.get_active())
        self.object.blur_radius.set_sensitive(self.object.static_wallpaper.get_active())
        self.object.apply.set_sensitive(True)

    def _reload_icon_view(self):
        self.file_list = scan_dir()
        list_store = Gtk.ListStore(Pixbuf, str)
        self.object.icon_view.set_model(list_store)
        self.object.icon_view.set_pixbuf_column(0)
        self.object.icon_view.set_text_column(1)
        for idx, video in enumerate(self.file_list):
            icon_theme = Gtk.IconTheme().get_default()
            pixbuf = icon_theme.load_icon('video-x-generic', 96, 0)
            list_store.append([pixbuf, os.path.basename(video)])
            thread = threading.Thread(target=get_thumbnail_gnome, args=(video, list_store, idx))
            thread.daemon = True
            thread.start()

    def _reload_widget(self):
        self.object.autostart.set_active(os.path.isfile(AUTOSTART_DESKTOP_PATH))
        self.object.static_wallpaper.set_active(self.config.static_wallpaper)
        self.object.detect_maximized.set_active(self.config.detect_maximized)
        self.object.mute_audio.set_active(self.config.mute_audio)
        self.object.volume_adjustment.set_value(self.config.audio_volume * 100)
        self.object.blur_adjustment.set_value(self.config.static_wallpaper_blur_radius)
        #
        self.object.icon_view.unselect_all()
        #
        self.object.volume.set_sensitive(not self.object.mute_audio.get_active())
        self.object.blur_radius.set_sensitive(self.object.static_wallpaper.get_active())
        self.object.apply.set_sensitive(False)


if __name__ == '__main__':
    ControlPanel()
