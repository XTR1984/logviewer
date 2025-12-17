import serial
import re
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox,Menu
import threading
import queue
import time
from datetime import datetime, timezone
from collections import defaultdict
import json
import os
import glob
#import serial.tools.list_ports
from tkinter import filedialog


def convert_with_local_offset(timestamp):
    """
    Преобразует дату с учетом локального часового пояса
    """
    today = datetime.now().date()

    input_date = datetime.strptime(str(today.year)+" "+timestamp, "%Y %H:%M:%S")
    # Предполагаем, что входная дата в UTC
    utc_date = input_date.replace(tzinfo=timezone.utc)
    
    # Конвертируем в локальный часовой пояс
    local_date = utc_date.astimezone()

    out_timestamp= local_date.strftime("%H:%M:%S")
    
    return out_timestamp


def extract_data_from_file(filename):
    """
    Извлекает данные из файла с таблицей и возвращает словарь
    Ключ: ID (без !)
    Значение: словарь с полями 'user', 'aka', 'id_raw' (исходный ID с !)
    """
    result_dict = {}
    
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            
        for line in lines:
            # Ищем строки с данными (начинаются с │ и содержат номер)
            if line.startswith('│') and re.match(r'│\s*\d+\s*│', line):
                # Разбиваем строку по символам │ и удаляем пустые элементы
                parts = [part.strip() for part in line.split('│') if part.strip()]
                
                # Проверяем, что строка содержит достаточно данных
                if len(parts) >= 4:
                    try:
                        # Извлекаем данные
                        user = parts[1]
                        id_raw = parts[2]  # ID с !
                        aka = parts[3]
                        
                        # Очищаем ID от символа !
                        user_id = id_raw.replace('!', '').upper()
                        
                        # Добавляем в словарь
                        result_dict[user_id[4:]] = {
                            'user': user,
                            'aka': aka,
                            'id_raw': id_raw  # сохраняем исходный ID
                        }
                    except (IndexError, ValueError) as e:
                        print(f"Ошибка обработки строки: {line[:50]}...")
                        continue
                        
    except FileNotFoundError:
        print(f"Файл '{filename}' не найден.")
        return {}
    except Exception as e:
        print(f"Ошибка при чтении файла: {e}")
        return {}
    
    return result_dict


def load_relayinfo(filename):
    relayinfo = {}
    try:
        with open(filename, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            
        for line in lines:
            sp = line.split(':')
            try:
                id = sp[0].strip().upper()
                name = sp[1].strip()
                if name=="":
                    name = id
                relayinfo[id]=name
            except Exception as e:
                pass

            
    except FileNotFoundError:
        print(f"Файл '{filename}' не найден.")
        return {}
    except Exception as e:
        print(f"Ошибка при чтении файла: {e}")
        return {}


    return relayinfo


class SerialReader:
    def __init__(self, port='/dev/ttyUSB0', baudrate=115200):
        self.port = port
        self.baudrate = baudrate
        self.serial = None
        self.running = False
        self.data_queue = queue.Queue()
        self.to_file = False

    @staticmethod
    def find_serial_ports():
        """Находит доступные COM-порты для Linux и Windows"""
        ports = []
        
        # Для Windows
        if os.name == 'nt':
            for i in range(256):
                try:
                    port = f'COM{i}'
                    s = serial.Serial(port)
                    s.close()
                    ports.append(port)
                except (OSError, serial.SerialException):
                    pass
        # Для Linux/Mac
        else:
            # Обычные USB-UART адаптеры
            ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
            # Bluetooth и другие
            ports += glob.glob('/dev/rfcomm*') + glob.glob('/dev/ttyS*')
        
        return sorted(ports)
    

    def start(self):
        """Запускает чтение из последовательного порта"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=5
            )
            self.running = True
            thread = threading.Thread(target=self._read_serial)
            thread.daemon = True
            thread.start()
            return True
        except Exception as e:
            print(f"Ошибка открытия порта {self.port}: {e}")
            return False

    def _read_serial(self):
        """Читает данные из последовательного порта"""
        buffer = ""
        while self.running:
            try:
                if self.serial.in_waiting > 0:
                    data = self.serial.read(self.serial.in_waiting).decode('utf-8', errors='ignore')
                    buffer += data

                    # Разделяем на строки
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        if line:
                            self.data_queue.put(line)
                            if (self.to_file):
                                f = open("raw.log", "a")
                                f.write(line+"\r\n")
                                f.close()
            except Exception as e:
                print(f"Ошибка чтения: {e}")
                time.sleep(0.1)

    def get_data(self):
        """Возвращает данные из очереди"""
        data = []
        while not self.data_queue.empty():
            try:
                data.append(self.data_queue.get_nowait())
            except queue.Empty:
                break
        return data

    def stop(self):
        """Останавливает чтение"""
        self.running = False
        if self.serial:
            self.serial.close()

class LogParser:
    def __init__(self):
        self.messages = defaultdict(list)  # Все события по ID пакета
        self.packet_stats = {}  # Статистика по пакетам
        self.busy_rx_count = 0
        self.nodes = set()
        self.start_time = time.time()
        self.filter_webserver = True
        self.time_correction = True

    def parse_line(self, line):
        """Парсит одну строку лога"""
        if not line.strip():
            return None

        # Извлекаем временную метку
        time_match = re.search(r'(\d{2}:\d{2}:\d{2}\s+\d+)\s+\[', line)
        if not time_match:
            return None

        timestamp = time_match.group(1).split()[0]
        if self.time_correction:
            timestamp = convert_with_local_offset(timestamp)



        # Ищем ID пакета
        id_match = re.search(r'id=0x([0-9a-fA-F]+)', line)
        packet_id = id_match.group(1) if id_match else None

        #fix id
        if packet_id !=None and len(packet_id)==7:
            packet_id = '0'+packet_id

        # Ищем отправителя
        from_match = re.search(r'fr=0x([0-9a-fA-F]+)', line)
        from_node = from_match.group(1).upper()[4:] if from_match else None

        # Ищем получателя
        to_match = re.search(r'to=0x([0-9a-fA-F]+)', line)
        to_node = to_match.group(1).upper()[4:] if to_match else None

        # Ищем сообщение
        msg_match = re.search(r'msg=(\d+)', line)
        message = msg_match.group(1) if msg_match else None

        # Ищем portnum
        port_match = re.search(r'Portnum=(\d+)', line)
        portnum = port_match.group(1) if port_match else None

        # Определяем тип события
        event_type = "OTHER"
        relay_node = None
        hop_lim = None
        rx_snr = None
        rx_rssi = None

        # Поиск ретранслятора
        relay_match = re.search(r'relay=0x([0-9a-fA-F]+)', line)
        if relay_match:
            try:
                relay_node = relay_match.group(1).upper()
                if len(relay_node)<2:
                    relay_node+="*"
            except ValueError as e:
                relay_node = None

        # Поиск HopLim
        hop_match = re.search(r'HopLim=(\d+)', line)
        if hop_match:
            try:
                hop_lim = int(hop_match.group(1))
            except ValueError as e:
                hop_lim = None

        # Поиск SNR и RSSI
        snr_match = re.search(r'rxSNR=([-\d.]+)', line)
        if snr_match:
            try:
                rx_snr = float(snr_match.group(1))
            except ValueError as e:
                rx_snr = None

        rssi_match = re.search(r'rxRSSI=([-\d]+(?:\.\d+)?)', line)
        if rssi_match:
            rssi_str = rssi_match.group(1)
            try:
                # Если есть точка - это float, иначе int
                if '.' in rssi_str:
                    rx_rssi = float(rssi_str)
                else:
                    rx_rssi = int(rssi_str)
            except ValueError as e:
                #print(f"Ошибка парсинга RSSI '{line}': {e} ")
                rx_rssi = None

        if 'Update changed' in line:
            return None



        # Определение типа события
        if 'Received text msg' in line:
            event_type = 'RECEIVED_TEXT'
        elif 'Received nodeinfo' in line:
            event_type = 'RECEIVED_NODEINFO'
        elif 'Received routing' in line:
            event_type = 'RECEIVED_ROUTING'
        elif 'Started Tx' in line:
            event_type = 'RETRANSMISSION'
        elif 'Lora RX' in line and 'Ignore dupe' not in line:
            event_type = 'RX'
        elif 'Ignore dupe incoming msg' in line:
            event_type = 'IGNORE_DUPLICATE'
        elif 'enqueue for send' in line:
            event_type = 'QUEUED'
        elif 'Completed sending' in line:
            event_type = 'TX_COMPLETE'
        elif 'Can not send yet, busyRx' in line:
            event_type = 'BUSY_RX'
            self.busy_rx_count += 1
        elif 'decoded message' in line:
            event_type = 'DECODED'
        elif 'Forwarding to phone' in line:
            event_type = 'TO_PHONE'
        elif "handleReceived" in line:
            event_type = 'HANDLE_RECEIVED'
        elif 'Received DeviceTelemetry' in line:
            event_type = 'TELEMETRY'
        elif 'Received position' in line:
            event_type = 'POSITION'
        elif 'Routing sniffing' in line:
            event_type = 'ROUTING_SNIFFING'            
        elif 'cancelSending' in line:
            event_type = 'CANSEL_SENDING'
            

        # Добавляем информацию о узлах
        if from_node:
            self.nodes.add(from_node)

        if self.filter_webserver:
            if '[WebServer]' in line:
                return None

        # Создаем запись о событии
        event = {
            'timestamp': timestamp,
            'raw_time': time.time(),
            'packet_id': packet_id,
            'from_node': from_node,
            'to_node': to_node,
            'message': message,
            'portnum': portnum,
            'event_type': event_type,
            'relay_node': relay_node,
            'hop_lim': hop_lim,
            'rx_snr': rx_snr,
            'rx_rssi': rx_rssi,
            'raw_line': line
        }



        # Сохраняем событие
        if packet_id:
            self.messages[packet_id].append(event)

            # Обновляем статистику пакета
            if packet_id not in self.packet_stats:
                self.packet_stats[packet_id] = {
                    'first_seen': timestamp,
                    'last_seen': timestamp,
                    'received_times': [],
                    'retransmission_time': None,
                    'message': message,
                    'from_node': from_node,
                    'to_node': to_node,
                    'duplicate_count': 0,
                    'relays': [],
                    'events': []
                }

            self.packet_stats[packet_id]['last_seen'] = timestamp
            self.packet_stats[packet_id]['events'].append(event)
            if relay_node != None:
                self.packet_stats[packet_id]['relays'].append(relay_node)

            if event_type == 'RECEIVED_TEXT':
                self.packet_stats[packet_id]['received_times'].append(timestamp)
            elif event_type == 'RETRANSMISSION':
                self.packet_stats[packet_id]['retransmission_time'] = timestamp
            elif event_type == 'RX_DUPLICATE':
                self.packet_stats[packet_id]['duplicate_count'] += 1
            

        return event

    def get_packet_summary(self, packet_id):
        """Возвращает сводку по пакету"""
        if packet_id not in self.packet_stats:
            return None

        stats = self.packet_stats[packet_id]
        events = self.messages.get(packet_id, [])

        # Находим время первого приёма
        first_rx = None
        retransmission_time = None

        #for event in events:
        #    if event['event_type'] in ['RECEIVED_TEXT', 'RX_NEW', 'WEBSERVER']:
        if len(events)>0:
            first_rx = events[0]['timestamp']
        #        break


        retransmission_time = stats.get('retransmission_time')

        # Вычисляем задержку
        delay = None
        if first_rx and retransmission_time:
            # Преобразуем время в секунды
            try:
                t1 = datetime.strptime(first_rx.split()[0], "%H:%M:%S")
                t2 = datetime.strptime(retransmission_time.split()[0], "%H:%M:%S")
                delay_seconds = (t2 - t1).seconds

                delay = delay_seconds
            except:
                delay = None




        summary = {
            'packet_id': packet_id,
            'message': stats.get('message', 'N/A'),
            'from_node': stats.get('from_node', 'N/A'),
            'to_node': stats.get('to_node', 'N/A'),
            'first_received': first_rx,
            'retransmission_time': retransmission_time,
            'delay_seconds': delay,
            'duplicate_count': stats.get('duplicate_count', 0),
            'event_count': len(events),
            'relays': stats.get('relays',  []),
            'received_count': len(stats.get('received_times', []))
        }

        return summary

    def get_all_packet_summaries(self):
        """Возвращает сводки по всем пакетам"""
        summaries = []
        for packet_id in self.packet_stats.keys():
            summary = self.get_packet_summary(packet_id)
            if summary:
                summaries.append(summary)

        # Сортируем по времени первого приёма
        summaries.sort(key=lambda x: x['first_received'] if x['first_received'] else '')
        return summaries

    def get_statistics(self):
        """Возвращает общую статистику"""
        total_packets = len(self.packet_stats)
        retransmitted = sum(1 for s in self.packet_stats.values() if s.get('retransmission_time'))

        # Средняя задержка ретрансляции
        delays = []
        for packet_id in self.packet_stats:
            summary = self.get_packet_summary(packet_id)
            if summary and summary['delay_seconds']:
                delays.append(summary['delay_seconds'])

        avg_delay = sum(delays) / len(delays) if delays else 0

        stats = {
            'total_packets': total_packets,
            'retransmitted_packets': retransmitted,
            'unique_nodes': len(self.nodes),
            'busy_rx_count': self.busy_rx_count,
            'avg_retransmission_delay': avg_delay,
            'uptime_seconds': time.time() - self.start_time
        }

        return stats

class LogAnalyzerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Meshtastic Node Log Analyzer")
        self.root.geometry("1400x900")

        # Инициализация компонентов
        self.parser = LogParser()
        self.serial_reader = None
        self.autoscroll = tk.BooleanVar(value=True)
        self.show_username = tk.BooleanVar(value=True)
        self.filter_webserver = tk.BooleanVar(value=True)
        self.serial_running = False
        self.rawline = tk.BooleanVar(value=False)
        self.time_correction = tk.BooleanVar(value=True)
        self.display_mode = tk.StringVar(value="auto") 

        nodesfile = 'nodes.txt'
        if os.path.exists(nodesfile):
            self.nodeinfo = extract_data_from_file(nodesfile)
        else:
            self.nodeinfo = {}
            


        self.update_relayinfo()

        self.create_menu()

        # Создание интерфейса
        self.create_widgets()

        self.create_status_bar()


        self.show_connection_dialog()

        #if not self.connection_established:
        #    self.root.destroy()
        #    return


        # Запуск обновления GUI
        self.update_gui()




    def show_connection_dialog(self):
        """Показывает диалог выбора источника данных"""
        dialog = ConnectionDialog(self.root)
        result = dialog.show()
        
        if not result:
            self.connection_established = False
            return False
            
        self.connection_type, self.connection_param = result

        
        
        if self.connection_type == 'serial':
            # Создаем SerialReader с выбранным портом
            self.serial_reader = SerialReader(port=self.connection_param)
            if not self.start_serial_reading():
                self.show_connection_dialog()
            self.connection_established = True                
        else:  # file
            # Загружаем данные из файла
            self.connection_established = True                
            self.load_from_file(self.connection_param)
            self.update_statistics()
            


    def load_from_file(self, filename):
        """Загружает данные из файла лога"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            self.update_status(f"Загрузка файла: {os.path.basename(filename)}")
            
            # Парсим все строки
            for line in lines:
                self.parser.parse_line(line.strip())
            
            self.update_status(f"Файл загружен: {len(lines)} строк")
            self.serial_indicator.config(text="📁 Файл")
            return True
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка загрузки файла: {e}")
            return False
            


    def create_status_bar(self):
        """Создает статус бар внизу окна"""
        self.status_bar = ttk.Frame(self.root, relief=tk.SUNKEN)
        self.status_bar.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.S))
        
        # Левая часть статус бара
        self.status_left = ttk.Label(self.status_bar, text="Готов")
        self.status_left.pack(side=tk.LEFT, padx=5)
        
        # Центральная часть - статистика
        self.stats_frame = ttk.Frame(self.status_bar)
        self.stats_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Правая часть - индикаторы
        self.status_right = ttk.Frame(self.status_bar)
        self.status_right.pack(side=tk.RIGHT, padx=5)
        
        # Создаем метки для статистики
        self.stats_labels = {}
        stats_items = [
            ('packets', '📦 Пакеты: 0'),
            ('retrans', '🔄 Ретранс: 0'),
            ('nodes', '📡 Узлы: 0'),
            ('busy', '⚠️ BusyRx: 0'),
            ('delay', '⏱️ Задержка: 0.0с')
        ]
        
        for key, default_text in stats_items:
            label = ttk.Label(self.stats_frame, text=default_text, font=('TkDefaultFont', 9))
            label.pack(side=tk.LEFT, padx=10)
            self.stats_labels[key] = label
        
        # Индикатор сериала
        self.serial_indicator = ttk.Label(self.status_right, text="🔴 Отключено")
        self.serial_indicator.pack(side=tk.LEFT, padx=5)
        
        # Индикатор автоскролла
        self.scroll_indicator = ttk.Label(self.status_right, text="⏏️ Автоскролл: ВКЛ")
        self.scroll_indicator.pack(side=tk.LEFT, padx=5)
        
        # Настраиваем расширение
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        

    def create_menu(self):
        """Создает меню приложения"""
        menubar = Menu(self.root)
        self.root.config(menu=menubar)
        
        # Меню Файл
        file_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Файл", menu=file_menu)
        file_menu.add_command(label="Источник данных", 
                            command=self.show_connection_dialog,
                            accelerator="Ctrl+O")
        #file_menu.add_command(label="Подключиться к порту...", 
        #                    command=self.reconnect_serial)
        file_menu.add_separator()
        file_menu.add_command(label="Экспорт в JSON", 
                            command=self.export_json, 
                            accelerator="Ctrl+E")
        file_menu.add_separator()
        file_menu.add_command(label="Выход", 
                            command=self.root.quit, 
                            accelerator="Alt+F4")
        
        # Меню Вид
        view_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Вид", menu=view_menu)
        
        # Группа радио-кнопок для режима отображения
        display_menu = Menu(view_menu, tearoff=0)
        view_menu.add_cascade(label="Режим отображения узлов", menu=display_menu)
        display_menu.add_radiobutton(label="Авто", 
                                   variable=self.display_mode, 
                                   value="auto",
                                   command=self.update_display_mode)
        display_menu.add_radiobutton(label="По имени", 
                                   variable=self.display_mode, 
                                   value="name",
                                   command=self.update_display_mode)
        display_menu.add_radiobutton(label="По ID", 
                                   variable=self.display_mode, 
                                   value="id",
                                   command=self.update_display_mode)
        display_menu.add_radiobutton(label="По AKA", 
                                   variable=self.display_mode, 
                                   value="aka",
                                   command=self.update_display_mode)
        
        view_menu.add_checkbutton(label="Автопрокрутка", 
                                variable=self.autoscroll, 
                                command=self.toggle_auto_scroll)
        view_menu.add_checkbutton(label="Raw line", 
                                variable=self.rawline, 
                                command=self.toggle_rawline)
        view_menu.add_checkbutton(label="фильтровать webserver", 
                                variable=self.filter_webserver,
                                command=self.toggle_filterwebserver
                                )
        view_menu.add_checkbutton(label="Коррекция времени", 
                        variable=self.time_correction,
                        command=self.toggle_time_correction)
        
        view_menu.add_separator()
        
        # Меню Сервис
        tools_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Сервис", menu=tools_menu)
        tools_menu.add_command(label="Обновить статистику", 
                             command=self.update_statistics, 
                             accelerator="F5")
        tools_menu.add_command(label="Перезагрузить relayinfo", 
                             command=self.update_relayinfo)
        tools_menu.add_separator()
        tools_menu.add_command(label="Очистить данные", 
                            command=self.clear_data, 
                            accelerator="Ctrl+D")


        # Меню Справка
        help_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Справка", menu=help_menu)
        help_menu.add_command(label="О программе", 
                            command=self.show_about)
        
        # Бинды клавиш
        #self.root.bind('<Control-o>', lambda e: self.open_log_file())
        self.root.bind('<F5>', lambda e: self.update_statistics())
        self.root.bind('<Control-e>', lambda e: self.export_json())
        self.root.bind('<Control-d>', lambda e: self.clear_data())


    def open_log_file(self):
        """Открывает диалог выбора файла лога"""
        if self.serial_running:
            self.serial_reader.stop()
            self.serial_running = False
        
        filename = filedialog.askopenfilename(
            title="Выберите файл лога",
            filetypes=[("Text files", "*.txt *.log"), ("All files", "*.*")]
        )
        
        if filename:
            self.clear_data()
            self.load_from_file(filename)
            self.serial_indicator.config(text="📁 Файл")
    
    def update_display_mode(self):
        """Обновляет режим отображения имен"""
        self.update_packets_table()
    
    def get_display_name(self, node_id, mode=None):
        """Возвращает отображаемое имя узла в зависимости от режима"""
        if mode is None:
            mode = self.display_mode.get()
        
        # Авторежим: если есть файл nodes.txt - по имени, иначе по ID
        if mode == "auto":
            if not self.nodeinfo or node_id not in self.nodeinfo:
                return node_id
            return self.nodeinfo[node_id]["user"]
        
        elif mode == "name":
            if node_id in self.nodeinfo:
                return self.nodeinfo[node_id]["user"]
            return node_id
        
        elif mode == "id":
            return node_id
        
        elif mode == "aka":
            if node_id in self.nodeinfo:
                return self.nodeinfo[node_id]["aka"]
            return node_id
        
        return node_id

    def create_menu_old(self):
        """Создает меню приложения"""
        menubar = Menu(self.root)
        self.root.config(menu=menubar)
        
        # Меню Файл
        file_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Файл", menu=file_menu)
        file_menu.add_command(label="Экспорт в JSON", command=self.export_json, accelerator="Ctrl+E")
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.root.quit, accelerator="Alt+F4")
        
        # Меню Вид
        view_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Вид", menu=view_menu)
        view_menu.add_checkbutton(label="Автопрокрутка", variable=self.autoscroll, 
                                  command=self.toggle_auto_scroll)
        view_menu.add_checkbutton(label="Raw line", variable=self.rawline, 
                                  command=self.toggle_rawline)
        
        view_menu.add_separator()
        view_menu.add_command(label="Очистить данные", command=self.clear_data, accelerator="Ctrl+D")
        
        # Меню Сервис
        tools_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Сервис", menu=tools_menu)
        tools_menu.add_command(label="Обновить статистику", command=self.update_statistics, accelerator="F5")
        tools_menu.add_command(label="Перезагрузить relayinfo", command=self.update_relayinfo)
        tools_menu.add_separator()
        tools_menu.add_command(label="Переподключить сериал", command=self.reconnect_serial)
        
        # Меню Справка
        help_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Справка", menu=help_menu)
        help_menu.add_command(label="О программе", command=self.show_about)
        
        # Бинды клавиш
        self.root.bind('<F5>', lambda e: self.update_statistics())
        self.root.bind('<Control-e>', lambda e: self.export_json())
        self.root.bind('<Control-d>', lambda e: self.clear_data())

    def update_relayinfo(self):
        self.relayinfo = {}
        relaysfile = 'relays.txt'
        if os.path.exists(relaysfile):
            self.relayinfo = load_relayinfo(relaysfile)



    def toggle_auto_scroll(self):
        """Переключает режим автоскролла"""
        status = "ВКЛ" if self.autoscroll.get() else "ВЫКЛ"
        self.scroll_indicator.config(text=f"⏏️ Автоскролл: {status}")
        self.update_status(f"Автоскролл {status}")


    def toggle_filterwebserver(self):
        self.parser.filter_webserver = self.filter_webserver.get() 


    def toggle_time_correction(self):
        self.parser.time_correction = self.time_correction.get() 


    def toggle_rawline(self):
        pass
        

    def update_status(self, message):
        """Обновляет левую часть статус бара"""
        self.status_left.config(text=message)

    def reconnect_serial(self):
        """Переподключает сериал порт"""
        if self.serial_running:
            self.serial_reader.stop()
            self.serial_running = False
        
        self.update_status("Переподключение...")
        self.serial_indicator.config(text="🟡 Подключение")
        self.root.after(1000, self.start_serial_reading)

    def show_about(self):
        """Показывает окно 'О программе'"""
        about_text = """Meshtastic Node Log Analyzer v1.0
        """
        messagebox.showinfo("О программе", about_text)


    def create_widgets(self):
        """Создает виджеты GUI"""
        # Основной фрейм
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Конфигурация расширенияаг
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        main_frame.rowconfigure(3, weight=1)


        # Таблица пакетов
        packets_frame = ttk.LabelFrame(main_frame, text="Пакеты", padding="10")
        packets_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        # Создание Treeview
        columns = ('ID', 'От', 'Кому', 'Первый', 'Ретрансляция', 'Задержка(с)', 'Дубли', 'relays', 'Событий')
        self.tree = ttk.Treeview(packets_frame, columns=columns, show='headings', height=15)

        # Настройка колонок
        col_widths = [40, 80, 80, 60, 60, 60, 80, 80, 40]
        for col, width in zip(columns, col_widths):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor=tk.CENTER)

        # Скроллбар для таблицы
        tree_scroll = ttk.Scrollbar(packets_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Конфигурация расширения для таблицы
        packets_frame.columnconfigure(0, weight=1)
        packets_frame.rowconfigure(0, weight=1)

        # Детали пакета
        details_frame = ttk.LabelFrame(main_frame, text="Детали пакета", padding="10")
        details_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Текстовая область для деталей
        self.details_text = scrolledtext.ScrolledText(details_frame, height=15)
        self.details_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Конфигурация расширения
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)

        # Привязка события выбора в таблице
        self.tree.bind('<<TreeviewSelect>>', self.on_packet_select)

    def start_serial_reading(self):
        """Запускает чтение из последовательного порта"""
        if self.serial_reader.start():
            self.serial_running = True
            self.serial_indicator.config(text="🟢 Подключен")
            self.update_status("Сериал порт подключен")
            return True
        else:
            self.serial_indicator.config(text="🔴 Ошибка")
            self.update_status("Ошибка подключения к сериал порту")
            return False


    def update_gui(self):
        """Обновляет GUI"""
        if self.serial_running:
            # Читаем новые данные
            lines = self.serial_reader.get_data()
            for line in lines:
                self.parser.parse_line(line)

            # Обновляем статистику каждые 2 секунды
            self.update_statistics()

        # Планируем следующее обновление
        self.root.after(2000, self.update_gui)

    def update_statistics(self):
        """Обновляет статистику в статус баре"""
        stats = self.parser.get_statistics()
        
        # Обновляем метки в статус баре
        self.stats_labels['packets'].config(text=f"📦 Пакеты: {stats['total_packets']}")
        self.stats_labels['retrans'].config(text=f"🔄 Ретранс: {stats['retransmitted_packets']}")
        self.stats_labels['nodes'].config(text=f"📡 Узлы: {stats['unique_nodes']}")
        self.stats_labels['busy'].config(text=f"⚠️ BusyRx: {stats['busy_rx_count']}")
        self.stats_labels['delay'].config(text=f"⏱️ Задержка: {stats['avg_retransmission_delay']:.1f}с")


        # Обновляем таблицу пакетов
        self.update_packets_table()

    def update_packets_table(self):
        """Обновляет таблицу пакетов"""

        # Сохраняем ID текущего выделенного пакета
        current_selection = self.tree.selection()
        selected_packet_id = None
        item_to_select = None
    
        if current_selection:
            selected_item = current_selection[0]
            item_values = self.tree.item(selected_item, 'values')
            if item_values:
                selected_packet_id = item_values[0]  # Первая колонка = ID пакета

        # Очищаем таблицу
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Добавляем данные
        summaries = self.parser.get_all_packet_summaries()
        packet_id_display= None
        item_id = None
        for summary in summaries:
            delay_str = f"{summary['delay_seconds']:.2f}" if summary['delay_seconds']!=None else "N/A"
            packet_id_display = f"0x{summary['packet_id']}"

            from_node = self.get_display_name(summary['from_node'])
            to_node = self.get_display_name(summary['to_node'])

            rs = summary['relays']
            relays = [self.relayinfo.get(x, x) for x in rs]

            item_id = self.tree.insert('', 'end', values=(
                packet_id_display,
                #summary['message'],
                from_node,
                to_node,
                summary['first_received'] or 'N/A',
                summary['retransmission_time'] or 'N/A',
                delay_str,
                summary['duplicate_count'],
                relays,
                summary['event_count']
            ))

            # Если это искомый пакет - запоминаем для выделения
            if packet_id_display == selected_packet_id:
                item_to_select = item_id

        # Выделяем нужный элемент
        if item_to_select:
            self.tree.unbind('<<TreeviewSelect>>')
            self.tree.selection_set(item_to_select)
            self.root.after(10, lambda: self.tree.bind('<<TreeviewSelect>>', self.on_packet_select))
            #self.tree.focus(item_to_select)
            #self.tree.see(item_to_select)    


            # Автоскролл в конец
        if self.autoscroll.get():    
            if self.tree.get_children():
                last_item = self.tree.get_children()[-1]
                self.tree.see(last_item)    

    def on_packet_select(self, event):
        """Обрабатывает выбор пакета в таблице"""
        selection = self.tree.selection()
        if not selection:
            return

        item = self.tree.item(selection[0])
        packet_id_hex = item['values'][0]
        packet_id = packet_id_hex[2:] if packet_id_hex.startswith('0x') else packet_id_hex

        # Показываем детали пакета
        self.show_packet_details(packet_id)

    def show_packet_details(self, packet_id):
        """Показывает детали выбранного пакета"""
        events = self.parser.messages.get(packet_id, [])

        details = f"Детали пакета 0x{packet_id}:\n"
        details += "="*80 + "\n\n"

        # Группируем события по типу
        event_types = defaultdict(list)
        for event in events:
            event_types[event['event_type']].append(event)

        # Показываем события в хронологическом порядке
        events_sorted = sorted(events, key=lambda x: x['raw_time'])

        for event in events_sorted:
            timestamp = event['timestamp']
            event_type = event['event_type']
            from_node = f"0x{event['from_node']}" if event['from_node'] else 'N/A'
            relay = f"0x{event['relay_node']}" if event['relay_node'] else 'N/A'
            msg = event['message'] or 'N/A'

            details += f"{timestamp} - {event_type}\n" 
            details += f"  От: {from_node}, Сообщение: {msg}\n"
            if event['relay_node']:
                details += f"  Ретранслятор: {relay}, HopLim: {event['hop_lim']}\n"
            if event['rx_snr'] is not None:
                if event_type == "RX_NEW":
                    details += f"  SNR: {event['rx_snr']}, RSSI: {event['rx_rssi']}\n"
            if self.rawline.get():
                details += f"raw: { event['raw_line']}\n"
            details += "\n"
            

        self.details_text.delete(1.0, tk.END)
        self.details_text.insert(1.0, details)

    def export_json(self):
        """Экспортирует данные в JSON файл"""
        try:
            data = {
                'statistics': self.parser.get_statistics(),
                'packets': self.parser.get_all_packet_summaries(),
                'nodes': list(self.parser.nodes)
            }

            filename = f"mesh_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            messagebox.showinfo("Экспорт", f"Данные экспортированы в {filename}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка экспорта: {e}")

    def clear_data(self):
        """Очищает все данные"""
        self.parser = LogParser()
        self.update_statistics()
        self.details_text.delete(1.0, tk.END)
        messagebox.showinfo("Очистка", "Данные очищены")

    def run(self):
        """Запускает главный цикл GUI"""
        self.root.mainloop()

    def __del__(self):
        """Останавливает чтение сериала при завершении"""
        if self.serial_running:
            self.serial_reader.stop()


class ConnectionDialog:
    def __init__(self, parent):
        self.parent = parent
        self.result = None
        
    def show(self):
        dialog = tk.Toplevel(self.parent)
        dialog.title("Выберите источник данных")
        dialog.geometry("400x300")
        dialog.transient(self.parent)
        #dialog.grab_set()
        
        ttk.Label(dialog, text="Выберите способ загрузки данных:", 
                 font=('TkDefaultFont', 10, 'bold')).pack(pady=10)
        
        # Фрейм для кнопок
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=20)
        
        # Кнопка выбора COM-порта
        ttk.Button(btn_frame, text="📡 Подключиться к COM-порту",
                  command=lambda: self.select_serial(dialog),
                  width=30).pack(pady=5)
        
        # Кнопка загрузки из файла
        ttk.Button(btn_frame, text="📁 Загрузить из файла лога",
                  command=lambda: self.select_file(dialog),
                  width=30).pack(pady=5)
        
        # Кнопка отмены
        ttk.Button(btn_frame, text="❌ Отмена",
                  command=lambda: self.cancel(dialog),
                  width=30).pack(pady=20)
        
        self.parent.wait_window(dialog)
        return self.result
    
    def select_serial(self, dialog):
        ports = SerialReader.find_serial_ports()
        
        if not ports:
            messagebox.showwarning("Порты не найдены", 
                                 "Не найдены доступные COM-порты")
            return
        
        # Создаем диалог выбора порта
        port_dialog = tk.Toplevel(dialog)
        port_dialog.title("Выберите COM-порт")
        port_dialog.geometry("300x400")
        port_dialog.grab_set() 
        
        ttk.Label(port_dialog, text="Доступные порты:").pack(pady=10)
        
        listbox = tk.Listbox(port_dialog, height=10)
        listbox.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        
        for port in ports:
            listbox.insert(tk.END, port)
        
        def on_select():
            selection = listbox.curselection()
            if selection:
                selected_port = listbox.get(selection[0])
                self.result = ('serial', selected_port)
                port_dialog.destroy()
                dialog.destroy()
        
        ttk.Button(port_dialog, text="Выбрать", command=on_select).pack(pady=5)
        listbox.bind('<Enter>', lambda e: on_select())
        listbox.bind('<Double-Button-1>', lambda e: on_select())
        
    def select_file(self, dialog):
        filename = filedialog.askopenfilename(
            title="Выберите файл лога",
            filetypes=[("Text files", "*.txt *.log"), ("All files", "*.*")]
        )
        if filename:
            self.result = ('file', filename)
            dialog.destroy()
    
    def cancel(self, dialog):
        self.result = None
        dialog.destroy()


# Запуск приложения
if __name__ == "__main__":
    app = LogAnalyzerGUI()
    if hasattr(app, 'connection_established') and app.connection_established:
        app.run()