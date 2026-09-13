"""Test script for timeline widget functionality."""

import threading
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
    marks=((0.0, "Start"), (50.0, "Middle"), (100.0, "End")),
)

# Checkbox to toggle the timeline's visibility, checked by default.
show_timeline_checkbox = server.gui.add_checkbox("Show timeline", initial_value=True)


@show_timeline_checkbox.on_update
def on_show_timeline_toggle(event):
    timeline.visible = show_timeline_checkbox.value


# Add callback for value updates
@timeline.on_update
def on_timeline_update(event):
    print(f"Timeline value updated: {timeline.value}")


# Hard-coded delay (in seconds) between each step of the playback loop, and
# the timeline's min/max/step (must match the values passed to add_timeline).
PLAY_STEP_DELAY = 0.1
TIMELINE_MIN = 0.0
TIMELINE_MAX = 100.0
TIMELINE_STEP = 1.0

is_playing = False


def play_loop():
    """Advance the timeline value by one step every PLAY_STEP_DELAY seconds."""
    global is_playing
    while is_playing:
        next_value = timeline.value + TIMELINE_STEP
        if next_value > TIMELINE_MAX:
            next_value = TIMELINE_MIN
        timeline.value = next_value
        time.sleep(PLAY_STEP_DELAY)


# Add callback for the play/pause button. It fires for both, so we look at
# timeline.playing to decide whether to start or stop the playback loop.
@timeline.on_play
def on_play_button(event):
    global is_playing
    if timeline.playing:
        print("Play button clicked!")
        if is_playing:
            return
        is_playing = True
        threading.Thread(target=play_loop, daemon=True).start()
    else:
        print("Pause button clicked!")
        is_playing = False


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
