# Folder Selection Guide: Getting Absolute Paths

This guide explains how to get absolute folder paths in Viser applications, overcoming browser security limitations.

## The Browser Security Challenge

Modern web browsers **do not allow JavaScript to access absolute file system paths** for security reasons. When using HTML file inputs with the `webkitdirectory` attribute, you only get:
- The folder **name** (e.g., "my_folder")
- **NOT** the absolute path (e.g., "C:/Users/name/Documents/my_folder")

## Solutions Comparison

| Solution | Absolute Paths | Browser Support | Complexity | Best For |
|----------|----------------|-----------------|------------|----------|
| **File System Access API** | ✅ Yes (partial) | Chrome 86+, Edge 86+ | Low | Modern web apps |
| **Server-Side Browser** | ✅ Yes (full) | All browsers | Medium | Production apps |
| **Manual Input** | ✅ Yes | All browsers | Low | Simple cases |
| **Electron/Tauri** | ✅ Yes (full) | Desktop only | High | Desktop apps |

---

## Solution 1: File System Access API (✨ Recommended for Web)

**Status:** ✅ **Already Implemented** in the updated `FolderSelectButton`

### How It Works
- Uses the modern `showDirectoryPicker()` API
- Automatically falls back to legacy method in older browsers
- Provides better path information in supported browsers

### Browser Support
- ✅ Chrome 86+ (2020)
- ✅ Edge 86+ (2020)
- ✅ Opera 72+ (2020)
- ❌ Firefox (as of 2024, behind flag)
- ❌ Safari (as of 2024, not supported)

### Usage
```python
import viser

server = viser.ViserServer()

folder_button = server.gui.add_folder_select_button("Select Folder")

@folder_button.on_select
def handle_selection(event):
    path = event.target.value
    print(f"Selected: {path}")
    # In Chrome/Edge: May get more path info
    # In Firefox/Safari: Falls back to folder name only
```

### Pros
- ✅ No extra dependencies
- ✅ Works in supported browsers
- ✅ Automatic fallback
- ✅ User-friendly dialog

### Cons
- ❌ Limited browser support
- ❌ Path resolution still experimental

---

## Solution 2: Server-Side File Browser (✨ Most Reliable)

**Best for:** Production applications needing guaranteed absolute paths

### How It Works
- Server navigates the file system
- Client displays folders in a GUI tree/dropdown
- No browser security issues

### Implementation
See `example_server_file_browser.py` for complete example.

```python
import viser
from pathlib import Path

def create_file_browser(server: viser.ViserServer, start_path: Path):
    current_path = start_path

    # Create navigation UI
    path_display = server.gui.add_text("Current", str(current_path), disabled=True)
    folder_dropdown = server.gui.add_dropdown("Folders", get_subdirs(current_path))
    enter_button = server.gui.add_button("Enter Folder")
    select_button = server.gui.add_button("Select This Folder")

    @select_button.on_click
    def on_select(event):
        print(f"Absolute path: {current_path.absolute()}")
        return str(current_path.absolute())
```

### Pros
- ✅ **100% reliable absolute paths**
- ✅ Works in all browsers
- ✅ Can enforce permissions server-side
- ✅ Can restrict to specific directories

### Cons
- ❌ More code required
- ❌ Server must have file system access
- ❌ Slightly less intuitive UX

---

## Solution 3: Manual Path Input (✨ Simplest)

**Best for:** Simple use cases, technical users

### Implementation
See `example_manual_path_input.py` for complete example.

```python
import viser
from pathlib import Path

server = viser.ViserServer()

path_input = server.gui.add_text(
    "Folder Path",
    initial_value=str(Path.home()),
    hint="Paste the absolute folder path"
)

validate_button = server.gui.add_button("Validate")

@validate_button.on_click
def validate_path(event):
    path = Path(path_input.value)
    if path.exists() and path.is_dir():
        print(f"Valid path: {path.absolute()}")
        return str(path.absolute())
```

### Pros
- ✅ **Extremely simple**
- ✅ Works everywhere
- ✅ Users can copy/paste paths

### Cons
- ❌ Less user-friendly
- ❌ Prone to typos
- ❌ No visual browsing

---

## Solution 4: Electron/Tauri (Desktop Apps)

**Best for:** Native desktop applications

### Electron Example
```javascript
// Main process
const { dialog } = require('electron');

ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog({
    properties: ['openDirectory']
  });
  return result.filePaths[0]; // Full absolute path
});
```

### Tauri Example
```rust
// Rust backend
use tauri::api::dialog::FileDialogBuilder;

FileDialogBuilder::new()
    .pick_folder(|folder_path| {
        // folder_path is the full absolute path
        println!("Selected: {:?}", folder_path);
    });
```

### Pros
- ✅ **Full file system access**
- ✅ Native OS dialogs
- ✅ Best user experience

### Cons
- ❌ Requires desktop app framework
- ❌ More complex deployment
- ❌ Not web-based

---

## Recommendation by Use Case

### For Web Applications (viser in browser)
1. **First choice:** Use the updated `FolderSelectButton` (File System Access API with fallback)
2. **Production apps:** Implement server-side file browser
3. **Quick prototypes:** Manual path input

### For Desktop Applications
1. **Best:** Use Electron or Tauri
2. **Alternative:** Server-side file browser still works

### For Maximum Compatibility
1. **Combine approaches:** Offer both File System Access API button AND manual input
2. Show appropriate UI based on browser support

---

## Example: Hybrid Approach (Best of Both Worlds)

```python
import viser
from pathlib import Path

server = viser.ViserServer()

# Method 1: File System Access API button
folder_button = server.gui.add_folder_select_button(
    "📁 Browse for Folder",
    hint="Works best in Chrome/Edge"
)

# Method 2: Manual input as fallback
server.gui.add_markdown("**Or enter path manually:**")
path_input = server.gui.add_text(
    "Folder Path",
    initial_value=str(Path.home()),
    hint="Paste absolute path here"
)

selected_path = server.gui.add_text(
    "Selected",
    initial_value="",
    disabled=True
)

@folder_button.on_select
def on_browse(event):
    selected_path.value = event.target.value

@path_input.on_update
def on_manual(event):
    path = Path(event.target.value)
    if path.exists() and path.is_dir():
        selected_path.value = str(path.absolute())
```

---

## Testing Browser Support

Check if File System Access API is available:
```javascript
if ('showDirectoryPicker' in window) {
  console.log('✅ File System Access API supported');
} else {
  console.log('❌ Fallback to legacy method');
}
```

---

## Security Considerations

### File System Access API
- ✅ User must explicitly grant permission
- ✅ Access is isolated per origin
- ✅ Can be revoked by user

### Server-Side Browser
- ⚠️ Server needs careful permission checks
- ⚠️ Restrict browsing to safe directories
- ⚠️ Validate all paths server-side

### Manual Input
- ⚠️ Always validate paths exist
- ⚠️ Check read/write permissions
- ⚠️ Sanitize path strings

---

## Summary

The **updated `FolderSelectButton`** now uses the File System Access API when available, giving you the best possible paths in modern browsers, with automatic fallback for older browsers.

For guaranteed absolute paths across all scenarios, implement a **server-side file browser** as shown in the examples.
