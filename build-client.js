const fs = require('fs');
const { execSync } = require('child_process');

console.log('Building Client...');

// Backup original package.json
const originalPackage = fs.readFileSync('package.json', 'utf8');

try {
  // Copy package-client.json to package.json
  const clientPackage = fs.readFileSync('package-client.json', 'utf8');
  fs.writeFileSync('package.json', clientPackage);
  
  console.log('Running electron-builder...');
  // Run electron-builder
  execSync('electron-builder --config electron-builder-client.json', { 
    stdio: 'inherit',
    encoding: 'utf8'
  });
  
  console.log('Client build completed!');
} catch (error) {
  console.error('Build failed:', error.message);
} finally {
  // Always restore original package.json
  console.log('Restoring package.json...');
  fs.writeFileSync('package.json', originalPackage);
}