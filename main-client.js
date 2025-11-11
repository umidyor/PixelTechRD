const { app, BrowserWindow, screen, ipcMain } = require('electron');
const path = require('path');

// GPU xatolarini tuzatish
app.disableHardwareAcceleration();

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 550,
    height: 450,
    resizable: false,
    title: 'PixelTechRD Client',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });
  
  const clientPath = path.join(__dirname, 'client', 'index.html');
  mainWindow.loadFile(clientPath);

  // Xatolarni ko'rish uchun (development mode)
  // mainWindow.webContents.openDevTools();

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('Failed to load:', errorDescription);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Screen size yuborish
  mainWindow.webContents.on('did-finish-load', () => {
    sendScreenSize();
  });
}

// IPC handler - screen size olish
ipcMain.on('get-screen-size', (event) => {
  sendScreenSize();
});

// Screen size yuborish funksiyasi
function sendScreenSize() {
  if (mainWindow && mainWindow.webContents) {
    try {
      const display = screen.getPrimaryDisplay();
      const size = {
        width: display.size.width,
        height: display.size.height
      };
      mainWindow.webContents.send('screen-size', size);
      console.log('Screen size sent:', size.width, 'x', size.height);
    } catch (err) {
      console.error('Failed to get screen size:', err);
    }
  }
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});