const { app, BrowserWindow, screen } = require('electron');
const path = require('path');

// GPU xatolarini tuzatish - BU JUDA MUHIM!
app.disableHardwareAcceleration();

let mainWindow = null;

function createWindow(type) {
  if (type === 'client') {
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
    mainWindow.loadFile('client/index.html');
    
  } else if (type === 'operator') {
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
    mainWindow.loadFile('operator/index.html');
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  const args = process.argv.slice(1);
  
  if (args.includes('--client')) {
    createWindow('client');
  } else if (args.includes('--operator')) {
    createWindow('operator');
  } else {
    createWindow('operator');
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow('operator');
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});