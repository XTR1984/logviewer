#  Meshtastic Node Log Analyzer

Навайбкоженый просмотрщик логов с ноды Мештастика по Serial-порту или из файла \
Не сказать чтобы очень полезно, но местами интересно \
GUI-интерфейс - Python Tkinter \
Писано под python3.10 \

Запуск:
```bash
pip3 install -r requirements.txt
python3 logviewer.py
```

Для отображения имён нод нужен файлик nodes.txt с таблицей нод. \
Пока что он рождается с помощью mestastic-cli (https://meshtastic.org/docs/software/python/cli/installation/):
```bash
    meshtastic --nodes > nodes.txt
```


Для отображения релеев(от кого прилетел пакет) в именованом виде - формируем файлик relay.txt по формату
```bash
    AA:name1
    BB:name2
```
увы всего 1 байт на идентификацию 



