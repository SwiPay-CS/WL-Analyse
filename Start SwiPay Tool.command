#!/bin/zsh
# Doppelklick-Starter fuer das SwiPay Worldline-Vergleichstool.
# Liegt bewusst im Projektordner, damit er den Umzug automatisch mitmacht.

cd "$(dirname "$0")"

echo "SwiPay · Worldline-Vergleich"
echo "============================"

if lsof -i :8501 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Läuft bereits auf Port 8501 — öffne Browser..."
    open "http://localhost:8501"
    sleep 1
    exit 0
fi

echo "Starte Streamlit ..."
echo "(dieses Fenster offen lassen, solange du das Tool benutzt)"
echo ""

uv run streamlit run app.py
