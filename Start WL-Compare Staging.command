#!/bin/zsh
# Doppelklick-Starter fuer das SwiPay Worldline-Vergleichstool.
# Liegt bewusst im Projektordner, damit er den Umzug automatisch mitmacht.

cd "$(dirname "$0")"

URL="http://localhost:8501"

echo "SwiPay · Worldline-Vergleich"
echo "============================"

if lsof -i :8501 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Läuft bereits auf Port 8501 — öffne Browser..."
    open "$URL"
    sleep 1
    exit 0
fi

echo "Starte Streamlit ..."
echo "(dieses Fenster offen lassen, solange du das Tool benutzt)"
echo ""

# Den Browser erst öffnen, wenn der Server wirklich antwortet. Streamlit
# selbst tut es nicht: .streamlit/config.toml setzt headless = true, damit
# der Browser-Pane der Entwicklungsumgebung kein Fenster aufreisst. Sofort
# zu öffnen würde auf einer Fehlerseite landen -- der Start dauert ein paar
# Sekunden. Der Health-Endpunkt antwortet erst, wenn die App bereit ist.
(
    for _ in {1..60}; do
        if curl -sf -o /dev/null "$URL/_stcore/health" 2>/dev/null; then
            open "$URL"
            exit 0
        fi
        sleep 0.5
    done
    echo "Server antwortet nicht — bitte $URL von Hand öffnen."
) &

uv run streamlit run app.py
