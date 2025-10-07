#!/bin/bash
# start_nodes.sh - launches all Grandpa nodes for local testing

# Node IDs and ports
NODE_IDS=(0 1 2 3 4)
BASE_PORT=13000
KEYS_FILE="keys.json"
CONFIG_FILE="nodes_config.json"

# Function to clean up all child processes on Ctrl+C
cleanup() {
    echo ""
    echo "Stopping all Grandpa nodes..."
    kill $(jobs -p) 2>/dev/null
    exit 0
}

# Catch Ctrl+C
trap cleanup SIGINT

# Launch all nodes in background
for NODE_ID in "${NODE_IDS[@]}"; do
    PORT=$((BASE_PORT + NODE_ID))
    echo "Starting node $NODE_ID on port $PORT..."
    python3 grandpa_prod.py --id $NODE_ID --keys $KEYS_FILE --config $CONFIG_FILE &
    sleep 0.2  # slight stagger to avoid TCP bind conflicts
done

echo "All nodes started. Waiting for full peer connectivity..."
sleep 5  # wait a few seconds for all TCP connections to establish

echo "Nodes should now be connected. Grandpa rounds can proceed."

# Wait for all background processes
wait
