"""Test script for timeline widget functionality."""
import time

import viser

# Create server
server = viser.ViserServer()
print("Viser server started. Open browser to view timeline.")

# Create timeline
timeline = server.add_timeline(
    min=0.0,
    max=100.0,
    step=1.0,
    initial_value=0.0,
    marks=[(0.0, "Start"), (50.0, "Middle"), (100.0, "End")],
)

# Add callback for value updates
@timeline.on_update
def on_timeline_update(event):
    print(f"Timeline value updated: {timeline.value}")

# Add callback for play button
@timeline.on_play
def on_play_button(event):
    print("Play button clicked!")

# Test visibility toggle
print("\nWaiting 5 seconds...")
time.sleep(5)

print("Hiding timeline...")
timeline.visible = False
time.sleep(2)

print("Showing timeline...")
timeline.visible = True
time.sleep(2)

# Test programmatic value change
print("Setting timeline value to 50...")
timeline.value = 50.0
time.sleep(2)

print("\nTimeline test running. Press Ctrl+C to stop.")
print("Try moving the slider and clicking the play button in the browser!")

# Keep server running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nShutting down...")
