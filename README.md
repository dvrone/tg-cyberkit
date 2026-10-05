# Telegram Data Collector

Telegram message collector and local analysis toolkit for authorized cybersecurity monitoring and threat intelligence research.

## Features

- Telegram message collection
- SQLite / CSV storage
- SQLAlchemy database layer
- Rich CLI
- Local LLM analysis via Ollama
- Suspicious message prefiltering
- Risk scoring and classification

## Stack

- Python 3.11+
- Telethon
- SQLAlchemy
- Rich
- Ollama
- Qwen3.5 4B

## Usage

### Collector

```bash
python tdcpro.py
````

### Analyze SQLite

```bash
python analyzer/analyzer.py \
  --db data/otc.db \
  --output exports/analysis.csv \
  --limit 20
```

### Analyze CSV

```bash
python analyzer/analyzer.py \
  --csv data/messages.csv \
  --output exports/analysis.csv \
  --limit 20
```

## Workflow

```text
Telegram
   ↓
Collector
   ↓
SQLite / CSV
   ↓
Prefilter
   ↓
Local LLM
   ↓
Analysis Results
```

> ⚠️ Use only with properly authorized Telegram groups and data.

## License

See `LICENSE`.
