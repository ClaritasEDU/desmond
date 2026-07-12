#!/bin/bash
#
# iMessage Exporter Setup Script
# Run this once to set everything up
#

echo "=================================="
echo "  iMessage Exporter Setup"
echo "=================================="
echo ""

# Get the current username
USERNAME=$(whoami)

# Define paths
SCRIPT_DIR="$HOME"
SCRIPT_PATH="$SCRIPT_DIR/imessage_exporter.py"
PLIST_NAME="com.user.imessage-exporter.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"
EXPORT_DIR="$HOME/Downloads/iMessages_Export"

echo "Step 1: Creating export directory..."
mkdir -p "$EXPORT_DIR"
echo "✓ Created: $EXPORT_DIR"
echo ""

echo "Step 2: Installing the exporter script..."
# The Python script should already be in the same directory as this setup script
if [ -f "./imessage_exporter.py" ]; then
    cp ./imessage_exporter.py "$SCRIPT_PATH"
    chmod +x "$SCRIPT_PATH"
    echo "✓ Installed: $SCRIPT_PATH"
else
    echo "✗ Error: imessage_exporter.py not found in current directory"
    echo "  Make sure both files are in the same folder"
    exit 1
fi
echo ""

echo "Step 3: Setting up automatic scheduling..."
mkdir -p "$HOME/Library/LaunchAgents"

# Create the plist with correct username
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.imessage-exporter</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$SCRIPT_PATH</string>
    </array>
    
    <key>StartInterval</key>
    <integer>3600</integer>
    
    <key>RunAtLoad</key>
    <true/>
    
    <key>StandardOutPath</key>
    <string>/tmp/imessage_exporter.log</string>
    
    <key>StandardErrorPath</key>
    <string>/tmp/imessage_exporter_error.log</string>
</dict>
</plist>
EOF

echo "✓ Created scheduler: $PLIST_PATH"
echo ""

echo "Step 4: Loading the scheduler..."
launchctl unload "$PLIST_PATH" 2>/dev/null
launchctl load "$PLIST_PATH"
echo "✓ Scheduler loaded (runs every hour)"
echo ""

echo "Step 5: Running initial full export..."
echo "(This may take a while if you have lots of messages)"
echo ""
python3 "$SCRIPT_PATH" --full
echo ""

echo "=================================="
echo "  Setup Complete!"
echo "=================================="
echo ""
echo "Your messages are now being exported to:"
echo "  $EXPORT_DIR"
echo ""
echo "The exporter will run automatically every hour."
echo ""
echo "IMPORTANT — permissions (macOS grants these per app):"
echo "  1. Open System Settings > Privacy & Security > Full Disk Access"
echo "  2. Add Terminal (or iTerm) — this covers manual runs"
echo "  3. ALSO add python3 for the HOURLY runs: press Cmd+Shift+G in the"
echo "     file picker and add the file this prints:"
echo "       $(readlink -f "$(command -v python3)" 2>/dev/null || command -v python3)"
echo "     (launchd runs python3 directly, so Terminal's access does NOT"
echo "      carry over — without this the hourly export can't read Messages)"
echo "  4. Restart Terminal"
echo ""
echo "If hourly exports don't appear, check: cat /tmp/imessage_exporter_error.log"
echo ""
echo "Useful commands:"
echo "  • Run manually:  python3 ~/imessage_exporter.py"
echo "  • Full re-export: python3 ~/imessage_exporter.py --full"
echo "  • Check logs:     cat /tmp/imessage_exporter.log"
echo "  • Stop auto-run:  launchctl unload ~/Library/LaunchAgents/$PLIST_NAME"
echo ""
