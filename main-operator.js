const { app, BrowserWindow } = require('electron');
const path = require('path');

// GPU xatolarini tuzatish
app.disableHardwareAcceleration();

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 650,
    title: 'PixelTechRD Operator',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });
  
  const operatorPath = path.join(__dirname, 'operator', 'index.html');
  mainWindow.loadFile(operatorPath);

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('Failed to load:', errorDescription);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
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