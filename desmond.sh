#!/bin/bash
#
# DESMOND
# "See you in another life, brother."
#
# Inspired by Desmond Hume from Lost, who pushed the button
# every 108 minutes to save the world.
#
# This Desmond pushes Sync Now to save your messages — but only if needed.
#
# To stop it: press Control + C
#

echo ""
echo "  ██████╗ ███████╗███████╗███╗   ███╗ ██████╗ ███╗   ██╗██████╗ "
echo "  ██╔══██╗██╔════╝██╔════╝████╗ ████║██╔═══██╗████╗  ██║██╔══██╗"
echo "  ██║  ██║█████╗  ███████╗██╔████╔██║██║   ██║██╔██╗ ██║██║  ██║"
echo "  ██║  ██║██╔══╝  ╚════██║██║╚██╔╝██║██║   ██║██║╚██╗██║██║  ██║"
echo "  ██████╔╝███████╗███████║██║ ╚═╝ ██║╚██████╔╝██║ ╚████║██████╔╝"
echo "  ╚═════╝ ╚══════╝╚══════╝╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═════╝ "
echo ""
echo "  \"We have to push the button.\""
echo "  \"4 8 15 16 23 42\""
echo ""

# Check if target was provided
TARGET_MESSAGES=$1
if [ -n "$TARGET_MESSAGES" ]; then
    echo "  Target: $TARGET_MESSAGES messages"
    echo ""
fi

echo "  Press Control + C to stop."
echo ""

count=0
last_msg_count=0
stall_count=0
MAX_STALLS=45  # ~12 min of no growth (each check is ~16s) — iCloud sync
               # routinely pauses for minutes; giving up after ~1 min would
               # declare victory with most history still un-synced.

while true; do
    count=$((count + 1))
    timestamp=$(date "+%H:%M:%S")

    # Get current message count. If sqlite3 fails (no Full Disk Access, no
    # chat.db), STOP with the real reason instead of counting "" as a stall
    # and printing a bogus "SYNC APPEARS COMPLETE".
    current_msg_count=$(sqlite3 ~/Library/Messages/chat.db "SELECT COUNT(*) FROM message;" 2>/dev/null)
    conv_count=$(sqlite3 ~/Library/Messages/chat.db "SELECT COUNT(DISTINCT chat_id) FROM chat_message_join;" 2>/dev/null)
    if ! [ "$current_msg_count" -ge 0 ] 2>/dev/null; then
        echo "[$timestamp] ERROR: could not read ~/Library/Messages/chat.db."
        echo "[$timestamp] Give Terminal Full Disk Access (System Settings >"
        echo "[$timestamp] Privacy & Security > Full Disk Access), restart"
        echo "[$timestamp] Terminal, and run ./desmond.sh again."
        exit 1
    fi

    # First run - just record the count and skip straight to syncing
    if [ $count -eq 1 ]; then
        echo "[$timestamp] ====== STARTING ======"
        echo "[$timestamp] Messages on Mac: $current_msg_count"
        echo "[$timestamp] Conversations: $conv_count"
        if [ -n "$TARGET_MESSAGES" ]; then
            remaining=$((TARGET_MESSAGES - current_msg_count))
            echo "[$timestamp] Remaining: ~$remaining messages"
        fi
        echo "[$timestamp] ========================"
        echo ""
        last_msg_count=$current_msg_count
    fi

    # Check if we've hit target
    if [ -n "$TARGET_MESSAGES" ] && [ "$current_msg_count" -ge "$TARGET_MESSAGES" ]; then
        echo ""
        echo "[$timestamp] ====== SYNC COMPLETE ======"
        echo "[$timestamp] Reached target: $current_msg_count messages"
        echo "[$timestamp] \"See you in another life, brother.\""
        echo ""
        exit 0
    fi
    
    # Check if messages are still growing (iteration 1 only recorded the
    # baseline — it can't be a "stall" yet)
    if [ "$current_msg_count" -gt "$last_msg_count" ]; then
        new_msgs=$((current_msg_count - last_msg_count))
        echo "[$timestamp] Push #$count - +$new_msgs new messages (total: $current_msg_count)"
        stall_count=0
        last_msg_count=$current_msg_count
    elif [ $count -gt 1 ]; then
        stall_count=$((stall_count + 1))
        echo "[$timestamp] Push #$count - No new messages (check $stall_count/$MAX_STALLS)"
        
        if [ $stall_count -ge $MAX_STALLS ]; then
            echo ""
            echo "[$timestamp] ====== SYNC APPEARS COMPLETE ======"
            echo "[$timestamp] No new messages for $MAX_STALLS checks."
            echo "[$timestamp] Final count: $current_msg_count messages in $conv_count conversations"
            echo ""
            echo "[$timestamp] If this seems low, run again with your target:"
            echo "[$timestamp]   ./desmond.sh 344254"
            echo ""
            echo "[$timestamp] \"See you in another life, brother.\""
            echo ""
            exit 0
        fi
    fi
    
    # Click the Sync Now button
    osascript <<EOF 2>/dev/null
        tell application "Messages" to activate
        delay 0.3
        tell application "System Events"
            tell process "Messages"
                keystroke "," using command down
                delay 0.5
                try
                    click button "Sync Now" of group 1 of group 1 of window "iMessage"
                    delay 0.2
                end try
            end tell
        end tell
EOF
    
    # Every 12 cycles (3 minutes), show detailed status
    if [ $((count % 12)) -eq 0 ]; then
        echo ""
        echo "[$timestamp] ====== THE NUMBERS ======"
        echo "[$timestamp] Messages on Mac: $current_msg_count"
        echo "[$timestamp] Conversations: $conv_count"
        if [ -n "$TARGET_MESSAGES" ]; then
            remaining=$((TARGET_MESSAGES - current_msg_count))
            pct=$((current_msg_count * 100 / TARGET_MESSAGES))
            echo "[$timestamp] Progress: $pct% (~$remaining remaining)"
        fi
        echo "[$timestamp] ==========================="
        echo ""
    fi
    
    sleep 15
done
