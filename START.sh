#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "🚀 Starting FinancialGenie Mapping Editor"
echo "   Backend:  http://localhost:8765"
echo "   Frontend: http://localhost:5173"
echo ""

# Kill any existing processes on the ports
fuser -k 8765/tcp 2>/dev/null || true
fuser -k 5173/tcp 2>/dev/null || true
sleep 1

# Start backend
echo "▶ Starting backend..."
cd "$ROOT"
python3 backend/server.py &
BACKEND_PID=$!
sleep 3

# Check if backend started
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "❌ Backend failed to start"
    exit 1
fi
echo "   Backend PID: $BACKEND_PID"

# Start frontend
echo "▶ Starting frontend..."
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
sleep 3

echo ""
echo "✅ Mapping Editor is running!"
echo "   Open http://localhost:5173 in your browser"
echo ""
echo "Press Ctrl+C to stop both servers"

# Trap cleanup
cleanup() {
    echo ""
    echo "⏹ Stopping servers..."
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    fuser -k 8765/tcp 2>/dev/null || true
    echo "Done."
}
trap cleanup EXIT INT TERM

wait
