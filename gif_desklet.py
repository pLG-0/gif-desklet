import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib
from PIL import Image, ImageSequence
import time
import threading
import os
import configparser
import pathlib
import sys
import logging
import psutil
import signal

# Configurar logging para depuração
logging.basicConfig(filename=os.path.expanduser("~/.gif_desklet/autostart.log"),
                    level=logging.DEBUG,
                    format="%(asctime)s - %(levelname)s - %(message)s")

class GifDesklet(Gtk.Window):
    def __init__(self, gif_path, monitor_index, position, margin, lock_file, custom_x=None, custom_y=None):
        logging.debug(f"Starting GifDesklet with gif_path={gif_path}, monitor={monitor_index}, position={position}, margin={margin}, custom_x={custom_x}, custom_y={custom_y}")
        Gtk.Window.__init__(self, title="GIF Desklet")

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(False)
        self.set_keep_below(True)
        self.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
        self.set_accept_focus(False)

        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)

        self.image = Gtk.Image()
        self.add(self.image)

        try:
            self.pil_gif = Image.open(gif_path)
        except Exception as e:
            logging.error(f"Failed to open GIF file {gif_path}: {e}")
            raise

        self.frames = []
        self.durations = []

        for frame in ImageSequence.Iterator(self.pil_gif):
            self.frames.append(frame.convert("RGBA").copy())
            duration = frame.info.get("duration", 100)
            self.durations.append(duration)

        self.frame_index = 0
        self.running = True

        self.set_default_size(self.frames[0].width, self.frames[0].height)

        # Posiciona janela
        monitor = screen.get_monitor_geometry(monitor_index)
        w, h = self.frames[0].size

        if position == "custom":
            # Validar custom_x e custom_y
            try:
                x = int(custom_x) if custom_x is not None else monitor.x
                y = int(custom_y) if custom_y is not None else monitor.y
                # Garantir que a posição esteja dentro dos limites do monitor
                x = max(monitor.x, min(x, monitor.x + monitor.width - w))
                y = max(monitor.y, min(y, monitor.y + monitor.height - h))
            except (ValueError, TypeError) as e:
                logging.warning(f"Invalid custom_x or custom_y ({custom_x}, {custom_y}), using default position: {e}")
                x = monitor.x
                y = monitor.y
        elif position == "bottom-right":
            x = monitor.x + monitor.width - w
            y = monitor.y + monitor.height - h - margin
        elif position == "bottom-left":
            x = monitor.x + margin
            y = monitor.y + monitor.height - h - margin
        elif position == "top-left":
            x = monitor.x + margin
            y = monitor.y + margin
        elif position == "top-right":
            x = monitor.x + monitor.width - w
            y = monitor.y + margin
        else:
            x = monitor.x
            y = monitor.y

        logging.debug(f"Positioning Desklet at x={x}, y={y} on monitor {monitor_index}")
        self.move(x, y)
        self.show_all()

        # Criar arquivo de lock com o PID
        self.lock_file = lock_file
        try:
            with open(self.lock_file, "w") as f:
                f.write(str(os.getpid()))
            logging.debug(f"Created lock file {self.lock_file} with PID {os.getpid()}")
        except Exception as e:
            logging.error(f"Failed to create lock file {self.lock_file}: {e}")

        # Habilitar arrastar se posição for custom
        self.is_dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        if position == "custom":
            self.connect("button-press-event", self.on_button_press)
            self.connect("button-release-event", self.on_button_release)
            self.connect("motion-notify-event", self.on_motion_notify)
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                           Gdk.EventMask.BUTTON_RELEASE_MASK |
                           Gdk.EventMask.POINTER_MOTION_MASK)

        # Configurar manipulador de sinal para limpar o lock file ao encerrar
        signal.signal(signal.SIGTERM, self.handle_shutdown)
        signal.signal(signal.SIGHUP, self.handle_shutdown)

        threading.Thread(target=self.update_loop, daemon=True).start()

    def handle_shutdown(self, signum, frame):
        logging.debug(f"Received signal {signum}, shutting down Desklet")
        self.destroy()

    def on_button_press(self, widget, event):
        if event.button == 1:  # Botão esquerdo do mouse
            self.is_dragging = True
            self.drag_start_x = event.x_root - self.get_position()[0]
            self.drag_start_y = event.y_root - self.get_position()[1]
            logging.debug(f"Started dragging at x={event.x_root}, y={event.y_root}")
        return True

    def on_button_release(self, widget, event):
        if event.button == 1:
            self.is_dragging = False
            x, y = self.get_position()
            logging.debug(f"Stopped dragging, new position x={x}, y={y}")
            # Salvar nova posição no arquivo de configuração
            config_dir = os.path.expanduser("~/.gif_desklet")
            config_file = os.path.join(config_dir, "gif_desklet.ini")
            config = configparser.ConfigParser()
            if os.path.exists(config_file):
                config.read(config_file)
                if "Desklet" in config:
                    config["Desklet"]["custom_x"] = str(x)
                    config["Desklet"]["custom_y"] = str(y)
                    with open(config_file, "w") as f:
                        config.write(f)
                    logging.debug(f"Saved custom position x={x}, y={y}")
        return True

    def on_motion_notify(self, widget, event):
        if self.is_dragging:
            new_x = int(event.x_root - self.drag_start_x)
            new_y = int(event.y_root - self.drag_start_y)
            self.move(new_x, new_y)
        return True

    def update_loop(self):
        while self.running:
            GLib.idle_add(self.update_frame)
            time.sleep(self.durations[self.frame_index] / 1000.0)
            self.frame_index = (self.frame_index + 1) % len(self.frames)

    def update_frame(self):
        frame = self.frames[self.frame_index]
        data = frame.tobytes()
        width, height = frame.size

        pb = GdkPixbuf.Pixbuf.new_from_data(
            data,
            GdkPixbuf.Colorspace.RGB,
            True,
            8,
            width,
            height,
            width * 4,
        )
        self.image.set_from_pixbuf(pb)

    def destroy(self):
        self.running = False
        # Remover arquivo de lock
        if os.path.exists(self.lock_file):
            try:
                os.remove(self.lock_file)
                logging.debug(f"Removed lock file {self.lock_file}")
            except Exception as e:
                logging.error(f"Failed to remove lock file {self.lock_file}: {e}")
        super().destroy()

class Controller(Gtk.Window):
    def __init__(self, has_desklet=False):
        logging.debug("Initializing Controller")
        Gtk.Window.__init__(self, title="Desklet Controller")
        self.set_border_width(10)
        self.set_default_size(400, 200)

        # Indicador se há um desklet ativo (usado para gerenciar o loop principal)
        self.has_desklet = has_desklet

        # Caminho do arquivo de configuração e lock
        self.config_dir = os.path.expanduser("~/.gif_desklet")
        self.config_file = os.path.join(self.config_dir, "gif_desklet.ini")
        self.lock_file = os.path.join(self.config_dir, "gif_desklet.lock")
        self.config = configparser.ConfigParser()

        grid = Gtk.Grid(column_spacing=10, row_spacing=10)
        self.add(grid)

        # Caminho do GIF
        self.gif_path = None
        lbl_path = Gtk.Label(label="GIF Path:")
        self.entry_path = Gtk.Entry()
        btn_browse = Gtk.Button(label="Browse")
        btn_browse.connect("clicked", self.on_browse)

        # Monitor
        lbl_monitor = Gtk.Label(label="Monitor (0,1,...):")
        self.spin_monitor = Gtk.SpinButton()
        self.spin_monitor.set_adjustment(Gtk.Adjustment(value=0, lower=0, upper=10, step_increment=1, page_increment=0, page_size=0))

        # Posição
        lbl_position = Gtk.Label(label="Position:")
        self.combo_position = Gtk.ComboBoxText()
        for pos in ["bottom-right", "bottom-left", "top-left", "top-right", "custom"]:
            self.combo_position.append_text(pos)
        self.combo_position.set_active(0)

        # Margem
        lbl_margin = Gtk.Label(label="Margin (px):")
        self.spin_margin = Gtk.SpinButton()
        self.spin_margin.set_adjustment(Gtk.Adjustment(value=20, lower=0, upper=200, step_increment=1, page_increment=0, page_size=0))

        # Autostart
        lbl_autostart = Gtk.Label(label="Autostart on login:")
        self.check_autostart = Gtk.CheckButton()
        self.check_autostart.connect("toggled", self.on_autostart_toggled)

        # Botões
        self.btn_start = Gtk.Button(label="Start Desklet")
        self.btn_stop = Gtk.Button(label="Stop Desklet")
        self.btn_stop.set_sensitive(False)

        self.btn_start.connect("clicked", self.on_start)
        self.btn_stop.connect("clicked", self.on_stop)

        # Layout
        grid.attach(lbl_path, 0, 0, 1, 1)
        grid.attach(self.entry_path, 1, 0, 2, 1)
        grid.attach(btn_browse, 3, 0, 1, 1)

        grid.attach(lbl_monitor, 0, 1, 1, 1)
        grid.attach(self.spin_monitor, 1, 1, 1, 1)

        grid.attach(lbl_position, 0, 2, 1, 1)
        grid.attach(self.combo_position, 1, 2, 1, 1)

        grid.attach(lbl_margin, 0, 3, 1, 1)
        grid.attach(self.spin_margin, 1, 3, 1, 1)

        grid.attach(lbl_autostart, 0, 4, 1, 1)
        grid.attach(self.check_autostart, 1, 4, 1, 1)

        grid.attach(self.btn_start, 0, 5, 2, 1)
        grid.attach(self.btn_stop, 2, 5, 2, 1)

        self.desklet = None

        # Carregar configurações salvas
        self.load_settings()

        # Verificar se há uma instância em execução
        self.check_running_instance()

    def load_settings(self):
        logging.debug(f"Loading settings from {self.config_file}")
        # Criar diretório de configuração se não existir
        pathlib.Path(self.config_dir).mkdir(parents=True, exist_ok=True)

        # Valores padrão
        default_settings = {
            "gif_path": "",
            "monitor": "0",
            "position": "bottom-right",
            "margin": "20",
            "autostart": "False",
            "custom_x": "0",
            "custom_y": "0"
        }

        # Ler configurações do arquivo, se existir
        if os.path.exists(self.config_file):
            self.config.read(self.config_file)
            if "Desklet" not in self.config:
                self.config["Desklet"] = default_settings
        else:
            self.config["Desklet"] = default_settings

        # Aplicar configurações na GUI
        settings = self.config["Desklet"]
        self.entry_path.set_text(settings.get("gif_path", ""))
        self.spin_monitor.set_value(int(settings.get("monitor", "0")))
        position = settings.get("position", "bottom-right")
        for i, pos in enumerate(["bottom-right", "bottom-left", "top-left", "top-right", "custom"]):
            if pos == position:
                self.combo_position.set_active(i)
                break
        self.spin_margin.set_value(int(settings.get("margin", "20")))
        self.check_autostart.set_active(settings.get("autostart", "False").lower() == "true")
        logging.debug(f"Loaded settings: {settings}")

    def save_settings(self):
        logging.debug("Saving settings")
        # Salvar configurações atuais
        self.config["Desklet"] = {
            "gif_path": self.entry_path.get_text(),
            "monitor": str(self.spin_monitor.get_value_as_int()),
            "position": self.combo_position.get_active_text(),
            "margin": str(self.spin_margin.get_value_as_int()),
            "autostart": str(self.check_autostart.get_active()),
            "custom_x": self.config["Desklet"].get("custom_x", "0"),
            "custom_y": self.config["Desklet"].get("custom_y", "0")
        }
        with open(self.config_file, "w") as f:
            self.config.write(f)
        logging.debug(f"Saved settings: {self.config['Desklet']}")

    def check_running_instance(self):
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, "r") as f:
                    pid = f.read().strip()
                pid = int(pid)
                if psutil.pid_exists(pid):
                    logging.debug(f"Found running instance with PID {pid}")
                    self.btn_start.set_sensitive(False)
                    self.btn_stop.set_sensitive(True)
                    self.running_pid = pid
                    self.has_desklet = True
                else:
                    logging.debug(f"Lock file exists but PID {pid} is not running, removing lock file")
                    os.remove(self.lock_file)
            except Exception as e:
                logging.error(f"Error checking lock file {self.lock_file}: {e}")
                if os.path.exists(self.lock_file):
                    os.remove(self.lock_file)
        else:
            logging.debug("No running instance found")

    def on_autostart_toggled(self, widget):
        autostart_enabled = self.check_autostart.get_active()
        autostart_dir = os.path.expanduser("~/.config/autostart")
        autostart_file = os.path.join(autostart_dir, "gif-desklet.desktop")

        logging.debug(f"Autostart toggled: {autostart_enabled}")
        if autostart_enabled:
            # Criar arquivo .desktop para autostart
            pathlib.Path(autostart_dir).mkdir(parents=True, exist_ok=True)
            script_path = os.path.abspath(__file__)
            desktop_entry = f"""[Desktop Entry]
Type=Application
Name=GIF Desklet
Exec=python3 {script_path} --autostart
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
            try:
                with open(autostart_file, "w") as f:
                    f.write(desktop_entry)
                logging.debug(f"Created .desktop file at {autostart_file}")
            except Exception as e:
                logging.error(f"Failed to create .desktop file: {e}")
        else:
            # Remover arquivo .desktop se existe
            if os.path.exists(autostart_file):
                try:
                    os.remove(autostart_file)
                    logging.debug(f"Removed .desktop file at {autostart_file}")
                except Exception as e:
                    logging.error(f"Failed to remove .desktop file: {e}")

        # Salvar configuração de autostart
        self.save_settings()

    def on_browse(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Select GIF file",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )

        filter_gif = Gtk.FileFilter()
        filter_gif.set_name("GIF files")
        filter_gif.add_pattern("*.gif")
        dialog.add_filter(filter_gif)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.gif_path = dialog.get_filename()
            self.entry_path.set_text(self.gif_path)
            logging.debug(f"Selected GIF path: {self.gif_path}")

        dialog.destroy()

    def on_start(self, widget):
        if not self.entry_path.get_text() or not os.path.isfile(self.entry_path.get_text()):
            self.show_error("Invalid GIF path!")
            logging.error("Invalid GIF path provided")
            return

        if self.desklet:
            self.desklet.destroy()
            self.desklet = None

        gif_path = self.entry_path.get_text()
        monitor_index = self.spin_monitor.get_value_as_int()
        position = self.combo_position.get_active_text()
        margin = self.spin_margin.get_value_as_int()

        # Verificar se há uma instância em execução
        if os.path.exists(self.lock_file):
            self.show_error("Another instance is already running. Please stop it first.")
            logging.error("Attempted to start Desklet while another instance is running")
            return

        # Salvar configurações antes de iniciar o desklet
        self.save_settings()

        try:
            # Validar custom_x e custom_y
            custom_x = None
            custom_y = None
            if position == "custom":
                try:
                    custom_x = int(self.config["Desklet"].get("custom_x", "0"))
                    custom_y = int(self.config["Desklet"].get("custom_y", "0"))
                    # Garantir que as coordenadas sejam válidas
                    screen = Gdk.Screen.get_default()
                    monitor_geom = screen.get_monitor_geometry(monitor_index)
                    w, h = Image.open(gif_path).size
                    custom_x = max(monitor_geom.x, min(custom_x, monitor_geom.x + monitor_geom.width - w))
                    custom_y = max(monitor_geom.y, min(custom_y, monitor_geom.y + monitor_geom.height - h))
                    logging.debug(f"Validated custom position: custom_x={custom_x}, custom_y={custom_y} for monitor {monitor_index}")
                except (ValueError, TypeError) as e:
                    logging.warning(f"Invalid custom_x or custom_y, using default: {e}")
                    custom_x = monitor_geom.x
                    custom_y = monitor_geom.y
            self.desklet = GifDesklet(gif_path, monitor_index, position, margin, self.lock_file, custom_x, custom_y)
            self.has_desklet = True
            self.btn_start.set_sensitive(False)
            self.btn_stop.set_sensitive(True)
            logging.debug("Desklet started successfully")
        except Exception as e:
            self.show_error(f"Failed to start Desklet: {str(e)}")
            logging.error(f"Failed to start Desklet: {e}")

    def on_stop(self, widget):
        if self.desklet:
            self.desklet.destroy()
            self.desklet = None
            self.has_desklet = False
            logging.debug("Desklet stopped")
        elif hasattr(self, "running_pid") and psutil.pid_exists(self.running_pid):
            try:
                process = psutil.Process(self.running_pid)
                logging.debug(f"Attempting to terminate Desklet with PID {self.running_pid}")
                process.terminate()
                try:
                    process.wait(timeout=1)
                    logging.debug(f"Terminated running Desklet with PID {self.running_pid}")
                except psutil.TimeoutExpired:
                    time.sleep(0.5)  # Wait briefly and retry
                    if not psutil.pid_exists(self.running_pid):
                        logging.debug(f"Desklet with PID {self.running_pid} terminated after retry")
                    else:
                        logging.warning(f"Timeout waiting for Desklet with PID {self.running_pid} to terminate after retry")
                self.has_desklet = False
            except Exception as e:
                logging.error(f"Failed to terminate running Desklet with PID {self.running_pid}: {e}")
                self.show_error(f"Failed to stop running Desklet: {str(e)}")
        self.btn_start.set_sensitive(True)
        self.btn_stop.set_sensitive(False)

    def show_error(self, msg):
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=msg,
        )
        dialog.run()
        dialog.destroy()

    def on_destroy(self, widget):
        logging.debug("Controller window destroyed")
        if not self.has_desklet:
            logging.debug("No active Desklet, quitting main loop")
            Gtk.main_quit()

def main():
    logging.debug("Starting main function")
    # Verificar se foi chamado com --autostart
    if "--autostart" in sys.argv:
        logging.debug("Autostart mode activated")
        config_dir = os.path.expanduser("~/.gif_desklet")
        config_file = os.path.join(config_dir, "gif_desklet.ini")
        lock_file = os.path.join(config_dir, "gif_desklet.lock")
        config = configparser.ConfigParser()
        try:
            if os.path.exists(config_file):
                config.read(config_file)
                settings = config["Desklet"]
                gif_path = settings.get("gif_path", "")
                if gif_path and os.path.isfile(gif_path):
                    # Verificar se há uma instância em execução
                    if os.path.exists(lock_file):
                        try:
                            with open(lock_file, "r") as f:
                                pid = f.read().strip()
                            pid = int(pid)
                            if psutil.pid_exists(pid):
                                logging.debug(f"Autostart aborted: instance with PID {pid} is running")
                                return
                            else:
                                logging.debug(f"Lock file exists but PID {pid} is not running, removing lock file")
                                os.remove(lock_file)
                        except Exception as e:
                            logging.error(f"Error checking lock file {lock_file}: {e}")
                            if os.path.exists(lock_file):
                                os.remove(lock_file)
                    monitor_index = int(settings.get("monitor", "0"))
                    position = settings.get("position", "bottom-right")
                    margin = int(settings.get("margin", "20"))
                    custom_x = None
                    custom_y = None
                    if position == "custom":
                        try:
                            custom_x = int(settings.get("custom_x", "0"))
                            custom_y = int(settings.get("custom_y", "0"))
                            # Validar coordenadas
                            screen = Gdk.Screen.get_default()
                            monitor_geom = screen.get_monitor_geometry(monitor_index)
                            w, h = Image.open(gif_path).size
                            custom_x = max(monitor_geom.x, min(custom_x, monitor_geom.x + monitor_geom.width - w))
                            custom_y = max(monitor_geom.y, min(custom_y, monitor_geom.y + monitor_geom.height - h))
                            logging.debug(f"Validated custom position for autostart: custom_x={custom_x}, custom_y={custom_y} for monitor {monitor_index}")
                        except (ValueError, TypeError) as e:
                            logging.warning(f"Invalid custom_x or custom_y in autostart, using default: {e}")
                            custom_x = monitor_geom.x
                            custom_y = monitor_geom.y
                    logging.debug(f"Autostart settings: gif_path={gif_path}, monitor={monitor_index}, position={position}, margin={margin}, custom_x={custom_x}, custom_y={custom_y}")
                    desklet = GifDesklet(gif_path, monitor_index, position, margin, lock_file, custom_x, custom_y)
                    Gtk.main()
                    return
                else:
                    logging.error(f"Invalid or missing GIF path: {gif_path}")
            else:
                logging.error(f"Config file not found: {config_file}")
        except Exception as e:
            logging.error(f"Autostart failed: {e}")
        return

    # Iniciar normalmente com a janela de controle
    logging.debug("Starting controller window")
    win = Controller()
    win.connect("destroy", win.on_destroy)
    win.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
