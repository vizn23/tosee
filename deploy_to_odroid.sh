#!/bin/bash
# deploy_to_odroid.sh - Jednoduchý deploy na Odroid N2+
# Použití: ./deploy_to_odroid.sh root@ODROID_IP

set -e

ODROID_HOST="${1:-root@odroid.local}"
CONFIG_PATH="/config/custom_components/tosee_doorbell"

echo "🚀 Deploying ToSee Doorbell to $ODROID_HOST..."

# Vytvoř archiv
echo "📦 Creating archive..."
tar -czf /tmp/tosee_doorbell.tar.gz -C "$(dirname "$0")" custom_components

# Zkopíruj na Odroid
echo "📤 Copying to Odroid..."
scp /tmp/tosee_doorbell.tar.gz "$ODROID_HOST:/tmp/"

# Rozbal a restartuj HA
echo "📂 Extracting on Odroid..."
ssh "$ODROID_HOST" "
    mkdir -p $CONFIG_PATH
    tar -xzf /tmp/tosee_doorbell.tar.gz -C /config
    echo '✅ Files extracted'
    
    # Restart HA (záleží na instalaci)
    if command -v ha &> /dev/null; then
        ha core restart
    elif systemctl is-active --quiet home-assistant; then
        systemctl restart home-assistant
    elif docker ps | grep -q homeassistant; then
        docker restart homeassistant
    else
        echo '⚠️ Restart HA manually'
    fi
"

echo "✅ Deploy complete! Check HA logs for 'tosee_doorbell'"