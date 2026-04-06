# Picon Generator Server

Dynamický HTTP server pro generování picon obrázků (loga TV/rádio stanic) s příznaky.
Kompatibilní s Kodi a TVheadend.

## Instalace

### Požadavky
- Python 3.10+

### Závislosti
```
pip install -r requirements.txt
```

### Windows 10
```
pip install -r requirements.txt
python server.py
```

### Linux
```
pip3 install -r requirements.txt
python3 server.py
```

## Konfigurace

Zkopíruj `config.yaml.sample` do `config.yaml` a uprav dle potřeby:
```
cp config.yaml.sample config.yaml
```

Zkopíruj `remap.txt.sample` do `remap.txt`:
```
cp remap.txt.sample remap.txt
```

Hlavní nastavení v `config.yaml`:
- `server.port` – port serveru (výchozí 8083)
- `cache.dnu_v_kesi` – počet dní pro disk cache (0 = vypnuto)
- `sources.github.url` – URL zdroje piconů na GitHubu
- `sources.remote.url` – vlastní vzdálené úložiště (volitelné)

## Použití

### Spuštění
```
python server.py
python server.py --port 9000
python server.py --debug
```

### Endpointy

| Endpoint | Popis |
|---|---|
| `GET /picons/<název>` | Vrátí picon PNG – Kodi/TVheadend kompatibilní |
| `GET /picons/<část1>/<část2>` | Podpora lomítka v názvu kanálu |
| `GET /editor` | Webový GUI editor badges |
| `GET /badges` | JSON seznam dostupných příznaků |
| `GET /health` | Health check |

### Příklady URL
```
http://localhost:8083/picons/ČT1 HD
http://localhost:8083/picons/Nova
http://localhost:8083/picons/nova.png
```

### Kodi / TVheadend
```
http://<IP>:8083/picons/%C    (TVheadend)
```

## Struktura adresářů

```
picon-server/
  logos/          ← Lokální loga stanic (PNG)
  badges/
    shared/       ← Sdílené příznaky (dvbs2, dvbt2, iptv, radio, enc)
    skylink/      ← Custom badges pro Skylink + layout.json
    sledovanitv/
    oneplay/
    playcz/
    radia/
    ivysilani/
  cache/          ← Disk cache stažených log (auto)
```

## GUI Editor

Otevři `http://localhost:8083/editor` v prohlížeči.

- Vyber službu (Skylink, SledováníTV, ...)
- Zaškrtni které příznaky chceš zobrazovat
- Nahraj PNG obrázky pro příznaky
- Táhni badges myší na požadovanou pozici
- Uprav velikost, otočení a průhlednost táhly
- Ulož layout tlačítkem "Uložit layout"

## Příznaky (Badges)

| Typ | Soubor | Výchozí pozice |
|---|---|---|
| DVB-S2 | dvbs2.png | Pravý dolní roh |
| DVB-T2 | dvbt2.png | Pravý dolní roh |
| IPTV | iptv.png | Pravý dolní roh |
| Radio | radio.png | Levý dolní roh |
| Encrypted | enc.png | Pravý horní roh |

## Linux systemd service

```bash
sudo cp scripts/picons_server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable picons_server
sudo systemctl start picons_server
```

## Remap

V souboru `remap.txt` lze mapovat nestandardní názvy kanálů:
```
# Formát: nestandardní název>jméno picony
France 24 English>france24inenglish
```
