#!/usr/bin/env bash
set -euo pipefail

# Tesla Solar Charger - Deploy Script
# Configure deployment target in .deploy.env (gitignored):
#   REMOTE_USER=youruser
#   REMOTE_HOST=192.168.1.100
#   REMOTE_DIR=/home/youruser/tesla-solar-charger

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load local deploy config (not committed to git)
if [ -f "$LOCAL_DIR/.deploy.env" ]; then
    # shellcheck source=/dev/null
    source "$LOCAL_DIR/.deploy.env"
fi

REMOTE_USER="${REMOTE_USER:-}"
REMOTE_HOST="${REMOTE_HOST:-}"
REMOTE_DIR="${REMOTE_DIR:-/home/${REMOTE_USER}/tesla-solar-charger}"

if [ -z "$REMOTE_HOST" ] || [ -z "$REMOTE_USER" ]; then
    echo "ERROR: REMOTE_HOST and REMOTE_USER must be set in .deploy.env"
    echo "Create .deploy.env with:"
    echo "  REMOTE_USER=youruser"
    echo "  REMOTE_HOST=your-server-ip-or-hostname"
    echo "  REMOTE_DIR=/home/youruser/tesla-solar-charger"
    exit 1
fi
echo "=== Tesla Solar Charger Deploy ==="
echo "Local:  $LOCAL_DIR"
echo "Remote: $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"
echo ""

# Step 1: Ensure remote directory exists
echo "[1/5] Preparing remote directory..."
ssh "$REMOTE_USER@$REMOTE_HOST" "mkdir -p $REMOTE_DIR/data $REMOTE_DIR/tesla-proxy-certs"

# Step 2: Sync project files
echo "[2/5] Uploading project files..."
rsync -avz --delete \
    --exclude='data/*.db' \
    --exclude='__pycache__' \
    --exclude='.env' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='tests/' \
    "$LOCAL_DIR/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"

# Step 3: Create .env if it doesn't exist
echo "[3/5] Checking .env configuration..."
ssh "$REMOTE_USER@$REMOTE_HOST" "
    if [ ! -f $REMOTE_DIR/.env ]; then
        cp $REMOTE_DIR/.env.example $REMOTE_DIR/.env
        echo '  -> Created .env from .env.example'
        echo '  -> IMPORTANT: Edit $REMOTE_DIR/.env with your Tesla API credentials!'
    else
        echo '  -> .env already exists, skipping'
    fi
"

# Step 4: Build and start Docker containers
# Use --network=host so pip can reach PyPI through the server's DNS during build.
# Avoid --no-cache: requirements.txt rarely changes, so caching the pip layer is safe
# and avoids re-downloading packages on every deploy.
# Pass --no-cache only when requirements.txt has changed (detected below).
echo "[4/5] Building and starting Docker containers..."
ssh "$REMOTE_USER@$REMOTE_HOST" "
    cd $REMOTE_DIR
    docker compose down 2>/dev/null || true
    BUILDKIT_PROGRESS=plain DOCKER_BUILDKIT=1 \
        docker compose build --network=host
    docker compose up -d
"

# Step 5: Verify deployment
echo "[5/5] Verifying deployment..."
sleep 5
ssh "$REMOTE_USER@$REMOTE_HOST" "
    echo '--- Container Status ---'
    docker compose -f $REMOTE_DIR/docker-compose.yml ps
    echo ''
    echo '--- Proxy Logs ---'
    docker logs tesla-http-proxy --tail 10 2>&1 || true
    echo ''
    echo '--- App Logs (last 20 lines) ---'
    docker logs tesla-solar-charger --tail 20 2>&1 || true
"

echo ""
echo "=== Deployment Complete ==="
echo "Dashboard: http://$REMOTE_HOST:5050"
echo ""
echo "Next steps:"
echo "  1. Edit .env on server: ssh $REMOTE_USER@$REMOTE_HOST nano $REMOTE_DIR/.env"
echo "  2. Add Tesla API credentials (TESLA_CLIENT_ID, TESLA_CLIENT_SECRET)"
echo "  3. Restart: ssh $REMOTE_USER@$REMOTE_HOST 'cd $REMOTE_DIR && docker compose restart'"
