const screenshot = require('screenshot-desktop');
const WebSocket = require('ws');
const { exec } = require('child_process');
const { ipcRenderer } = require('electron');

let ws = null;
let isRunning = false;
let screenInterval = null;

const roomInput = document.getElementById('roomInput');
const connectBtn = document.getElementById('connectBtn');
const disconnectBtn = document.getElementById('disconnectBtn');
const statusText = document.getElementById('statusText');

// Screen size
let screenWidth = 1920;
let screenHeight = 1080;

// Get screen size from main process
ipcRenderer.on('screen-size', (event, size) => {
  screenWidth = size.width;
  screenHeight = size.height;
  console.log('Screen size received:', screenWidth, 'x', screenHeight);
});

// Request screen size on startup
ipcRenderer.send('get-screen-size');

// Connect button
connectBtn.addEventListener('click', () => {
  const room = roomInput.value.trim();
  
  if (!room) {
    alert('Please enter Room ID');
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

// Connect to server
function connect(room) {
  const wsUrl = `wss://deskweb.duckdns.org/ws/${room}/client`;
  
  updateStatus('connecting', 'Connecting...');
  connectBtn.disabled = true;
  roomInput.disabled = true;

  ws = new WebSocket(wsUrl, {
    rejectUnauthorized: false
  });

  ws.on('open', () => {
    console.log('Connected to server');
    isRunning = true;
    updateStatus('connected', 'Connected');
    disconnectBtn.disabled = false;
    
    startScreenSharing();
  });

  ws.on('message', (data) => {
    try {
      const message = JSON.parse(data.toString());
      handleCommand(message);
    } catch (err) {
      console.error('Message parse error:', err);
    }
  });

  ws.on('close', () => {
    console.log('Connection closed');
    disconnect();
  });

  ws.on('error', (err) => {
    console.error('WebSocket error:', err);
    updateStatus('error', 'Connection error');
    setTimeout(() => {
      disconnect();
    }, 1000);
  });
}

// Disconnect
function disconnect() {
  isRunning = false;
  
  if (screenInterval) {
    clearInterval(screenInterval);
    screenInterval = null;
  }

  if (ws) {
    try {
      ws.close();
    } catch (err) {
      console.error('Close error:', err);
    }
    ws = null;
  }

  connectBtn.disabled = false;
  disconnectBtn.disabled = true;
  roomInput.disabled = false;
  updateStatus('disconnected', 'Disconnected');
}

// Start screen sharing - Python style (25 FPS)
function startScreenSharing() {
  const fps = 25;
  const interval = 40; // 1000/25 = 40ms

  console.log('Starting screen sharing at', fps, 'FPS');

  // Use setInterval like Python's asyncio.sleep(0.04)
  screenInterval = setInterval(async () => {
    if (!isRunning || !ws || ws.readyState !== WebSocket.OPEN) {
      return;
    }

    try {
      // Capture screenshot (PNG format like Python)
      const imgBuffer = await screenshot({ format: 'png' });
      const base64 = imgBuffer.toString('base64');

      // Send exactly like Python
      ws.send(JSON.stringify({
        type: 'screen',
        data: base64,
        width: screenWidth,
        height: screenHeight
      }));

    } catch (err) {
      console.error('Screenshot error:', err);
    }
  }, interval);
}

// Handle commands from operator
function handleCommand(message) {
  if (!isRunning) return;

  const { type } = message;

  try {
    switch (type) {
      case 'mouse_move':
        const moveX = Math.round(message.x * screenWidth);
        const moveY = Math.round(message.y * screenHeight);
        moveMouse(moveX, moveY);
        break;

      case 'mouse_click':
        const clickX = Math.round(message.x * screenWidth);
        const clickY = Math.round(message.y * screenHeight);
        clickMouse(clickX, clickY, message.button || 'left');
        break;

      case 'scroll':
        scrollMouse(message.delta);
        break;

      case 'type_text':
        typeText(message.text);
        break;

      case 'hotkey':
        executeHotkey(message.keys);
        break;

      case 'key_down':
        pressKey(message.key, true);
        break;

      case 'key_up':
        pressKey(message.key, false);
        break;

      case 'peer_disconnected':
        disconnect();
        alert('Operator disconnected');
        break;
    }
  } catch (err) {
    console.error('Command execution error:', err);
  }
}

// Mouse move using PowerShell
function moveMouse(x, y) {
  const script = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point(${x}, ${y})`;
  exec(`powershell -Command "${script}"`, (err) => {
    if (err) console.error('Mouse move error:', err);
  });
}

// Mouse click using PowerShell
function clickMouse(x, y, button) {
  moveMouse(x, y);
  
  setTimeout(() => {
    if (button === 'right') {
      const script = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait("+{F10}")`;
      exec(`powershell -Command "${script}"`, (err) => {
        if (err) console.error('Right click error:', err);
      });
    } else {
      const script = `Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Mouse {
  [DllImport("user32.dll")]
  public static extern void mouse_event(int flags, int dx, int dy, int cButtons, int info);
}
"@
[Mouse]::mouse_event(0x02, 0, 0, 0, 0)
[Mouse]::mouse_event(0x04, 0, 0, 0, 0)`;
      exec(`powershell -Command "${script}"`, (err) => {
        if (err) console.error('Left click error:', err);
      });
    }
  }, 50);
}

// Scroll mouse
function scrollMouse(delta) {
  const script = `Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Mouse {
  [DllImport("user32.dll")]
  public static extern void mouse_event(int flags, int dx, int dy, int cButtons, int info);
}
"@
[Mouse]::mouse_event(0x0800, 0, 0, ${delta * 120}, 0)`;
  exec(`powershell -Command "${script}"`, (err) => {
    if (err) console.error('Scroll error:', err);
  });
}

// Type text
function typeText(text) {
  const escapedText = text.replace(/[+^%~(){}]/g, '{$&}').replace(/"/g, '""');
  const script = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait("${escapedText}")`;
  exec(`powershell -Command "${script}"`, (err) => {
    if (err) console.error('Type text error:', err);
  });
}

// Press key
function pressKey(key, down) {
  const keyMap = {
    'enter': '{ENTER}',
    'escape': '{ESC}',
    'esc': '{ESC}',
    'tab': '{TAB}',
    'backspace': '{BACKSPACE}',
    'delete': '{DELETE}',
    'home': '{HOME}',
    'end': '{END}',
    'pageup': '{PGUP}',
    'pagedown': '{PGDN}',
    'left': '{LEFT}',
    'right': '{RIGHT}',
    'up': '{UP}',
    'down': '{DOWN}',
    'f1': '{F1}',
    'f2': '{F2}',
    'f3': '{F3}',
    'f4': '{F4}',
    'f5': '{F5}',
    'f6': '{F6}',
    'f7': '{F7}',
    'f8': '{F8}',
    'f9': '{F9}',
    'f10': '{F10}',
    'f11': '{F11}',
    'f12': '{F12}'
  };

  const mappedKey = keyMap[key.toLowerCase()] || key;
  
  const script = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait("${mappedKey}")`;
  exec(`powershell -Command "${script}"`, (err) => {
    if (err) console.error('Key press error:', err);
  });
}

// Execute hotkey combinations
function executeHotkey(keys) {
  let hotkeyStr = '';

  keys.forEach(key => {
    switch(key) {
      case 'ctrl':
        hotkeyStr += '^';
        break;
      case 'alt':
        hotkeyStr += '%';
        break;
      case 'shift':
        hotkeyStr += '+';
        break;
      case 'win':
        hotkeyStr += '^{ESC}';
        return;
      default:
        const specialKeys = {
          'r': 'r',
          'e': 'e',
          'd': 'd',
          'l': 'l',
          'x': 'x',
          'tab': '{TAB}',
          'esc': '{ESC}',
          'f4': '{F4}'
        };
        hotkeyStr += specialKeys[key.toLowerCase()] || key;
    }
  });

  const script = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait("${hotkeyStr}")`;
  exec(`powershell -Command "${script}"`, (err) => {
    if (err) console.error('Hotkey error:', err);
  });
}

// Update status UI
function updateStatus(status, text) {
  statusText.textContent = `● ${text}`;
  statusText.className = `status-${status}`;
}