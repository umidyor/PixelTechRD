const fs = require('fs');
const { execSync } = require('child_process');

console.log('Building Operator...');

// Backup original package.json
const originalPackage = fs.readFileSync('package.json', 'utf8');

try {
  // Copy package-operator.json to package.json
  const operatorPackage = fs.readFileSync('package-operator.json', 'utf8');
  fs.writeFileSync('package.json', operatorPackage);
  
  console.log('Running electron-builder...');
  // Run electron-builder
  execSync('electron-builder --config electron-builder-operator.json', { 
    stdio: 'inherit',
    encoding: 'utf8'
  });
  
  console.log('Operator build completed!');
} catch (error) {
  console.error('Build failed:', error.message);
} finally {
  // Always restore original package.json
  console.log('Restoring package.json...');
  fs.writeFileSync('package.json', originalPackage);
}