const WebSocket = require('ws');

let ws = null;
let isConnected = false;
let clientConnected = false;
let currentRoom = null;

// Canvas
const canvas = document.getElementById('screenCanvas');
const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });

// UI Elements
const loginScreen = document.getElementById('loginScreen');
const controlScreen = document.getElementById('controlScreen');
const roomInput = document.getElementById('roomInput');
const connectBtn = document.getElementById('connectBtn');
const generateBtn = document.getElementById('generateBtn');
const disconnectBtn = document.getElementById('disconnectBtn');
const roomLabel = document.getElementById('roomLabel');
const statusLabel = document.getElementById('statusLabel');
const fpsLabel = document.getElementById('fpsLabel');
const waitingOverlay = document.getElementById('waitingOverlay');

// FPS tracking
let frameCount = 0;
let lastFpsUpdate = Date.now();

// Image cache
let lastImage = null;

// Generate random room ID
generateBtn.addEventListener('click', () => {
  const roomId = generateRoomId();
  roomInput.value = roomId;
  roomInput.focus();
});

// Connect button
connectBtn.addEventListener('click', () => {
  const room = roomInput.value.trim();
  
  if (!room) {
    alert('Please enter or generate Room ID');
    return;
  }

  connect(room);
});

// Disconnect button
disconnectBtn.addEventListener('click', () => {
  disconnect();
});

// Enter key support
roomInput.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') {
    connectBtn.click();
  }
});

// Generate room ID
function generateRoomId() {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let result = '';
  for (let i = 0; i < 6; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}

// Connect to server
function connect(room) {
  currentRoom = room;
  const wsUrl = `wss://deskweb.duckdns.org/ws/${room}/operator`;

  console.log('Connecting to:', wsUrl);

  ws = new WebSocket(wsUrl, {
    rejectUnauthorized: false
  });

  ws.on('open', () => {
    console.log('Connected to server as operator');
    isConnected = true;
    
    // Show control screen
    loginScreen.style.display = 'none';
    controlScreen.style.display = 'flex';
    
    roomLabel.textContent = room;
    updateStatus('waiting');
    
    // Setup canvas
    resizeCanvas();
  });

  ws.on('message', (data) => {
    try {
      const message = JSON.parse(data.toString());
      handleMessage(message);
    } catch (err) {
      console.error('Message parse error:', err);
    }
  });

  ws.on('close', () => {
    console.log('Connection closed');
    if (isConnected) {
      disconnect();
    }
  });

  ws.on('error', (err) => {
    console.error('WebSocket error:', err);
    alert('Connection error. Please check server.');
    disconnect();
  });
}

// Disconnect
function disconnect() {
  isConnected = false;
  clientConnected = false;
  
  if (ws) {
    try {
      ws.close();
    } catch (err) {
      console.error('Close error:', err);
    }
    ws = null;
  }

  // Show login screen
  controlScreen.style.display = 'none';
  loginScreen.style.display = 'flex';
  
  // Clear canvas
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  
  // Reset FPS
  fpsLabel.textContent = '0';
  frameCount = 0;
}

// Handle messages from server
function handleMessage(message) {
  const { type } = message;

  switch (type) {
    case 'screen':
      if (!clientConnected) {
        clientConnected = true;
        updateStatus('connected');
        waitingOverlay.style.display = 'none';
        console.log('Client connected - receiving frames');
      }
      
      renderFrame(message.data, message.width, message.height, message.format);
      updateFPS();
      break;

    case 'peer_connected':
      console.log('Client connected (peer_connected)');
      clientConnected = true;
      updateStatus('connected');
      waitingOverlay.style.display = 'none';
      break;

    case 'peer_disconnected':
      console.log('Client disconnected');
      clientConnected = false;
      updateStatus('waiting');
      waitingOverlay.style.display = 'flex';
      
      // Clear canvas
      ctx.fillStyle = '#000';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      
      alert('Client disconnected from session');
      break;
  }
}

// Render frame - support both PNG and JPEG
function renderFrame(base64Data, imgWidth, imgHeight, format = 'png') {
  try {
    const img = new Image();
    
    img.onload = () => {
      requestAnimationFrame(() => {
        try {
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          lastImage = img;
        } catch (err) {
          console.error('Canvas draw error:', err);
        }
      });
    };
    
    img.onerror = (err) => {
      console.error('Image load error:', err);
    };
    
    // Detect format from message or default to jpeg
    const imageFormat = format || 'jpeg';
    img.src = `data:image/${imageFormat};base64,${base64Data}`;
    
  } catch (err) {
    console.error('Render frame error:', err);
  }
}

// Update FPS counter
function updateFPS() {
  frameCount++;
  const now = Date.now();
  
  if (now - lastFpsUpdate >= 1000) {
    fpsLabel.textContent = frameCount;
    frameCount = 0;
    lastFpsUpdate = now;
  }
}

// Update status badge
function updateStatus(status) {
  if (status === 'connected') {
    statusLabel.textContent = '🟢 Connected';
    statusLabel.className = 'status-badge connected';
  } else if (status === 'waiting') {
    statusLabel.textContent = '⏳ Waiting for client';
    statusLabel.className = 'status-badge waiting';
  }
}

// Resize canvas to fit container
function resizeCanvas() {
  const container = canvas.parentElement;
  const rect = container.getBoundingClientRect();
  
  canvas.width = rect.width;
  canvas.height = rect.height;
  
  console.log('Canvas resized to:', canvas.width, 'x', canvas.height);
  
  // Redraw last frame if exists
  if (lastImage && clientConnected) {
    ctx.drawImage(lastImage, 0, 0, canvas.width, canvas.height);
  }
}

// Resize on window resize
window.addEventListener('resize', () => {
  if (isConnected) {
    resizeCanvas();
  }
});

// Mouse events with proper coordinate mapping
canvas.addEventListener('mousemove', (e) => {
  if (!clientConnected) return;
  
  const rect = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;
  
  sendCommand({
    type: 'mouse_move',
    x: Math.max(0, Math.min(1, x)),
    y: Math.max(0, Math.min(1, y))
  });
});

canvas.addEventListener('mousedown', (e) => {
  if (!clientConnected) return;
  
  e.preventDefault();
  
  const rect = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;
  
  sendCommand({
    type: 'mouse_click',
    x: Math.max(0, Math.min(1, x)),
    y: Math.max(0, Math.min(1, y)),
    button: e.button === 2 ? 'right' : 'left'
  });
});

canvas.addEventListener('contextmenu', (e) => {
  e.preventDefault();
});

canvas.addEventListener('wheel', (e) => {
  if (!clientConnected) return;
  
  e.preventDefault();
  const delta = e.deltaY > 0 ? -3 : 3;
  
  sendCommand({
    type: 'scroll',
    delta: delta
  });
}, { passive: false });

// Keyboard events - FOCUS ON CANVAS
canvas.addEventListener('click', () => {
  canvas.focus();
});

canvas.setAttribute('tabindex', '0');

canvas.addEventListener('keydown', (e) => {
  if (!clientConnected) return;
  
  e.preventDefault();
  
  // Check for hotkey combinations
  if (e.ctrlKey || e.altKey || e.metaKey) {
    const keys = [];
    if (e.ctrlKey) keys.push('ctrl');
    if (e.altKey) keys.push('alt');
    if (e.shiftKey) keys.push('shift');
    if (e.metaKey) keys.push('win');
    
    const key = e.key.toLowerCase();
    if (key !== 'control' && key !== 'alt' && key !== 'shift' && key !== 'meta') {
      keys.push(key);
      
      sendCommand({
        type: 'hotkey',
        keys: keys
      });
    }
  } else if (e.key.length === 1) {
    // Regular character
    sendCommand({
      type: 'type_text',
      text: e.key
    });
  } else {
    // Special keys
    sendCommand({
      type: 'key_down',
      key: e.key.toLowerCase()
    });
  }
});

canvas.addEventListener('keyup', (e) => {
  if (!clientConnected) return;
  
  e.preventDefault();
  
  if (e.key.length > 1) {
    sendCommand({
      type: 'key_up',
      key: e.key.toLowerCase()
    });
  }
});

// Send command to client
function sendCommand(command) {
  if (ws && ws.readyState === WebSocket.OPEN && clientConnected) {
    try {
      ws.send(JSON.stringify(command));
    } catch (err) {
      console.error('Send command error:', err);
    }
  }
}

// Focus canvas on control screen show
window.addEventListener('load', () => {
  roomInput.focus();
});

// Auto-focus canvas when client connects
function focusCanvas() {
  if (clientConnected) {
    setTimeout(() => {
      canvas.focus();
      console.log('Canvas focused');
    }, 500);
  }
}

// Call after client connects
const originalHandleMessage = handleMessage;
handleMessage = function(message) {
  originalHandleMessage(message);
  
  if (message.type === 'screen' && clientConnected) {
    focusCanvas();
  }
};